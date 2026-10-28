"""Open task product URLs in an isolated headless browser and collect evidence."""

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
from app.models import utc_now
from app.schemas import CaptureSnapshotSubmit
from app.variant_controller import select_and_capture

COLLECTOR_SCRIPT = Path(__file__).with_name("browser_assets") / "collector.js"
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


def session_name(platform: str) -> str:
    return "taobao" if platform in {"taobao", "tmall"} else "jd"


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
    return urlunsplit(("https", hosts[identity.platform], path, urlencode(query), ""))


def public_addresses(host: str) -> list[str]:
    return list({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def ipv4_addresses(host: str) -> list[str]:
    return list(
        {
            row[4][0]
            for row in socket.getaddrinfo(
                host, 443, type=socket.SOCK_STREAM, family=socket.AF_INET
            )
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
        return "redirected", "页面没有停留在请求的同一商品详情页"
    return None


def access_gate(url: str, signals: dict, expected: ProductUrlIdentity, status: int | None):
    """Return a safe code and an observed static access marker, never account text."""
    if status in {401, 403, 429}:
        return "access_denied", f"HTTP {status}"
    destination = navigation_destination(url, expected)
    if destination:
        return destination
    text, title = signals.get("text", ""), signals.get("title", "")
    if not signals.get("hasProduct"):
        for pattern, code in (
            (
                r"安全验证|滑动验证|请完成验证|请输入验证码|访问受限|验证后继续",
                "verification_required",
            ),
            (r"请先登录|登录后查看|登录后继续|扫码登录|账号密码登录", "login_required"),
        ):
            match = re.search(pattern, f"{title}\n{text}")
            if match:
                return code, match.group(0)
        if not text.strip():
            return "empty_page", "商品页尚无可读取正文"
        return "product_not_ready", "页面尚未出现可识别的商品详情区域"
    return None


def unavailable_snapshot(identity: ProductUrlIdentity, code: str, marker: str, failed=False):
    status = "missing" if failed else "blocked"
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
                    "raw_value": marker,
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
                    "excerpt": marker,
                    "locator": code,
                }
            ],
            "coverage": [
                {
                    "field_path": "page.access",
                    "status": status,
                    "note": marker,
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
                    },
                },
                deadline=deadline,
                check_cancel=check_cancel,
            )
            return CollectionResult(
                CaptureSnapshotSubmit.model_validate(result["snapshot"])
                if result.get("snapshot")
                else None,
                result.get("error_code"),
                result.get("message"),
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
            browser = playwright.chromium.launch(
                headless=True, timeout=remaining_ms(), args=browser_args
            )
            context = None
            try:
                state_path = (
                    self.settings.browser_session_dir / f"{session_name(identity.platform)}.json"
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

                context.route("**/*", route_request)
                # Do not connect_to_server: Playwright keeps this socket local. A synchronous
                # close inside this callback can deadlock the driver's dispatch greenlet.
                context.route_web_socket("**/*", lambda route: None)
                page = context.new_page()
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
                for _ in range(12):
                    if time.monotonic() >= deadline:
                        break
                    page.wait_for_timeout(remaining_ms(250))
                    signals = page.evaluate(PAGE_SIGNALS)
                    if signals.get("hasProduct"):
                        break
                signals = page.evaluate(PAGE_SIGNALS)
                gate = navigation_gate or access_gate(
                    page.url, signals, identity, response.status if response else None
                )
                if gate:
                    code, marker = gate
                    return CollectionResult(
                        unavailable_snapshot(identity, code, marker),
                        code,
                        "后台页面受阻，需要登录、验证或人工接管。",
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
                if timed_out:
                    raw["status"] = "partial"
                    raw["coverage"].append(
                        {
                            "field_path": "page.loading",
                            "status": "ambiguous",
                            "note": "页面等待达到时限，只记录实际加载的资料。",
                        }
                    )
                return CollectionResult(CaptureSnapshotSubmit.model_validate(raw))
            finally:
                if context:
                    context.close()
                browser.close()
