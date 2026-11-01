"""Enrich a captured snapshot with text read out of its detail images.

Detail artwork carries most of a product's real attributes, but it is pixels: the extension
can read the DOM and cannot read the pictures, while the backend holds the model
credentials. This module is the shared step both capture channels call, so a snapshot means
the same thing regardless of which browser produced it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.detail_dedupe import dedupe_detail_images, dedupe_texts
from app.detail_vision import BATCH_SIZE, chunked, read_detail_batch
from app.image_fetch import fetch_image

# How many distinct detail images may be read per snapshot. Artwork can run to dozens and
# carries real specifications, so the cap is generous; duplicates are removed first.
DETAIL_IMAGE_LIMIT = 32
# Downloads are cheap I/O; vision calls are bounded by the gateway.
DETAIL_FETCH_WORKERS = 5
DETAIL_VISION_WORKERS = 10

LABELS = {
    "parameter": "详情图",
    "claim": "详情图 · 宣传",
    "policy": "详情图 · 购买条件",
}
NOTES = {
    "parameter": "来自商品详情图，属商家标注，未经独立核实。",
    "claim": "详情图中的评价或宣传表述，不能作为参数依据。",
    "policy": "详情图中的售后、配送或购买条件，以页面实际为准。",
}


def detail_image_indexes(snapshot: dict) -> set[str]:
    """Indexes of images the parser filed under the detail section.

    The image record itself carries no section field; the evidence path
    ``images.detail[<index>]`` is what records it.
    """
    return {
        entry["field_path"].split("[", 1)[1].rstrip("]")
        for entry in snapshot.get("evidence", [])
        if entry.get("field_path", "").startswith("images.detail[")
    }


def _add_items(snapshot: dict, kind: str, lines: list[str]) -> None:
    """Record recognised detail-image text as its own observation set.

    Each kind stays under its own path so none can be mistaken for the measured
    specification table (``specs.*``), which comes from the DOM. The user-facing label is a
    single "详情图" tag; the source URL stays in the evidence locator for traceability.
    """
    evidence_ids = [
        entry["evidence_id"]
        for entry in snapshot.get("evidence", [])
        if entry.get("source") == "detail_image_vision"
    ][:20]
    for position, line in enumerate(lines, start=1):
        snapshot.setdefault("fields", []).append(
            {
                "observation_id": f"detail-{kind}-{position:02d}",
                "field_path": f"detail_images.{kind}_{position:02d}",
                "label": LABELS.get(kind, "详情图"),
                "raw_value": line[:2048],
                "status": "ambiguous",
                "evidence_ids": evidence_ids,
            }
        )
    snapshot.setdefault("coverage", []).append(
        {
            "field_path": f"detail_images.{kind}",
            "status": "ambiguous",
            "note": NOTES.get(kind, "来自商品详情图。"),
            "evidence_ids": evidence_ids,
        }
    )


def enrich_detail_images(
    snapshot: dict,
    settings,
    platform: str,
    policy,
    *,
    check_cancel=lambda: None,
) -> dict:
    """Read every distinct detail image and attach the classified text to ``snapshot``.

    ``snapshot`` is a plain dict (as sent by the extension or built by the collector). The
    function mutates and returns it. Failures are recorded rather than raised: a detail
    image that cannot be read must not discard the page snapshot that was just captured.
    """
    detail_indexes = detail_image_indexes(snapshot)
    images = [
        image
        for image in snapshot.get("images", [])
        if image.get("source_url") and str(image.get("index")) in detail_indexes
    ]
    if not images:
        return snapshot
    chosen, duplicates = dedupe_detail_images(images, DETAIL_IMAGE_LIMIT)
    if not chosen:
        return snapshot
    coverage = snapshot.setdefault("coverage", [])
    skipped: list[str] = []

    downloaded: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
        futures = {
            pool.submit(fetch_image, image["source_url"], platform, policy): position
            for position, image in enumerate(chosen)
        }
        for future in as_completed(futures):
            check_cancel()
            position = futures[future]
            try:
                content, note = future.result()
            except Exception:  # A single failed download must not end the enrichment.
                skipped.append("详情图下载异常。")
                continue
            if content is None:
                skipped.append(note)
            else:
                downloaded[position] = content

    results: dict[int, tuple[list[str], list[str], list[str]]] = {}
    batches = chunked(sorted(downloaded.items()), BATCH_SIZE)
    with ThreadPoolExecutor(max_workers=min(DETAIL_VISION_WORKERS, max(1, len(batches)))) as pool:
        futures = {
            pool.submit(read_detail_batch, [content for _, content in group], settings): group
            for group in batches
        }
        for future in as_completed(futures):
            check_cancel()
            group = futures[future]
            try:
                grouped, note = future.result()
            except Exception:  # One failed batch must not discard the others.
                skipped.append("详情图识别批次异常。")
                continue
            if not grouped:
                skipped.append(note)
                continue
            for offset, buckets in grouped.items():
                if 0 <= offset < len(group):
                    results[group[offset][0]] = buckets

    parameter_lines: list[str] = []
    claim_lines: list[str] = []
    policy_lines: list[str] = []
    read_images = 0
    for position, image in enumerate(chosen):
        buckets = results.get(position)
        if not buckets:
            continue
        parameters, claims, policies = buckets
        if not parameters and not claims and not policies:
            continue
        read_images += 1
        index = image.get("index", 0)
        evidence_id = f"detail-text-{index}"
        snapshot.setdefault("evidence", []).append(
            {
                "evidence_id": evidence_id,
                "field_path": f"detail_images[{index}].text",
                "source": "detail_image_vision",
                "excerpt": " / ".join(parameters + claims + policies)[:2048],
                "locator": image["source_url"][:256],
            }
        )
        coverage.append(
            {
                "field_path": f"detail_images[{index}].text",
                # Not "observed": the wording is the seller's own description.
                "status": "ambiguous",
                "note": "详情图文字由视觉模型读取，属商家标注。",
                "evidence_ids": [evidence_id],
            }
        )
        parameter_lines.extend(parameters)
        claim_lines.extend(claims)
        policy_lines.extend(policies)

    # The same line often appears on several panels; keep one copy of each.
    parameter_lines = dedupe_texts(parameter_lines)
    claim_lines = dedupe_texts(claim_lines)
    policy_lines = dedupe_texts(policy_lines)
    if parameter_lines:
        _add_items(snapshot, "parameter", parameter_lines)
    if claim_lines:
        _add_items(snapshot, "claim", claim_lines)
    if policy_lines:
        _add_items(snapshot, "policy", policy_lines)
    if not read_images:
        coverage.append(
            {
                "field_path": "detail_images.text",
                "status": "missing",
                "note": skipped[0] if skipped else "未读取详情图文字。",
                "evidence_ids": [],
            }
        )
    elif duplicates:
        coverage.append(
            {
                "field_path": "detail_images.dedupe",
                "status": "ambiguous",
                "note": f"跳过 {duplicates} 张重复或同源的详情图，未重复调用识别。",
                "evidence_ids": [],
            }
        )
    return snapshot
