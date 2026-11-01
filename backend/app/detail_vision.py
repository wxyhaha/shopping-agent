"""Read text out of product detail images.

Marketplace detail sections are mostly merchant artwork: banners, certification collages
and promotional claims. The words on them cannot be obtained from the DOM because they are
pixels, so a vision model reads them.

What comes back is **marketing copy, not verified fact**. A certification badge painted on
an image is the seller's claim, not an independently checked attribute, so the recognised
text is stored as separate observations the report must attribute to the image rather than
merge into the measured specification table. The reader therefore never emits structured
parameters; it emits lines of text with the image they came from.
"""

from __future__ import annotations

import base64
import re

import httpx

# Detail images are merchant artwork; keep the prompt strictly extractive.
_PROMPT = (
    "这张图是电商商品的详情宣传图片。"
    "请只提取图片中真实出现的文字要点，重点关注型号、规格、材质、认证、尺寸、重量、功率等参数类信息。"
    "每行一条，最多 8 行，不要解释、不要推测、不要补全图片里没有的内容。"
    "如果图中没有可读的参数类文字，只输出 NONE。"
)

MAX_LINES = 8
MAX_LINE_LENGTH = 120
# Very large artwork gains nothing after downscaling by the model; skip huge payloads.
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def parse_detail_lines(reply: str) -> list[str]:
    """Turn a model reply into clean lines, dropping anything that is not extractive text."""
    text = (reply or "").strip()
    if not text or text.upper().startswith("NONE"):
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", raw).strip()
        if not line or line.upper() == "NONE":
            continue
        lines.append(line[:MAX_LINE_LENGTH])
    # Deduplicate while keeping order; the same claim often repeats across panels.
    seen: set[str] = set()
    unique = [line for line in lines if not (line in seen or seen.add(line))]
    return unique[:MAX_LINES]


def read_detail_image(image: bytes, settings, *, transport=None) -> tuple[list[str], str]:
    """Extract parameter-like text from one detail image.

    Returns ``(lines, note)``. An empty list means nothing trustworthy was read, which is
    the honest answer for an image that is purely decorative.
    """
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base or not settings.llm_model:
        return [], "模型网关未配置，无法读取详情图。"
    if len(image) > MAX_IMAGE_BYTES:
        return [], "详情图过大，未读取。"
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
                            "url": "data:image/jpeg;base64," + base64.b64encode(image).decode()
                        },
                    },
                ],
            }
        ],
        "max_tokens": 300,
    }
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(f"{base}/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return [], f"读取详情图的请求失败：{type(exc).__name__}。"
    if response.status_code != 200:
        return [], f"读取详情图的请求返回 HTTP {response.status_code}。"
    try:
        reply = response.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return [], "读取详情图的响应格式无法解析。"
    lines = parse_detail_lines(reply)
    if not lines:
        return [], "详情图中未识别到参数类文字。"
    return lines, "详情图文字由视觉模型读取，属于商家宣传材料，未经独立核实。"
