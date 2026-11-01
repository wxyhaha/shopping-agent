"""Rendered-price reading: only a bare price may be trusted, never a guess."""

from decimal import Decimal

import pytest

from app.config import Settings
from app.price_vision import looks_obfuscated, parse_amount, read_rendered_price


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("¥4299.00", Decimal("4299.00")),
        ("￥52.37", Decimal("52.37")),
        ("4299", Decimal("4299")),
        ("  1,299.5  ", Decimal("1299.5")),
        ("¥ 88 元", Decimal("88")),
    ],
)
def test_parse_amount_accepts_a_single_price(reply, expected):
    assert parse_amount(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "NONE",
        "图中没有价格",
        "价格大约在 4000 到 4500 之间",
        "¥4299.00 或 ¥4399.00",
        "0",
        "-5",
        "¥99999999",
        "abc",
    ],
)
def test_parse_amount_rejects_anything_ambiguous(reply):
    """A vague or multi-value answer must never become a number."""
    assert parse_amount(reply) is None


def test_obfuscated_detection_uses_private_use_characters():
    # What JD's font actually puts in the DOM.
    assert looks_obfuscated("¥\uf261\uf4d5\uec82\uec82.\ue19b\ue19b")
    assert looks_obfuscated("¥4??9 登录查看价格") is False
    assert looks_obfuscated("优惠前 ￥52.37") is False
    assert looks_obfuscated(None) is False
    assert looks_obfuscated("") is False


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _settings(**overrides):
    base = {
        "llm_base_url": "http://127.0.0.1:1/v1",
        "llm_model": "vision-model",
        "llm_api_key": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_read_rendered_price_returns_the_amount(monkeypatch):
    payload = {"choices": [{"message": {"content": "¥4299.00"}}]}
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.price_vision.httpx.Client", lambda **_: client)
    amount, note = read_rendered_price(b"png", _settings())
    assert amount == Decimal("4299.00")
    assert "自定义字体" in note


def test_read_rendered_price_refuses_a_vague_answer(monkeypatch):
    payload = {"choices": [{"message": {"content": "看起来四千多"}}]}
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.price_vision.httpx.Client", lambda **_: client)
    amount, note = read_rendered_price(b"png", _settings())
    assert amount is None
    assert "未采用" in note


def test_read_rendered_price_reports_http_failure(monkeypatch):
    monkeypatch.setattr("app.price_vision.httpx.Client", lambda **_: _Client(_Response(503, {})))
    amount, note = read_rendered_price(b"png", _settings())
    assert amount is None
    assert "503" in note


def test_read_rendered_price_without_gateway_configuration():
    amount, note = read_rendered_price(b"png", _settings(llm_base_url=None))
    assert amount is None
    assert "未配置" in note
