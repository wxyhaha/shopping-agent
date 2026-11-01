"""Read text out of product detail images, separating facts from sales language.

Marketplace detail sections are mostly merchant artwork: banners, certification collages
and promotional claims. Their words cannot be obtained from the DOM because they are
pixels, so a vision model reads them.

Two things matter more than raw recall:

1. **Detail artwork is where most real specifications live** (model numbers, gauges,
   materials, dimensions), so the read must be thorough rather than a single glance.
2. **The same artwork also carries marketing adjectives** — "国际权威认证", "比普通加粗芯
   更快", "隐形走线". Those must never be presented as measured attributes.

The model therefore returns each item tagged as either ``spec`` (a parameter-shaped
statement) or ``claim`` (subjective, comparative or promotional). Only ``spec`` items may be
shown as specifications, and even those are attributed to the detail image rather than
treated as independently verified.
"""

from __future__ import annotations

import base64
import json
import re

import httpx

MAX_ITEMS = 24
MAX_TEXT_LENGTH = 160

_PROMPT = (
    "这张图是电商商品的详情图，可能包含参数表、规格说明或宣传文案。"
    "请逐项提取图片中真实出现的文字信息，分为两类：\n"
    '1) "spec"：可核对的参数，例如型号、材质、尺寸、重量、长度、功率、接口、'
    "认证名称与编号、性能数值。\n"
    '2) "claim"：主观或宣传性表述，例如「更好」「更快」「权威认证」「隐形走线」「品质之选」、'
    "与其他产品对比、无法核实的功效描述。\n"
    '只输出 JSON，格式为 {"items":[{"type":"spec","text":"..."}]}。'
    "text 必须是图片中出现的原文要点，不要解释，不要推测，不要补全图片里没有的内容。"
    '如果两类都没有，输出 {"items":[]}。'
)


def _strip_markers(line: str) -> str:
    return re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()


def parse_detail_items(reply: str) -> tuple[list[str], list[str]]:
    """Split a model reply into ``(spec_lines, claim_lines)``.

    Anything that cannot be classified is discarded rather than guessed: an unlabelled
    sentence could be a marketing line, and treating it as a specification would be worse
    than reading nothing.
    """
    text = (reply or "").strip()
    if not text or text.upper().startswith("NONE"):
        return [], []
    payload = text
    # Models often wrap JSON in a fenced block; strip it before parsing.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        payload = fence.group(1).strip()
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        # A plain list is still usable: treat unknown lines as claims, never as specs.
        return _fallback_split(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return _fallback_split(text)
    specs: list[str] = []
    claims: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _strip_markers(str(item.get("text") or ""))[:MAX_TEXT_LENGTH]
        if not value:
            continue
        if item.get("type") == "spec":
            specs.append(value)
        else:
            claims.append(value)
    return specs[:MAX_ITEMS], claims[:MAX_ITEMS]


def _fallback_split(text: str) -> tuple[list[str], list[str]]:
    """When no structured answer arrives, keep the words but treat them all as claims."""
    claims = [
        _strip_markers(line)[:MAX_TEXT_LENGTH]
        for line in text.splitlines()
        if _strip_markers(line) and line.strip().upper() != "NONE"
    ]
    return [], claims[:MAX_ITEMS]


def read_detail_image(
    image: bytes, settings, *, transport=None
) -> tuple[list[str], list[str], str]:
    """Extract specifications and claims from one detail image.

    Returns ``(spec_lines, claim_lines, note)``. Both lists empty means nothing trustworthy
    was read, which is the honest answer for purely decorative artwork.
    """
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base or not settings.llm_model:
        return [], [], "模型网关未配置，无法读取详情图。"
    if len(image) > 4 * 1024 * 1024:
        return [], [], "详情图过大，未读取。"
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
        "max_tokens": 700,
    }
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(f"{base}/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return [], [], f"读取详情图的请求失败：{type(exc).__name__}。"
    if response.status_code != 200:
        return [], [], f"读取详情图的请求返回 HTTP {response.status_code}。"
    try:
        reply = response.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return [], [], "读取详情图的响应格式无法解析。"
    specs, claims = parse_detail_items(reply)
    if not specs and not claims:
        return [], [], "详情图中未识别到文字。"
    return specs, claims, "详情图文字由视觉模型读取；spec 为图中参数，claim 为商家宣传表述。"
