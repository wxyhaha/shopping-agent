"""Real model + real headless Chromium; controlled synthetic merchant HTML only."""

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

PAGE = """<!doctype html><meta charset="utf-8"><title>合成商品</title>
<h1 class="sku-name">合成家用扁平网线</h1>
<div id="choose-attrs"><span class="selected">10米 扁平 超六类</span></div>
<div id="summary-price">优惠前 ￥42.00</div><div id="sellerName">合成测试店</div>
<div id="parameter"><ul><li>品牌：合成品牌</li><li>线缆长度：10米</li></ul></div>"""


def main():
    with TemporaryDirectory(prefix="shopping-background-probe-") as directory:
        app = create_app(URL.create("sqlite", database=str(Path(directory) / "probe.sqlite3")))
        app.state.research_runtime.collector = BackgroundCollector(
            Settings(_env_file=None, data_dir=Path(directory), browser_max_scrolls=1),
            route_fixture=lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=PAGE
            ),
            resolver=lambda _: ["93.184.216.34"],
        )
        with TestClient(app) as client:
            task = client.post(
                "/api/v1/tasks",
                json={
                    "title": "后台采集合成探针",
                    "items": [{"url": "https://item.jd.com/100000000001.html", "source": "web"}],
                },
                headers={"Idempotency-Key": str(uuid4())},
            ).json()
            started = time.monotonic()
            client.post(
                f"/api/v1/tasks/{task['id']}/research",
                json={
                    "goal": "这是受控合成测试。请先读已有证据，缺失时自行后台打开任务商品链接采集。"
                    "只核对当前选中的规格是否包含10米、扁平、超六类，并用新证据报告。"
                    "本轮不研究价格、不搜索、不要求扩展或用户操作。",
                },
                headers={"Idempotency-Key": str(uuid4())},
            ).raise_for_status()
            while time.monotonic() - started < 170:
                run = client.get(f"/api/v1/tasks/{task['id']}/research").json()[0]
                if run["status"] in {
                    "completed",
                    "needs_input",
                    "failed",
                    "cancelled",
                } and not app.state.research_runtime.is_active(run["id"]):
                    break
                time.sleep(0.2)
            events = [e for e in run["events"] if e["kind"] == "tool"]
            tools = [e["data"]["tool"] for e in events]
            snapshots = [
                e["data"]["output"].get("snapshot_id")
                for e in events
                if e["data"]["tool"] == "collect_product_page"
            ]
            refs = [
                ref
                for finding in (run["report"] or {}).get("findings", [])
                for ref in finding["evidence_refs"]
            ]
            cited = any(ref.startswith(f"{snapshot}:") for snapshot in snapshots for ref in refs)
            passed = run["status"] == "completed" and cited and "capture_product_page" not in tools
            print(
                json.dumps(
                    {
                        "result": "passed" if passed else "not_passed",
                        "status": run["status"],
                        "tools": tools,
                        "new_snapshot_cited": cited,
                        "model_calls": sum(e["kind"] == "model" for e in run["events"]),
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "browser": "real_headless_chromium",
                        "data": "synthetic_only",
                        "error": run["error"],
                    },
                    ensure_ascii=False,
                )
            )
            if not passed:
                raise SystemExit("后台采集模型联调未通过；不代表真实商品覆盖验收。")


if __name__ == "__main__":
    main()
