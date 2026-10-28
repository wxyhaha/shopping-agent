"""Real Chromium against controlled synthetic pages; no shopping account or extension."""

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_schemas import CaptureProductPageArgs
from app.background_collector import (
    BackgroundCollector,
    BrowserNetworkPolicy,
    CollectionResult,
    access_gate,
    navigation_destination,
    unavailable_snapshot,
)
from app.capture_urls import product_url_identity
from app.config import Settings
from app.models import AgentRun, CaptureSnapshot, ProductItem
from app.research_agent import AgentStopped
from tests.test_research_agent import (
    application,
    observations,
    report,
    seed,
    start,
    task_items,
    tool,
    wait_run,
)

PAGE = """<!doctype html><html><head><title>合成网线商品</title></head><body>
<h1 class="sku-name">合成超六类扁平家用网线</h1><div id="sellerName">合成测试店</div>
<div id="summary-price">优惠前 ￥39.00</div>
<div id="parameter"><ul><li>品牌：合成品牌</li><li>线缆长度：10米</li></ul></div>
<div id="choose-attrs"><div class="selected">10米 扁平 超六类</div></div>
<div style="height:2000px"></div><div id="detail"></div>
<script>setTimeout(() => {
document.querySelector('#summary-price').textContent='优惠前 ￥42.00'}, 500);
addEventListener('scroll',()=> {document.querySelector('#detail').innerHTML=
'<p>合成详情资料</p>'});</script></body></html>"""


def fixture_route(html=PAGE):
    return lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=html
    )


def collector(tmp_path, html=PAGE):
    return BackgroundCollector(
        Settings(_env_file=None, data_dir=tmp_path, browser_max_scrolls=2),
        route_fixture=fixture_route(html),
        resolver=lambda _: ["93.184.216.34"],
    )


def test_headless_opens_dynamic_page_and_extracts_shared_evidence(tmp_path):
    result = collector(tmp_path).collect("https://item.jd.com/100001.html?tracking=private")
    assert result.error_code is None
    snapshot = result.snapshot
    assert snapshot.adapter_id == "jd-playwright"
    assert snapshot.status in {"partial", "complete"}
    assert snapshot.selected_variant.status == "observed"
    assert "10米" in snapshot.selected_variant.label
    assert any("42.00" in entry.excerpt for entry in snapshot.evidence)
    assert not any("39.00" in entry.excerpt for entry in snapshot.evidence)
    assert "tracking" not in snapshot.page_url


def test_login_is_saved_as_access_evidence_not_product_data(tmp_path):
    result = collector(tmp_path, "<title>登录</title><body>请先登录 扫码登录</body>").collect(
        "https://detail.tmall.com/item.htm?id=100001"
    )
    assert result.error_code == "login_required"
    assert result.snapshot.status == "blocked"
    assert result.snapshot.fields[0].field_path == "page.access"
    assert result.snapshot.evidence[0].source == "navigation"
    assert result.snapshot.prices == []


def test_network_rejects_private_addresses_and_foreign_domains():
    public = BrowserNetworkPolicy(lambda _: ["93.184.216.34"])
    private = BrowserNetworkPolicy(lambda _: ["127.0.0.1", "93.184.216.34"])
    assert public.allows("https://item.jd.com/100001.html")
    for url in [
        "http://item.jd.com/1.html",
        "https://evil.jd.com.attacker.invalid/",
        "https://item.jd.com:8000/",
        "https://user:password@item.jd.com/",
        "http://127.0.0.1/",
        "file:///etc/passwd",
    ]:
        assert not public.allows(url)
    assert not private.allows("https://item.jd.com/100001.html")
    fake_ip = BrowserNetworkPolicy(lambda _: ["198.18.0.113"])
    assert not fake_ip.allows("https://detail.tmall.com/item.htm?id=1")
    trusted = BrowserNetworkPolicy(lambda _: ["198.18.0.113"], trust_fake_ip_dns=True)
    assert trusted.allows("https://detail.tmall.com/item.htm?id=1")
    assert not BrowserNetworkPolicy(lambda _: ["192.168.1.1"], trust_fake_ip_dns=True).allows(
        "https://detail.tmall.com/item.htm?id=1"
    )
    identity = product_url_identity("https://item.jd.com/100001.html")
    assert navigation_destination("https://item.jd.com/100001.html?canonical=1", identity) is None
    assert (
        access_gate("https://passport.jd.com/new/login.aspx", {}, identity, 200)[0]
        == "login_required"
    )
    assert access_gate("https://item.jd.com/100002.html", {}, identity, 200)[0] == "redirected"


def test_cancellation_closes_browser_and_releases_slot(tmp_path):
    service = collector(tmp_path)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks >= 5:
            raise AgentStopped()

    with pytest.raises(AgentStopped):
        service.collect("https://item.jd.com/100001.html", check_cancel=cancelled)
    assert service.slots.acquire(blocking=False)
    assert service.slots.acquire(blocking=False)
    service.slots.release()
    service.slots.release()


def test_agent_backend_tool_persists_cites_and_replays_without_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(llm_base_url="http://gateway.invalid/v1", llm_model="test-model"),
    )

    def model(messages, _):
        seen = observations(messages)
        item_id = task_items(messages)[0]["item_id"]
        if not seen:
            return tool("read_product_evidence", {"item_id": item_id})
        if len(seen) == 1:
            return tool("collect_product_page", {"item_id": item_id, "reason": "核对新规格证据"})
        assert seen[-1]["mode"] == "headless"
        assert seen[-1]["freshness"] == "captured_now"
        return tool("submit_report", report(seen[-1]["evidence"][0]["ref"]))

    app = application(tmp_path / "integration.sqlite3", model)
    service = collector(tmp_path)
    app.state.research_runtime.collector = service
    with TestClient(app) as client:
        task_id = seed(client)
        start(client, task_id)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = client.get(f"/api/v1/tasks/{task_id}/research").json()[0]
            if run["status"] in {
                "completed",
                "failed",
            } and not app.state.research_runtime.is_active(run["id"]):
                break
            time.sleep(0.05)
        assert run["status"] == "completed", run["error"]
        assert client.get("/api/v1/collection/status").json()["requires_open_product_page"] is False
        event = next(
            e
            for e in run["events"]
            if e["kind"] == "tool" and e["data"]["tool"] == "collect_product_page"
        )
        snapshot_id = event["data"]["output"]["snapshot_id"]
        assert run["report"]["findings"][0]["evidence_refs"][0].startswith(snapshot_id)
        with app.state.session_factory() as session:
            assert len(session.scalars(select(CaptureSnapshot)).all()) == 2
            stored = session.get(AgentRun, run["id"])
            stored.status = "running"
            session.commit()
            item_id = session.scalar(select(ProductItem.id))
        call_id = next(
            e["data"]["output"]["tool_calls"][0]["id"]
            for e in run["events"]
            if e["kind"] == "model"
            and e["data"]["output"]["tool_calls"][0]["function"]["name"] == "collect_product_page"
        )
        service.collect = lambda *args, **kwargs: pytest.fail("replay must reuse saved operation")
        result, _ = app.state.research_runtime._collect_page(
            run["id"],
            task_id,
            call_id,
            CaptureProductPageArgs(item_id=item_id, reason="核对新规格证据"),
            time.monotonic() + 20,
        )
        assert result["snapshot_id"] == snapshot_id
        with app.state.session_factory() as session:
            session.get(AgentRun, run["id"]).status = "completed"
            session.commit()


def test_failed_attempt_budget_and_late_cancel_are_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(llm_base_url="http://gateway.invalid/v1", llm_model="test-model"),
    )
    app = application(
        tmp_path / "limits.sqlite3",
        lambda *_: tool("ask_user", {"question": "需要什么规格？", "reason": "先确定用户需求"}),
    )
    with TestClient(app) as client:
        task_id = seed(client)
        start(client, task_id)
        run = wait_run(client, task_id, {"needs_input"})
        runtime = app.state.research_runtime
        with app.state.session_factory() as session:
            session.get(AgentRun, run["id"]).status = "running"
            item_id = session.scalar(select(ProductItem.id))
            session.commit()
        calls = []

        def fail(url, **_):
            calls.append(url)
            return CollectionResult(None, "network_error", "合成网络失败")

        runtime.collector = SimpleNamespace(collect=fail)
        args = CaptureProductPageArgs(item_id=item_id, reason="核对规格")
        for call_id in ["one", "two", "one", "three"]:
            result, _ = runtime._collect_page(
                run["id"], task_id, call_id, args, time.monotonic() + 20
            )
            assert result["error"] == "合成网络失败"
        result, _ = runtime._collect_page(run["id"], task_id, "four", args, time.monotonic() + 20)
        assert "上限" in result["error"]
        assert len(calls) == 3
        with app.state.session_factory() as session:
            record = session.get(AgentRun, run["id"])
            record.events = []
            session.commit()

        def cancel_during_read(url, **_):
            with app.state.session_factory() as session:
                session.get(AgentRun, run["id"]).status = "cancelled"
                session.commit()
            return CollectionResult(
                unavailable_snapshot(product_url_identity(url), "test", "合成受阻")
            )

        runtime.collector = SimpleNamespace(collect=cancel_during_read)
        with pytest.raises(AgentStopped):
            runtime._collect_page(run["id"], task_id, "late", args, time.monotonic() + 20)
        with app.state.session_factory() as session:
            assert len(session.scalars(select(CaptureSnapshot)).all()) == 1
