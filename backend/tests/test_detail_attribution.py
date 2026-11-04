"""Each recognised line keeps its own image, including panels beyond the first twenty."""

from types import SimpleNamespace

from app.detail_enrich import enrich_detail_images
from app.evidence_search import detail_research_summary


def snapshot(count):
    return {
        "fields": [],
        "coverage": [],
        "images": [
            {"index": i, "source_url": f"https://img.alicdn.com/{i}.jpg"}
            for i in range(1, count + 1)
        ],
        "evidence": [
            {
                "evidence_id": f"img-{i}",
                "field_path": f"images.detail[{i}]",
                "source": "image_metadata",
            }
            for i in range(1, count + 1)
        ],
    }


def test_late_parameters_and_duplicate_lines_keep_their_real_sources(monkeypatch):
    monkeypatch.setattr(
        "app.detail_enrich.fetch_image",
        lambda url, *_: (url.rsplit("/", 1)[1].split(".")[0].encode(), "ok"),
    )

    def read(images, _settings):
        result = {}
        for offset, content in enumerate(images):
            number = int(content)
            parameters = [f"参数{number}"]
            if number in {1, 24}:
                parameters.append("重复参数")
            result[offset] = (parameters, [], [f"购买条件{number}"])
        return result, "ok"

    monkeypatch.setattr("app.detail_enrich.read_detail_batch", read)
    result = enrich_detail_images(snapshot(24), SimpleNamespace(), "tmall", None)
    fields = {field["raw_value"]: field for field in result["fields"]}
    assert fields["参数24"]["evidence_ids"] == ["detail-text-24"]
    assert fields["购买条件24"]["evidence_ids"] == ["detail-text-24"]
    assert fields["重复参数"]["evidence_ids"] == ["detail-text-1", "detail-text-24"]
    summary = detail_research_summary(result["fields"], ref_prefix="snap:")
    index = summary["detail_parameters"].index("参数24")
    assert summary["detail_parameter_refs"][index] == ["snap:detail-text-24"]
    assert all(field["status"] == "ambiguous" for field in result["fields"])


def test_unattributed_batch_stays_a_claim_with_all_possible_sources(monkeypatch):
    monkeypatch.setattr("app.detail_enrich.fetch_image", lambda *_: (b"image", "ok"))
    monkeypatch.setattr(
        "app.detail_enrich.read_detail_batch",
        lambda *_: ({-1: (["无法定位的参数文案"], [], [])}, "unattributed"),
    )
    result = enrich_detail_images(snapshot(2), SimpleNamespace(), "tmall", None)
    assert len(result["fields"]) == 1
    field = result["fields"][0]
    assert field["field_path"].startswith("detail_images.claim_")
    assert field["evidence_ids"] == ["detail-text-1", "detail-text-2"]
    assert len([e for e in result["evidence"] if e["source"] == "detail_image_vision"]) == 2
    assert all(
        "未定位到单张图片" in e["excerpt"]
        for e in result["evidence"]
        if e["source"] == "detail_image_vision"
    )
