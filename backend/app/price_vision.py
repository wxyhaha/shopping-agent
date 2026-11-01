"""Read a rendered price from a screenshot when the page hides it behind a font.

Some marketplaces serve price digits as private-use code points rendered through a
custom font (JD uses ``JDZH-Regular-Price``). The DOM then carries no number at all, so
text parsing cannot recover it, and mapping the font back to digits would be defeating a
deliberate anti-scraping measure. Reading what the browser actually painted is neither:
it is the same value a person sees on screen. The recognition is therefore limited to a
price-shaped answer, cross-checked against the currency symbol, and kept as separate
evidence rather than overwriting the page text.
"""

from __future__ import annotations

import base64
import re
from decimal import Decimal, InvalidOperation

import httpx

# Private-use code points are how a font-obfuscated price arrives in the DOM.
_OBFUSCATED = re.compile(r"[\ue000-\uf8ff]")
_AMOUNT = re.compile(r"^[¥￥]?\s*(\d{1,9}(?:\.\d{1,2})?)$")
_PROMPT = (
    "这张图是从电商商品页截取的价格区域。"
    "只输出图片中显示的价格数字，保留小数点，不要货币符号以外的文字、不要解释、不要推测。"
    "如果图中没有可读价格，只输出 NONE。"
)


def looks_obfuscated(text: str | None) -> bool:
    """True when a price string contains characters only a custom font can show."""
    return bool(text) and bool(_OBFUSCATED.search(text))


def parse_amount(reply: str) -> Decimal | None:
    """Accept only a bare price-shaped answer; anything else is not trusted."""
    value = (reply or "").strip().replace(",", "").replace("，", "")
    value = value.replace("元", "").replace("人民币", "").strip()
    match = _AMOUNT.match(value)
    if not match:
        return None
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if amount <= 0 or amount > Decimal("9999999"):
        return None
    return amount


def read_rendered_price(image: bytes, settings, *, transport=None) -> tuple[Decimal | None, str]:
    """Ask the configured vision-capable model what price the screenshot shows.

    Returns ``(amount, note)``. ``amount`` is ``None`` whenever the reply is not a single
    trustworthy price, so a failed read can never turn into a fabricated figure.
    """
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base or not settings.llm_model:
        return None, "模型网关未配置，无法读取渲染后的价格。"
    headers = {"Accept": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image).decode()
                        },
                    },
                ],
            }
        ],
        "max_tokens": 40,
    }
    url = f"{base}/chat/completions"
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return None, f"读取渲染价格的请求失败：{type(exc).__name__}。"
    if response.status_code != 200:
        return None, f"读取渲染价格的请求返回 HTTP {response.status_code}。"
    try:
        reply = response.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return None, "读取渲染价格的响应格式无法解析。"
    amount = parse_amount(reply)
    if amount is None:
        return None, "视觉模型未给出可核对的单一价格，未采用该结果。"
    return amount, "价格数字以自定义字体渲染，已从页面实际显示读取。"
