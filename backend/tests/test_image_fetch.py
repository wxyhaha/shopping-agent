"""Image fetching must obey the same host policy as page collection."""

from app.image_fetch import MAX_BYTES, fetch_image


class _Policy:
    def __init__(self, allowed):
        self._allowed = allowed

    def allows(self, _url):
        return self._allowed


class _Response:
    def __init__(self, status_code=200, content_type="image/jpeg", content=b"jpeg"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = content


class _Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return self.response


def test_refuses_a_host_outside_the_policy(monkeypatch):
    content, note = fetch_image("https://evil.invalid/x.jpg", "tmall", _Policy(False))
    assert content is None
    assert "未通过" in note


def test_rejects_a_non_image_response(monkeypatch):
    monkeypatch.setattr(
        "app.image_fetch.httpx.Client",
        lambda **_: _Client(_Response(content_type="text/html", content=b"<html>")),
    )
    content, note = fetch_image("https://img.alicdn.com/x.jpg", "tmall", _Policy(True))
    assert content is None
    assert "不是图片" in note


def test_rejects_an_http_error(monkeypatch):
    monkeypatch.setattr(
        "app.image_fetch.httpx.Client", lambda **_: _Client(_Response(status_code=403))
    )
    content, note = fetch_image("https://img.alicdn.com/x.jpg", "tmall", _Policy(True))
    assert content is None
    assert "403" in note


def test_rejects_oversized_payload(monkeypatch):
    huge = b"x" * (MAX_BYTES + 1)
    monkeypatch.setattr(
        "app.image_fetch.httpx.Client", lambda **_: _Client(_Response(content=huge))
    )
    content, note = fetch_image("https://img.alicdn.com/x.jpg", "tmall", _Policy(True))
    assert content is None
    assert "大小" in note


def test_returns_bytes_for_a_valid_image(monkeypatch):
    monkeypatch.setattr("app.image_fetch.httpx.Client", lambda **_: _Client(_Response()))
    content, note = fetch_image("https://img.alicdn.com/x.jpg", "tmall", _Policy(True))
    assert content == b"jpeg"
    assert note == ""
