import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class ProductUrlIdentity:
    platform: str
    product_id: str
    safe_url: str


def _first_query_value(query: dict[str, list[str]], *names: str) -> str | None:
    for name in names:
        for value in query.get(name, []):
            if value:
                return value
    return None


def product_url_identity(value: str) -> ProductUrlIdentity | None:
    try:
        parts = urlsplit(value)
        port = parts.port
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        query = {
            key.lower(): values
            for key, values in parse_qs(parts.query, keep_blank_values=False).items()
        }
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    expected_port = 80 if scheme == "http" else 443
    if (
        scheme not in {"http", "https"}
        or not host
        or parts.username
        or parts.password
        or port not in {None, expected_port}
    ):
        return None

    platform: str
    product_id: str | None
    safe_query: str

    if (host == "taobao.com" or host.endswith(".taobao.com")) and path.endswith("/item.htm"):
        platform = "taobao"
        product_id = _first_query_value(query, "id", "itemid")
        safe_query = urlencode({"id": product_id}) if product_id else ""
    elif (host == "tmall.com" or host.endswith(".tmall.com")) and path.endswith("/item.htm"):
        platform = "tmall"
        product_id = _first_query_value(query, "id", "itemid")
        safe_query = urlencode({"id": product_id}) if product_id else ""
    elif host in {"item.jd.com", "item.m.jd.com"}:
        platform = "jd"
        match = re.fullmatch(r"/(\d+)\.html", path)
        if match is None:
            match = re.fullmatch(r"/product/(\d+)\.html", path)
        product_id = match.group(1) if match else _first_query_value(query, "wareid")
        safe_query = urlencode({"wareId": product_id}) if product_id and not match else ""
    else:
        return None

    if product_id is None or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", product_id) is None:
        return None

    safe_url = urlunsplit((scheme, host, parts.path, safe_query, ""))
    return ProductUrlIdentity(platform, product_id, safe_url)


def safe_product_image_url(value: str) -> str | None:
    try:
        parts = urlsplit(value)
        port = parts.port
        host = (parts.hostname or "").lower()
    except ValueError:
        return None

    if (
        parts.scheme.lower() != "https"
        or not host
        or parts.username
        or parts.password
        or port not in {None, 443}
        or not any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in ("alicdn.com", "tbcdn.cn", "360buyimg.com")
        )
    ):
        return None

    return urlunsplit(("https", host, parts.path, "", ""))
