"""Exercise the extension's actual browser code against offline dynamic pages."""

from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from app.schemas import CaptureSnapshotSubmit

URL = "https://detail.tmall.com/item.htm?id=100001"
BUNDLE = Path(__file__).parents[1] / "app/browser_assets/collector.js"
BASE = """<!doctype html><meta charset="utf-8"><title>合成保温杯</title>
<h1>合成保温杯500ml</h1><div class="slogo-shopname">合成店铺</div>
<div id="J_PromoPrice">优惠前 ￥79.00</div>
<div class="skuOptionsArea"><div data-sku-group="容量">
<button type="button" data-sku-option aria-pressed="true">500ml</button>
<button type="button" data-sku-option>350ml</button></div></div>
"""
IMAGE = '<img width="800" height="800" src="https://img.alicdn.com/fixture.jpg">'


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        yield page
        browser.close()


def load(page, html):
    requests = []

    def route(request):
        requests.append(request.request.url)
        if request.request.url == URL:
            request.fulfill(body=html, content_type="text/html; charset=utf-8")
        elif request.request.url == "https://img.alicdn.com/fixture.jpg":
            request.fulfill(
                body='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"/>',
                content_type="image/svg+xml",
            )
        else:
            request.abort()

    page.route("**/*", route)
    page.goto(URL)
    page.add_script_tag(path=str(BUNDLE))
    return requests


def capture(page, **options):
    result = page.evaluate(
        "options => ShoppingAgentCollector.captureOpenedProduct(location.href, options)",
        {"scroll": True, **options},
    )
    return CaptureSnapshotSubmit.model_validate(result)


def preparation(snapshot):
    return next(c for c in snapshot.coverage if c.field_path == "page.detail_preparation")


def test_lazy_detail_and_parameter_expansion_restore_scroll_and_validate_schema(page):
    html = (
        BASE
        + """<button type="button" id="params">规格参数</button>
    <div id="J_AttrUL"></div><div style="height:1100px"></div>
    <div id="description"></div><script>
    document.querySelector('#params').onclick=()=>{
      document.querySelector('#J_AttrUL').innerHTML='<li>品牌：合成品牌</li><li>容量：500ml</li>';
    };
    addEventListener('scroll',()=>{
      if(scrollY>500 && !document.querySelector('#description img'))
        document.querySelector('#description').innerHTML=IMAGE;
    });</script>""".replace("IMAGE", repr(IMAGE))
    )
    requests = load(page, html)
    snapshot = capture(page)
    assert snapshot.status == "partial"
    assert any(
        f.field_path == "product.brand" and f.raw_value == "合成品牌" for f in snapshot.fields
    )
    assert snapshot.selected_variant.status == "observed"
    assert snapshot.prices[0].amount == Decimal("79.00")
    assert any(i.loaded for i in snapshot.images)
    assert "短暂观察期内稳定" in preparation(snapshot).note
    assert page.evaluate("scrollY") == 0
    assert all(url in {URL, "https://img.alicdn.com/fixture.jpg"} for url in requests)


def test_missing_container_is_reported_as_wait_limit(page):
    load(page, BASE)
    snapshot = capture(page, budgetMs=100)
    assert "等待期限" in preparation(snapshot).note
    assert "未识别详情区域" in preparation(snapshot).note


def test_late_container_is_observed_after_initially_missing(page):
    load(page, BASE)
    page.evaluate("""() => setTimeout(()=>{
      const node=document.createElement('div');node.id='description';
      node.innerHTML='<p>延迟详情</p>';document.body.append(node);
    },200)""")
    snapshot = capture(page)
    assert "已识别详情区域" in preparation(snapshot).note


def test_passive_bridge_does_not_click_or_scroll(page):
    load(
        page,
        BASE
        + """<button type="button" onclick="window.clicked=true">规格参数</button>
    <div style="height:2000px"></div>""",
    )
    snapshot = capture(page, scroll=False)
    assert "没有滚动" in preparation(snapshot).note
    assert page.evaluate("scrollY") == 0
    assert not page.evaluate("Boolean(window.clicked)")


@pytest.mark.parametrize("change", ["cancel", "selection", "navigation", "wheel"])
def test_interruption_never_returns_a_snapshot(page, change):
    load(page, BASE + '<div style="height:4000px"></div>')
    with pytest.raises(Error, match="取消|规格|变化|页面操作"):
        page.evaluate(
            """async change => {
            const abort=new AbortController();
            setTimeout(()=>{
              if(change==='cancel')abort.abort();
              if(change==='selection')document.querySelector('[aria-pressed]').setAttribute('aria-pressed','false');
              if(change==='navigation')history.pushState({},'','/item.htm?id=100002');
              if(change==='wheel')dispatchEvent(new WheelEvent('wheel'));
            },150);
            return ShoppingAgentCollector.captureOpenedProduct(
              location.href,{scroll:true,signal:abort.signal});
            }""",
            change,
        )


def test_price_updates_during_final_observation_are_rejected(page):
    load(page, BASE)
    page.evaluate("""() => setTimeout(()=>{
      document.querySelector('#J_PromoPrice').textContent='优惠前 ￥99.00';
    },200)""")
    with pytest.raises(Error, match="价格仍在更新"):
        capture(page, scroll=False)


def test_image_reference_cap_is_visible_in_coverage(page):
    load(page, BASE + '<div id="description">' + IMAGE * 45 + "</div>")
    page.wait_for_function("[...document.images].every(i=>i.complete&&i.naturalWidth>0)")
    snapshot = capture(page, scroll=False)
    assert len(snapshot.images) == 40
    coverage = next(c for c in snapshot.coverage if c.field_path == "images.detail")
    assert coverage.status == "ambiguous"
    assert "上限" in coverage.note and "45" in coverage.note


def test_parameter_expansion_skips_links_submit_controls_and_plain_headings(page):
    load(
        page,
        BASE
        + """<a href="/other"><span>规格参数</span></a>
    <form onsubmit="window.submitted=true;return false">
      <button><span>商品参数</span></button></form>
    <h3 onclick="window.headingClicked=true">产品参数</h3>
    <button type="button" disabled>参数规格</button>""",
    )
    capture(page, budgetMs=0)
    assert page.url == URL
    assert not page.evaluate("Boolean(window.submitted||window.headingClicked)")


def test_current_tmall_parameter_cards_respect_value_order_and_ai_annotation(page):
    load(
        page,
        BASE
        + """<div class="paramsInfoArea"><div class="paramsWrap--fixture">
    <div class="emphasisParamsInfoItem--fixture">
      <div class="emphasisParamsInfoItemTitle--fixture">合成品牌</div>
      <div class="emphasisParamsInfoItemSubTitle--fixture">品牌</div></div>
    <div class="emphasisParamsInfoItem--fixture">
      <div class="emphasisParamsInfoItemTitle--fixture">316L不锈钢</div>
      <div class="emphasisParamsInfoItemSubTitle--fixture">材质</div></div>
    <div class="emphasisParamsInfoItem--fixture">
      <div class="emphasisParamsInfoItemTitle--fixture">蓝色</div>
      <div class="emphasisParamsInfoItemSubTitle--fixture aiAnnotatedText--fixture">颜色</div>
    </div><div class="generalParamsInfoItem--fixture">
      <div class="generalParamsInfoItemTitle--fixture">型号</div>
      <div class="generalParamsInfoItemSubTitle--fixture">TEST500</div></div></div></div>""",
    )
    snapshot = capture(page, scroll=False)
    brand = next(f for f in snapshot.fields if f.field_path == "product.brand")
    assert brand.raw_value == "合成品牌" and brand.status == "observed"
    specs = {f.label: f for f in snapshot.fields if f.field_path.startswith("specs.")}
    assert specs["材质"].normalized_value == "316L不锈钢"
    assert specs["型号"].normalized_value == "TEST500"
    assert specs["颜色"].normalized_value == "蓝色"
    assert specs["颜色"].status == "ambiguous"
    assert any("平台 AI" in (c.note or "") for c in snapshot.coverage)


def test_many_parameters_keep_individual_evidence_without_exceeding_coverage_schema(page):
    rows = "".join(
        f'<div class="generalParamsInfoItem--fixture">'
        f'<div class="generalParamsInfoItemTitle--fixture">参数{i}</div>'
        f'<div class="generalParamsInfoItemSubTitle--fixture">值{i}</div></div>'
        for i in range(25)
    )
    load(page, BASE + f'<div class="paramsInfoArea">{rows}</div>')
    snapshot = capture(page, scroll=False)
    specs = [f for f in snapshot.fields if f.field_path.startswith("specs.")]
    assert len(specs) == 25
    assert all(f.evidence_ids for f in specs)
    coverage = next(c for c in snapshot.coverage if c.field_path == "specifications")
    assert len(coverage.evidence_ids) == 20
