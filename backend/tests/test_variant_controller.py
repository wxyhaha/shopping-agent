import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_schemas import SelectProductVariantArgs
from app.background_collector import BackgroundCollector
from app.config import Settings
from app.models import AgentRun, CaptureSnapshot
from tests.test_research_agent import application, observations, seed, start, task_items, tool

PAGE = """<!doctype html><meta charset="utf-8"><h1>合成可选规格家用网线</h1>
<div class="sku-name">合成家用网线</div><div id="sellerName">合成测试店</div>
<div id="choose-attrs">
 <div data-sku-group="线型">
 <button type="button" data-sku-option aria-pressed="true">圆线 超六类</button>
 <button type="button" data-sku-option>扁平 超六类</button>
 <button type="button" data-sku-option disabled>扁平 七类缺货</button></div>
 <div data-sku-group="长度"><button type="button" data-sku-option aria-pressed="true">1米</button>
 <button type="button" data-sku-option>10米</button>
 <button type="button" data-sku-option>加入购物车</button></div>
</div><div id="summary-price">优惠前 ￥9.00</div>
<script>document.addEventListener('click', e => {
 const button=e.target.closest('[data-sku-option]'); if(!button) return;
 const row=button.closest('[data-sku-group]');
 row.querySelectorAll('[data-sku-option]').forEach(n=>n.setAttribute('aria-pressed','false'));
 button.setAttribute('aria-pressed','true');
 const price=document.querySelector('#summary-price'); price.setAttribute('aria-busy','true');
 setTimeout(()=>{price.textContent='优惠前 ￥68.00';price.removeAttribute('aria-busy')},1100);
});</script>"""


def service(tmp_path, html=PAGE):
    return BackgroundCollector(
        Settings(_env_file=None, data_dir=tmp_path, browser_max_scrolls=0),
        route_fixture=lambda route: route.fulfill(
            content_type="text/html; charset=utf-8", body=html
        ),
        resolver=lambda _: ["93.184.216.34"],
    )


def choice(catalog, group_label, option_label):
    group = next(g for g in catalog.groups if g.label == group_label)
    option = next(o for o in group.options if o.label == option_label)
    return {
        "group_id": group.id,
        "option_id": option.id,
        "group_label": group.label,
        "option_label": option.label,
    }


def test_catalog_native_selection_waits_for_price_and_produces_evidence(tmp_path):
    browser = service(tmp_path)
    url = "https://item.jd.com/100001.html"
    before = browser.collect(url).snapshot
    assert len(before.variant_catalog.groups) == 2
    assert before.selected_variant.status == "observed"
    choices = [
        choice(before.variant_catalog, "线型", "扁平 超六类"),
        choice(before.variant_catalog, "长度", "10米"),
    ]
    started = time.monotonic()
    result = browser.collect(url, variant_request={"choices": choices})
    assert result.error_code is None
    after = result.snapshot
    assert after.variant_verification.status == "selected"
    assert after.variant_verification.price_binding == "same_page_state"
    assert {a.raw_value for a in after.selected_variant.attributes} == {"扁平 超六类", "10米"}
    assert after.prices[0].raw_text == "优惠前 ￥68.00"
    assert time.monotonic() - started >= 2
    assert after.variant_verification.evidence_ids


@pytest.mark.parametrize("kind", ["disabled", "unsafe", "stale"])
def test_invalid_choices_never_claim_selected_or_change_price(tmp_path, kind):
    browser = service(tmp_path)
    url = "https://item.jd.com/100001.html"
    catalog = browser.collect(url).snapshot.variant_catalog
    if kind == "disabled":
        selected = choice(catalog, "线型", "扁平 七类缺货")
    elif kind == "unsafe":
        selected = choice(catalog, "长度", "加入购物车")
    else:
        selected = choice(catalog, "长度", "10米")
        selected["option_label"] = "已变更的规格"
    after = browser.collect(url, variant_request={"choices": [selected]}).snapshot
    assert after.variant_verification.status in {"unavailable", "stale_catalog"}
    assert after.variant_verification.price_binding == "unknown"
    assert after.prices[0].raw_text == "优惠前 ￥9.00"


def test_agent_chooses_catalog_ids_and_report_cites_selected_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(llm_base_url="http://gateway.invalid/v1", llm_model="test-model"),
    )

    def model(messages, _):
        seen = observations(messages)
        item_id = task_items(messages)[0]["item_id"]
        if not seen:
            return tool("read_product_evidence", {"item_id": item_id})
        if len(seen) == 1:
            return tool("inspect_product_variants", {"item_id": item_id, "reason": "核对可选规格"})
        if len(seen) == 2:
            choices = [
                {"group_id": group["id"], "option_id": option["id"]}
                for group in seen[-1]["variant_catalog"]["groups"]
                for option in group["options"]
                if option["label"] in {"10米", "扁平 超六类"}
            ]
            assert len(choices) == 2
            return tool(
                "select_product_variant",
                {
                    "item_id": item_id,
                    "catalog_snapshot_id": seen[-1]["snapshot_id"],
                    "choices": choices,
                    "reason": "选择用户目标规格",
                },
            )
        assert seen[-1]["variant_verification"]["status"] == "selected"
        ref = next(
            e["ref"]
            for e in seen[-1]["evidence"]
            if "10米" in (e.get("excerpt") or "") and "已选中" in e["excerpt"]
        )
        return tool(
            "submit_report",
            {
                "summary": "合成选中规格已核对，优惠前价不是最终支付价。",
                "findings": [{"text": "当前选中10米规格。", "evidence_refs": [ref]}],
                "unknowns": [],
            },
        )

    app = application(tmp_path / "variant-agent.sqlite3", model)
    browser = service(tmp_path)
    app.state.research_runtime.collector = browser
    with TestClient(app) as client:
        task_id = seed(client)
        start(client, task_id)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            run = client.get(f"/api/v1/tasks/{task_id}/research").json()[0]
            if run["status"] in {
                "completed",
                "failed",
            } and not app.state.research_runtime.is_active(run["id"]):
                break
            time.sleep(0.05)
        assert run["status"] == "completed", run["error"]
        event = next(
            e
            for e in run["events"]
            if e["kind"] == "tool" and e["data"]["tool"] == "select_product_variant"
        )
        assert run["report"]["findings"][0]["evidence_refs"][0].startswith(
            event["data"]["output"]["snapshot_id"]
        )
        call_id = next(
            e["data"]["output"]["tool_calls"][0]["id"]
            for e in run["events"]
            if e["kind"] == "model"
            and e["data"]["output"]["tool_calls"][0]["function"]["name"] == "select_product_variant"
        )
        with app.state.session_factory() as db:
            db.get(AgentRun, run["id"]).status = "running"
            assert len(db.scalars(select(CaptureSnapshot)).all()) == 3
            db.commit()

        browser.collect = lambda *args, **kwargs: pytest.fail(
            "Saved selection must not be clicked again"
        )
        args = SelectProductVariantArgs.model_validate(event["data"]["input"])
        replay, _ = app.state.research_runtime._collect_page(
            run["id"], task_id, call_id, args, time.monotonic() + 10, "select_product_variant"
        )
        assert replay["snapshot_id"] == event["data"]["output"]["snapshot_id"]
        invalid = args.model_copy(update={"catalog_snapshot_id": "outside-this-item"})
        output, _ = app.state.research_runtime._collect_page(
            run["id"],
            task_id,
            "foreign-catalog",
            invalid,
            time.monotonic() + 10,
            "select_product_variant",
        )
        assert "不属于" in output["error"]
        with app.state.session_factory() as db:
            db.get(AgentRun, run["id"]).status = "completed"
            db.commit()


def test_restores_previous_group_for_followup_selection(tmp_path):
    # Flat cable is disabled until length is selected, and each request uses a fresh browser.
    html = PAGE.replace("data-sku-option>扁平 超六类", "data-sku-option disabled>扁平 超六类")
    html += """<script>document.addEventListener('click', e=>{
    if(e.target.textContent==='10米') document.querySelectorAll('[data-sku-option]').forEach(n=>{
    if(n.textContent==='扁平 超六类') n.removeAttribute('disabled')})})</script>"""
    browser = service(tmp_path, html)
    url = "https://item.jd.com/100001.html"
    initial = browser.collect(url).snapshot.variant_catalog
    length = choice(initial, "长度", "10米")
    first = browser.collect(url, variant_request={"choices": [length]}).snapshot
    flat = choice(first.variant_catalog, "线型", "扁平 超六类")
    final = browser.collect(url, variant_request={"choices": [flat], "baseline": [length]}).snapshot
    assert final.variant_verification.status == "selected"
    assert {a.raw_value for a in final.selected_variant.attributes} == {"扁平 超六类", "10米"}


@pytest.mark.parametrize(
    "markup",
    [
        '<div class="skuItem--test"><div class="labelWrapTitle--test">长度</div>'
        '<div class="valueItem--test isSelected--test" data-vid="a">'
        '<span class="valueItemText--x">10米</span></div>'
        '<div class="valueItem--test isDisabled--test" data-vid="b">20米</div></div>',
        '<div id="J_isku"><dl><dt>长度</dt><dd><ul>'
        '<li data-value="a" class="tb-selected"><a href="javascript:;">10米</a></li>'
        '<li data-value="b" class="disabled">20米</li></ul></dd></dl></div>',
        '<div id="choose-attrs"><div class="li"><div class="dt">长度</div><div class="dd">'
        '<div class="item selected"><a href="javascript:;">10米</a></div>'
        '<div class="item disabled">20米</div></div></div></div>',
    ],
)
def test_platform_group_shapes_preserve_selected_disabled_evidence(tmp_path, markup):
    snapshot = (
        service(tmp_path, '<meta charset="utf-8"><h1>合成商品规格结构回归测试</h1>' + markup)
        .collect("https://item.jd.com/100001.html")
        .snapshot
    )
    assert snapshot.variant_catalog.status == "ready"
    group = snapshot.variant_catalog.groups[0]
    assert group.label == "长度"
    assert group.options[0].selected
    assert group.options[1].disabled
    assert snapshot.selected_variant.attributes[0].raw_value == "10米"
    assert all(option.evidence_ids for option in group.options)


def test_click_without_selected_state_is_not_success(tmp_path):
    html = PAGE.split("<script>")[0]
    browser = service(tmp_path, html)
    url = "https://item.jd.com/100001.html"
    catalog = browser.collect(url).snapshot.variant_catalog
    snapshot = browser.collect(
        url, variant_request={"choices": [choice(catalog, "长度", "10米")]}
    ).snapshot
    assert snapshot.variant_verification.status == "not_selected"
    assert snapshot.variant_verification.price_binding == "unknown"
