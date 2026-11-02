"""Open task product URLs in an isolated headless browser and collect evidence."""

import base64
import ipaddress
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from app.capture_urls import ProductUrlIdentity, product_url_identity
from app.collection_process import (
    CollectionDeadline,
    CollectionProcessError,
    run_collection_process,
)
from app.config import Settings
from app.detail_enrich import enrich_detail_images
from app.models import utc_now
from app.price_vision import looks_obfuscated, read_rendered_price
from app.schemas import CaptureSnapshotSubmit
from app.spec_panel import expand_spec_panel
from app.variant_controller import select_and_capture

COLLECTOR_SCRIPT = Path(__file__).with_name("browser_assets") / "collector.js"
# The element that paints the price; used when the digits must be read from pixels.
PRICE_ELEMENT_SELECTORS = (
    ".price_wrap, #priceFloor, #summary-price, .summary-price, "
    "[class*='SkuPanel'] [class*='price'], [itemprop='price'], [class*='price']"
)
# How many distinct detail images may be read per collection. Detail artwork carries most
# of the product's real specifications, so this is generous; duplicates are removed first.
DETAIL_IMAGE_LIMIT = 32
# Concurrency: downloads are cheap I/O; vision calls are bounded by the gateway.
DETAIL_FETCH_WORKERS = 5
DETAIL_VISION_WORKERS = 10
RESOURCE_DOMAINS = (
    "taobao.com",
    "tmall.com",
    "alicdn.com",
    "tbcdn.cn",
    "taobaocdn.com",
    "jd.com",
    "360buyimg.com",
    "jdstatic.com",
    "jcloudcs.com",
    # JD's own static asset host (e.g. jstatic.3.cn/item_v2/price.<hash>.js). The mobile
    # item page renders its price area from a script served here, so blocking it left the
    # page with no price element at all rather than a masked one.
    "3.cn",
)
PAGE_SIGNALS = """() => ({
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 8000),
    hasProduct: Boolean(document.querySelector(
      '.sku-name, #J_ImgBooth, .picGalleryEle, [class*="SkuPanel"]')) ||
      [...document.querySelectorAll('h1')].some(el => (el.innerText || '').trim().length >= 8)
})"""


@dataclass
class CollectionResult:
    snapshot: CaptureSnapshotSubmit | None
    error_code: str | None = None
    message: str | None = None
    # Base64 PNG of the price area when the digits were painted by a custom font. Only the
    # parent process may read it, so the isolated worker hands the pixels back instead.
    price_screenshot_png: str | None = None
    # Set when the snapshot still needs its detail artwork read. Reading ~40 panels takes about
    # a minute, which does not fit inside one collection, so the caller runs it afterwards with
    # a pending marker instead of extending the browser step.
    detail_read_pending: bool = False


def visible_price_element(page):
    """Return the first price element that is actually painted.

    A page can hold several price containers, and only one of them is displayed. Taking the
    first DOM match would select a hidden node whose bounding box is empty, and screenshotting
    it fails, so the visible one must be chosen explicitly.
    """
    for element in page.query_selector_all(PRICE_ELEMENT_SELECTORS):
        try:
            box = element.bounding_box()
        except PlaywrightError:
            continue
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            return element
    return None


def session_name(platform: str) -> str:
    return "taobao" if platform in {"taobao", "tmall"} else "jd"


def session_home_url(platform: str) -> str:
    """Site front page used to establish an active session before opening a product page."""
    return "https://www.jd.com/" if platform == "jd" else "https://www.taobao.com/"


# How long to keep waiting for the lazily injected detail section, and how often to look.
# This is the only reliable signal on Tmall: readyState completes at ~800 ms with the document
# still 3 600 px tall, and the detail panels arrive several seconds later. The budget is
# therefore generous, while still leaving time to parse and to check the deadline.
DETAIL_WAIT_SECONDS = 25.0
DETAIL_WAIT_INTERVAL = 0.5
# Counts images inside the detail containers, mirroring the selectors in collector.js. The
# count only decides whether to keep waiting.
DETAIL_SECTION_PROBE = """
() => {
  const selectors = ['#J_DivItemDesc', '#description', '#detailContent', '#detail',
    "[class*='descV8']", "[class*='detail-content']", "[class*='detailContent']",
    '.page-content-area'];
  let count = 0;
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      count += node.querySelectorAll('img').length;
    }
  }
  return count;
}
"""
# Counts the containers themselves. Used once before a long wait: a page with no container at
# all will not grow one, so waiting the full budget there only delays the result.
DETAIL_CONTAINER_PROBE = """
() => {
  const selectors = ['#J_DivItemDesc', '#description', '#detailContent', '#detail',
    "[class*='descV8']", "[class*='detail-content']", "[class*='detailContent']",
    '.page-content-area'];
  let count = 0;
  for (const selector of selectors) count += document.querySelectorAll(selector).length;
  return count;
}
"""


def wait_for_detail_images(page, deadline: float, check_cancel, wait_seconds=None) -> str:
    """Wait until the detail section has images, and say which happened.

    Returning one of ``ready`` / ``empty`` / ``timeout`` separates "the page has no detail
    section" from "it had not rendered yet", so a missing read is not mistaken for an absent
    section. The wait never takes more than its own budget nor more than what is left of the
    collection, so a slow page still returns instead of overrunning the deadline.
    """
    now = time.monotonic()
    remaining = deadline - now
    if remaining <= 1.0:
        return "timeout"
    # Check once before committing to a long wait. With no container at all the page will not
    # grow one, so waiting the full budget only delays the result. A container that exists but
    # holds no images also gets a short grace period rather than the full budget: that is the
    # shape of a synthetic fixture whose detail block is text only.
    try:
        containers = int(page.evaluate(DETAIL_CONTAINER_PROBE) or 0)
    except PlaywrightError:
        return "timeout"
    if containers == 0:
        return "empty"
    # Leave a margin for parsing and for the deadline check that follows, but otherwise use
    # the wait budget: the section can take far longer than the scroll pass to appear.
    budget = min(
        DETAIL_WAIT_SECONDS if wait_seconds is None else wait_seconds,
        max(0.0, remaining - 8.0),
    )
    limit = now + budget
    seen = 0
    while True:
        check_cancel()
        try:
            seen = int(page.evaluate(DETAIL_SECTION_PROBE) or 0)
        except PlaywrightError:
            return "timeout"
        if seen > 0:
            return "ready"
        if time.monotonic() >= limit:
            return "empty"
        # Keep the page moving while watching. The section is injected on scroll, and staying
        # still at one position is what left the page at 3 600 px on earlier attempts.
        try:
            page.evaluate(
                "() => {"
                " const h = document.body.scrollHeight;"
                " if (window.scrollY + innerHeight >= h - 300) {"
                "   window.scrollTo(0, Math.max(0, h * 0.35));"
                " } else {"
                "   window.scrollBy(0, Math.max(500, innerHeight * 0.8));"
                " } }"
            )
        except PlaywrightError:
            return "timeout"
        page.wait_for_timeout(int(DETAIL_WAIT_INTERVAL * 1000))


def product_navigation_url(identity: ProductUrlIdentity, original_url: str) -> str:
    hosts = {"taobao": "item.taobao.com", "tmall": "detail.tmall.com", "jd": "item.jd.com"}
    if identity.platform == "jd":
        path, query = f"/{identity.product_id}.html", {}
    else:
        path, query = "/item.htm", {"id": identity.product_id}
    original = {
        key.lower(): values for key, values in parse_qs(urlsplit(original_url).query).items()
    }
    for name in ("skuid", "sku"):
        value = (original.get(name) or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            query["skuId" if name == "skuid" else "sku"] = value
    host = hosts[identity.platform]
    # JD serves the PC item page behind a risk-control interstitial that also blocks
    # signed-in visitors, while the mobile host renders the same product. Keep the mobile
    # host when the caller came from it; compare hosts exactly, because "item.jd.com"
    # contains the substring "m.jd.com".
    if identity.platform == "jd" and (urlsplit(original_url).hostname or "").lower() == (
        "item.m.jd.com"
    ):
        host = "item.m.jd.com"
        path = f"/product/{identity.product_id}.html"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def login_entry_url(identity: ProductUrlIdentity, original_url: str) -> str:
    """Where the visible login window should open.

    Opening the product page directly is wrong for JD: its PC item host answers with a
    risk-control page that hides the product even after signing in, so the user would
    authenticate on a page that cannot show what they came for. The JD home page lets
    the user sign in first; collection then uses the saved session.
    """
    url = product_navigation_url(identity, original_url)
    if identity.platform == "jd":
        return urlunsplit(("https", "www.jd.com", "/", "", ""))
    return url


def public_addresses(host: str) -> list[str]:
    return list({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def ipv4_addresses(host: str) -> list[str]:
    return list(
        {
            row[4][0]
            for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, family=socket.AF_INET)
        }
    )


class BrowserNetworkPolicy:
    def __init__(self, resolver=public_addresses, *, trust_fake_ip_dns=False):
        self.resolver = resolver
        self.trust_fake_ip_dns = trust_fake_ip_dns
        self.checked: dict[str, bool] = {}

    def allows(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if (
                parts.scheme != "https"
                or parts.username
                or parts.password
                or parts.port not in {None, 443}
                or not any(
                    host == domain or host.endswith(f".{domain}") for domain in RESOURCE_DOMAINS
                )
            ):
                return False
            if host not in self.checked:
                addresses = self.resolver(host)
                self.checked[host] = bool(addresses) and all(
                    ipaddress.ip_address(value).is_global
                    or (
                        self.trust_fake_ip_dns
                        and ipaddress.ip_address(value) in ipaddress.ip_network("198.18.0.0/15")
                    )
                    for value in addresses
                )
            return self.checked[host]
        except (ValueError, OSError):
            return False


def reading_signals(page) -> dict:
    """Read page signals, tolerating a navigation that replaces the JS context mid-read."""
    attempt = 0
    while True:
        try:
            return page.evaluate(PAGE_SIGNALS)
        except PlaywrightError:
            # A risk-control or login interstitial can replace the document after load.
            # That is an observed condition, not a collection crash: retry the read.
            attempt += 1
            if attempt >= 3:
                raise
            try:
                page.wait_for_timeout(250)
            except PlaywrightError:
                raise


def interstitial_host(url: str) -> str | None:
    """Hosts that serve an access interstitial instead of the product page itself."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "pc-frequent-pro.pf.jd.com":
        return "risk_control"
    if host == "cfe.m.jd.com" and "/risk_handler/" in urlsplit(url).path:
        return "risk_control"
    return None


def navigation_destination(url: str, expected: ProductUrlIdentity):
    """Check navigation identity without treating a redirect as a rendered page."""
    host = (urlsplit(url).hostname or "").lower()
    if re.search(r"(^|\.)(login|passport|auth|safe)\.", host):
        return "login_required", "页面转向登录或身份验证服务"
    actual = product_url_identity(url)
    if not actual or (actual.platform, actual.product_id) != (
        expected.platform,
        expected.product_id,
    ):
        if interstitial_host(url):
            return "risk_control", "页面被平台风控拦截，未展示商品内容"
        return "redirected", "页面没有停留在请求的同一商品详情页"
    return None


def access_gate(url: str, signals: dict, expected: ProductUrlIdentity, status: int | None):
    """Return a safe code and an observed static access marker, never account text."""
    if status in {401, 403, 429}:
        return "access_denied", f"HTTP {status}"
    text, title = signals.get("text", ""), signals.get("title", "")
    observed = f"{title}\n{text}"
    # An interstitial is recognised by what the page says. Its URL alone is ambiguous:
    # JD can serve the block on its own host or replace the product document in place.
    for pattern, code in (
        (
            r"安全验证|滑动验证|请完成验证|请输入验证码|访问受限|验证后继续",
            "verification_required",
        ),
        (
            r"暂时无法展示该商品的信息|访问过于频繁|当前访问异常|网络环境存在风险",
            "risk_control",
        ),
        (r"请先登录|登录后查看|登录后继续|扫码登录|账号密码登录", "login_required"),
    ):
        match = re.search(pattern, observed)
        if match and not signals.get("hasProduct"):
            return code, match.group(0)
    destination = navigation_destination(url, expected)
    if destination:
        return destination
    if not signals.get("hasProduct"):
        if not text.strip():
            return "empty_page", "商品页尚无可读取正文"
        return "product_not_ready", "页面尚未出现可识别的商品详情区域"
    return None


def access_message(code: str) -> str:
    """User-facing next step; a risk block is not a login problem."""
    if code == "risk_control":
        return (
            "平台风控拦截了后台访问，未获得商品内容；重新登录通常无效，"
            "请稍后重试，或用已登录商品页的扩展接管。"
        )
    if code == "login_required":
        return "后台访问需要登录；可打开登录窗口，或用已登录商品页的扩展接管。"
    return "后台页面受阻，需要登录、验证或人工接管。"


def unavailable_snapshot(
    identity: ProductUrlIdentity,
    code: str,
    marker: str,
    failed=False,
    *,
    observed_host: str | None = None,
    http_status: int | None = None,
):
    status = "missing" if failed else "blocked"
    # Keep the observed host and HTTP status so a blocked result can be checked against
    # what the browser actually received instead of only naming a reason.
    detail = marker
    if observed_host:
        detail = f"{detail}（最终主机 {observed_host}）"
    if http_status is not None:
        detail = f"{detail}（HTTP {http_status}）"
    return CaptureSnapshotSubmit.model_validate(
        {
            "adapter_id": f"{identity.platform}-playwright",
            "adapter_version": "0.1.0",
            "page_url": identity.safe_url,
            "captured_at": utc_now(),
            "status": "failed" if failed else "blocked",
            "fields": [
                {
                    "observation_id": "o-access",
                    "field_path": "page.access",
                    "label": "后台页面访问",
                    "raw_value": detail,
                    "status": status,
                    "evidence_ids": ["e-access"],
                }
            ],
            "selected_variant": {"status": status},
            "evidence": [
                {
                    "evidence_id": "e-access",
                    "field_path": "page.access",
                    "source": "navigation",
                    "excerpt": detail,
                    "locator": code,
                }
            ],
            "coverage": [
                {
                    "field_path": "page.access",
                    "status": status,
                    "note": detail,
                    "evidence_ids": ["e-access"],
                }
            ],
        }
    )


class BackgroundCollector:
    def __init__(self, settings: Settings, *, route_fixture=None, resolver=public_addresses):
        self.settings = settings
        self.route_fixture = route_fixture  # Only injected by isolated browser integration tests.
        self.resolver = resolver
        self.slots = threading.BoundedSemaphore(2)
        self._availability: dict | None = None

    def availability(self) -> dict:
        if self._availability is None:
            installed, available = False, False
            try:
                with sync_playwright() as playwright:
                    installed = Path(playwright.chromium.executable_path).is_file()
                    if installed:
                        browser = playwright.chromium.launch(headless=True, timeout=10000)
                        browser.close()
                        available = True
            except PlaywrightError:
                pass
            self._availability = {
                "available": available and COLLECTOR_SCRIPT.is_file(),
                "browser_installed": installed,
                "parser_built": COLLECTOR_SCRIPT.is_file(),
                "mode": "headless",
                "requires_open_product_page": False,
            }
        return dict(self._availability)

    def collect(
        self, url: str, *, check_cancel: Callable[[], None] = lambda: None, variant_request=None
    ) -> CollectionResult:
        identity = product_url_identity(url)
        if identity is None:
            return CollectionResult(
                None, "unsupported_url", "仅支持任务内淘宝、天猫和京东商品详情链接。"
            )
        if not self.availability()["available"]:
            return CollectionResult(
                None, "browser_unavailable", "后台浏览器或共享采集器未安装，请检查服务配置。"
            )
        deadline = time.monotonic() + self.settings.browser_timeout_seconds
        while not self.slots.acquire(timeout=0.2):
            check_cancel()
            if time.monotonic() >= deadline:
                return CollectionResult(None, "browser_busy", "后台采集器繁忙，请稍后重试。")
        try:
            if self.route_fixture is not None:
                # In-process fixture injection is only for controlled DOM integration tests.
                return self._collect(identity, url, deadline, check_cancel, variant_request)
            result = run_collection_process(
                {
                    "url": url,
                    "variant_request": variant_request,
                    "settings": {
                        "data_dir": self.settings.data_dir.as_posix(),
                        "browser_timeout_seconds": self.settings.browser_timeout_seconds,
                        "browser_max_scrolls": self.settings.browser_max_scrolls,
                        "browser_trust_fake_ip_dns": self.settings.browser_trust_fake_ip_dns,
                        # The worker captures the price pixels; this process reads them,
                        # because only it holds the model credentials.
                        "price_vision_enabled": self.settings.price_vision_enabled,
                        # The browser runs in this worker, so the takeover settings must
                        # travel with it; the worker reads no .env of its own.
                        "browser_cdp_enabled": self.settings.browser_cdp_enabled,
                        "browser_cdp_url": self.settings.browser_cdp_url,
                        # The worker decides whether a detail read is still owed, so it must
                        # know the switch; otherwise it always reports "nothing pending".
                        "detail_vision_enabled": self.settings.detail_vision_enabled,
                    },
                },
                deadline=deadline,
                check_cancel=check_cancel,
            )
            snapshot = result.get("snapshot")
            screenshot = result.get("price_screenshot_png")
            if snapshot and screenshot and self.settings.price_vision_enabled:
                snapshot = self._apply_vision_price(snapshot, screenshot)
            detail_pending = False
            if snapshot and self.settings.detail_vision_enabled:
                # Detail artwork is pixels, so its words need a vision read. That read takes
                # about a minute for a full section and would push the collection past its
                # deadline, so it is requested here and performed by the caller afterwards.
                detail_pending = True
            return CollectionResult(
                CaptureSnapshotSubmit.model_validate(snapshot) if snapshot else None,
                result.get("error_code"),
                result.get("message"),
                # The worker reports the flag it computed itself; trusting only the parent's
                # own configuration left it unset and detail text was silently never read.
                detail_read_pending=detail_pending
                and bool(result.get("detail_read_pending", True)),
            )
        except CollectionDeadline:
            return CollectionResult(
                unavailable_snapshot(
                    identity,
                    "collection_timeout",
                    "后台采集达到时限，浏览器进程已回收。",
                    failed=True,
                ),
                "collection_timeout",
                "后台采集超时，已结束本次浏览器访问，可重试或接管。",
            )
        except CollectionProcessError:
            return CollectionResult(
                unavailable_snapshot(
                    identity,
                    "worker_failed",
                    "后台采集进程异常退出，未获得有效商品资料。",
                    failed=True,
                ),
                "worker_failed",
                "后台采集进程异常，已回收，可重试或接管。",
            )
        except (PlaywrightError, ValidationError):
            return CollectionResult(
                unavailable_snapshot(
                    identity, "browser_error", "后台浏览器访问或解析失败。", failed=True
                ),
                "browser_error",
                "后台浏览器访问失败；没有将空白页面当成成功采集。",
            )
        finally:
            self.slots.release()

    def _apply_detail_vision(self, snapshot: dict, platform: str, check_cancel):
        """Read detail artwork; the shared module keeps both capture channels consistent."""
        policy = BrowserNetworkPolicy(
            self.resolver, trust_fake_ip_dns=self.settings.browser_trust_fake_ip_dns
        )
        return enrich_detail_images(
            snapshot, self.settings, platform, policy, check_cancel=check_cancel
        )

    def _apply_vision_price(self, snapshot: dict, encoded_png: str) -> dict:
        """Replace a font-obfuscated amount with the value the browser actually painted."""
        try:
            image = base64.b64decode(encoded_png)
        except (ValueError, TypeError):
            return snapshot
        amount, note = read_rendered_price(image, self.settings)
        coverage = snapshot.setdefault("coverage", [])
        if amount is None:
            coverage.append(
                {
                    "field_path": "price.render",
                    "status": "ambiguous",
                    "note": note,
                    "evidence_ids": [],
                }
            )
            return snapshot
        filled = False
        for entry in snapshot.get("prices", []):
            if looks_obfuscated(entry.get("raw_text")):
                entry["amount"] = str(amount)
                entry["status"] = "observed"
                entry["currency"] = entry.get("currency") or "CNY"
                filled = True
        if filled:
            # An "observed" coverage must cite evidence, and the price entry it belongs to
            # already carries the DOM evidence for the painted text.
            price_evidence = [
                evidence_id
                for entry in snapshot.get("prices", [])
                if looks_obfuscated(entry.get("raw_text"))
                for evidence_id in entry.get("evidence_ids", [])
            ]
            coverage.append(
                {
                    "field_path": "price.render",
                    "status": "observed",
                    "note": note,
                    "evidence_ids": price_evidence[:20],
                }
            )
        return snapshot

    def _read_obfuscated_price(self, page, raw, check_cancel):
        """Attach the price screenshot so the caller can read it with the vision model.

        The worker process is deliberately isolated from secrets (``_env_file=None``), so it
        must not call the gateway. It only captures the pixels and reports that a read is
        needed; the parent, which holds the model configuration, performs the recognition.
        """
        prices = raw.get("prices") or []
        masked = [entry for entry in prices if looks_obfuscated(entry.get("raw_text"))]
        if not masked:
            return raw
        check_cancel()
        try:
            element = visible_price_element(page)
            if element is None:
                return self._mark_vision_unavailable(raw, "未找到可截图的价格元素。")
            image = element.screenshot(timeout=10000)
        except PlaywrightError:
            return self._mark_vision_unavailable(raw, "价格区域截图失败。")
        raw["price_screenshot_png"] = base64.b64encode(image).decode()
        return raw

    def _mark_vision_unavailable(self, raw, note):
        raw.setdefault("coverage", []).append(
            {
                "field_path": "price.render",
                "status": "ambiguous",
                "note": note,
                "evidence_ids": [],
            }
        )
        return raw

    def _collect(self, identity, original_url, deadline, check_cancel, variant_request=None):
        def remaining_ms(limit=15000):
            check_cancel()
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise PlaywrightTimeout("collection deadline")
            return min(limit, remaining)

        resolver = self.resolver
        if self.settings.browser_trust_fake_ip_dns and resolver is public_addresses:
            # WSL Fake-IP DNS can return a synthetic IPv4 address alongside a private AAAA
            # response. This opt-in mode validates and uses only the IPv4 resolver path.
            resolver = ipv4_addresses
        policy = BrowserNetworkPolicy(
            resolver, trust_fake_ip_dns=self.settings.browser_trust_fake_ip_dns
        )
        target = product_navigation_url(identity, original_url)
        if not policy.allows(target):
            return CollectionResult(
                None, "destination_blocked", "目标商品地址未通过允许域名及公网地址校验。"
            )
        with sync_playwright() as playwright:
            browser_args = ["--disable-ipv6"] if self.settings.browser_trust_fake_ip_dns else []
            attached = False
            attach_error = ""
            browser = None
            context = None
            if self.settings.browser_cdp_enabled:
                # Prefer the browser the user signed into: Taobao/Tmall send a launched
                # Chromium to a login wall because it reports navigator.webdriver, and that
                # wall rejects the slider verification. This only attaches when that browser
                # is actually running with its debugging port.
                try:
                    browser = playwright.chromium.connect_over_cdp(
                        self.settings.browser_cdp_url, timeout=min(5000, remaining_ms())
                    )
                    context = browser.contexts[0] if browser.contexts else None
                    attached = context is not None
                    if not attached:
                        attach_error = "该浏览器没有可用的上下文"
                except Exception as exc:
                    # Keep the reason: a silent fallback looks identical to a normal run, and
                    # then a "需要登录" result hides the fact that takeover never happened.
                    browser, context, attached = None, None, False
                    attach_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            if not attached:
                browser = playwright.chromium.launch(
                    headless=True, timeout=remaining_ms(), args=browser_args
                )
            try:
                if attached:
                    # The shared context is the user's own session; only a page this run opens
                    # is touched, and its cookie jar is not replaced.
                    context.set_default_timeout(remaining_ms())
                else:
                    state_path = (
                        self.settings.browser_session_dir
                        / f"{session_name(identity.platform)}.json"
                    )
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 1000},
                        locale="zh-CN",
                        service_workers="block",
                        accept_downloads=False,
                        storage_state=str(state_path) if state_path.is_file() else None,
                    )
                    context.set_default_timeout(remaining_ms())

                navigation_gate = None

                def fetch_request(route):
                    nonlocal navigation_gate
                    if not policy.allows(route.request.url) or route.request.resource_type in {
                        "media"
                    }:
                        route.abort()
                    elif self.route_fixture is not None:
                        self.route_fixture(route)
                    else:
                        # Inspect HTTP redirects before following them, including subresources.
                        # route.continue_ alone would leave redirect destinations unchecked.
                        target_url = route.request.url
                        response = None
                        for _ in range(6):
                            response = route.fetch(
                                url=target_url, max_redirects=0, timeout=remaining_ms(15000)
                            )
                            location = response.headers.get("location")
                            if response.status not in {301, 302, 303, 307, 308} or not location:
                                route.fulfill(response=response)
                                response.dispose()
                                return
                            redirected = urljoin(target_url, location)
                            response.dispose()
                            if not policy.allows(redirected):
                                route.abort()
                                return
                            if (
                                route.request.is_navigation_request()
                                and route.request.frame == page.main_frame
                            ):
                                gate = navigation_destination(redirected, identity)
                                if gate:
                                    navigation_gate = gate
                                    route.fulfill(
                                        status=403,
                                        content_type="text/html",
                                        body="<html><body>页面访问受阻</body></html>",
                                    )
                                    return
                            target_url = redirected
                        route.abort()

                def route_request(route):
                    nonlocal navigation_gate
                    try:
                        fetch_request(route)
                    except PlaywrightError:
                        if (
                            route.request.is_navigation_request()
                            and route.request.frame == page.main_frame
                        ):
                            navigation_gate = ("network_error", "后台请求商品页超时或网络连接失败")
                        try:
                            route.abort()
                        except PlaywrightError:
                            pass

                if attached:
                    # Taobao treats a page opened in an otherwise idle profile as a new
                    # visitor and asks it to log in again, even when the stored cookies are
                    # valid. Visiting the site's own front page first establishes the session
                    # so the product page loads normally. Measured: without this the same
                    # product page redirected to login.taobao.com every time.
                    try:
                        warm = context.new_page()
                        warm.goto(
                            session_home_url(identity.platform),
                            wait_until="domcontentloaded",
                            timeout=remaining_ms(15000),
                        )
                        warm.wait_for_timeout(1200)
                        warm.close()
                    except (PlaywrightError, PlaywrightTimeout):
                        # A failed warm-up must not decide the collection; the product page
                        # navigation below still reports its own outcome.
                        pass
                page = context.new_page()
                if attached:
                    # Route on this page only. Routing the shared context would also filter
                    # the tabs the user is browsing in the same window.
                    page.route("**/*", route_request)
                    page.route_web_socket("**/*", lambda route: None)
                else:
                    context.route("**/*", route_request)
                    # Do not connect_to_server: Playwright keeps this socket local. A
                    # synchronous close inside this callback can deadlock the driver's
                    # dispatch greenlet.
                    context.route_web_socket("**/*", lambda route: None)
                context.on("page", lambda popup: popup.close())
                timed_out = False
                response = None
                try:
                    response = page.goto(
                        target, wait_until="domcontentloaded", timeout=remaining_ms(25000)
                    )
                except PlaywrightTimeout:
                    timed_out = True
                check_cancel()
                # readyState is not a usable signal here: on Tmall item pages it reaches
                # "complete" at about 800 ms while the document is still 3 643 px tall, against
                # ~49 000 px once the detail section is injected. Waiting for it therefore
                # released the page far too early and the detail section was never found.
                # Only a short settle is applied here; the detail wait below is what actually
                # watches for the content.
                page.wait_for_timeout(remaining_ms(1200))
                signals = reading_signals(page)
                gate = navigation_gate or access_gate(
                    page.url, signals, identity, response.status if response else None
                )
                if gate:
                    code, marker = gate
                    # State which browser saw this, because "需要登录" means something very
                    # different when read from the user's signed-in browser.
                    mode_note = "已接管用户浏览器" if attached else "后台自带浏览器（无登录态）"
                    marker = f"{marker}；读取方式：{mode_note}"
                    if not attached and attach_error:
                        marker = f"{marker}，接管失败：{attach_error}"
                    return CollectionResult(
                        unavailable_snapshot(
                            identity,
                            code,
                            marker,
                            observed_host=(urlsplit(page.url).hostname or "").lower(),
                            http_status=response.status if response else None,
                        ),
                        code,
                        access_message(code),
                    )
                page.wait_for_timeout(remaining_ms(1200))
                for _ in range(self.settings.browser_max_scrolls):
                    check_cancel()
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    page.evaluate("window.scrollBy(0, Math.max(600, innerHeight * .85))")
                    page.wait_for_timeout(remaining_ms(450))
                check_cancel()
                # The detail section is loaded lazily, so reaching the bottom does not mean it
                # exists yet. Sampling it once would report "missing" for a page that simply
                # had not rendered, which is what made most captures lose all detail text.
                # Wait until detail images appear, and record which of the two happened.
                detail_state = wait_for_detail_images(
                    page,
                    deadline,
                    check_cancel,
                    self.settings.browser_detail_wait_seconds,
                )
                # Reveal a collapsed parameter table before parsing; without the click the
                # table is simply absent from the DOM and would be reported as missing.
                expanded = expand_spec_panel(page)
                check_cancel()
                # Only project-built trusted code is evaluated; merchant text is never executed.
                script = COLLECTOR_SCRIPT.read_text(encoding="utf-8")
                if variant_request:
                    raw = select_and_capture(
                        page, script, variant_request, identity, remaining_ms, check_cancel
                    )
                    if raw is None:
                        return CollectionResult(
                            unavailable_snapshot(
                                identity, "page_changed", "规格操作后页面不再是原商品。"
                            ),
                            "page_changed",
                            "页面变化，未保存其他商品的数据。",
                        )
                else:
                    raw = page.evaluate(
                        f"() => {{\n{script}\nreturn ShoppingAgentCollector.collect();\n}}"
                    )
                raw["adapter_id"] = f"{identity.platform}-playwright"
                raw["adapter_version"] = "0.2.0"
                if expanded:
                    # Recorded as a note on the loading step: expanding a tab is an action,
                    # not page evidence, so it must not claim to cite the specification table.
                    raw.setdefault("coverage", []).append(
                        {
                            "field_path": "page.interaction",
                            "status": "ambiguous",
                            "note": f"已展开“{expanded}”标签后再读取；展开本身不是参数证据。",
                        }
                    )
                if detail_state != "ready":
                    # Distinguish "this page has no detail section" from "it had not rendered
                    # in time"; the two used to be reported as the same missing field.
                    raw.setdefault("coverage", []).append(
                        {
                            "field_path": "page.detail_loading",
                            "status": "ambiguous",
                            "note": (
                                f"等待 {int(DETAIL_WAIT_SECONDS)} 秒后仍未看到详情图区域，"
                                "按页面没有该区域记录（可能尚未加载或被折叠）。"
                                if detail_state == "empty"
                                else "等待详情图区域时页面读取中断，未确认该区域是否存在。"
                            ),
                        }
                    )
                if self.settings.price_vision_enabled:
                    # The page can render the price through a custom font, leaving only
                    # private-use characters in the DOM. Capture what was painted; the
                    # caller reads it, because this code may run in a credential-free worker.
                    raw = self._read_obfuscated_price(page, raw, check_cancel)
                if timed_out:
                    raw["status"] = "partial"
                    raw["coverage"].append(
                        {
                            "field_path": "page.loading",
                            "status": "ambiguous",
                            "note": "页面等待达到时限，只记录实际加载的资料。",
                        }
                    )
                # Record which browser actually read the page. Attaching to the user's own
                # browser and launching a private one produce very different results on sites
                # that require a login, so the difference must be visible rather than inferred.
                raw["coverage"].append(
                    {
                        "field_path": "page.capture_mode",
                        # "ambiguous" because this records how the page was read rather than a
                        # fact observed on the page; observed coverage must cite evidence.
                        "status": "ambiguous",
                        "note": (
                            "已接管用户已登录的浏览器读取页面。"
                            if attached
                            else "使用后台自带浏览器读取页面（无用户登录态）"
                            + (f"；接管失败原因：{attach_error}" if attach_error else "")
                        ),
                    }
                )
                screenshot = raw.pop("price_screenshot_png", None)
                return CollectionResult(
                    CaptureSnapshotSubmit.model_validate(raw),
                    price_screenshot_png=screenshot,
                    # This process owns the browser step only. The caller holds the model
                    # credentials and the database, so it performs the detail read itself.
                    #
                    # A variant switch does not ask for that read: choosing an option changes
                    # the selection and its price, never the artwork. Re-reading it took over
                    # a minute per switch and pushed whole runs past their deadline, while the
                    # text was already captured on the first read of this product.
                    detail_read_pending=bool(
                        self.settings.detail_vision_enabled
                        and raw.get("images")
                        and variant_request is None
                    ),
                )
            finally:
                if attached:
                    # Never close the shared context: it is the user's own browser session.
                    # Closing the CDP connection only detaches this process.
                    try:
                        page.close()  # type: ignore[possibly-undefined]
                    except Exception:
                        pass
                    browser.close()
                else:
                    if context:
                        context.close()
                    if browser:
                        browser.close()
