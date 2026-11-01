"""Detail-image deduplication: same artwork must not be read twice."""

import pytest

from app.detail_dedupe import dedupe_detail_images, dedupe_texts, normalize_image_key


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Same artwork served at two CDN sizes.
        (
            "https://img.alicdn.com/imgextra/i2/a/O1CN010y47Y01.jpg",
            "https://img.alicdn.com/imgextra/i2/a/O1CN010y47Y01.jpg_q50.jpg_.webp",
        ),
        # Same file with a quality suffix.
        (
            "https://m.360buyimg.com/mobilecms/s750x750_jfs/t1/x.jpg",
            "https://m.360buyimg.com/mobilecms/s750x750_jfs/t1/x.jpg!q80.dpg",
        ),
        # Same artwork under two size prefixes.
        (
            "https://img14.360buyimg.com/n4/jfs/t1/x.jpg",
            "https://m.360buyimg.com/mobilecms/s750x750_jfs/t1/x.jpg",
        ),
    ],
)
def test_same_artwork_normalises_to_one_key(left, right):
    assert normalize_image_key(left) == normalize_image_key(right)


def test_distinct_artwork_keeps_distinct_keys():
    a = normalize_image_key("https://img.alicdn.com/imgextra/i1/713/O1CN01AAA_!!713.jpg")
    b = normalize_image_key("https://img.alicdn.com/imgextra/i1/713/O1CN01BBB_!!713.jpg")
    assert a != b


def test_dedupe_keeps_first_occurrence_and_counts_skips():
    images = [
        {"index": 7, "source_url": "https://img.alicdn.com/x/A.jpg"},
        {"index": 8, "source_url": "https://img.alicdn.com/x/A.jpg_q50.jpg_.webp"},
        {"index": 9, "source_url": "https://img.alicdn.com/x/B.jpg"},
    ]
    chosen, duplicates = dedupe_detail_images(images, limit=8)
    assert [image["index"] for image in chosen] == [7, 9]
    assert duplicates == 1


def test_dedupe_respects_limit_and_drops_blank_urls():
    images = [{"index": i, "source_url": f"https://img.alicdn.com/x/{i}.jpg"} for i in range(6)]
    images.append({"index": 99, "source_url": ""})
    chosen, skipped = dedupe_detail_images(images, limit=4)
    assert len(chosen) == 4
    # Six distinct images plus one unusable URL, of which four are kept.
    assert skipped == 3


def test_dedupe_texts_ignores_whitespace_differences():
    lines = ["材质：纯铜", "材质： 纯铜", "长度：15m"]
    assert dedupe_texts(lines) == ["材质：纯铜", "长度：15m"]
