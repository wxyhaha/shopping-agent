"""Detail-image reading: extractive text only, never structured specification."""

import pytest

from app.config import Settings
from app.detail_vision import MAX_LINES, parse_detail_lines, read_detail_image
from app.image_fetch import fetch_image


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("规格型号：NW102六类千兆", ["规格型号：NW102六类千兆"]),
        ("- 材质：纯铜\n- 长度：15m", ["材质：纯铜", "长度：15m"]),
        ("1. 型号A\n2. 型号B", ["型号A", "型号B"]),
        ("• 认证：CE", ["认证：CE"]),
    ],
)
def test_parse_detail_lines_cleans_markers(reply, expected):
    assert parse_detail_lines(reply) == expected


@pytest.mark.parametrize("reply", ["", "NONE", "none", "  NONE  "])
def test_parse_detail_lines_returns_nothing_for_empty_answers(reply):
    assert parse_detail_lines(reply) == []


def test_parse_detail_lines_deduplicates_and_bounds():
    repeated = "\n".join(["材质：纯铜"] * 5 + [f"参数{i}" for i in range(20)])
    lines = parse_detail_lines(repeated)
    assert lines[0] == "材质：纯铜"
    assert lines.count("材质：纯铜") == 1
    assert len(lines) <= MAX_LINES


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


def test_read_detail_image_returns_lines(monkeypatch):
    payload = {"choices": [{"message": {"content": "型号：NW102\n长度：15m"}}]}
    client = _Client(_Response(200, payload))
    monkeypatch.setattr("app.detail_vision.httpx.Client", lambda **_: client)
    lines, note = read_detail_image(b"jpeg", _settings())
    assert lines == ["型号：NW102", "长度：15m"]
    # The wording must say the text is merchant material, not verified fact.
    assert "宣传材料" in note


def test_read_detail_image_rejects_oversized_and_unconfigured(monkeypatch):
    lines, note = read_detail_image(b"x" * (5 * 1024 * 1024), _settings())
    assert lines == [] and "过大" in note
    lines, note = read_detail_image(b"jpeg", _settings(llm_base_url=None))
    assert lines == [] and "未配置" in note


def test_fetch_image_refuses_a_host_outside_the_policy():
    class _Policy:
        def allows(self, _url):
            return False

    content, note = fetch_image("https://evil.invalid/x.jpg", "tmall", _Policy())
    assert content is None
    assert "未通过" in note


def test_fetch_image_rejects_a_non_image_response(monkeypatch):
    class _Policy:
        def allows(self, _url):
            return True

    class _Html:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"<html></html>"

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers=None):
            return _Html()

    monkeypatch.setattr("app.image_fetch.httpx.Client", lambda **_: _Client())
    content, note = fetch_image("https://img.alicdn.com/x.jpg", "tmall", _Policy())
    assert content is None
    assert "不是图片" in note
