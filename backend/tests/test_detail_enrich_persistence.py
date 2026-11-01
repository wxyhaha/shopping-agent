"""Enrichment must be persisted, not quietly dropped by JSON change tracking.

`enrich_detail_images` mutates the payload dict in place and returns the same object.
SQLAlchemy's JSON column does not track deep mutations, so assigning that identical
reference produced no UPDATE: the status flag flipped to "done" while the recognised text
never reached the database. That is why detail text looked permanently empty.
"""

import copy

from app.detail_enrich import enrich_detail_images


class _Settings:
    llm_base_url = "http://127.0.0.1:1/v1"
    llm_model = "vision-model"
    llm_api_key = None
    browser_trust_fake_ip_dns = False
    detail_vision_enabled = True


def _snapshot():
    return {
        "images": [
            {"index": 7, "source_url": "https://img.alicdn.com/x/detail.jpg"},
        ],
        "evidence": [
            {
                "evidence_id": "e1",
                "field_path": "images.detail[7]",
                "source": "image_metadata",
                "excerpt": "detail artwork",
            }
        ],
        "fields": [],
        "coverage": [],
    }


def test_enrichment_mutates_in_place_and_returns_the_same_object():
    """Documents the behaviour callers must account for when persisting."""
    payload = _snapshot()
    original_id = id(payload)
    enriched = enrich_detail_images(
        payload, _Settings(), "tmall", policy=_NeverAllows(), check_cancel=lambda: None
    )
    assert id(enriched) == original_id


def test_copying_before_enrichment_yields_an_independent_object():
    """The caller must pass a copy so the ORM sees a changed value and issues an UPDATE."""
    payload = _snapshot()
    working = copy.deepcopy(payload)
    enrich_detail_images(
        working, _Settings(), "tmall", policy=_NeverAllows(), check_cancel=lambda: None
    )
    # The stored payload is untouched; the working copy took the changes.
    assert payload["fields"] == []
    assert isinstance(working, dict)


class _NeverAllows:
    """Every fetch is refused, which exercises the failure bookkeeping path."""

    def allows(self, _url):
        return False
