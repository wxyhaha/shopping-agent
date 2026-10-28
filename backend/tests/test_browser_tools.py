import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_research_agent import application, observations, seed, start, task_items, tool, wait_run

from app.browser_capture import BrowserPresenceMonitor
from app.capture_urls import product_url_identity
from app.models import BrowserCaptureJob, CaptureSnapshot, utc_now

PAGE_URL = "https://item.jd.com/100001.html"


@pytest.fixture(autouse=True)
def configured_gateway(monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(
            llm_base_url="http://gateway.invalid/v1",
            llm_model="test-model",
        ),
    )


def capture_model(messages, _):
    seen = observations(messages)
    if not seen:
        return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
    captured = next((entry for entry in seen if entry.get("status") == "captured"), None)
    if captured:
        assert captured["freshness"] == "captured_now"
        assert captured["snapshot_id"] != seen[0]["snapshot_id"]
        return tool(
            "submit_report",
            {
                "summary": "已收到新的页面规格资料，价格仍需核实。",
                "outcome": "needs_evidence",
                "findings": [
                    {
                        "text": captured["selected_variant"]["label"],
                        "evidence_refs": [captured["evidence"][0]["ref"]],
                    }
                ],
                "unknowns": ["尚未核实到手价。"],
            },
        )
    if any(entry.get("status") == "unavailable" for entry in seen):
        return tool(
            "ask_user", {"question": "请打开商品页并检查扩展。", "reason": "未收到新的页面资料。"}
        )
    return tool(
        "capture_product_page",
        {"item_id": task_items(messages)[0]["item_id"], "reason": "补读当前选中规格"},
    )


def claim(client, client_id, page_url=PAGE_URL):
    return client.post(
        "/api/v1/browser-captures/claim",
        json={
            "page_url": page_url,
            "client_id": client_id,
            "browser": "edge",
        },
    )


def wait_claim(client, client_id):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        response = claim(client, client_id)
        assert response.status_code == 200
        if response.json()["job"]:
            return response.json()["job"]
        time.sleep(0.03)
    raise AssertionError("Agent did not create a claimable browser job")


def capture_payload(url=PAGE_URL):
    return {
        "adapter_id": "jd-dom",
        "adapter_version": "test",
        "page_url": url,
        "captured_at": utc_now().isoformat(),
        "status": "partial",
        "selected_variant": {
            "status": "observed",
            "label": "合成选中规格：15米扁平网线",
            "evidence_ids": ["e001"],
        },
        "evidence": [
            {
                "evidence_id": "e001",
                "field_path": "selected_variant",
                "source": "visible_dom",
                "excerpt": "合成选中规格：15米扁平网线",
            }
        ],
    }


def complete(client, job, client_id, payload=None, token=None):
    return client.post(
        f"/api/v1/browser-captures/{job['id']}/complete",
        json={
            "client_id": client_id,
            "capture": payload or capture_payload(),
        },
        headers={"X-Capture-Lease": token or job["lease_token"]},
    )


def test_capture_result_is_new_evidence_and_lost_ack_replay_is_idempotent(tmp_path):
    with TestClient(application(tmp_path / "success.sqlite3", capture_model)) as client:
        task_id = seed(client)
        start(client, task_id)
        client_id = str(uuid4())
        job = wait_claim(client, client_id)
        assert claim(client, str(uuid4())).json()["job"] is None
        payload = capture_payload()
        first = complete(client, job, client_id, payload)
        assert first.status_code == 200, first.json()
        replay = complete(client, job, client_id, payload)
        assert replay.json() == first.json()
        run = wait_run(client, task_id, {"completed"})
        assert run["report"]["findings"][0]["text"] == payload["selected_variant"]["label"]
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(CaptureSnapshot)) == 2
        assert "lease_token" not in str(run["events"])
        assert job["lease_token"] not in str(run["events"])


def test_wrong_product_and_wrong_lease_cannot_supply_agent_evidence(tmp_path):
    with TestClient(application(tmp_path / "scope.sqlite3", capture_model)) as client:
        task_id = seed(client)
        start(client, task_id)
        client_id = str(uuid4())
        job = wait_claim(client, client_id)
        assert claim(client, str(uuid4()), "https://item.jd.com/200002.html").json()["job"] is None
        assert claim(client, str(uuid4()), "https://example.com/").status_code == 422
        assert complete(client, job, client_id, token="a" * 43).status_code == 403
        wrong_page = complete(
            client, job, client_id, capture_payload("https://item.jd.com/200002.html")
        )
        assert wrong_page.status_code == 422
        assert complete(client, job, client_id).status_code == 200
        wait_run(client, task_id, {"completed"})


def test_expired_lease_can_be_reclaimed_once_and_old_owner_is_rejected(tmp_path):
    with TestClient(application(tmp_path / "lease.sqlite3", capture_model)) as client:
        task_id = seed(client)
        start(client, task_id)
        owner = str(uuid4())
        first = wait_claim(client, owner)
        with client.app.state.session_factory() as session:
            session.get(BrowserCaptureJob, first["id"]).lease_expires_at = utc_now() - timedelta(
                seconds=1
            )
            session.commit()
        second_owner = str(uuid4())
        second = wait_claim(client, second_owner)
        assert second["id"] == first["id"]
        assert complete(client, first, owner).status_code == 403
        assert complete(client, second, second_owner).status_code == 200
        wait_run(client, task_id, {"completed"})


def test_cancelled_request_rejects_late_results_and_task_delete_cascades(tmp_path):
    with TestClient(application(tmp_path / "cancel.sqlite3", capture_model)) as client:
        task_id = seed(client)
        run = start(client, task_id).json()
        client_id = str(uuid4())
        job = wait_claim(client, client_id)
        assert (
            client.post(f"/api/v1/tasks/{task_id}/research/{run['id']}/cancel").status_code == 200
        )
        assert complete(client, job, client_id).status_code == 409
        wait_run(client, task_id, {"cancelled"})
        assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 204
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(BrowserCaptureJob)) == 0


def test_no_extension_times_out_into_a_real_tool_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("app.browser_capture.JOB_TTL_SECONDS", 0.1)
    with TestClient(application(tmp_path / "timeout.sqlite3", capture_model)) as client:
        task_id = seed(client)
        start(client, task_id)
        run = wait_run(client, task_id, {"needs_input"})
        outputs = [event["data"].get("output", {}) for event in run["events"]]
        assert any(output.get("error_code") == "extension_timeout" for output in outputs)
        assert run["report"] is None


def test_restart_replays_same_browser_job_instead_of_creating_another(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(application(path, capture_model)) as client:
        task_id = seed(client)
        run = start(client, task_id).json()
        job = wait_claim(client, str(uuid4()))
    with TestClient(application(path, capture_model)) as client:
        response = client.post(f"/api/v1/tasks/{task_id}/research/{run['id']}/resume", json={})
        assert response.status_code == 202
        with client.app.state.session_factory() as session:
            row = session.get(BrowserCaptureJob, job["id"])
            row.lease_expires_at = utc_now() - timedelta(seconds=1)
            session.commit()
        owner = str(uuid4())
        resumed_job = wait_claim(client, owner)
        assert resumed_job["id"] == job["id"]
        assert complete(client, resumed_job, owner).status_code == 200
        wait_run(client, task_id, {"completed"})
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(BrowserCaptureJob)) == 1


def test_old_snapshot_timestamp_is_rejected(tmp_path):
    with TestClient(application(tmp_path / "stale.sqlite3", capture_model)) as client:
        task_id = seed(client)
        run = start(client, task_id).json()
        owner = str(uuid4())
        job = wait_claim(client, owner)
        stale = capture_payload()
        stale["captured_at"] = (utc_now() - timedelta(minutes=5)).isoformat()
        assert complete(client, job, owner, stale).status_code == 422
        client.post(f"/api/v1/tasks/{task_id}/research/{run['id']}/cancel")
        wait_run(client, task_id, {"cancelled"})


def test_connection_status_requires_matching_product_and_reports_extension_version(tmp_path):
    with TestClient(application(tmp_path / "connection.sqlite3", capture_model)) as client:
        task_id = seed(client)
        endpoint = f"/api/v1/browser-captures/status/{task_id}"
        assert client.get(endpoint).json()[0]["connected"] is False
        claim(client, str(uuid4()), "https://item.jd.com/200002.html")
        assert client.get(endpoint).json()[0]["connected"] is False
        response = client.post(
            "/api/v1/browser-captures/claim",
            json={
                "page_url": PAGE_URL,
                "client_id": str(uuid4()),
                "browser": "edge",
                "extension_version": "0.2.1",
            },
        )
        assert response.json()["job"] is None
        status = client.get(endpoint).json()[0]
        assert status["connected"] is True
        assert status["extension_version"] == "0.2.1"
        assert status["browser"] == "edge"


def test_page_connection_expires_without_new_extension_messages():
    now = [0.0]
    monitor = BrowserPresenceMonitor(clock=lambda: now[0])
    identity = product_url_identity(PAGE_URL)
    monitor.observe(identity, "chrome", "0.2.1")
    assert monitor.lookup(identity)["connected"] is True
    now[0] = 91
    assert monitor.lookup(identity)["connected"] is False
