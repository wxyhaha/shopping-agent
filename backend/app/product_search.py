"""Read bounded first-page results with a private or explicitly attached browser."""

import hashlib
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from app.background_collector import (
    BrowserNetworkPolicy,
    ipv4_addresses,
    product_navigation_url,
    public_addresses,
    reading_signals,
)
from app.capture_urls import product_url_identity
from app.collection_process import CollectionProcessError, run_collection_process
from app.models import utc_now
from app.native_network import NativeRequestGuard
from app.search_schemas import SearchProductsArgs, SearchResult

SEARCH_HOSTS = {"taobao": "s.taobao.com", "jd": "search.jd.com"}
SEARCH_RESULTS_WAIT_SECONDS = 20.0
SEARCH_LINKS = """(platform) => {
  const visible = el => el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const rows = [];
  if (platform === 'jd' || location.hostname === 'search.jd.com') {
    // The current JD search uses click handlers on product cards rather than <a href>.
    // Only known result-card shapes with an explicit decimal SKU and title are admitted.
    const cards = document.querySelectorAll(
      '.plugin_goodsCardWrapper[data-sku], .gl-item[data-sku]');
    for (const card of cards) {
      if (!visible(card)) continue;
      const sku = card.getAttribute('data-sku') || '';
      if (!/^[1-9][0-9]{0,19}$/.test(sku)) continue;
      const heading = card.querySelector('[class*="goods_title"] [title]')
        || card.querySelector('[class*="goods_title"]') || card.querySelector('.p-name');
      const title = (heading?.getAttribute('title') || heading?.innerText || '')
        .trim().slice(0, 512);
      if (!title) continue;
      const selector = card.classList.contains('plugin_goodsCardWrapper')
        ? '.plugin_goodsCardWrapper' : '.gl-item';
      rows.push({url: `https://item.jd.com/${sku}.html`, title, identity_source: 'sku_card',
        locator: `${selector}[data-sku="${sku}"]`});
      if (rows.length >= 60) break;
    }
  }
  return rows.concat(Array.from(document.querySelectorAll('a[href]'))
    .filter(visible).slice(0, 1200).map(el => ({
    url: el.href,
    title: (el.innerText || el.getAttribute('title') || el.querySelector('img')?.alt || '')
      .trim().slice(0, 512), identity_source: 'direct_link', locator: 'a[href]'
  })));
}"""


def search_url(platform: str, query: str) -> str:
    return f"https://{SEARCH_HOSTS[platform]}/" + (
        "Search?" + urlencode({"keyword": query})
        if platform == "jd"
        else "search?" + urlencode({"q": query})
    )


def candidate_rows(rows: list, platform: str) -> list[dict]:
    """Admit supported product identities from links or recognised SKU cards, without clicks."""
    candidates = {}
    for row in rows[:1200]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        identity = product_url_identity(row["url"])
        if not identity or (identity.platform == "jd") != (platform == "jd"):
            continue
        title = " ".join(str(row.get("title") or "").split())[:512]
        if not title:
            continue
        url = product_navigation_url(identity, identity.safe_url)
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        candidates.setdefault(
            key,
            {
                "id": f"candidate-{key}",
                "platform": identity.platform,
                "url": url,
                "title": title,
                "identity_source": "sku_card"
                if platform == "jd" and row.get("identity_source") == "sku_card"
                else "direct_link",
                "locator": str(row.get("locator") or "a[href]")[:256],
            },
        )
        if len(candidates) == 5:
            break
    return list(candidates.values())


def search_gate(url: str, platform: str, title: str, text: str, status: int | None):
    if status in {401, 403, 429}:
        return "access_denied"
    host = (urlsplit(url).hostname or "").lower()
    if host == "pc-frequent-pro.pf.jd.com" or re.search(
        r"暂时无法展示该商品的信息|访问过于频繁|当前访问异常|网络环境存在风险", f"{title}\n{text}"
    ):
        return "risk_control"
    if host == "cfe.m.jd.com" and "/risk_handler/" in urlsplit(url).path:
        return "verification_required"
    if re.search(r"(^|\.)(login|passport|auth|safe)\.", host):
        return "login_required"
    if host != SEARCH_HOSTS[platform]:
        return "redirected"
    # Search with a verification overlay is blocked even when product links exist behind it.
    if re.search(
        r"安全验证|滑动验证|请完成验证|请输入验证码|访问受限|验证后继续", f"{title}\n{text}"
    ):
        return "verification_required"
    if re.search(r"请先登录|登录后查看|登录后继续|扫码登录|账号密码登录", f"{title}\n{text}"):
        return "login_required"
    return None


def search_block_note(code: str) -> str:
    action = {
        "login_required": "搜索页面需要登录，请在该平台的专用浏览器中手动登录后重试。",
        "verification_required": "搜索页面要求人工验证，请在专用浏览器中手动完成后重试。",
        "risk_control": "平台风控拦截搜索，请稍后重试；重新登录不保证解除风控。",
        "destination_blocked": "搜索页面跳转地址未通过允许域名及公网地址校验。",
    }.get(code, "搜索页面访问受阻，请核对平台页面或稍后重试。")
    return action + "未获得结果不代表没有合格商品。"


class ProductSearch:
    def __init__(self, settings, *, route_fixture=None, resolver=public_addresses):
        self.settings = settings
        self.route_fixture = route_fixture
        self.resolver = resolver

    def _handoff(self, request):
        if (
            request.platform == "jd"
            and not self.settings.jd_background_enabled
            and self.route_fixture is None
        ):
            return self.result(
                request,
                "blocked",
                "jd_automation_disabled",
                "京东自动搜索已暂停，本次没有请求平台，也不能据此认定无候选。"
                "请等待访问恢复或手动提供已取得的候选链接；不需要继续尝试登录。",
            )
        return None

    def search(self, request: SearchProductsArgs, *, check_cancel=lambda: None) -> dict:
        if handoff := self._handoff(request):
            return handoff
        deadline = time.monotonic() + self.settings.browser_timeout_seconds
        try:
            if self.route_fixture is not None:
                return self._search(request, deadline, check_cancel)
            result = run_collection_process(
                {
                    "operation": "search",
                    "request": request.model_dump(),
                    "settings": {
                        "data_dir": self.settings.data_dir.as_posix(),
                        "browser_timeout_seconds": self.settings.browser_timeout_seconds,
                        "browser_trust_fake_ip_dns": self.settings.browser_trust_fake_ip_dns,
                        "browser_cdp_enabled": self.settings.browser_cdp_enabled,
                        "browser_cdp_url": self.settings.browser_cdp_url,
                        "jd_background_enabled": self.settings.jd_background_enabled,
                    },
                },
                deadline=deadline,
                check_cancel=check_cancel,
            )
            return SearchResult.model_validate(result).model_dump()
        except (CollectionProcessError, PlaywrightError, ValidationError, OSError):
            return self.result(
                request,
                "unavailable",
                "search_failed",
                "搜索访问失败或超时；未获得结果不代表没有合格商品。",
            )

    @staticmethod
    def result(
        request,
        status,
        code=None,
        note="搜索结果仅作候选线索，尚未读取详情或核验品牌、规格和价格。",
        candidates=None,
    ):
        return SearchResult(
            status=status,
            platform=request.platform,
            query=request.query,
            search_url=search_url(request.platform, request.query),
            observed_at=utc_now().isoformat(),
            candidates=candidates or [],
            error_code=code,
            note=note,
        ).model_dump()

    def _search(self, request, deadline, check_cancel):
        if handoff := self._handoff(request):
            return handoff

        def remaining_ms(limit=12000):
            check_cancel()
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise PlaywrightError("search deadline")
            return min(limit, remaining)

        resolver = self.resolver
        if self.settings.browser_trust_fake_ip_dns and resolver is public_addresses:
            resolver = ipv4_addresses
        policy = BrowserNetworkPolicy(
            resolver, trust_fake_ip_dns=self.settings.browser_trust_fake_ip_dns
        )
        target = search_url(request.platform, request.query)
        if not policy.allows(target):
            return self.result(
                request,
                "unavailable",
                "destination_blocked",
                "搜索地址未通过允许域名及公网地址校验。",
            )
        with sync_playwright() as playwright:
            browser = None
            context = None
            page = None
            attached = False
            attach_failed = False
            if self.settings.browser_cdp_enabled and self.route_fixture is None:
                try:
                    browser = playwright.chromium.connect_over_cdp(
                        self.settings.browser_cdp_url, timeout=remaining_ms(5000)
                    )
                    context = browser.contexts[0] if browser.contexts else None
                    attached = context is not None
                    attach_failed = not attached
                    if not attached:
                        browser.close()
                except PlaywrightError:
                    attach_failed = True
            if not attached:
                browser = playwright.chromium.launch(
                    headless=True,
                    timeout=remaining_ms(),
                    args=["--disable-ipv6"] if self.settings.browser_trust_fake_ip_dns else [],
                )

            def finish(status, code=None, note=None, candidates=None):
                result = self.result(request, status, code, candidates=candidates)
                mode = "已接管专用浏览器搜索。" if attached else "使用独立浏览器搜索。"
                if attach_failed:
                    mode = "未连接到配置的专用浏览器，已退回独立浏览器搜索。"
                result["note"] = mode + (note or result["note"])
                return result

            try:
                if not attached:
                    state_path = self.settings.browser_session_dir / f"{request.platform}.json"
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 1000},
                        locale="zh-CN",
                        service_workers="block",
                        accept_downloads=False,
                        storage_state=str(state_path) if state_path.is_file() else None,
                    )
                gate = None

                def route_request(route):
                    nonlocal gate
                    try:
                        if (
                            not policy.allows(route.request.url)
                            or route.request.resource_type == "media"
                        ):
                            route.abort()
                            return
                        if self.route_fixture is not None:
                            self.route_fixture(route)
                            return
                        destination = route.request.url
                        for _ in range(6):
                            response = route.fetch(
                                url=destination, max_redirects=0, timeout=remaining_ms()
                            )
                            location = response.headers.get("location")
                            if response.status not in {301, 302, 303, 307, 308} or not location:
                                route.fulfill(response=response)
                                response.dispose()
                                return
                            destination = urljoin(destination, location)
                            response.dispose()
                            if not policy.allows(destination):
                                route.abort()
                                return
                            if (
                                route.request.is_navigation_request()
                                and route.request.frame == page.main_frame
                            ):
                                gate = search_gate(destination, request.platform, "", "", None)
                                if gate:
                                    route.fulfill(
                                        status=403, content_type="text/html", body="搜索访问受阻"
                                    )
                                    return
                        route.abort()
                    except PlaywrightError:
                        if (
                            route.request.is_navigation_request()
                            and route.request.frame == page.main_frame
                        ):
                            gate = "network_error"
                        try:
                            route.abort()
                        except PlaywrightError:
                            pass

                page = context.new_page()
                page.set_default_timeout(remaining_ms())
                page.on("popup", lambda popup: popup.close())
                native_guard = None
                if attached:

                    def navigation_check(url):
                        code = search_gate(url, request.platform, "", "", None)
                        return (code, search_block_note(code)) if code else None

                    native_guard = NativeRequestGuard(page, policy, navigation_check)
                    page.route_web_socket("**/*", lambda route: None)
                else:
                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda route: None)
                try:
                    response = page.goto(
                        target, wait_until="domcontentloaded", timeout=remaining_ms(20000)
                    )
                except PlaywrightError:
                    if native_guard and native_guard.navigation_gate:
                        code = native_guard.navigation_gate[0]
                        return finish("blocked", code, search_block_note(code))
                    raise
                wait_until = min(deadline - 1, time.monotonic() + SEARCH_RESULTS_WAIT_SECONDS)
                while True:
                    check_cancel()
                    signals = reading_signals(page)
                    code = (
                        gate
                        or (
                            native_guard.navigation_gate[0]
                            if native_guard and native_guard.navigation_gate
                            else None
                        )
                        or search_gate(
                            page.url,
                            request.platform,
                            signals["title"],
                            signals["text"],
                            response.status if response else None,
                        )
                    )
                    if not code:
                        login_frames = page.locator("iframe:visible").evaluate_all(
                            "frames => frames.map(frame => frame.src)"
                        )
                        if any(
                            re.search(
                                r"(^|\.)(login|passport|auth)\.", urlsplit(url).hostname or ""
                            )
                            for url in login_frames
                        ):
                            code = "login_required"
                    if code:
                        return finish("blocked", code, search_block_note(code))
                    candidates = candidate_rows(
                        page.evaluate(SEARCH_LINKS, request.platform), request.platform
                    )
                    if candidates:
                        return finish("results", candidates=candidates)
                    if re.search(r"没有找到|暂无.*商品|未找到.*商品|无搜索结果", signals["text"]):
                        return finish(
                            "no_results",
                            note="当前查询第一页明确显示无搜索结果；不代表全平台无合格商品。",
                        )
                    if time.monotonic() >= wait_until:
                        break
                    page.evaluate("window.scrollBy(0, 650)")
                    page.wait_for_timeout(
                        min(remaining_ms(600), max(1, int((wait_until - time.monotonic()) * 1000)))
                    )
                return finish(
                    "unavailable",
                    "results_not_recognized",
                    "未识别到可核实的商品链接或商品卡片；可能尚未加载或页面布局不支持，不能断言无候选。",
                )
            finally:
                try:
                    if attached:
                        if page is not None:
                            page.close()
                    elif context:
                        context.close()
                finally:
                    if browser:
                        browser.close()
