"""Real gateway + simulated extension transport; all product data is synthetic."""

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
    page_url = "https://item.jd.com/100000000001.html"
    with TemporaryDirectory(prefix="shopping-browser-probe-") as directory:
        app = create_app(URL.create("sqlite", database=str(Path(directory) / "probe.sqlite3")))
        with TestClient(app) as client:
            task_response = client.post(
                "/api/v1/tasks",
                json={
                    "title": "合成浏览器补采集探针",
                    "items": [{"url": page_url, "source": "web"}],
                },
                headers={"Idempotency-Key": str(uuid4())},
            )
            task_response.raise_for_status()
            task = task_response.json()
            with app.state.session_factory() as session:
                session.add(
                    CaptureSnapshot(
                        item_id=task["items"][0]["id"],
                        platform="jd",
                        adapter_id="jd-dom",
                        adapter_version="synthetic",
                        status="partial",
                        captured_at=utc_now(),
                        page_url=page_url,
                        idempotency_key=str(uuid4()),
                        request_hash="synthetic",
                        payload={"selected_variant": {"status": "missing"}, "evidence": []},
                    )
                )
                session.commit()
            started = time.monotonic()
            client.post(
                f"/api/v1/tasks/{task['id']}/research",
                json={
                    "goal": "这是合成联调，请先读取已有资料，再请求扩展补采集已打开的商品页。"
                    "本次只核对页面当前选中的长度，收到新快照后据此报告；不用核实价格或要求用户手动采集。",
                },
                headers={"Idempotency-Key": str(uuid4())},
            ).raise_for_status()
            client_id, snapshot_id = str(uuid4()), None
            while time.monotonic() - started < 160:
                if not snapshot_id:
                    claim = client.post(
                        "/api/v1/browser-captures/claim",
                        json={
                            "page_url": page_url,
                            "client_id": client_id,
                            "browser": "edge",
                        },
                    )
                    claim.raise_for_status()
                    job = claim.json()["job"]
                    if job:
                        result = client.post(
                            f"/api/v1/browser-captures/{job['id']}/complete",
                            json={
                                "client_id": client_id,
                                "capture": {
                                    "adapter_id": "jd-dom",
                                    "adapter_version": "synthetic",
                                    "page_url": page_url,
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
                                },
                            },
                            headers={"X-Capture-Lease": job["lease_token"]},
                        )
                        result.raise_for_status()
                        snapshot_id = result.json()["snapshot_id"]
                run = client.get(f"/api/v1/tasks/{task['id']}/research").json()[0]
                if run["status"] in {"completed", "needs_input", "failed", "cancelled"}:
                    if not app.state.research_runtime.is_active(run["id"]):
                        break
                time.sleep(0.2)
            tools = [event["data"]["tool"] for event in run["events"] if event["kind"] == "tool"]
            refs = [
                ref
                for finding in (run["report"] or {}).get("findings", [])
                for ref in finding["evidence_refs"]
            ]
            cited = bool(snapshot_id and any(ref.startswith(f"{snapshot_id}:") for ref in refs))
            passed = run["status"] == "completed" and "capture_product_page" in tools and cited
            print(
                json.dumps(
                    {
                        "result": "passed" if passed else "not_passed",
                        "status": run["status"],
                        "tools": tools,
                        "new_snapshot_cited": cited,
                        "model_calls": sum(event["kind"] == "model" for event in run["events"]),
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "browser_transport": "simulated_extension",
                        "data": "synthetic_only",
                        "error": run["error"],
                        "report": run["report"],
                    },
                    ensure_ascii=False,
                )
            )
            if not passed:
                raise RuntimeError("浏览器工具联调未通过；此探针不代表 Windows 扩展验收。")


if __name__ == "__main__":
    main()
