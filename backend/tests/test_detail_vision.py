"""Detail-image classification: attribute statements vs evaluations vs transaction terms.

The rule under test is "what is the sentence doing", not "can it be independently verified".
A material claim such as 升级纯铜芯 is still an attribute, while 品质之选 is an evaluation.
"""

import pytest

from app.config import Settings
from app.detail_vision import (
    MAX_ITEMS,
    _is_ui_noise,
    parse_detail_items,
    read_detail_image,
)


def test_three_way_split():
    reply = (
        '{"items":['
        '{"type":"parameter","text":"长度15m"},'
        '{"type":"claim","text":"品质之选"},'
        '{"type":"policy","text":"7天无理由退货"}]}'
    )
    parameters, claims, policies = parse_detail_items(reply)
    assert parameters == ["长度15m"]
    assert claims == ["品质之选"]
    assert policies == ["7天无理由退货"]


@pytest.mark.parametrize(
    "text",
    ["升级纯铜芯", "三重屏蔽", "扁平线身", "六类千兆", "下载约1250MB/S"],
)
def test_attribute_statements_are_parameters_even_without_proof(text):
    """These describe what the product is; lacking a test report does not make them ads."""
    reply = '{"items":[{"type":"parameter","text":"' + text + '"}]}'
    parameters, claims, _ = parse_detail_items(reply)
    assert parameters == [text]
    assert claims == []


@pytest.mark.parametrize(
    "text",
    ["五重国际权威认证", "品质之选 精工打造", "上市企业 全球化品牌", "更快更稳"],
)
def test_evaluations_and_certifications_are_claims(text):
    reply = '{"items":[{"type":"claim","text":"' + text + '"}]}'
    parameters, claims, _ = parse_detail_items(reply)
    assert parameters == []
    assert claims == [text]


@pytest.mark.parametrize(
    "text",
    ["7天无理由退货", "满200减25", "限购1件", "预计3天内发货"],
)
def test_transaction_terms_are_policies(text):
    reply = '{"items":[{"type":"policy","text":"' + text + '"}]}'
    _, _, policies = parse_detail_items(reply)
    assert policies == [text]


def test_combined_discount_stays_one_policy():
    """The prompt asks for one complete statement, so a discount is not split into pieces."""
    reply = (
        '{"items":[{"type":"policy","text":"¥25 满 200 可用 88VIP专享"},'
        '{"type":"policy","text":"¥60 满 480 可用 88VIP专享"}]}'
    )
    _, _, policies = parse_detail_items(reply)
    assert policies == ["¥25 满 200 可用 88VIP专享", "¥60 满 480 可用 88VIP专享"]


def test_unknown_label_defaults_to_claim_not_parameter():
    reply = '{"items":[{"type":"spec","text":"国际权威认证"}]}'
    parameters, claims, _ = parse_detail_items(reply)
    assert parameters == []
    assert claims == ["国际权威认证"]


def test_plain_text_reply_becomes_claims_only():
    parameters, claims, policies = parse_detail_items("更好更快\n权威认证")
    assert parameters == [] and policies == []
    assert claims == ["更好更快", "权威认证"]


@pytest.mark.parametrize("reply", ["", "NONE", '{"items":[]}'])
def test_empty_replies_yield_nothing(reply):
    assert parse_detail_items(reply) == ([], [], [])


@pytest.mark.parametrize(
    "line",
    ["点击购买 >", "立即购买", "了解更多", ">", "»", "加入购物车", "查看详情"],
)
def test_call_to_action_chrome_is_noise(line):
    assert _is_ui_noise(line) is True


@pytest.mark.parametrize(
    "line",
    ["长度15m", "六类千兆", "7天无理由退货", "满200减25"],
)
def test_product_content_is_not_noise(line):
    assert _is_ui_noise(line) is False


def test_noise_is_dropped_from_all_buckets():
    reply = (
        '{"items":[{"type":"parameter","text":"长度15m"},{"type":"policy","text":"点击购买 >"}]}'
    )
    parameters, _, policies = parse_detail_items(reply)
    assert parameters == ["长度15m"]
    assert policies == []


def test_items_are_bounded():
    items = ",".join(
        f'{{"type":"parameter","text":"参数{index}"}}' for index in range(MAX_ITEMS + 10)
    )
    parameters, _, _ = parse_detail_items(f'{{"items":[{items}]}}')
    assert len(parameters) == MAX_ITEMS


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return self.response


def _settings(**overrides):
    base = {
        "llm_base_url": "http://127.0.0.1:1/v1",
        "llm_model": "vision-model",
        "llm_api_key": None,
        "detail_vision_enabled": True,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_read_detail_image_returns_all_three_buckets(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"items":[{"type":"parameter","text":"长度15m"},'
                    '{"type":"claim","text":"品质之选"},'
                    '{"type":"policy","text":"7天无理由"}]}'
                }
            }
        ]
    }
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.detail_vision.httpx.Client", lambda **_: client)
    parameters, claims, policies, note = read_detail_image(b"jpeg", _settings())
    assert parameters == ["长度15m"]
    assert claims == ["品质之选"]
    assert policies == ["7天无理由"]
    assert "商家标注" in note


def test_read_detail_image_rejects_oversized_and_unconfigured():
    parameters, claims, policies, note = read_detail_image(b"x" * (5 * 1024 * 1024), _settings())
    assert not parameters and not claims and not policies
    assert "过大" in note
    parameters, claims, policies, note = read_detail_image(b"jpeg", _settings(llm_base_url=None))
    assert not parameters and not claims and not policies
    assert "未配置" in note
