"""A reread must deliver new captures and in-place asynchronous image enrichment."""

import copy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import CaptureSnapshot, utc_now
from tests.test_research_agent import application, seed


@pytest.fixture
def setup(tmp_path):
    app = application(tmp_path / "refresh.sqlite3", lambda *_: None)
    with TestClient(app) as client:
        task_id = seed(client)
        with app.state.session_factory() as session:
            record = session.scalar(select(CaptureSnapshot))
            item_id = record.item_id
        yield app, task_id, item_id


def test_new_snapshot_is_returned_with_its_actual_evidence(setup):
    app, task_id, item_id = setup
    runtime = app.state.research_runtime
    first, _ = runtime._read_evidence(task_id, item_id)
    with app.state.session_factory() as session:
        original = session.get(CaptureSnapshot, first["snapshot_id"])
        payload = copy.deepcopy(original.payload)
        payload["evidence"][0]["excerpt"] = "优惠前 ￥20"
        session.add(
            CaptureSnapshot(
                item_id=item_id,
                platform="jd",
                adapter_id="test",
                adapter_version="1",
                status="partial",
                captured_at=utc_now(),
                page_url=original.page_url,
                idempotency_key=str(uuid4()),
                request_hash="new",
                payload=payload,
            )
        )
        session.commit()
    updated, refs = runtime._repeat_read(
        task_id, item_id, first["snapshot_id"], first["evidence_version"]
    )
    assert updated["snapshot_id"] != first["snapshot_id"]
    assert updated.get("freshness") != "same_snapshot"
    assert updated["evidence"][0]["excerpt"] == "优惠前 ￥20"
    assert updated["evidence"][0]["ref"] in refs


def test_same_id_with_completed_image_read_refreshes_content(setup):
    app, task_id, item_id = setup
    runtime = app.state.research_runtime
    with app.state.session_factory() as session:
        record = session.scalar(select(CaptureSnapshot))
        record.detail_enrichment = "pending"
        session.commit()
    first, _ = runtime._read_evidence(task_id, item_id)
    assert "尚未完成" in first["image_note"]
    with app.state.session_factory() as session:
        record = session.get(CaptureSnapshot, first["snapshot_id"])
        payload = copy.deepcopy(record.payload)
        payload["fields"] = [
            {
                "field_path": "detail_images.parameter_01",
                "raw_value": "电池6800mAh",
                "evidence_ids": ["detail-text-1"],
            }
        ]
        payload["evidence"].append(
            {
                "evidence_id": "detail-text-1",
                "source": "detail_image_vision",
                "field_path": "detail_images[1].text",
                "excerpt": "电池6800mAh",
            }
        )
        record.payload = payload
        record.detail_enrichment = "done"
        session.commit()
    updated, refs = runtime._repeat_read(
        task_id, item_id, first["snapshot_id"], first["evidence_version"]
    )
    assert updated["snapshot_id"] == first["snapshot_id"]
    assert updated["evidence_version"] != first["evidence_version"]
    assert updated.get("freshness") != "same_snapshot"
    assert updated["detail_parameters"] == ["电池6800mAh"]
    assert "已取得 1 张" in updated["image_note"]
    assert "未经独立核实" in updated["image_note"]
    assert any(ref.endswith(":detail-text-1") for ref in refs)
    repeated, refs = runtime._repeat_read(
        task_id, item_id, updated["snapshot_id"], updated["evidence_version"]
    )
    assert repeated["freshness"] == "same_snapshot"
    assert "evidence" not in repeated
    assert refs == {}


def test_old_checkpoint_without_version_gets_one_full_refresh(setup):
    app, task_id, item_id = setup
    runtime = app.state.research_runtime
    first, _ = runtime._read_evidence(task_id, item_id)
    updated, refs = runtime._repeat_read(task_id, item_id, first["snapshot_id"])
    assert updated["evidence_version"] == first["evidence_version"]
    assert updated["evidence"]
    assert refs
    assert "没有可用" in updated["image_note"]
