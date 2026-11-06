"""Enrich a captured snapshot with text read out of its detail images.

Detail artwork carries most of a product's real attributes, but it is pixels: the extension
can read the DOM and cannot read the pictures, while the backend holds the model
credentials. This module is the shared step both capture channels call, so a snapshot means
the same thing regardless of which browser produced it.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.detail_dedupe import dedupe_detail_images
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


def _add_items(snapshot: dict, kind: str, readings: list[tuple[str, list[str]]]) -> None:
    """Record recognised detail-image text as its own observation set.

    Each kind stays under its own path so none can be mistaken for the measured
    specification table (``specs.*``), which comes from the DOM. The user-facing label is a
    single "详情图" tag; the source URL stays in the evidence locator for traceability.
    """
    # Deduplicate text while retaining all its actual source panels. A late panel must not
    # inherit the first twenty panels' references simply because those were read earlier.
    unique: dict[str, tuple[str, list[str]]] = {}
    fields = snapshot.setdefault("fields", [])
    existing_by_text = {
        re.sub(r"\s+", "", field.get("raw_value", "")): field
        for field in fields
        if field.get("field_path", "").startswith(f"detail_images.{kind}_")
    }
    all_refs: list[str] = []
    for line, refs in readings:
        key = re.sub(r"\s+", "", line)
        if not key:
            continue
        all_refs.extend(refs)
        existing = existing_by_text.get(key)
        if existing is not None:
            existing["evidence_ids"] = list(
                dict.fromkeys([*existing.get("evidence_ids", []), *refs])
            )[:20]
            continue
        if key not in unique:
            unique[key] = (line, [])
        attached = unique[key][1]
        attached[:] = list(dict.fromkeys([*attached, *refs]))[:20]
    evidence_ids = list(dict.fromkeys(all_refs))[:20]
    existing_positions = [
        int(match.group(1))
        for field in fields
        if field.get("field_path", "").startswith(f"detail_images.{kind}_")
        and (match := re.search(r"_(\d+)$", field.get("field_path", "")))
    ]
    start = max(existing_positions, default=0) + 1
    remaining = max(0, 200 - len(fields))
    for position, (line, refs) in enumerate(list(unique.values())[:remaining], start=start):
        snapshot.setdefault("fields", []).append(
            {
                "observation_id": f"detail-{kind}-{position:02d}",
                "field_path": f"detail_images.{kind}_{position:02d}",
                "label": LABELS.get(kind, "详情图"),
                "raw_value": line[:2048],
                "status": "ambiguous",
                "evidence_ids": refs,
            }
        )
    coverage = snapshot.setdefault("coverage", [])
    coverage[:] = [
        entry for entry in coverage if entry.get("field_path") != f"detail_images.{kind}"
    ]
    coverage.append(
        {
            "field_path": f"detail_images.{kind}",
            "status": "ambiguous",
            "note": NOTES.get(kind, "来自商品详情图。"),
            "evidence_ids": evidence_ids,
        }
    )


def enrich_detail_screenshots(
    snapshot: dict,
    screenshots: list[dict],
    settings,
    page_url: str,
    batch_signature: str,
    *,
    check_cancel=lambda: None,
) -> dict:
    """Read temporary extension screenshots without downloading or storing image bytes."""
    ordered = sorted(screenshots, key=lambda entry: entry["position"])
    evidence_by_position: dict[int, dict] = {}
    for entry in ordered:
        position = entry["position"]
        evidence_id = f"detail-shot-{batch_signature[:24]}-{position:02d}"
        evidence = {
            "evidence_id": evidence_id,
            "field_path": f"detail_screenshots[{position:02d}]",
            "source": "extension_screenshot",
            "excerpt": f"详情图可见区域截图，页面滚动位置 {entry['scroll_y']}px。",
            "locator": (
                f"{page_url[:180]} · 扩展截图第 {position} 段 · scrollY={entry['scroll_y']}"
            )[:256],
        }
        snapshot.setdefault("evidence", []).append(evidence)
        evidence_by_position[position] = evidence

    all_screenshot_refs = [
        entry["evidence_id"]
        for entry in snapshot.get("evidence", [])
        if entry.get("source") == "extension_screenshot"
    ]
    coverage = snapshot.setdefault("coverage", [])
    coverage[:] = [
        entry for entry in coverage if entry.get("field_path") != "detail_images.browser_screenshot"
    ]
    coverage.append(
        {
            "field_path": "detail_images.browser_screenshot",
            "status": "ambiguous",
            "note": (
                f"浏览器扩展截取 {len(all_screenshot_refs)} 个详情图可见区域；"
                "截图原图只用于识别，不落盘。"
            ),
            "evidence_ids": all_screenshot_refs,
        }
    )

    grouped_results: list[tuple[tuple[int, ...], tuple[list[str], list[str], list[str]]]] = []
    batches = chunked(ordered, BATCH_SIZE)
    with ThreadPoolExecutor(max_workers=min(DETAIL_VISION_WORKERS, max(1, len(batches)))) as pool:
        futures = {
            pool.submit(read_detail_batch, [entry["content"] for entry in group], settings): group
            for group in batches
        }
        for future in as_completed(futures):
            check_cancel()
            group = futures[future]
            try:
                readings, _note = future.result()
            except Exception:
                continue
            for offset, buckets in readings.items():
                if 0 <= offset < len(group):
                    grouped_results.append(((group[offset]["position"],), buckets))
                elif offset == -1:
                    grouped_results.append(
                        (
                            tuple(entry["position"] for entry in group),
                            ([], [line for bucket in buckets for line in bucket], []),
                        )
                    )

    parameter_lines: list[tuple[str, list[str]]] = []
    claim_lines: list[tuple[str, list[str]]] = []
    policy_lines: list[tuple[str, list[str]]] = []
    for positions, buckets in sorted(grouped_results, key=lambda result: result[0]):
        parameters, claims, policies = buckets
        if not parameters and not claims and not policies:
            continue
        refs = [evidence_by_position[position]["evidence_id"] for position in positions]
        excerpt = " / ".join(parameters + claims + policies)
        if len(positions) > 1:
            excerpt = "批次文案，未定位到单个截图片段：" + excerpt
        for position in positions:
            evidence = evidence_by_position[position]
            evidence["excerpt"] = excerpt[:2048]
        parameter_lines.extend((line, refs) for line in parameters)
        claim_lines.extend((line, refs) for line in claims)
        policy_lines.extend((line, refs) for line in policies)

    if parameter_lines:
        _add_items(snapshot, "parameter", parameter_lines)
    if claim_lines:
        _add_items(snapshot, "claim", claim_lines)
    if policy_lines:
        _add_items(snapshot, "policy", policy_lines)
    if not parameter_lines and not claim_lines and not policy_lines:
        coverage[:] = [
            entry for entry in coverage if entry.get("field_path") != "detail_images.text"
        ]
        coverage.append(
            {
                "field_path": "detail_images.text",
                "status": "missing",
                "note": "已处理扩展截图片段，但没有识别到可引用的详情图文字。",
                "evidence_ids": all_screenshot_refs,
            }
        )
    else:
        coverage[:] = [
            entry
            for entry in coverage
            if not (
                entry.get("field_path") == "detail_images.text" and entry.get("status") == "missing"
            )
        ]
    return snapshot


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

    results: list[tuple[tuple[int, ...], tuple[list[str], list[str], list[str]]]] = []
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
                    results.append(((group[offset][0],), buckets))
                elif offset == -1:
                    # Unattributed text is a batch-level merchant claim. Keep all possible
                    # source panels rather than fabricating a precise first-image citation.
                    results.append(
                        (
                            tuple(position for position, _ in group),
                            ([], [line for bucket in buckets for line in bucket], []),
                        )
                    )

    parameter_lines: list[tuple[str, list[str]]] = []
    claim_lines: list[tuple[str, list[str]]] = []
    policy_lines: list[tuple[str, list[str]]] = []
    image_evidence: dict[int, dict] = {}
    for positions, buckets in sorted(results, key=lambda entry: entry[0]):
        parameters, claims, policies = buckets
        if not parameters and not claims and not policies:
            continue
        refs = [f"detail-text-{chosen[position].get('index', 0)}" for position in positions]
        text = " / ".join(parameters + claims + policies)
        if len(positions) > 1:
            text = "批次文案，未定位到单张图片：" + text
        for position in positions:
            image = chosen[position]
            index = image.get("index", 0)
            entry = image_evidence.setdefault(
                position,
                {
                    "evidence_id": f"detail-text-{index}",
                    "field_path": f"detail_images[{index}].text",
                    "source": "detail_image_vision",
                    "excerpt": "",
                    "locator": image["source_url"][:256],
                },
            )
            entry["excerpt"] = " / ".join(filter(None, [entry["excerpt"], text]))[:2048]
        parameter_lines.extend((line, refs) for line in parameters)
        claim_lines.extend((line, refs) for line in claims)
        policy_lines.extend((line, refs) for line in policies)

    for _position, entry in sorted(image_evidence.items()):
        snapshot.setdefault("evidence", []).append(entry)
        coverage.append(
            {
                "field_path": entry["field_path"],
                # Not "observed": the wording is the seller's own description.
                "status": "ambiguous",
                "note": "详情图文字由视觉模型读取，属商家标注。",
                "evidence_ids": [entry["evidence_id"]],
            }
        )
    if parameter_lines:
        _add_items(snapshot, "parameter", parameter_lines)
    if claim_lines:
        _add_items(snapshot, "claim", claim_lines)
    if policy_lines:
        _add_items(snapshot, "policy", policy_lines)
    if not image_evidence:
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
