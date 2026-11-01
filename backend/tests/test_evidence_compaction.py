"""What the model sees must stay decision-sized without losing attributes or citations.

A page with ~30 detail panels produced ~60 field records and ~90 evidence entries. Sending all
of it on every tool call made a two-product run exceed both the request size and the model
timeout. These tests pin the split: attributes and purchase conditions stay complete and
citable, promotional copy is summarised, and repeated reads do not repeat the payload.
"""

import json

from app.evidence_search import detail_kind, detail_research_summary, search_detail_evidence
from app.research_agent import compact_coverage, compact_detail_fields, compact_evidence


def _field(path, value, refs=("detail-text-1",)):
    return {
        "observation_id": "x",
        "field_path": path,
        "label": "详情图",
        "raw_value": value,
        "status": "ambiguous",
        "evidence_ids": list(refs),
    }


def test_attributes_and_purchase_terms_are_kept_in_full():
    fields = [
        _field("detail_images.parameter_01", "家装工程级千兆网线"),
        _field("detail_images.parameter_02", "下载约1250MB/S"),
        _field("detail_images.policy_01", "7天无理由退货"),
    ]
    summary = detail_research_summary(fields)
    assert summary["detail_parameters"] == ["家装工程级千兆网线", "下载约1250MB/S"]
    assert summary["detail_policies"] == ["7天无理由退货"]


def test_promotional_copy_becomes_a_count_with_examples():
    fields = [_field(f"detail_images.claim_{i:02d}", f"宣传语{i}") for i in range(40)]
    summary = detail_research_summary(fields, max_claim_examples=3)
    claims = summary["claim_summary"]
    assert claims["count"] == 40
    assert claims["examples"] == ["宣传语0", "宣传语1", "宣传语2"]
    # The note must stop these being reused as specifications.
    assert "不作为参数依据" in claims["note"]
    assert "detail_claims" not in summary


def test_shared_references_are_listed_once_not_per_line():
    """All recognised lines share one evidence set; repeating it per line wasted thousands of
    characters and never identified the source image anyway."""
    fields = [_field(f"detail_images.parameter_{i:02d}", f"参数{i}") for i in range(20)]
    summary = detail_research_summary(fields, ref_prefix="snap-1:")
    assert summary["detail_refs"] == ["snap-1:detail-text-1"]
    assert "共 1 条" in summary["detail_refs_note"]
    # The lines themselves stay plain text, without per-line reference arrays.
    assert summary["detail_parameters"][0] == "参数0"


def test_differing_references_are_not_collapsed():
    fields = [
        _field("detail_images.parameter_01", "甲", refs=("detail-text-1",)),
        _field("detail_images.parameter_02", "乙", refs=("detail-text-2",)),
    ]
    summary = detail_research_summary(fields, ref_prefix="snap-1:")
    assert "detail_refs" not in summary
    assert "详见 evidence" in summary["detail_refs_note"]


def test_ordinary_fields_are_preserved_verbatim():
    fields = [_field("product.title", "绿联网线"), _field("detail_images.parameter_01", "六类")]
    compact = compact_detail_fields(fields)
    assert compact["fields"] == [fields[0]]


def test_empty_detail_kinds_are_omitted():
    compact = compact_detail_fields([_field("product.title", "x")])
    assert "detail_parameters" not in compact
    assert "claim_summary" not in compact


def test_compaction_shrinks_a_promotional_heavy_payload():
    fields = [_field(f"detail_images.claim_{i:02d}", f"荣誉与代言宣传语句{i}") for i in range(50)]
    before = len(json.dumps(fields, ensure_ascii=False))
    after = len(json.dumps(compact_detail_fields(fields), ensure_ascii=False))
    assert after < before / 3


def test_per_image_coverage_rows_are_dropped():
    """Both stored spellings must be removed; either one alone adds dozens of identical rows."""
    coverage = [
        {"field_path": "detail_images[7].text", "status": "ambiguous", "note": "详情图文字"},
        {"field_path": "images.detail[8].text", "status": "ambiguous", "note": "详情图文字"},
        {"field_path": "detail_images.parameter", "status": "ambiguous", "note": "参数"},
        {"field_path": "price", "status": "observed", "note": "价格"},
    ]
    kept = compact_coverage(coverage)
    assert [entry["field_path"] for entry in kept] == ["detail_images.parameter", "price"]


def test_image_metadata_evidence_is_dropped():
    """Image URLs and pixel sizes say nothing about what the product is."""
    entries = [
        {"evidence_id": "e1", "source": "image_metadata", "excerpt": "https://cdn/x.jpg 750x1000"},
        {"evidence_id": "e2", "source": "visible_dom", "excerpt": "长度15m"},
    ]
    kept = compact_evidence(entries)
    assert [entry["evidence_id"] for entry in kept] == ["e2"]
    assert kept[0]["excerpt"] == "长度15m"


def test_detail_vision_excerpt_is_trimmed_but_locator_kept():
    entries = [
        {
            "evidence_id": "detail-text-7",
            "source": "detail_image_vision",
            "excerpt": "品质之选 / 三重屏蔽",
            "locator": "https://img.alicdn.com/x.png",
        }
    ]
    kept = compact_evidence(entries)
    assert "excerpt" not in kept[0]
    # The locator stays so the claim is still traceable to its panel.
    assert kept[0]["locator"] == "https://img.alicdn.com/x.png"


def test_ordinary_evidence_is_untouched():
    entry = {"evidence_id": "e1", "source": "json_ld", "excerpt": "品牌：绿联"}
    assert compact_evidence([entry]) == [entry]


def test_detail_kind_only_matches_detail_paths():
    assert detail_kind("detail_images.parameter_01") == "parameter"
    assert detail_kind("detail_images.claim_01") == "claim"
    assert detail_kind("detail_images.policy_01") == "policy"
    assert detail_kind("product.title") is None


def test_search_finds_attribute_lines_by_keyword():
    fields = [
        _field("detail_images.parameter_01", "三重屏蔽抗干扰"),
        _field("detail_images.parameter_02", "纯铜导体"),
        _field("detail_images.claim_01", "三重屏蔽是行业标杆", refs=("detail-text-9",)),
    ]
    hits = search_detail_evidence(fields, "屏蔽", kind="parameter", ref_prefix="snap-1:")
    assert [hit["text"] for hit in hits] == ["三重屏蔽抗干扰"]
    assert hits[0]["refs"] == ["snap-1:detail-text-1"]


def test_search_without_kind_covers_every_bucket():
    fields = [
        _field("detail_images.parameter_01", "三重屏蔽"),
        _field("detail_images.claim_01", "三重屏蔽是行业标杆"),
        _field("detail_images.policy_01", "满200可用"),
    ]
    assert len(search_detail_evidence(fields, "屏蔽")) == 2


def test_search_ignores_spacing_and_case():
    fields = [_field("detail_images.parameter_01", "HDPE 绝缘外被")]
    assert search_detail_evidence(fields, "hdpe绝缘") != []


def test_search_respects_limit_and_reports_nothing_on_miss():
    fields = [_field(f"detail_images.parameter_{i:02d}", f"参数{i}") for i in range(10)]
    assert len(search_detail_evidence(fields, "参数", limit=4)) == 4
    assert search_detail_evidence(fields, "不存在的词") == []
