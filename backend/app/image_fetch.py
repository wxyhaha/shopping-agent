"""Fetch a product detail image so a vision model can read it.

Image CDNs for these marketplaces reject requests without a plausible Referer, and the
collector's network policy is the only place that decides which hosts may be contacted.
Both concerns live here so the caller does not have to remember them.
"""

from __future__ import annotations

import httpx

MAX_BYTES = 4 * 1024 * 1024
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# The CDN checks that the request looks like it came from the marketplace page.
_REFERERS = {
    "jd": "https://item.m.jd.com/",
    "taobao": "https://item.taobao.com/",
    "tmall": "https://detail.tmall.com/",
}


def fetch_image(url: str, platform: str, policy) -> tuple[bytes | None, str]:
    """Download one detail image, honouring the same host policy as page collection.

    ``policy`` is the caller's `BrowserNetworkPolicy`; passing it in keeps this module free
    of a circular import back to the collector.
    """
    if not policy.allows(url):
        return None, "详情图地址未通过允许域名及公网地址校验。"
    headers = {"User-Agent": _USER_AGENT, "Accept": "image/avif,image/webp,image/jpeg,image/*"}
    referer = _REFERERS.get(platform)
    if referer:
        headers["Referer"] = referer
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return None, f"详情图下载失败：{type(exc).__name__}。"
    if response.status_code != 200:
        return None, f"详情图下载返回 HTTP {response.status_code}。"
    content_type = (response.headers.get("content-type") or "").lower()
    if not content_type.startswith("image/"):
        return None, "详情图地址返回的不是图片。"
    content = response.content
    if not content or len(content) > MAX_BYTES:
        return None, "详情图大小不符合要求。"
    return content, ""
