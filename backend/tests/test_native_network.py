"""Native Chromium requests retain cookies and cannot follow unchecked redirects."""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.native_network import NativeRequestGuard


@pytest.fixture
def origin():
    requests = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            requests[path] += 1
            redirects = {
                "/redirect": "/product",
                "/blocked-redirect": "/forbidden",
                "/asset.js": "/forbidden.js",
                "/other-product": "/different-product",
            }
            if path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[path])
                self.end_headers()
                return
            if path == "/api":
                body = json.dumps({"session": self.headers.get("Cookie") == "fixture=ok"})
                content_type = "application/json"
            elif path == "/detail.js":
                body = """fetch('/api').then(r=>r.json()).then(data=>{
                  document.body.dataset.session=String(data.session);
                  document.body.innerHTML += '<div class="descV8-test">详情已加载</div>';
                });"""
                content_type = "application/javascript"
            elif path == "/product":
                body = '<h1>合成商品</h1><script src="/detail.js"></script>'
                content_type = "text/html; charset=utf-8"
            elif path == "/assets":
                body = (
                    '<h1>合成商品</h1><script src="/asset.js"></script>'
                    '<iframe src="/frame"></iframe>'
                )
                content_type = "text/html"
            elif path == "/worker-page":
                body = (
                    '<h1>商品</h1><script>const worker=new Worker("/worker.js");'
                    "worker.onmessage=e=>{window.workerResult=e.data;};</script>"
                )
                content_type = "text/html"
            elif path == "/worker.js":
                body = (
                    'fetch("/forbidden").then(()=>postMessage("leaked"))'
                    '.catch(()=>postMessage("blocked"));'
                )
                content_type = "application/javascript"
            else:
                body, content_type = "test", "text/plain"
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if path == "/product":
                self.send_header("Set-Cookie", "fixture=ok; HttpOnly; Path=/")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield SimpleNamespace(base=base, requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


def guard_for(page, origin, navigation_check=lambda _url: None):
    # Local HTTP is a fixture-only policy. Production still requires HTTPS and public DNS.
    policy = SimpleNamespace(
        allows=lambda url: (
            url.startswith(origin.base + "/") and not urlsplit(url).path.startswith("/forbidden")
        )
    )
    return NativeRequestGuard(page, policy, navigation_check)


def test_native_redirect_script_and_fetch_keep_browser_session(context, origin):
    page = context.new_page()
    guard = guard_for(page, origin)
    try:
        page.goto(origin.base + "/redirect")
        page.wait_for_selector(".descV8-test")
        assert page.locator("body").get_attribute("data-session") == "true"
        assert origin.requests["/api"] == 1
        assert guard.navigation_gate is None
    finally:
        page.close()
        guard.close()


def test_main_document_redirect_is_checked_before_destination_is_contacted(context, origin):
    page = context.new_page()
    guard = guard_for(page, origin)
    try:
        with pytest.raises(PlaywrightError):
            page.goto(origin.base + "/blocked-redirect")
        assert origin.requests["/blocked-redirect"] == 1
        assert origin.requests["/forbidden"] == 0
        assert guard.navigation_gate[0] == "destination_blocked"
    finally:
        page.close()
        guard.close()


def test_subresource_redirect_and_child_document_cannot_escape_guard(context, origin):
    page = context.new_page()
    guard = guard_for(page, origin)
    try:
        page.goto(origin.base + "/assets", wait_until="networkidle")
        assert origin.requests["/asset.js"] == 1
        assert origin.requests["/forbidden.js"] == 0
        assert origin.requests["/frame"] == 0
    finally:
        page.close()
        guard.close()


def test_navigation_to_another_product_is_stopped_before_request(context, origin):
    page = context.new_page()
    guard = guard_for(
        page,
        origin,
        lambda url: ("page_changed", "其他商品") if url.endswith("/different-product") else None,
    )
    try:
        with pytest.raises(PlaywrightError):
            page.goto(origin.base + "/other-product")
        assert origin.requests["/different-product"] == 0
        assert guard.navigation_gate[0] == "page_changed"
    finally:
        page.close()
        guard.close()


def test_shared_context_other_tabs_remain_unfiltered(context, origin):
    page = context.new_page()
    guard = guard_for(page, origin)
    other = context.new_page()
    try:
        other.goto(origin.base + "/forbidden")
        assert not other.is_closed()
        assert origin.requests["/forbidden"] == 1
    finally:
        other.close()
        page.close()
        guard.close()


def test_worker_cannot_send_an_unchecked_request(context, origin):
    page = context.new_page()
    guard = guard_for(page, origin)
    try:
        page.goto(origin.base + "/worker-page", wait_until="networkidle")
        page.wait_for_function("window.workerResult !== undefined", timeout=3000)
        assert origin.requests["/worker.js"] == 1
        assert page.evaluate("window.workerResult") == "blocked"
        assert origin.requests["/forbidden"] == 0
    finally:
        page.close()
        guard.close()
