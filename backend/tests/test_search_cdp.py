"""Search attaches to an owned test browser without touching its existing pages."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.product_search import ProductSearch, search_block_note, search_gate
from app.search_schemas import SearchProductsArgs


@pytest.fixture(scope="module")
def browser_session(tmp_path_factory):
    profile = tmp_path_factory.mktemp("search-cdp-profile")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile, headless=True, args=["--remote-debugging-port=0"]
        )
        port = (profile / "DevToolsActivePort").read_text().splitlines()[0]
        keeper = context.pages[0]
        keeper.set_content("<p>用户保留的标签</p>")
        yield SimpleNamespace(context=context, keeper=keeper, url=f"http://127.0.0.1:{port}")
        context.close()


@pytest.fixture
def origin(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            if query == "跳转测试":
                self.send_response(302)
                self.send_header("Location", "http://blocked.invalid/forbidden")
                self.end_headers()
                return
            if query == "验证测试":
                body = '<p>请完成验证</p><a href="https://detail.tmall.com/item.htm?id=100002">商品</a>'
            elif query == "空结果测试":
                body = "<p>没有找到符合条件的商品</p>"
            elif query == "空白测试":
                body = "<h1>搜索中</h1>"
            elif query == "京东卡片测试":
                body = """<h1>搜索结果</h1>
                  <div data-sku="900001">无卡片形态的无关数据</div>
                  <div class="plugin_goodsCardWrapper" data-sku="100002">
                    <div class="_goods_title_container_test">
                      <span title="合成保温杯500ml">合成杯</span></div>
                  </div>
                  <div class="plugin_goodsCardWrapper" data-sku="100004" style="display:none">
                    <div class="_goods_title">隐藏杯子</div></div>
                  <div class="plugin_goodsCardWrapper" data-sku="12e3">
                    <div class="_goods_title">非法编号</div></div>
                  <div class="plugin_goodsCardWrapper" data-sku="100005">缺少标题</div>
                  <div class="gl-item" data-sku="100003"><div class="p-name">旧版合成杯</div></div>
                  <a href="https://item.jd.com/100002.html">同一商品重复链接</a>"""
            else:
                delay = 5200 if query == "延迟测试" else 0
                body = f"""<h1>搜索结果</h1><script>setTimeout(()=>{{
                  const a=document.createElement('a');
                  a.href='https://detail.tmall.com/item.htm?id=100002';
                  a.textContent='合成品牌乙保温杯500ml';document.body.append(a);
                }}, {delay});</script>"""
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    # Local HTTP/DNS exceptions are limited to this test. Production keeps its public-HTTPS policy.
    monkeypatch.setattr(
        "app.product_search.SEARCH_HOSTS", {"taobao": "127.0.0.1", "jd": "127.0.0.1"}
    )
    monkeypatch.setattr(
        "app.product_search.search_url",
        lambda platform, query: base + "/search?q=" + query,
    )
    monkeypatch.setattr(
        "app.product_search.BrowserNetworkPolicy",
        lambda *a, **kw: SimpleNamespace(allows=lambda url: url.startswith(base + "/")),
    )
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def service(tmp_path, browser_session):
    return ProductSearch(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            jd_background_enabled=True,
            browser_cdp_enabled=True,
            browser_cdp_url=browser_session.url,
        )
    )


def request(query="保温杯测试"):
    return SearchProductsArgs(platform="taobao", query=query, reason="受控搜索验收")


def run_search(searcher, args, browser_session, cancel=lambda: None):
    # The fixture owns a Playwright loop in this thread. Production search owns another
    # client in its worker thread/process, so run it on a separate thread here as well.
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(searcher._search, args, time.monotonic() + 15, cancel).result(20)
    finally:
        # Drain the fixture client's tab events before inspecting its cached page list.
        browser_session.keeper.title()


def test_attach_reads_results_and_preserves_other_tabs(tmp_path, browser_session, origin):
    count = len(browser_session.context.pages)
    result = run_search(service(tmp_path, browser_session), request(), browser_session)
    assert result["status"] == "results"
    assert result["candidates"][0]["url"] == "https://detail.tmall.com/item.htm?id=100002"
    assert "已接管专用浏览器" in result["note"]
    assert len(browser_session.context.pages) == count
    assert not browser_session.keeper.is_closed()
    assert "用户保留" in browser_session.keeper.inner_text("body")


def test_waits_for_result_cards_loaded_after_five_seconds(tmp_path, browser_session, origin):
    result = run_search(service(tmp_path, browser_session), request("延迟测试"), browser_session)
    assert result["status"] == "results"


@pytest.mark.parametrize(
    "query,expected,code",
    [
        ("验证测试", "blocked", "verification_required"),
        ("跳转测试", "blocked", "destination_blocked"),
        ("空结果测试", "no_results", None),
    ],
)
def test_blocked_and_explicit_empty_have_distinct_states(
    tmp_path, browser_session, origin, query, expected, code
):
    result = run_search(service(tmp_path, browser_session), request(query), browser_session)
    assert result["status"] == expected
    assert result["error_code"] == code
    assert result["candidates"] == []
    assert not browser_session.keeper.is_closed()


def test_cancellation_closes_only_the_new_page(tmp_path, browser_session, origin):
    count = len(browser_session.context.pages)

    class Cancelled(Exception):
        pass

    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise Cancelled()

    with pytest.raises(Cancelled):
        run_search(service(tmp_path, browser_session), request(), browser_session, cancel)
    assert len(browser_session.context.pages) == count
    assert not browser_session.keeper.is_closed()


def test_worker_receives_takeover_configuration(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        browser_cdp_enabled=True,
        browser_cdp_url="http://127.0.0.1:12345",
    )

    def worker(payload, **kwargs):
        assert payload["settings"]["browser_cdp_enabled"] is True
        assert payload["settings"]["browser_cdp_url"] == settings.browser_cdp_url
        assert "llm_api_key" not in payload["settings"]
        return ProductSearch.result(request(), "unavailable", "fixture")

    monkeypatch.setattr("app.product_search.run_collection_process", worker)
    assert ProductSearch(settings).search(request())["error_code"] == "fixture"


def test_jd_cards_without_links_are_scoped_validated_and_deduplicated(
    tmp_path, browser_session, origin
):
    args = request("京东卡片测试").model_copy(update={"platform": "jd"})
    result = run_search(service(tmp_path, browser_session), args, browser_session)
    assert [c["url"] for c in result["candidates"]] == [
        "https://item.jd.com/100002.html",
        "https://item.jd.com/100003.html",
    ]
    assert result["candidates"][0]["title"] == "合成保温杯500ml"
    assert all(c["identity_source"] == "sku_card" for c in result["candidates"])
    assert 'data-sku="100002"' in result["candidates"][0]["locator"]


def test_unavailable_browser_fallback_is_explicit(tmp_path, browser_session, origin):
    searcher = service(tmp_path, browser_session)
    searcher.settings = searcher.settings.model_copy(
        update={"browser_cdp_url": "http://127.0.0.1:1"}
    )
    result = run_search(searcher, request(), browser_session)
    assert result["status"] == "results"
    assert "未连接到配置的专用浏览器" in result["note"]
    assert not browser_session.keeper.is_closed()


def test_unrecognised_page_does_not_become_no_results(
    tmp_path, browser_session, origin, monkeypatch
):
    monkeypatch.setattr("app.product_search.SEARCH_RESULTS_WAIT_SECONDS", 0.1)
    result = run_search(service(tmp_path, browser_session), request("空白测试"), browser_session)
    assert result["status"] == "unavailable"
    assert result["error_code"] == "results_not_recognized"


def test_risk_control_is_reported_without_suggesting_login_will_fix_it():
    assert search_gate("https://pc-frequent-pro.pf.jd.com/", "jd", "", "", 200) == "risk_control"
    assert "不保证解除" in search_block_note("risk_control")
    assert "人工验证" in search_block_note("verification_required")
