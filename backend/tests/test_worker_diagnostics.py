"""Worker failures retain safe diagnostics instead of becoming an unexplained exit."""

import json

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.background_collector import CollectionResult
from app.capture_urls import product_url_identity
from app.collection_worker import collect_output
from app.config import Settings
from app.schemas import CaptureSnapshotSubmit

IDENTITY = product_url_identity("https://item.jd.com/100001.html")
SECRET = "private-account-secret-not-to-log"


def run(monkeypatch, call):
    monkeypatch.setattr("app.collection_worker.BackgroundCollector._collect", call)
    return collect_output({"url": IDENTITY.safe_url}, Settings(_env_file=None), IDENTITY)


@pytest.mark.parametrize(
    "exception,code",
    [
        (RuntimeError(SECRET), "worker_exception"),
        (PlaywrightError(SECRET), "browser_error"),
        (PlaywrightTimeout(SECRET), "browser_timeout"),
    ],
)
def test_failures_are_structured_without_exception_values(monkeypatch, exception, code):
    def fail(*args):
        raise exception

    output = run(monkeypatch, fail)
    assert output["error_code"] == code
    assert SECRET not in json.dumps(output)
    snapshot = CaptureSnapshotSubmit.model_validate(output["snapshot"])
    assert snapshot.status == "failed"
    assert snapshot.evidence[0].locator == code
    assert snapshot.evidence[0].source == "navigation"
    assert not output["detail_read_pending"]


def test_schema_failure_reports_safe_paths_and_types_without_extra_key_values(monkeypatch):
    def fail(*args):
        CaptureSnapshotSubmit.model_validate({"adapter_id": SECRET, SECRET: SECRET})

    output = run(monkeypatch, fail)
    assert output["error_code"] == "invalid_snapshot"
    assert "adapter_version:missing" in output["message"]
    assert SECRET not in json.dumps(output)
    assert output["snapshot"]["status"] == "failed"


def test_oversized_result_has_its_own_reason(monkeypatch):
    monkeypatch.setattr("app.collection_worker.MAX_RESULT_BYTES", 100)
    output = run(monkeypatch, lambda *_: CollectionResult(None, message="x" * 200))
    assert output["error_code"] == "oversized_snapshot"
    assert "超过传输上限" in output["message"]


def test_success_keeps_the_price_pixels_and_detail_marker(monkeypatch):
    output = run(
        monkeypatch,
        lambda *_: CollectionResult(
            None,
            price_screenshot_png="controlled-pixels",
            detail_read_pending=True,
        ),
    )
    assert output["error_code"] is None
    assert output["price_screenshot_png"] == "controlled-pixels"
    assert output["detail_read_pending"] is True
