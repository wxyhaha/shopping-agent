"""Conservative arithmetic for prices tied to observed product selections."""

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from itertools import combinations
from typing import Any

COMPARABLE_PRICE_KINDS = {"current", "list", "member", "coupon"}
PRICE_KIND_ORDER = {"current": 0, "list": 1, "member": 2, "coupon": 3}
LENGTH_LABEL = re.compile(r"长度|线长|length", re.IGNORECASE)
MASS_LABEL = re.compile(r"重量|净重|质量|净含量|weight|mass", re.IGNORECASE)
VOLUME_LABEL = re.compile(r"容量|容积|净含量|含量|volume|capacity", re.IGNORECASE)
COUNT_LABEL = re.compile(
    r"^(?:商品)?(?:数量|件数|个数|片数|支数|瓶数|盒数|包装数量|每包数量|count|quantity|pack size)$",
    re.IGNORECASE,
)
STORAGE_LABEL = re.compile(r"存储容量|硬盘|固态|ssd|storage|disk", re.IGNORECASE)
MEASURE_VALUE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(毫克|千克|公斤|毫升|公尺|毫米|厘米|kg|mg|ml|cm|mm|tb|gb|mb|g|克|l|升|m|米|个|件|只|支|片|颗|瓶|盒|包|套|台|双|对|张|条|根|卷|把|粒|枚)\s*(?:装|长)?\s*$",
    re.IGNORECASE,
)
MEASURE_UNITS = {
    "kg": ("mass", "kg", Decimal("1")),
    "千克": ("mass", "kg", Decimal("1")),
    "公斤": ("mass", "kg", Decimal("1")),
    "g": ("mass", "kg", Decimal("0.001")),
    "克": ("mass", "kg", Decimal("0.001")),
    "mg": ("mass", "kg", Decimal("0.000001")),
    "毫克": ("mass", "kg", Decimal("0.000001")),
    "l": ("volume", "L", Decimal("1")),
    "升": ("volume", "L", Decimal("1")),
    "ml": ("volume", "L", Decimal("0.001")),
    "毫升": ("volume", "L", Decimal("0.001")),
    "m": ("length", "m", Decimal("1")),
    "米": ("length", "m", Decimal("1")),
    "公尺": ("length", "m", Decimal("1")),
    "cm": ("length", "m", Decimal("0.01")),
    "厘米": ("length", "m", Decimal("0.01")),
    "mm": ("length", "m", Decimal("0.001")),
    "毫米": ("length", "m", Decimal("0.001")),
    "tb": ("storage", "GB", Decimal("1000")),
    "gb": ("storage", "GB", Decimal("1")),
    "mb": ("storage", "GB", Decimal("0.001")),
    **{
        unit: ("count", unit, Decimal("1"))
        for unit in (
            "个",
            "件",
            "只",
            "支",
            "片",
            "颗",
            "瓶",
            "盒",
            "包",
            "套",
            "台",
            "双",
            "对",
            "张",
            "条",
            "根",
            "卷",
            "把",
            "粒",
            "枚",
        )
    },
}
MONEY_QUANTUM = Decimal("0.01")
UNIT_PRICE_QUANTUM = Decimal("0.000001")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    # Bound legacy/malformed values before division and quantize (including exponents).
    if len(result.as_tuple().digits) > 18 or not -12 <= result.as_tuple().exponent <= 14:
        return None
    return result if result < Decimal("1e14") else None


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f") if normalized else "0"


def _reference(snapshot_id: str, evidence_id: str) -> str:
    return f"{snapshot_id}:{evidence_id}"


def _references(snapshot_id: str, evidence_ids: list[str], limit: int = 40) -> list[str]:
    return list(dict.fromkeys(_reference(snapshot_id, item) for item in evidence_ids))[:limit]


def _verified_selection(snapshot_id: str | None, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    if not snapshot_id:
        return None
    selected = snapshot.get("selected_variant") or {}
    verification = snapshot.get("variant_verification") or {}
    if selected.get("status") not in {"observed", "user_confirmed"}:
        return None
    if verification.get("status") != "selected":
        return None
    if verification.get("price_binding") != "same_page_state":
        return None
    label = selected.get("label")
    attribute_refs = [
        evidence_id
        for attribute in selected.get("attributes", [])
        if attribute.get("status") in {"observed", "user_confirmed"}
        for evidence_id in attribute.get("evidence_ids", [])
    ]
    evidence_ids = list(selected.get("evidence_ids", [])) + list(
        verification.get("evidence_ids", [])
    )
    if not evidence_ids:
        evidence_ids = attribute_refs
    if not evidence_ids or not _valid_refs(snapshot, evidence_ids):
        return None
    return {
        "label": label,
        "evidence_refs": _references(snapshot_id, evidence_ids, limit=20),
        "attributes": selected.get("attributes", []),
    }


def _valid_refs(snapshot: dict, references: list[str]) -> bool:
    known = {entry.get("evidence_id") for entry in snapshot.get("evidence", [])}
    return bool(references) and set(references).issubset(known)


def _condition_signature(
    price: dict[str, Any], snapshot: dict
) -> tuple[tuple[str, str], ...] | None:
    conditions: list[tuple[str, str]] = []
    for entry in price.get("conditions", []):
        if entry.get("status") not in {"observed", "user_confirmed"}:
            return None
        if not _valid_refs(snapshot, entry.get("evidence_ids", [])):
            return None
        key = entry.get("condition")
        value = entry.get("normalized_value") or entry.get("raw_value")
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            return None
        normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        conditions.append((key, normalized))
    if len({key for key, _ in conditions}) != len(conditions):
        return None
    return tuple(sorted(conditions))


def _eligible_prices(snapshot_id: str | None, snapshot: dict[str, Any]) -> dict[tuple, list[dict]]:
    if not snapshot_id:
        return {}
    prices: dict[tuple, list[dict]] = {}
    for entry in snapshot.get("prices", []):
        kind = entry.get("kind")
        if kind not in COMPARABLE_PRICE_KINDS:
            continue
        if entry.get("status") not in {"observed", "user_confirmed"}:
            continue
        amount = _decimal(entry.get("amount"))
        currency = entry.get("currency")
        raw_text = entry.get("raw_text")
        evidence_ids = entry.get("evidence_ids") or []
        conditions = _condition_signature(entry, snapshot)
        if kind in {"member", "coupon"} and not entry.get("conditions"):
            continue
        if (
            amount is None
            or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)
            or not isinstance(raw_text, str)
            or not raw_text
            or not _valid_refs(snapshot, evidence_ids)
            or conditions is None
        ):
            continue
        condition_refs = [
            evidence_id
            for condition in entry.get("conditions", [])
            for evidence_id in condition.get("evidence_ids", [])
        ]
        evidence_refs = _references(snapshot_id, list(evidence_ids) + condition_refs, limit=40)
        condition_labels = []
        for condition in entry.get("conditions", []):
            value = condition.get("normalized_value") or condition.get("raw_value")
            condition_labels.append(f"{condition.get('condition')}：{value}")
        value = {
            "amount_decimal": amount,
            "currency": currency,
            "kind": kind,
            "raw_text": raw_text,
            "conditions": condition_labels,
            "evidence_refs": evidence_refs,
        }
        prices.setdefault((kind, conditions), []).append(value)
    return prices


def _measurement_labels_match(dimension: str, label: str) -> bool:
    if dimension == "length":
        return bool(LENGTH_LABEL.search(label))
    if dimension == "mass":
        return bool(MASS_LABEL.search(label) or VOLUME_LABEL.search(label))
    if dimension == "volume":
        return bool(VOLUME_LABEL.search(label))
    if dimension == "count":
        return bool(COUNT_LABEL.fullmatch(label.strip()))
    if dimension == "storage":
        return bool(STORAGE_LABEL.search(label))
    return False


def _measurements(
    snapshot_id: str | None, selected: dict[str, Any] | None, snapshot: dict
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    measurements: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if not snapshot_id or not selected:
        return measurements
    for attribute in selected.get("attributes", []):
        label = attribute.get("label") or attribute.get("field_path") or ""
        if attribute.get("status") not in {"observed", "user_confirmed"}:
            continue
        raw_value = attribute.get("normalized_value") or attribute.get("raw_value")
        if not isinstance(raw_value, str):
            continue
        parsed = MEASURE_VALUE.fullmatch(unicodedata.normalize("NFKC", raw_value))
        if not parsed:
            continue
        amount = _decimal(parsed.group(1))
        if amount is None or amount == 0:
            continue
        source_unit = parsed.group(2).casefold()
        definition = MEASURE_UNITS.get(source_unit)
        if definition is None:
            continue
        dimension, canonical_unit, factor = definition
        if not _measurement_labels_match(dimension, str(label)):
            continue
        evidence_ids = attribute.get("evidence_ids") or []
        if _valid_refs(snapshot, evidence_ids):
            key = (dimension, canonical_unit)
            measurements.setdefault(key, []).append(
                {
                    "quantity": amount * factor,
                    "evidence_refs": _references(snapshot_id, evidence_ids),
                }
            )
    return measurements


def _product(item: dict[str, Any], selected: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "item_id": item["item_id"],
        "title": (item.get("title") or "未命名商品")[:512],
        "selected_variant": (selected or {}).get("label"),
        "evidence_refs": (selected or {}).get("evidence_refs", []),
    }


def _price_value(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "amount": _decimal_text(value["amount_decimal"]),
        "currency": value["currency"],
        "kind": value["kind"],
        "raw_text": value["raw_text"][:512],
        "conditions": value["conditions"],
        "evidence_refs": value["evidence_refs"],
    }


def _basis_calculation(
    item_a: dict[str, Any],
    item_b: dict[str, Any],
    price_a: dict[str, Any],
    price_b: dict[str, Any],
    measurements_a: dict[tuple[str, str], list[dict[str, Any]]],
    measurements_b: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    amount_a = price_a["amount_decimal"]
    amount_b = price_b["amount_decimal"]
    difference = abs(amount_a - amount_b)
    higher = max(amount_a, amount_b)
    percent = (
        (difference / higher * Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if higher > 0
        else None
    )
    lower_item_id = (
        item_a["item_id"]
        if amount_a < amount_b
        else item_b["item_id"]
        if amount_b < amount_a
        else None
    )
    unit_prices = []
    ambiguous_measurement = False
    for dimension, unit in sorted(set(measurements_a) & set(measurements_b)):
        values_a = measurements_a[(dimension, unit)]
        values_b = measurements_b[(dimension, unit)]
        if len(values_a) != 1 or len(values_b) != 1:
            ambiguous_measurement = True
            continue
        quantity_a = values_a[0]["quantity"]
        quantity_b = values_b[0]["quantity"]
        raw_a, raw_b = amount_a / quantity_a, amount_b / quantity_b
        price_a_per_unit = raw_a.quantize(UNIT_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
        price_b_per_unit = raw_b.quantize(UNIT_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
        difference_per_unit = abs(raw_a - raw_b).quantize(
            UNIT_PRICE_QUANTUM, rounding=ROUND_HALF_UP
        )
        lower_per_unit = (
            item_a["item_id"] if raw_a < raw_b else item_b["item_id"] if raw_b < raw_a else None
        )
        unit_prices.append(
            {
                "dimension": dimension,
                "unit": unit,
                "quantity_a": _decimal_text(quantity_a),
                "quantity_b": _decimal_text(quantity_b),
                "price_a_per_unit": _decimal_text(price_a_per_unit),
                "price_b_per_unit": _decimal_text(price_b_per_unit),
                "difference_per_unit": _decimal_text(difference_per_unit),
                "lower_price_item_id": lower_per_unit,
                "currency": price_a["currency"],
                "evidence_refs": list(
                    dict.fromkeys(
                        values_a[0]["evidence_refs"]
                        + values_b[0]["evidence_refs"]
                        + price_a["evidence_refs"]
                        + price_b["evidence_refs"]
                    )
                )[:40],
            }
        )
    has_measurements = bool(measurements_a or measurements_b)
    if ambiguous_measurement:
        unit_price_unavailable_reason = "部分计量规格对应多条证据，无法唯一核算。"
    elif not unit_prices and has_measurements:
        unit_price_unavailable_reason = (
            "两件商品没有相同计量维度和单位的已核验规格，不能折算单位价。"
        )
    else:
        unit_price_unavailable_reason = None
    return {
        "kind": price_a["kind"],
        "price_a": _price_value(price_a),
        "price_b": _price_value(price_b),
        "difference_amount": _decimal_text(difference),
        "difference_percent_of_higher": _decimal_text(percent) if percent is not None else None,
        "lower_price_item_id": lower_item_id,
        "unit_prices": unit_prices,
        "unit_price_unavailable_reason": unit_price_unavailable_reason,
    }


def calculate_price_comparisons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calculate pairwise values only when source data supports a like-for-like basis."""
    prepared: list[dict[str, Any]] = []
    for item in items:
        snapshot_id = item.get("snapshot_id")
        snapshot = item.get("snapshot") or {}
        selected = _verified_selection(snapshot_id, snapshot)
        prepared.append(
            {
                **item,
                "snapshot": snapshot,
                "selected": selected,
                "prices": _eligible_prices(snapshot_id, snapshot),
                "measurements": _measurements(snapshot_id, selected, snapshot),
            }
        )

    comparisons = []
    for item_a, item_b in combinations(prepared, 2):
        product_a = _product(item_a, item_a["selected"])
        product_b = _product(item_b, item_b["selected"])
        if item_a["selected"] is None or item_b["selected"] is None:
            comparisons.append(
                {
                    "product_a": product_a,
                    "product_b": product_b,
                    "status": "unavailable",
                    "reason": "必须先核验两件商品的页面选中规格及选中后的价格状态。",
                    "bases": [],
                }
            )
            continue

        common_keys = sorted(
            set(item_a["prices"]) & set(item_b["prices"]),
            key=lambda key: (PRICE_KIND_ORDER[key[0]], key[1]),
        )
        bases = []
        ambiguous = False
        currency_mismatch = False
        for key in common_keys:
            prices_a = item_a["prices"][key]
            prices_b = item_b["prices"][key]
            if len(prices_a) != 1 or len(prices_b) != 1:
                ambiguous = True
                continue
            price_a, price_b = prices_a[0], prices_b[0]
            if price_a["currency"] != price_b["currency"]:
                currency_mismatch = True
                continue
            with localcontext() as context:
                context.prec = 60
                bases.append(
                    _basis_calculation(
                        item_a,
                        item_b,
                        price_a,
                        price_b,
                        item_a["measurements"],
                        item_b["measurements"],
                    )
                )

        if bases:
            comparisons.append(
                {
                    "product_a": product_a,
                    "product_b": product_b,
                    "status": "calculated",
                    "reason": None,
                    "bases": bases,
                }
            )
        else:
            reason = (
                "同口径价格存在多条，无法唯一确定计算对象。"
                if ambiguous
                else "找到的同类价格币种不一致，不能直接相减。"
                if currency_mismatch
                else "未采集到两边同币种、同价格类型及同优惠条件的可核对价格。"
            )
            comparisons.append(
                {
                    "product_a": product_a,
                    "product_b": product_b,
                    "status": "unavailable",
                    "reason": reason,
                    "bases": [],
                }
            )
    return comparisons
