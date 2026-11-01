"""Decide which detail images are worth a vision request.

Marketplace detail sections repeat themselves heavily: the same banner appears in several
sizes, a "参数" panel is reposted as a crop, and long sections chain near-identical frames.
Sending each one to a vision model wastes calls and inflates duplicated claims.

Deduplication here is deliberately cheap and explainable — the same image URL, the same
declared dimensions with a near-identical URL stem, or the same recognised text. It never
tries to judge image content, because that judgement is what the model is for.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# CDN size prefixes such as /s750x750_jfs/ and trailing !q80.dpg render the same artwork.
# CDN delivery prefixes: `/s750x750_` (mobile), `/n4/`, `/imgzone/` style size segments.
_SIZE_PREFIX = re.compile(r"/(?:s\d+x\d+_|n\d+|imgzone/)")
# Quality/scaling suffix such as `!q80.dpg`.
_QUALITY_SUFFIX = re.compile(r"!q\d+(?:\.\w+)?$", re.IGNORECASE)
# Alibaba reposts the same file as `<stem>.jpg_q50.jpg_.webp`; only the first extension is
# part of the artwork's identity.
_ALI_RESIZE = re.compile(r"(_q\d+)?\.(?:jpg|jpeg|png|webp)_\.\w+$", re.IGNORECASE)


def normalize_image_key(url: str) -> str:
    """Reduce a CDN URL to the artwork it points at, ignoring host, size and quality variants.

    The same image is routinely served from several hosts (``img14.360buyimg.com`` vs
    ``m.360buyimg.com``) and at several sizes, so neither the host nor the delivery prefix can
    be part of the identity. Only the trailing path that names the stored file is kept.
    """
    path = urlsplit(url).path or ""
    path = _ALI_RESIZE.sub("", path)
    path = _QUALITY_SUFFIX.sub("", path)
    path = _SIZE_PREFIX.sub("/", path)
    # JD artwork is identified by its `/jfs/...` tail; whatever precedes it is delivery detail.
    marker = path.find("/jfs/")
    if marker >= 0:
        path = path[marker:]
    return path.lstrip("/")


def dedupe_detail_images(images: list[dict], limit: int) -> tuple[list[dict], int]:
    """Return the images to read and how many were skipped as duplicates or unusable.

    ``images`` are snapshot image dicts with ``index`` and ``source_url``. Order is
    preserved so the earliest (usually topmost) artwork wins.
    """
    chosen: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for image in images:
        key = normalize_image_key(image.get("source_url") or "")
        if not key or key in seen:
            skipped += 1
            continue
        seen.add(key)
        chosen.append(image)
    # Trim after de-duplication so the limit counts distinct artwork only.
    if len(chosen) > limit:
        skipped += len(chosen) - limit
        chosen = chosen[:limit]
    return chosen, skipped


def dedupe_texts(lines: list[str]) -> list[str]:
    """Drop repeated lines across images; detail sections restate the same claims."""
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        key = re.sub(r"\s+", "", line)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(line)
    return unique
