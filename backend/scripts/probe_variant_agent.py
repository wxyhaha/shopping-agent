"""Real gateway chooses SKU IDs against a real Chromium synthetic page; no account data."""

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

PAGE = """<!doctype html><meta charset="utf-8"><h1 class="sku-name">合成网线商品</h1>
<div data-sku-group="线型"><button data-sku-option aria-pressed="true">圆线 超六类</button>
<button data-sku-option>扁平 超六类</button></div>
<div data-sku-group="长度"><button data-sku-option aria-pressed="true">1米</button>
<button data-sku-option>10米</button></div><div id="summary-price">优惠前 ￥9.00</div>
<script>document.addEventListener('click',e=>{const n=e.target.closest('[data-sku-option]');
if(!n)return;
n.parentElement.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));
n.setAttribute('aria-pressed','true');setTimeout(()=>{
document.querySelector('#summary-price').textContent='优惠前 ￥68.00'},400)})</script>"""


def main():
    with TemporaryDirectory(prefix="shopping-variant-probe-") as directory:
        app = create_app(URL.create("sqlite", database=str(Path(directory) / "probe.sqlite3")))
        app.state.research_runtime.collector = BackgroundCollector(
            Settings(_env_file=None, data_dir=Path(directory), browser_max_scrolls=0),
            route_fixture=lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=PAGE
            ),
            resolver=lambda _: ["93.184.216.34"],
        )
        with TestClient(app) as client:
            task = client.post(
                "/api/v1/tasks",
                json={
                    "title": "规格核验合成探针",
                    "items": [{"url": "https://item.jd.com/100000000001.html", "source": "web"}],
                },
                headers={"Idempotency-Key": str(uuid4())},
            ).json()
            started = time.monotonic()
            client.post(
                f"/api/v1/tasks/{task['id']}/research",
                json={
                    "goal": "这是合成测试，请自行读取可选规格，"
                    "选择线型为扁平超六类、长度为10米的组合，"
                    "核对实际选中状态并报告页面优惠前价格及其条件。本轮只研究这两个规格和页面价格，"
                    "不搜索、不核实品牌，无需用户选择规格。请引用选择后的新证据。"
                },
                headers={"Idempotency-Key": str(uuid4())},
            ).raise_for_status()
            while time.monotonic() - started < 170:
                run = client.get(f"/api/v1/tasks/{task['id']}/research").json()[0]
                if run["status"] in {
                    "completed",
                    "needs_input",
                    "failed",
                } and not app.state.research_runtime.is_active(run["id"]):
                    break
                time.sleep(0.2)
            events = [e for e in run["events"] if e["kind"] == "tool"]
            tools = [e["data"]["tool"] for e in events]
            selections = [
                e["data"]["output"] for e in events if e["data"]["tool"] == "select_product_variant"
            ]
            refs = [
                ref
                for finding in (run["report"] or {}).get("findings", [])
                for ref in finding["evidence_refs"]
            ]
            verified = [
                s
                for s in selections
                if (s.get("variant_verification") or {}).get("status") == "selected"
            ]
            cited = any(ref.startswith(f"{s['snapshot_id']}:") for s in verified for ref in refs)
            passed = run["status"] == "completed" and cited and "ask_user" not in tools
            print(
                json.dumps(
                    {
                        "result": "passed" if passed else "not_passed",
                        "status": run["status"],
                        "tools": tools,
                        "selected_snapshot_cited": cited,
                        "model_calls": sum(e["kind"] == "model" for e in run["events"]),
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "data": "synthetic_only",
                        "browser": "real_chromium",
                        "error": run["error"],
                    },
                    ensure_ascii=False,
                )
            )
            if not passed:
                raise SystemExit("规格工具真实网关联调未通过，不代表真实平台验收。")


if __name__ == "__main__":
    main()
