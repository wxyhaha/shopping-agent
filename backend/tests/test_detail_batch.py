"""Batch reading of detail images: several panels per request, attributed per image."""

import json

from app.config import Settings
from app.detail_vision import BATCH_SIZE, chunked, read_detail_batch


def _settings(**overrides):
    base = {
        "llm_base_url": "http://127.0.0.1:1/v1",
        "llm_model": "vision-model",
        "llm_api_key": None,
        "detail_vision_enabled": True,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self.calls += 1
        return self.response


def _reply(items):
    return {"choices": [{"message": {"content": json.dumps({"items": items})}}]}


def test_items_are_attributed_to_their_image(monkeypatch):
    payload = _reply(
        [
            {"image": 1, "type": "parameter", "text": "长度15m"},
            {"image": 2, "type": "claim", "text": "品质之选"},
            {"image": 3, "type": "policy", "text": "7天无理由"},
        ]
    )
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.detail_vision.httpx.Client", lambda **_: client)
    grouped, note = read_detail_batch([b"a", b"b", b"c"], _settings())
    assert grouped[0][0] == ["长度15m"]
    assert grouped[1][1] == ["品质之选"]
    assert grouped[2][2] == ["7天无理由"]
    assert "分批" in note


def test_out_of_range_image_number_falls_back_to_first(monkeypatch):
    """An unusable image number must not invent an attribution to a missing panel."""
    payload = _reply([{"image": 99, "type": "parameter", "text": "长度15m"}])
    monkeypatch.setattr(
        "app.detail_vision.httpx.Client", lambda **_: _Client(_Response(200, payload))
    )
    grouped, _ = read_detail_batch([b"a", b"b"], _settings())
    assert grouped[0][0] == ["长度15m"]


def test_unstructured_reply_degrades_to_one_bucket(monkeypatch):
    payload = {"choices": [{"message": {"content": "品质之选\n更快"}}]}
    monkeypatch.setattr(
        "app.detail_vision.httpx.Client", lambda **_: _Client(_Response(200, payload))
    )
    grouped, note = read_detail_batch([b"a"], _settings())
    assert grouped[0][1] == ["品质之选", "更快"]
    assert "归入首张" in note


def test_failed_request_returns_nothing(monkeypatch):
    monkeypatch.setattr("app.detail_vision.httpx.Client", lambda **_: _Client(_Response(500, {})))
    grouped, note = read_detail_batch([b"a"], _settings())
    assert grouped == {}
    assert "失败" in note


def test_missing_gateway_is_reported():
    grouped, note = read_detail_batch([b"a"], _settings(llm_base_url=None))
    assert grouped == {}
    assert "未配置" in note


def test_oversized_image_aborts_the_batch(monkeypatch):
    monkeypatch.setattr(
        "app.detail_vision.httpx.Client", lambda **_: _Client(_Response(200, _reply([])))
    )
    grouped, note = read_detail_batch([b"x" * (5 * 1024 * 1024)], _settings())
    assert grouped == {}
    assert "过大" in note


def test_chunked_splits_into_fixed_sizes():
    assert chunked(list(range(7)), 5) == [[0, 1, 2, 3, 4], [5, 6]]
    assert chunked([], 5) == []
    assert chunked([1, 2], 5) == [[1, 2]]


def test_batch_size_default_is_five():
    """Measured against a per-image baseline: five kept every parameter and policy."""
    assert BATCH_SIZE == 5
