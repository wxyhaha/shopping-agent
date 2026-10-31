"""Business cases for conservative, evidence-bound price arithmetic."""

from copy import deepcopy

import pytest

from app.agent_schemas import ProductPriceComparison
from app.price_comparison import calculate_price_comparisons


def item(identity, amount="10", measurement=None):
    attributes = []
    if measurement:
        label, value = measurement
        attributes.append(
            {
                "label": label,
                "raw_value": value,
                "status": "observed",
                "evidence_ids": ["measure"],
            }
        )
    return {
        "item_id": identity,
        "title": "合成商品",
        "snapshot_id": f"snapshot-{identity}",
        "snapshot": {
            "evidence": [{"evidence_id": key} for key in ("selection", "price", "measure")],
            "selected_variant": {
                "status": "observed",
                "label": "已核验选项",
                "evidence_ids": ["selection"],
                "attributes": attributes,
            },
            "variant_verification": {
                "status": "selected",
                "price_binding": "same_page_state",
                "evidence_ids": ["selection"],
            },
            "prices": [
                {
                    "kind": "list",
                    "status": "observed",
                    "amount": amount,
                    "currency": "CNY",
                    "raw_text": f"优惠前 {amount}",
                    "evidence_ids": ["price"],
                    "conditions": [],
                }
            ],
        },
    }


def compare(a, b):
    result = calculate_price_comparisons([a, b])[0]
    ProductPriceComparison.model_validate(result)
    return result


@pytest.mark.parametrize(
    "measurement_a,measurement_b,unit,quantity",
    [
        (("长度", "1500厘米"), ("长度", "15米"), "m", "15"),
        (("净含量", "500g"), ("净重", "0.5千克"), "kg", "0.5"),
        (("容量", "500毫升"), ("净含量", "0.5L"), "L", "0.5"),
        (("数量", "10片"), ("片数", "10片"), "片", "10"),
        (("硬盘容量", "1TB"), ("存储容量", "1000GB"), "GB", "1000"),
    ],
)
def test_normalizes_only_explicit_compatible_measurements(
    measurement_a, measurement_b, unit, quantity
):
    result = compare(item("a", "20", measurement_a), item("b", "10", measurement_b))
    basis = result["bases"][0]
    assert basis["difference_amount"] == "10"
    assert basis["difference_percent_of_higher"] == "50"
    calculation = basis["unit_prices"][0]
    assert calculation["unit"] == unit
    assert calculation["quantity_a"] == calculation["quantity_b"] == quantity
    assert "snapshot-a:measure" in calculation["evidence_refs"]


@pytest.mark.parametrize(
    "a,b",
    [
        (None, None),
        (("净重", "1kg"), ("容量", "1L")),
        (("数量", "10片"), ("数量", "10包")),
        (("型号", "15m"), ("型号", "15m")),
        (("净重", "500g×2"), ("净重", "1kg")),
        (("长度", "0m"), ("长度", "15m")),
    ],
)
def test_does_not_invent_unit_prices(a, b):
    assert (
        compare(item("a", measurement=a), item("b", measurement=b))["bases"][0]["unit_prices"] == []
    )


@pytest.mark.parametrize(
    "mutation", ["unverified", "unknown_binding", "currency", "kind", "duplicate", "coupon"]
)
def test_rejects_noncomparable_prices(mutation):
    a, b = item("a"), item("b")
    snapshot = b["snapshot"]
    price = snapshot["prices"][0]
    if mutation == "unverified":
        snapshot["selected_variant"]["status"] = "missing"
    elif mutation == "unknown_binding":
        snapshot["variant_verification"]["price_binding"] = "unknown"
    elif mutation == "currency":
        price["currency"] = "USD"
    elif mutation == "kind":
        price["kind"] = "current"
    elif mutation == "duplicate":
        snapshot["prices"].append(deepcopy(price))
    else:
        a["snapshot"]["prices"][0]["kind"] = price["kind"] = "coupon"
    assert compare(a, b)["status"] == "unavailable"


def test_conditions_must_match_and_have_observed_evidence():
    a, b = item("a"), item("b")
    for product in (a, b):
        product["snapshot"]["prices"][0].update(
            kind="coupon",
            conditions=[
                {
                    "condition": "coupon",
                    "raw_value": "领取同一公开券",
                    "status": "observed",
                    "evidence_ids": ["price"],
                }
            ],
        )
    assert compare(a, b)["status"] == "calculated"
    condition = b["snapshot"]["prices"][0]["conditions"][0]
    condition["raw_value"] = "仅限新用户"
    assert compare(a, b)["status"] == "unavailable"
    condition["raw_value"] = "领取同一公开券"
    condition["evidence_ids"] = []
    assert compare(a, b)["status"] == "unavailable"


def test_zero_price_has_no_percentage_and_ambiguous_measurements_are_skipped():
    a, b = item("a", "0", ("净重", "1kg")), item("b", "0", ("净重", "1kg"))
    a["snapshot"]["selected_variant"]["attributes"].append(
        {
            "label": "净重",
            "raw_value": "2kg",
            "status": "observed",
            "evidence_ids": ["measure"],
        }
    )
    basis = compare(a, b)["bases"][0]
    assert basis["difference_percent_of_higher"] is None
    assert basis["lower_price_item_id"] is None
    assert basis["unit_prices"] == []
    assert basis["unit_price_unavailable_reason"]


def test_rounding_does_not_turn_a_lower_unit_price_into_a_tie():
    basis = compare(item("a", "10", ("数量", "300片")), item("b", "10", ("数量", "301片")))[
        "bases"
    ][0]
    assert basis["unit_prices"][0]["lower_price_item_id"] == "b"


@pytest.mark.parametrize("field", ["selection", "price", "measure"])
def test_references_must_exist_in_the_same_snapshot(field):
    a, b = item("a", measurement=("净重", "1kg")), item("b", measurement=("净重", "1kg"))
    a["snapshot"]["evidence"] = [
        entry for entry in a["snapshot"]["evidence"] if entry["evidence_id"] != field
    ]
    result = compare(a, b)
    if field == "measure":
        assert result["bases"][0]["unit_prices"] == []
    else:
        assert result["status"] == "unavailable"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "1e1000", "1e-1000"])
def test_invalid_or_unbounded_numbers_do_not_break_report(amount):
    assert compare(item("a", amount), item("b"))["status"] == "unavailable"
