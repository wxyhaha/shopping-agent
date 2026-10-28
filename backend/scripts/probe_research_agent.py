"""Exercise the configured gateway with synthetic evidence in an isolated database."""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import URL

from app.main import create_app
from app.models import CaptureSnapshot, utc_now


def main() -> None:
    with TemporaryDirectory(prefix="shopping-research-probe-") as directory:
        app = create_app(URL.create("sqlite", database=str(Path(directory) / "probe.sqlite3")))
        with TestClient(app) as client:
            task = client.post(
                "/api/v1/tasks",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "title": "合成网线研究探针",
                    "items": [
                        {
                            "url": "https://item.jd.com/100000000001.html",
                            "source": "web",
                            "page_title": "合成网线商品（仅用于工具协议验证）",
                        }
                    ],
                },
            ).json()
            with app.state.session_factory() as session:
                session.add(
                    CaptureSnapshot(
                        item_id=task["items"][0]["id"],
                        platform="jd",
                        adapter_id="synthetic-probe",
                        adapter_version="1",
                        status="partial",
                        captured_at=utc_now(),
                        page_url="https://item.jd.com/100000000001.html",
                        idempotency_key=str(uuid4()),
                        request_hash="synthetic-probe",
                        payload={
                            "fields": [],
                            "prices": [{"raw_text": "优惠前 ￥10", "kind": "list"}],
                            "selected_variant": {"status": "missing"},
                            "coverage": [{"field_path": "selected_variant", "status": "missing"}],
                            "evidence": [
                                {
                                    "evidence_id": "e001",
                                    "field_path": "price",
                                    "source": "visible_dom",
                                    "excerpt": "优惠前 ￥10",
                                }
                            ],
                        },
                    )
                )
                session.commit()
            started = time.monotonic()
            response = client.post(
                f"/api/v1/tasks/{task['id']}/research",
                json={
                    "goal": "这是合成协议验证。我要15米家用网线，请读取商品证据；"
                    "选中长度未知时先向我确认，再给出有依据的阶段性结论。",
                },
                headers={"Idempotency-Key": str(uuid4())},
            )
            if response.status_code != 202:
                raise RuntimeError(f"研究未启动，HTTP {response.status_code}")
            answered = False
            while time.monotonic() - started < 180:
                run = client.get(f"/api/v1/tasks/{task['id']}/research").json()[0]
                active = app.state.research_runtime.is_active(run["id"])
                if run["status"] == "needs_input" and not active:
                    if answered:
                        raise RuntimeError("网关再次请求确认，探针未完成。")
                    resumed = client.post(
                        f"/api/v1/tasks/{task['id']}/research/{run['id']}/resume",
                        json={
                            "answer": "合成用户确认：所选长度是15米，尚未核实到手价。"
                            "请给出阶段性结论。"
                        },
                    )
                    if resumed.status_code != 202:
                        raise RuntimeError("人工确认后恢复失败。")
                    answered = True
                if run["status"] in {"completed", "failed", "cancelled"} and not active:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("探针等待超时。")
            tools = [
                event["data"]["tool"]
                for event in run["events"]
                if event["kind"] in {"tool", "question"}
            ]
            passed = (
                run["status"] == "completed"
                and answered
                and all(
                    name in tools for name in ["read_product_evidence", "ask_user", "submit_report"]
                )
            )
            print(
                json.dumps(
                    {
                        "result": "passed" if passed else "not_passed",
                        "status": run["status"],
                        "tools": tools,
                        "resumed_after_answer": answered,
                        "model_calls": sum(event["kind"] == "model" for event in run["events"]),
                        "usage": [
                            event["data"].get("usage", {})
                            for event in run["events"]
                            if event["kind"] == "model"
                        ],
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "error": run["error"],
                        "data": "synthetic_only",
                        "report": run["report"],
                    },
                    ensure_ascii=False,
                )
            )
            if not passed:
                raise RuntimeError("真实网关工具循环未通过，见上方脱敏结果。")


if __name__ == "__main__":
    main()
