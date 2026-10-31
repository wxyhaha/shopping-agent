"""Real configured model and Chromium; synthetic marketplace pages, isolated data."""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import URL

from app.background_collector import BackgroundCollector
from app.config import Settings
from app.main import create_app
from app.models import CaptureSnapshot, utc_now
from app.product_search import ProductSearch


def main():
    with TemporaryDirectory(prefix="shopping-search-probe-") as directory:
        root = Path(directory)
        app = create_app(URL.create("sqlite", database=(root / "probe.sqlite3").as_posix()))
        settings = Settings(_env_file=None, data_dir=root)

        def search_fixture(route):
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body=(
                    '<h1>合成搜索结果</h1><a href="https://item.jd.com/100002.html">'
                    "合成品牌乙 1L 水壶</a>"
                ),
            )

        def product_fixture(route):
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body=(
                    '<h1>合成品牌乙 1L 水壶</h1><ul id="parameter">'
                    '<li title="品牌：合成品牌乙">品牌：合成品牌乙</li>'
                    '<li title="容量：1L">容量：1L</li></ul>'
                ),
            )

        app.state.research_runtime.searcher = ProductSearch(
            settings, route_fixture=search_fixture, resolver=lambda _: ["1.1.1.1"]
        )
        app.state.research_runtime.collector = BackgroundCollector(
            settings, route_fixture=product_fixture, resolver=lambda _: ["1.1.1.1"]
        )
        with TestClient(app) as client:
            task_response = client.post(
                "/api/v1/tasks",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "title": "合成替代搜索验证",
                    "items": [
                        {
                            "url": "https://item.jd.com/100001.html",
                            "page_title": "合成品牌甲水壶",
                            "source": "web",
                        }
                    ],
                },
            )
            task_response.raise_for_status()
            task = task_response.json()
            with app.state.session_factory() as session:
                session.add(
                    CaptureSnapshot(
                        item_id=task["items"][0]["id"],
                        platform="jd",
                        adapter_id="synthetic-probe",
                        adapter_version="1",
                        status="partial",
                        captured_at=utc_now(),
                        page_url=task["items"][0]["url"],
                        idempotency_key=str(uuid4()),
                        request_hash="synthetic",
                        payload={
                            "fields": [
                                {
                                    "field_path": "product.brand",
                                    "raw_value": "合成品牌甲",
                                    "status": "observed",
                                    "evidence_ids": ["brand"],
                                }
                            ],
                            "evidence": [
                                {
                                    "evidence_id": "brand",
                                    "field_path": "product.brand",
                                    "source": "visible_dom",
                                    "excerpt": "品牌：合成品牌甲",
                                },
                                {
                                    "evidence_id": "capacity",
                                    "field_path": "specifications.capacity",
                                    "source": "visible_dom",
                                    "excerpt": "容量：1L",
                                },
                            ],
                        },
                    )
                )
                session.commit()
            started = time.monotonic()
            client.post(
                f"/api/v1/tasks/{task['id']}/research",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "goal": "合成工具协议验收：请从京东搜索其他品牌的1L水壶作为此商品的替代品。"
                    "硬条件仅为容量1L和不同品牌；价格、颜色、材质均不作为硬条件。"
                    "先读原商品，登记条件后搜索、加入候选、读取详情核验，提交引用报告。"
                    "所有页面由测试环境提供，无需人工确认；不要推测未提供的价格。",
                },
            ).raise_for_status()
            while time.monotonic() - started < 180:
                run = client.get(f"/api/v1/tasks/{task['id']}/research").json()[0]
                if run["status"] in {
                    "completed",
                    "needs_input",
                    "failed",
                    "cancelled",
                } and not app.state.research_runtime.is_active(run["id"]):
                    break
                time.sleep(0.2)
            tools = [
                event["data"].get("tool")
                for event in run["events"]
                if event["kind"] in {"tool", "question"}
            ]
            passed = (
                run["status"] == "completed"
                and any(
                    entry["status"] == "qualified"
                    for entry in (run.get("report") or {}).get("alternatives", [])
                )
                and all(
                    name in tools
                    for name in (
                        "search_products",
                        "adopt_search_candidate",
                        "collect_product_page",
                    )
                )
            )
            print(
                json.dumps(
                    {
                        "passed": passed,
                        "scope": "real_gateway_synthetic_marketplace",
                        "status": run["status"],
                        "seconds": round(time.monotonic() - started, 2),
                        "model_calls": sum(event["kind"] == "model" for event in run["events"]),
                        "tools": tools,
                        "error": run["error"],
                        "tool_errors": [
                            event["data"]["output"]["error"]
                            for event in run["events"]
                            if event["kind"] == "tool"
                            and "error" in event["data"].get("output", {})
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            if not passed:
                raise SystemExit(1)


if __name__ == "__main__":
    main()
