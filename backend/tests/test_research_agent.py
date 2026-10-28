import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL

from app.agent_schemas import ResearchReport
from app.main import create_app
from app.models import CaptureSnapshot, utc_now
from app.research_agent import AgentFailure


def tool(name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": str(uuid4()),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def observations(messages):
    return [json.loads(message["content"]) for message in messages if message["role"] == "tool"]


def task_items(messages):
    return json.loads(messages[1]["content"])["items"]


def report(ref):
    return {
        "summary": "目前只能确认页面标价，不能确认所选规格到手价。",
        "findings": [{"text": "页面标为优惠前价格。", "evidence_refs": [ref]}],
        "unknowns": ["所选长度与最终支付价尚待核实。"],
    }


def wait_run(client, task_id, expected):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        run = client.get(f"/api/v1/tasks/{task_id}/research").json()[0]
        if run["status"] in expected and not client.app.state.research_runtime.is_active(run["id"]):
            return run
        time.sleep(0.02)
    raise AssertionError(f"run did not reach {expected}: {run['status']} {run['error']}")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(
            llm_base_url="http://gateway.invalid/v1",
            llm_model="test-model",
        ),
    )


def application(path, model):
    app = create_app(URL.create("sqlite", database=path.as_posix()))
    app.state.research_runtime.model_call = model
    return app


def seed(client):
    task = client.post(
        "/api/v1/tasks",
        json={
            "title": "网线比较",
            "items": [{"url": "https://item.jd.com/100001.html", "source": "web"}],
        },
        headers={"Idempotency-Key": str(uuid4())},
    ).json()
    with client.app.state.session_factory() as session:
        session.add(
            CaptureSnapshot(
                item_id=task["items"][0]["id"],
                platform="jd",
                adapter_id="test",
                adapter_version="1",
                status="partial",
                captured_at=utc_now(),
                page_url="https://item.jd.com/100001.html",
                idempotency_key=str(uuid4()),
                request_hash="synthetic",
                payload={
                    "evidence": [
                        {
                            "evidence_id": "e001",
                            "excerpt": "优惠前 ￥10",
                            "source": "visible_dom",
                            "field_path": "price",
                        }
                    ],
                    "coverage": [{"field_path": "selected_variant", "status": "missing"}],
                },
            )
        )
        session.commit()
    return task["id"]


def start(client, task_id, key=None):
    return client.post(
        f"/api/v1/tasks/{task_id}/research",
        json={"goal": "比较网线"},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_pause_restart_resume_preserves_tool_context(tmp_path, configured):
    def model(messages, _):
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        if not any("answer" in result for result in seen):
            return tool(
                "ask_user", {"question": "需要多长的网线？", "reason": "长度影响规格和价格。"}
            )
        return tool("submit_report", report(seen[0]["evidence"][0]["ref"]))

    path = tmp_path / "research.sqlite3"
    with TestClient(application(path, model)) as client:
        task_id = seed(client)
        assert start(client, task_id).status_code == 202
        paused = wait_run(client, task_id, {"needs_input"})
        assert paused["question"]["question"] == "需要多长的网线？"
        assert start(client, task_id).status_code == 409
    with TestClient(application(path, model)) as client:
        response = client.post(
            f"/api/v1/tasks/{task_id}/research/{paused['id']}/resume",
            json={"answer": "需要15米，价格还未确认。"},
        )
        assert response.status_code == 202
        completed = wait_run(client, task_id, {"completed"})
        assert completed["report"]["findings"][0]["evidence_refs"]
        assert any(event["kind"] == "answer" for event in completed["events"])
        assert sum(event["kind"] == "model" for event in completed["events"]) == 3
        assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 204
    import sqlite3

    with sqlite3.connect(tmp_path / "agent_checkpoints.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0


def test_invalid_citation_is_returned_to_model_before_acceptance(tmp_path, configured):
    def model(messages, _):
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        ref = "fabricated:e999" if len(seen) == 1 else seen[0]["evidence"][0]["ref"]
        return tool("submit_report", report(ref))

    with TestClient(application(tmp_path / "citation.sqlite3", model)) as client:
        task_id = seed(client)
        start(client, task_id)
        completed = wait_run(client, task_id, {"completed"})
        assert any(
            "不存在" in event["data"].get("output", {}).get("error", "")
            for event in completed["events"]
        )
        assert completed["report"]["findings"][0]["evidence_refs"] != ["fabricated:e999"]


def test_gateway_failure_retry_and_idempotent_start(tmp_path, configured):
    attempts = 0

    def model(messages, _):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AgentFailure("模型渠道连接失败或超时，可重试此运行。")
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        return tool("submit_report", report(seen[0]["evidence"][0]["ref"]))

    with TestClient(application(tmp_path / "retry.sqlite3", model)) as client:
        task_id = seed(client)
        key = str(uuid4())
        first = start(client, task_id, key).json()
        failed = wait_run(client, task_id, {"failed"})
        assert start(client, task_id, key).json()["id"] == first["id"]
        assert (
            client.post(
                f"/api/v1/tasks/{task_id}/research/{failed['id']}/resume", json={}
            ).status_code
            == 202
        )
        assert wait_run(client, task_id, {"completed"})["id"] == first["id"]


def test_foreign_item_cannot_be_read_and_run_stops_at_limit(tmp_path, configured, monkeypatch):
    monkeypatch.setattr("app.research_agent.MAX_TURNS", 3)

    def model(messages, _):
        return tool("read_product_evidence", {"item_id": "outside-this-task"})

    with TestClient(application(tmp_path / "scope.sqlite3", model)) as client:
        task_id = seed(client)
        start(client, task_id)
        failed = wait_run(client, task_id, {"failed"})
        outputs = [event["data"]["output"] for event in failed["events"] if event["kind"] == "tool"]
        assert len(outputs) == 3
        assert all(output == {"error": "商品不属于此任务。"} for output in outputs)
        assert failed["report"] is None


def test_cancel_waiting_run_cannot_resume(tmp_path, configured):
    def model(messages, _):
        return tool("ask_user", {"question": "使用场景是什么？", "reason": "需要明确需求。"})

    with TestClient(application(tmp_path / "cancel.sqlite3", model)) as client:
        task_id = seed(client)
        start(client, task_id)
        run = wait_run(client, task_id, {"needs_input"})
        path = f"/api/v1/tasks/{task_id}/research/{run['id']}"
        assert client.post(f"{path}/cancel").json()["status"] == "cancelled"
        assert client.post(f"{path}/resume", json={"answer": "家用"}).status_code == 409


def test_repeat_read_identifies_same_snapshot_and_keeps_price_limits(tmp_path, configured):
    def model(messages, _):
        seen = observations(messages)
        if len(seen) < 2:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        assert seen[0]["freshness"] == "new_to_this_run"
        assert seen[1]["freshness"] == "same_snapshot"
        assert seen[0]["snapshot_id"] == seen[1]["snapshot_id"]
        assert "优惠前不表示起售价" in seen[1]["observation_limits"]["price_label"]
        assert "用户未选择规格" in seen[1]["observation_limits"]["missing_fields"]
        return tool("submit_report", report(seen[1]["evidence"][0]["ref"]))

    with TestClient(application(tmp_path / "freshness.sqlite3", model)) as client:
        task_id = seed(client)
        start(client, task_id)
        run = wait_run(client, task_id, {"completed"})
        assert run["report"]["outcome"] == "needs_evidence"


def test_legacy_report_is_displayed_as_partial_instead_of_recommendation():
    legacy = report("snapshot:e001")
    assert ResearchReport.model_validate(legacy).outcome == "needs_evidence"
