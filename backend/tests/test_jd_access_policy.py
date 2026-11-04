"""A paused JD platform must not launch a browser, resolve DNS, or retry login."""

import time

import pytest
from fastapi.testclient import TestClient

from app.background_collector import BackgroundCollector
from app.browser_login import BrowserLoginError, BrowserLoginManager
from app.capture_urls import product_url_identity
from app.collection_worker import collect_output
from app.config import Settings
from app.product_search import ProductSearch
from app.search_schemas import SearchProductsArgs
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
from tests.test_research_agent import (
    configured as configured,
)


def never(*_args, **_kwargs):
    pytest.fail("Paused JD work must not access a browser, DNS, or worker")


@pytest.mark.parametrize("inside_worker", [False, True])
def test_collection_pause_is_enforced_at_parent_and_worker_boundaries(monkeypatch, inside_worker):
    monkeypatch.setattr("app.background_collector.sync_playwright", never)
    monkeypatch.setattr("app.background_collector.run_collection_process", never)
    collector = BackgroundCollector(Settings(_env_file=None), resolver=never)
    url = "https://item.jd.com/100001.html"
    if inside_worker:
        result = collect_output({"url": url}, collector.settings, product_url_identity(url))
        assert result["error_code"] == "jd_automation_disabled"
        assert result["snapshot"]["status"] == "blocked"
    else:
        result = collector.collect(url)
        assert result.error_code == "jd_automation_disabled"
        assert result.snapshot.status == "blocked"
        assert "没有访问商品页" in result.message
        assert "不需要继续尝试登录" in result.message


@pytest.mark.parametrize("inside_worker", [False, True])
def test_search_pause_is_enforced_without_network(monkeypatch, inside_worker):
    monkeypatch.setattr("app.product_search.sync_playwright", never)
    monkeypatch.setattr("app.product_search.run_collection_process", never)
    searcher = ProductSearch(Settings(_env_file=None), resolver=never)
    request = SearchProductsArgs(platform="jd", query="保温杯500ml", reason="暂停策略检查")
    result = (
        searcher._search(request, time.monotonic() + 10, lambda: None)
        if inside_worker
        else searcher.search(request)
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "jd_automation_disabled"
    assert result["candidates"] == []
    assert "本次没有请求平台" in result["note"]


def test_login_pause_does_not_start_a_thread_or_window(monkeypatch):
    manager = BrowserLoginManager(Settings(_env_file=None), launcher=never)
    with pytest.raises(BrowserLoginError, match="已暂停"):
        manager.start("task", "item", "https://item.jd.com/100001.html")
    assert manager.thread is None
    assert manager.state is None
    from scripts import browser_session

    monkeypatch.setattr("scripts.browser_session.get_settings", lambda: manager.settings)
    monkeypatch.setattr("scripts.browser_session.sync_playwright", never)
    monkeypatch.setattr("sys.argv", ["browser_session", "jd"])
    with pytest.raises(SystemExit, match="已暂停"):
        browser_session.main()


def test_pause_does_not_apply_to_taobao_or_tmall():
    collector = BackgroundCollector(Settings(_env_file=None))
    for url in [
        "https://item.taobao.com/item.htm?id=100001",
        "https://detail.tmall.com/item.htm?id=100001",
    ]:
        assert collector._handoff(product_url_identity(url)) is None
    request = SearchProductsArgs(platform="taobao", query="保温杯500ml", reason="暂停策略检查")
    assert ProductSearch(collector.settings)._handoff(request) is None


def test_paused_run_records_handoff_without_claiming_a_fresh_visit(
    tmp_path, configured, monkeypatch
):
    monkeypatch.setattr("app.background_collector.sync_playwright", never)
    monkeypatch.setattr("app.background_collector.run_collection_process", never)

    def model(messages, _):
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        if len(seen) == 1:
            return tool(
                "collect_product_page", {"item_id": seen[0]["item_id"], "reason": "核对关键资料"}
            )
        handoff = seen[1]
        assert handoff["access_code"] == "jd_automation_disabled"
        assert handoff["mode"] == "handoff_required"
        assert handoff["freshness"] == "not_visited"
        assert "不需要继续尝试登录" in handoff["error"]
        assert "可完成独立登录" not in handoff["error"]
        payload = report(handoff["evidence"][0]["ref"])
        payload["summary"] = "京东自动访问已暂停，本次未取得新的商品参数。"
        payload["findings"][0]["text"] = payload["summary"]
        return tool("submit_report", payload)

    app = application(tmp_path / "paused.sqlite3", model)
    app.state.research_runtime.collector = BackgroundCollector(Settings(_env_file=None))
    with TestClient(app) as client:
        task_id = seed(client)
        start(client, task_id)
        result = wait_run(client, task_id, {"completed"})
        assert result["report"]["outcome"] == "needs_evidence"
        assert all(e["kind"] != "question" for e in result["events"])
