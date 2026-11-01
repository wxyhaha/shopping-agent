"""Read text out of product detail images and classify what it says.

Marketplace detail sections are mostly merchant artwork: specification panels, banners,
after-sales notes and promotional copy. Their words are pixels, so a vision model reads
them. Detail artwork is where most of a product's real attributes live, so the read has to
be thorough — and thorough means every distinct panel, not a sample of three.

The classification rule is about **what a sentence is doing**, not about whether its claim
can be independently verified:

``parameter``
    It describes *what the product is* — material, structure, form, dimensions, weight,
    performance figures, model numbers, standard categories. "升级纯铜芯" and "三重屏蔽"
    are still attribute statements even though the seller offers no test report; calling
    them marketing would throw away real product description.

``claim``
    It expresses *an evaluation or attitude* — subjective praise, comparisons, awards,
    spokespeople, slogans, certification names.

``policy``
    It states *transaction terms* — after-sales service, delivery, discounts, purchase
    limits.

Batching: several images travel in one request to cut round trips. Measured on real
artwork, five images per request kept every ``parameter`` and ``policy`` while eight lost a
little fidelity, so five is the default.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable

import httpx

MAX_ITEMS = 24
MAX_TEXT_LENGTH = 160
# Images travelling in one request. Validated against a per-image baseline: five preserved
# every parameter and policy, eight was faster but less faithful.
BATCH_SIZE = 5
MAX_IMAGE_BYTES = 4 * 1024 * 1024

_PROMPT = (
    "这张图是电商商品的详情图，可能包含规格参数、售后条款、优惠信息或宣传文案。\n"
    "请逐项提取图中真实出现的文字，并判断每一句在说什么：\n"
    '1) "parameter"：在描述商品本身是什么——材质、结构、形态、尺寸、重量、长度、功率、'
    "接口、性能数值、型号、标准分类（如六类/Cat6）。\n"
    "   注意：「升级纯铜芯」「三重屏蔽」「扁平线身」「下载约1250MB/S」都属于此类，"
    "它们是在陈述商品属性，即使商家没有提供检测报告。\n"
    '2) "claim"：在表达评价或态度——主观赞誉、与他人比较、荣誉奖项、代言人、品牌口号、'
    "认证名称（如「五重国际权威认证」「欧盟REACH认证」）。\n"
    '3) "policy"：在说明交易条件——售后保修、配送、优惠券、满减、购买数量限制、预售预约。\n'
    "   同一项优惠的金额、门槛与适用条件要合并成一条完整表述，不要拆散。\n"
    "判断依据是「这句话在描述商品属性，还是在表达评价/交易条件」，"
    "不是「这个说法能否被第三方验证」。\n"
    "若一句话同时包含属性描述和宣传评价，整句判为 claim。\n"
    '只输出 JSON：{"items":[{"type":"parameter","text":"..."}]}。\n'
    "text 必须是图中原文片段，不要改写、不要解释、不要推测。没有可提取内容时输出 "
    '{"items":[]}。'
)

_MULTI_PROMPT = (
    "下面按顺序给出同一个商品的 {count} 张详情图，每张以「第N张」标注。\n"
    "请逐张提取图中真实出现的文字，并判断每一句在说什么：\n"
    '1) "parameter"：在描述商品本身是什么——材质、结构、形态、尺寸、重量、长度、功率、'
    "接口、性能数值、型号、标准分类。\n"
    '2) "claim"：在表达评价或态度——主观赞誉、比较、荣誉奖项、代言人、品牌口号、认证名称。\n'
    '3) "policy"：在说明交易条件——售后保修、配送、优惠券、满减、购买限制、预售预约。'
    "同一项优惠的金额、门槛与条件合并为一条。\n"
    "判断依据是「描述商品属性，还是表达评价/交易条件」，不是「能否被第三方验证」。\n"
    "混合了属性与评价的整句判为 claim。\n"
    '只输出 JSON：{"items":[{"image":1,"type":"parameter","text":"..."}]}。\n'
    "image 是该条内容所在的图号（从 1 开始）。text 必须是图中原文，不要改写或推测。"
)


def _strip_markers(line: str) -> str:
    return re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()


# Artwork often carries call-to-action chrome ("点击购买 >") that is page furniture rather
# than product information. It is filtered out before anything is stored.
_UI_NOISE = re.compile(
    r"^(?:点击|立即|马上|去|查看|了解|加入)(?:购买|下单|了解|查看|购物车|详情|更多)|"
    r"^>$|^>>$|^查看更多$|^立即抢购$",
)


def _is_ui_noise(line: str) -> bool:
    """True for call-to-action wording that carries no product meaning."""
    value = (line or "").strip()
    if not value:
        return True
    if _UI_NOISE.search(value):
        return True
    # A bare arrow or symbol left over from a button has no content of its own.
    return bool(re.fullmatch(r"[>\u203a\u00bb\s]+", value))


def _clean(kind: str, raw: object) -> tuple[str, str] | None:
    """Return ``(bucket, text)`` for a stored item, or None when it should be dropped."""
    value = _strip_markers(str(raw or ""))[:MAX_TEXT_LENGTH]
    if not value or _is_ui_noise(value):
        return None
    if kind == "parameter":
        return "parameter", value
    if kind == "policy":
        return "policy", value
    # An unexpected label could be promotional wording; never promote it to a parameter.
    return "claim", value


def parse_detail_items(reply: str) -> tuple[list[str], list[str], list[str]]:
    """Split a model reply into ``(parameters, claims, policies)``."""
    text = (reply or "").strip()
    if not text or text.upper().startswith("NONE"):
        return [], [], []
    payload = text
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        payload = fence.group(1).strip()
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return _fallback_split(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return _fallback_split(text)
    parameters: list[str] = []
    claims: list[str] = []
    policies: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned = _clean(str(item.get("type") or ""), item.get("text"))
        if cleaned is None:
            continue
        bucket, value = cleaned
        {"parameter": parameters, "claim": claims, "policy": policies}[bucket].append(value)
    return parameters[:MAX_ITEMS], claims[:MAX_ITEMS], policies[:MAX_ITEMS]


def _fallback_split(text: str) -> tuple[list[str], list[str], list[str]]:
    """When no structured answer arrives, keep the words but treat them all as claims."""
    claims = [
        _strip_markers(line)[:MAX_TEXT_LENGTH]
        for line in text.splitlines()
        if _strip_markers(line) and line.strip().upper() != "NONE"
    ]
    return [], claims[:MAX_ITEMS], []


def _endpoint(settings) -> tuple[str, dict] | None:
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base or not settings.llm_model:
        return None
    headers = {"Accept": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
    return f"{base}/chat/completions", headers


def _post(payload: dict, settings) -> str | None:
    """Send one request and return the message text, or None on any failure."""
    target = _endpoint(settings)
    if target is None:
        return None
    url, headers = target
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def read_detail_image(
    image: bytes, settings, *, transport=None
) -> tuple[list[str], list[str], list[str], str]:
    """Extract parameters, claims and policies from one detail image.

    Returns ``(parameters, claims, policies, note)``. All lists empty means nothing
    trustworthy was read, which is the honest answer for decorative artwork.
    """
    if _endpoint(settings) is None:
        return [], [], [], "模型网关未配置，无法读取详情图。"
    if len(image) > MAX_IMAGE_BYTES:
        return [], [], [], "详情图过大，未读取。"
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
        "max_tokens": 900,
    }
    reply = _post(payload, settings)
    if reply is None:
        return [], [], [], "读取详情图的请求失败或响应无法解析。"
    parameters, claims, policies = parse_detail_items(reply)
    if not parameters and not claims and not policies:
        return [], [], [], "详情图中未识别到文字。"
    return parameters, claims, policies, "详情图文字由视觉模型读取，属商家标注。"


def read_detail_batch(
    images: list[bytes], settings
) -> tuple[dict[int, tuple[list[str], list[str], list[str]]], str]:
    """Read several images in one request.

    Returns ``({index: (parameters, claims, policies)}, note)`` where ``index`` is the
    zero-based position in ``images``. Items whose ``image`` field cannot be trusted are
    attributed to the whole batch, which is why the caller also keeps per-image evidence.
    """
    if not images:
        return {}, "没有可读取的详情图。"
    if _endpoint(settings) is None:
        return {}, "模型网关未配置，无法读取详情图。"
    content: list[dict] = [
        {"type": "text", "text": _MULTI_PROMPT.replace("{count}", str(len(images)))}
    ]
    for position, image in enumerate(images, start=1):
        if len(image) > MAX_IMAGE_BYTES:
            return {}, f"第 {position} 张详情图过大，未读取。"
        content.append({"type": "text", "text": f"\n--- 第{position}张 ---"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()},
            }
        )
    payload = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1200 + 400 * len(images),
    }
    reply = _post(payload, settings)
    if reply is None:
        return {}, "批量读取详情图的请求失败或响应无法解析。"
    text = reply.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # No usable structure: fall back to one text bucket for the whole batch.
        parameters, claims, policies = parse_detail_items(reply)
        if not parameters and not claims and not policies:
            return {}, "批量读取未识别到文字。"
        return {0: (parameters, claims, policies)}, "批量结果无法定位到单张图，归入首张。"

    grouped: dict[int, tuple[list[str], list[str], list[str]]] = {}
    buckets: dict[int, tuple[list[str], list[str], list[str]]] = {}
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        cleaned = _clean(str(item.get("type") or ""), item.get("text"))
        if cleaned is None:
            continue
        bucket, value = cleaned
        raw_index = item.get("image")
        index = raw_index - 1 if isinstance(raw_index, int) and 1 <= raw_index <= len(images) else 0
        entry = buckets.setdefault(index, ([], [], []))
        lists = {"parameter": entry[0], "claim": entry[1], "policy": entry[2]}
        lists[bucket].append(value)
    for index, (parameters, claims, policies) in buckets.items():
        grouped[index] = (
            parameters[:MAX_ITEMS],
            claims[:MAX_ITEMS],
            policies[:MAX_ITEMS],
        )
    if not grouped:
        return {}, "批量读取未识别到文字。"
    return grouped, "详情图文字由视觉模型分批读取，属商家标注。"


def chunked(items: Iterable, size: int) -> list[list]:
    """Split items into fixed-size batches; the vision endpoint reads one batch per call."""
    values = list(items)
    return [values[start : start + size] for start in range(0, len(values), size)]
