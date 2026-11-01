"""Keep complete capture evidence while giving the model only decision-relevant excerpts.

Detail artwork is fully recognised and stored, but a 30-panel product can produce dozens of
marketing lines and dozens of field records. Sending every one of them on every tool call made
the request grow with each product read until a model call timed out.

Two facts shape the design here:

* Attribute statements and purchase conditions matter for a decision, so they stay complete.
* Promotional copy is presentation, so only its count and a few examples are passed on.

Evidence references are also stored once, not per line: the enrichment step attaches the same
set of detail-image evidence ids to every recognised line, so repeating that set on each entry
duplicated thousands of characters without identifying which image a line came from.
"""

from __future__ import annotations

from collections.abc import Iterable

_KINDS = {
    "detail_images.parameter_": "parameter",
    "detail_images.claim_": "claim",
    "detail_images.policy_": "policy",
}
_MAX_CLAIM_EXAMPLES = 5
_MAX_SHARED_REFS = 6

DETAIL_REFERENCE_NOTE = (
    "各条识别结果同属详情图证据组，引用时使用 evidence 中 source=detail_image_vision 的 ref。"
)


def detail_kind(path: str) -> str | None:
    """Return the detail-image bucket for a field path, or None for ordinary fields."""
    for prefix, kind in _KINDS.items():
        if path.startswith(prefix):
            return kind
    return None


def _qualified_refs(field: dict, ref_prefix: str) -> tuple[str, ...]:
    return tuple(f"{ref_prefix}{ref}" for ref in (field.get("evidence_ids") or []))


def _shared_refs(fields: Iterable[dict], ref_prefix: str) -> list[str]:
    """Return the common reference set, but only when every field really shares it.

    When the sets differ the lines carry their own references and no sharing is assumed.
    """
    sets = {_qualified_refs(field, ref_prefix) for field in fields}
    if len(sets) != 1:
        return []
    return list(next(iter(sets)))


def detail_research_summary(
    fields: Iterable[dict],
    *,
    ref_prefix: str = "",
    max_claim_examples: int = _MAX_CLAIM_EXAMPLES,
) -> dict:
    """Build a compact research view without discarding attributes or purchase conditions."""
    fields = list(fields)
    parameters: list[str] = []
    policies: list[str] = []
    claims: list[str] = []
    for field in fields:
        kind = detail_kind(str(field.get("field_path") or ""))
        if kind is None:
            continue
        value = str(field.get("raw_value") or "").strip()
        if not value:
            continue
        if kind == "parameter":
            parameters.append(value)
        elif kind == "policy":
            policies.append(value)
        else:
            claims.append(value)
    result: dict[str, object] = {}
    if parameters:
        result["detail_parameters"] = parameters
    if policies:
        result["detail_policies"] = policies
    if claims:
        result["claim_summary"] = {
            "count": len(claims),
            "examples": claims[:max_claim_examples],
            "note": "其余为认证、荣誉、代言、宣传语等商家表述，不作为参数依据。",
        }
    if parameters or policies or claims:
        shared = _shared_refs(fields, ref_prefix)
        if shared:
            result["detail_refs"] = shared[:_MAX_SHARED_REFS]
            result["detail_refs_note"] = (
                f"{DETAIL_REFERENCE_NOTE}共 {len(shared)} 条，此处列前若干条。"
            )
        else:
            result["detail_refs_note"] = "各条识别结果自带引用，详见 evidence。"
    return result


def _normalise(value: str) -> str:
    return "".join(value.split()).casefold()


def search_detail_evidence(
    fields: Iterable[dict],
    query: str,
    kind: str | None = None,
    limit: int = 12,
    ref_prefix: str = "",
) -> list[dict]:
    """Find detail-image lines whose text contains the query.

    Matching is deliberately simple and deterministic: no model call, no vector index. A query
    like "屏蔽" therefore yields the exact recorded attribute lines that mention it.
    """
    needle = _normalise(query)
    if not needle:
        return []
    matches: list[dict] = []
    for field in fields:
        kind_of_field = detail_kind(str(field.get("field_path") or ""))
        if kind_of_field is None:
            continue
        if kind is not None and kind_of_field != kind:
            continue
        value = str(field.get("raw_value") or "").strip()
        if needle in _normalise(value):
            matches.append(
                {
                    "text": value,
                    "kind": kind_of_field,
                    "refs": list(_qualified_refs(field, ref_prefix)),
                }
            )
            if len(matches) >= max(1, min(limit, 20)):
                break
    return matches
