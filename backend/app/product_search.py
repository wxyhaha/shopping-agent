"""Read bounded first-page marketplace search results in an isolated browser."""

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
)
from app.capture_urls import product_url_identity
from app.collection_process import CollectionProcessError, run_collection_process
from app.models import utc_now
from app.search_schemas import SearchProductsArgs, SearchResult

SEARCH_HOSTS = {"taobao": "s.taobao.com", "jd": "search.jd.com"}
SEARCH_LINKS = """() => {
  const visible = el => el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  return Array.from(document.querySelectorAll('a[href]'))
    .filter(visible).slice(0, 1200).map(el => ({
    url: el.href,
    title: (el.innerText || el.getAttribute('title') || el.querySelector('img')?.alt || '')
      .trim().slice(0, 512)
  }));
}"""


def search_url(platform: str, query: str) -> str:
    return f"https://{SEARCH_HOSTS[platform]}/" + (
        "Search?" + urlencode({"keyword": query})
        if platform == "jd"
        else "search?" + urlencode({"q": query})
    )


def candidate_rows(rows: list, platform: str) -> list[dict]:
    """Admit only direct supported product identities; never follow tracking redirects."""
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
            {"id": f"candidate-{key}", "platform": identity.platform, "url": url, "title": title},
        )
        if len(candidates) == 5:
            break
    return list(candidates.values())


def search_gate(url: str, platform: str, title: str, text: str, status: int | None):
    if status in {401, 403, 429}:
        return "access_denied"
    host = (urlsplit(url).hostname or "").lower()
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


class ProductSearch:
    def __init__(self, settings, *, route_fixture=None, resolver=public_addresses):
        self.settings = settings
        self.route_fixture = route_fixture
        self.resolver = resolver

    def search(self, request: SearchProductsArgs, *, check_cancel=lambda: None) -> dict:
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
            browser = playwright.chromium.launch(
                headless=True,
                timeout=remaining_ms(),
                args=["--disable-ipv6"] if self.settings.browser_trust_fake_ip_dns else [],
            )
            context = None
            try:
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

                context.route("**/*", route_request)
                context.route_web_socket("**/*", lambda route: None)
                page = context.new_page()
                response = page.goto(
                    target, wait_until="domcontentloaded", timeout=remaining_ms(20000)
                )
                for _ in range(8):
                    check_cancel()
                    signals = page.evaluate(
                        "() => ({title: document.title, "
                        "text: (document.body?.innerText || '').slice(0, 8000)})"
                    )
                    code = gate or search_gate(
                        page.url,
                        request.platform,
                        signals["title"],
                        signals["text"],
                        response.status if response else None,
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
                        return self.result(
                            request,
                            "blocked",
                            code,
                            "搜索页面受阻，需独立会话登录或人工验证；不能据此认定无候选。",
                        )
                    candidates = candidate_rows(page.evaluate(SEARCH_LINKS), request.platform)
                    if candidates:
                        return self.result(request, "results", candidates=candidates)
                    if re.search(r"没有找到|暂无.*商品|未找到.*商品|无搜索结果", signals["text"]):
                        return self.result(
                            request,
                            "no_results",
                            note="当前查询第一页明确显示无搜索结果；不代表全平台无合格商品。",
                        )
                    page.evaluate("window.scrollBy(0, 650)")
                    page.wait_for_timeout(remaining_ms(600))
                return self.result(
                    request,
                    "unavailable",
                    "results_not_recognized",
                    "未识别到可核实的直接商品链接；可能尚未加载或页面布局不支持，不能断言无候选。",
                )
            finally:
                if context:
                    context.close()
                browser.close()
