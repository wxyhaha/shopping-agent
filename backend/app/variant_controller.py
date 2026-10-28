"""Execute only catalogued SKU choices, then observe selection and price in one page state."""

import json
import re
import time

from playwright.sync_api import Error as PlaywrightError

from app.capture_urls import product_url_identity


def browser_call(page, script, method, args=None):
    # Method names are constants in this module, never supplied by the model.
    return page.evaluate(
        f"arg => {{\n{script}\nreturn ShoppingAgentCollector.{method}(arg);\n}}", args
    )


def _same_product(page, expected):
    current = product_url_identity(page.url)
    return current and (current.platform, current.product_id) == (
        expected.platform,
        expected.product_id,
    )


def select_and_capture(page, script, request, identity, remaining_ms, check_cancel):
    pending = set()

    def started(req):
        if req.resource_type in {"xhr", "fetch"} and re.search(r"sku|price|detail", req.url, re.I):
            pending.add(req)

    def finished(req):
        pending.discard(req)

    page.on("request", started)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    try:
        return _select_and_capture(
            page, script, request, identity, remaining_ms, check_cancel, pending
        )
    finally:
        page.remove_listener("request", started)
        page.remove_listener("requestfinished", finished)
        page.remove_listener("requestfailed", finished)


def _select_and_capture(page, script, request, identity, remaining_ms, check_cancel, pending):
    choices = request["choices"]
    actions = [*request.get("baseline", []), *choices]
    requested = [{"group_id": c["group_id"], "option_id": c["option_id"]} for c in choices]
    status, note = "selected", "已从页面选中状态核对本次要求的规格。价格仅按页面原文解释。"

    def find(catalog, choice):
        group = next((g for g in catalog["groups"] if g["id"] == choice["group_id"]), None)
        option = (
            next((o for o in group["options"] if o["id"] == choice["option_id"]), None)
            if group
            else None
        )
        if (
            not group
            or not option
            or group["label"] != choice["group_label"]
            or option["label"] != choice["option_label"]
        ):
            return None
        return option

    try:
        catalog = browser_call(page, script, "inspectVariants")
        if catalog["status"] != "ready" or any(not find(catalog, c) for c in actions):
            status, note = (
                "stale_catalog",
                "规格目录与本次选择不匹配，未执行点击；请重新读取可选规格。",
            )
        elif any(not find(catalog, c)["selectable"] for c in actions):
            status, note = "unavailable", "所选规格当前禁用或不支持原地选择，未执行点击。"
        else:
            for choice in actions:
                check_cancel()
                catalog = browser_call(page, script, "inspectVariants")
                option = find(catalog, choice)
                if not option or option["disabled"] or not option["selectable"]:
                    status, note = "unavailable", "其他规格变化后，该选项不可用，已停止后续点击。"
                    break
                if not option["selected"]:
                    ids = [choice["group_id"], choice["option_id"]]
                    # Revalidate target eligibility immediately before Playwright's native click.
                    valid = page.evaluate(
                        f"arg => {{\n{script}\n"
                        "return ShoppingAgentCollector.validateVariantTarget(...arg);\n}",
                        ids,
                    )
                    locator = page.locator(f'[data-shopping-agent-variant="{ids[0]}:{ids[1]}"]')
                    if not valid or locator.count() != 1:
                        status, note = "stale_catalog", "规格目标已变化，未继续点击。"
                        break
                    locator.click(timeout=remaining_ms(3500))
                if not _same_product(page, identity):
                    status, note = (
                        "page_changed",
                        "规格操作导致商品页面变化，未将新页面当成原商品结果。",
                    )
                    break
                # Wait for selected state; clicking itself is not evidence of success.
                for _ in range(12):
                    current = browser_call(page, script, "inspectVariants")
                    option = find(current, choice)
                    if option and option["selected"]:
                        break
                    page.wait_for_timeout(remaining_ms(150))
                else:
                    status, note = "not_selected", "点击后没有观察到目标规格处于选中状态。"
                    break
    except PlaywrightError:
        status, note = "not_selected", "规格操作未能完成可见性、可点击性或选中状态核对。"

    if not _same_product(page, identity):
        # Do not collect a navigated page and misattribute it to the original product.
        return None
    stable = False
    raw = browser_call(page, script, "collect")
    if status == "selected":
        started, last_change, previous = time.monotonic(), time.monotonic(), None
        for _ in range(40):
            check_cancel()
            raw = browser_call(page, script, "collect")
            signature = json.dumps(
                {"catalog": raw.get("variant_catalog"), "prices": raw["prices"]}, sort_keys=True
            )
            # Evidence IDs are deterministic; captured_at is deliberately excluded.
            if signature != previous:
                previous, last_change = signature, time.monotonic()
            busy = (
                page.locator(
                    '[aria-busy="true"], [class*="skuLoading"], [class*="priceLoading"]'
                ).count()
                > 0
            )
            if (
                not pending
                and not busy
                and time.monotonic() - started >= 2
                and time.monotonic() - last_change >= 0.8
            ):
                stable = True
                break
            page.wait_for_timeout(remaining_ms(200))
        final_catalog = raw.get("variant_catalog") or {"groups": []}
        if any(
            not (option := find(final_catalog, c)) or not option["selected"] or option["disabled"]
            for c in choices
        ):
            status, note = "not_selected", "最终选中状态与请求不一致，未确认本次选择成功。"
        elif not stable:
            status, note = "unstable", "页面规格或价格仍在变化，尚不能核对价格对应关系。"
    selected_refs = raw["selected_variant"].get("evidence_ids", [])
    price_refs = [ref for price in raw["prices"] for ref in price["evidence_ids"]]
    evidence_id = "e-variant-verification"
    raw["evidence"].append(
        {
            "evidence_id": evidence_id,
            "field_path": "variant_verification",
            "source": "visible_dom",
            "excerpt": note,
            "locator": "post-action selected state and price observation",
        }
    )
    raw["variant_verification"] = {
        "status": status,
        "requested": requested,
        "price_binding": "same_page_state"
        if status == "selected"
        and stable
        and price_refs
        and raw["selected_variant"]["status"] == "observed"
        else "unknown",
        "note": note,
        "evidence_ids": [evidence_id, *selected_refs, *price_refs][:20],
    }
    raw["status"] = "partial" if status != "selected" else raw["status"]
    return raw
