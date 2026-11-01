"""The rendered-price step must fill only obfuscated prices and keep evidence intact."""

from app.background_collector import BackgroundCollector
from app.config import Settings


def _collector(*, enabled=True, llm_ok=True):
    settings = Settings(
        _env_file=None,
        llm_base_url="http://127.0.0.1:1/v1" if llm_ok else None,
        llm_model="vision-model" if llm_ok else None,
        price_vision_enabled=enabled,
    )
    return BackgroundCollector(settings)


def _snapshot(raw_text, *, status="ambiguous"):
    return {
        "prices": [
            {
                "observation_id": "o001",
                "kind": "unknown",
                "raw_text": raw_text,
                "amount": None,
                "currency": "CNY",
                "status": status,
                "conditions": [],
                "evidence_ids": ["e001"],
            }
        ],
        "coverage": [{"field_path": "price", "status": "ambiguous", "evidence_ids": ["e001"]}],
        "evidence": [
            {
                "evidence_id": "e001",
                "field_path": "prices[0]",
                "source": "visible_dom",
                "excerpt": raw_text,
            }
        ],
    }


OBFUSCATED = "¥\uf261\uf4d5\uec82\uec82.\ue19b\ue19b"


def test_obfuscated_price_is_filled_from_the_rendered_pixels(monkeypatch):
    import base64
    from decimal import Decimal

    monkeypatch.setattr(
        "app.background_collector.read_rendered_price",
        lambda image, settings: (
            Decimal("4299.00"),
            "价格数字以自定义字体渲染，已从页面实际显示读取。",
        ),
    )
    collector = _collector()
    # 1x1 PNG is enough: the reader is stubbed, so only the plumbing is under test.
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    result = collector._apply_vision_price(_snapshot(OBFUSCATED), png)
    price = result["prices"][0]
    assert str(price["amount"]) == "4299.00"
    assert price["status"] == "observed"
    render = [entry for entry in result["coverage"] if entry["field_path"] == "price.render"]
    assert render and render[0]["status"] == "observed"
    # The rendered figure must stay tied to the DOM evidence it was read from.
    assert render[0]["evidence_ids"], "rendered price coverage must cite evidence"


def test_plain_and_masked_prices_are_left_untouched(monkeypatch):
    from decimal import Decimal

    called = []
    monkeypatch.setattr(
        "app.background_collector.read_rendered_price",
        lambda image, settings: (called.append(1), (Decimal("1"), "x"))[1],
    )
    collector = _collector()
    for raw_text in ["优惠前 ￥52.37", "¥4??9 登录查看价格"]:
        result = collector._apply_vision_price(_snapshot(raw_text), "cG5n")
        assert result["prices"][0]["amount"] is None
        assert result["prices"][0]["status"] == "ambiguous"


def test_unreadable_rendered_price_stays_ambiguous(monkeypatch):
    monkeypatch.setattr(
        "app.background_collector.read_rendered_price",
        lambda image, settings: (None, "视觉模型未给出可核对的单一价格，未采用该结果。"),
    )
    collector = _collector()
    result = collector._apply_vision_price(_snapshot(OBFUSCATED), "cG5n")
    assert result["prices"][0]["amount"] is None
    assert result["prices"][0]["status"] == "ambiguous"
    render = [entry for entry in result["coverage"] if entry["field_path"] == "price.render"]
    assert render and render[0]["status"] == "ambiguous"


def test_vision_is_off_by_default():
    """The normal path must not spend a model request on pages with plain text."""
    assert Settings(_env_file=None).price_vision_enabled is False
