"""Detail-image reading: specifications and sales language must stay apart."""

import pytest

from app.config import Settings
from app.detail_vision import MAX_ITEMS, parse_detail_items, read_detail_image


def test_structured_reply_is_split_into_specs_and_claims():
    reply = (
        '{"items":[{"type":"spec","text":"型号：NW102"},'
        '{"type":"claim","text":"隐形走线 品质之选"},'
        '{"type":"spec","text":"长度：15m"}]}'
    )
    specs, claims = parse_detail_items(reply)
    assert specs == ["型号：NW102", "长度：15m"]
    assert claims == ["隐形走线 品质之选"]


def test_fenced_json_is_accepted():
    reply = '```json\n{"items":[{"type":"spec","text":"材质：纯铜"}]}\n```'
    specs, claims = parse_detail_items(reply)
    assert specs == ["材质：纯铜"]
    assert claims == []


def test_unknown_type_is_treated_as_a_claim_not_a_spec():
    """An unlabelled statement could be marketing, so it must never become a specification."""
    reply = '{"items":[{"type":"other","text":"国际权威认证"}]}'
    specs, claims = parse_detail_items(reply)
    assert specs == []
    assert claims == ["国际权威认证"]


def test_plain_text_reply_becomes_claims_only():
    specs, claims = parse_detail_items("更好更快\n权威认证")
    assert specs == []
    assert claims == ["更好更快", "权威认证"]


@pytest.mark.parametrize("reply", ["", "NONE", '{"items":[]}'])
def test_empty_replies_yield_nothing(reply):
    assert parse_detail_items(reply) == ([], [])


def test_items_are_bounded_and_trimmed():
    items = ",".join(f'{{"type":"spec","text":"参数{index}"}}' for index in range(MAX_ITEMS + 10))
    specs, _ = parse_detail_items(f'{{"items":[{items}]}}')
    assert len(specs) == MAX_ITEMS


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


def test_read_detail_image_returns_both_buckets(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"items":[{"type":"spec","text":"型号：NW102"},'
                    '{"type":"claim","text":"下载约1250MB/S"}]}'
                }
            }
        ]
    }
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.detail_vision.httpx.Client", lambda **_: client)
    specs, claims, note = read_detail_image(b"jpeg", _settings())
    assert specs == ["型号：NW102"]
    assert claims == ["下载约1250MB/S"]
    assert "宣传" in note


def test_read_detail_image_rejects_oversized_and_unconfigured():
    specs, claims, note = read_detail_image(b"x" * (5 * 1024 * 1024), _settings())
    assert specs == [] and claims == [] and "过大" in note
    specs, claims, note = read_detail_image(b"jpeg", _settings(llm_base_url=None))
    assert specs == [] and claims == [] and "未配置" in note
