"""Classic OCR 批次预算的公共接口行为测试。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from vibeocr.classic.pyside.batch_budget import (
    BatchBudget,
    BatchEntry,
    image_pixel_count,
    partition_batches,
)


def _values(entries, budget):
    return [chunk.values for chunk in partition_batches(entries, budget)]


def test_ocr_default_preserves_client_transfer_limits() -> None:
    assert BatchBudget.ocr_default() == BatchBudget(
        max_items=16,
        max_encoded_bytes=64 * 1024 * 1024,
        max_pixels=64_000_000,
    )


def test_ocr_default_a4_300dpi_pages_fit_seven_per_chunk() -> None:
    entries = [
        BatchEntry(value=index, encoded_bytes=1, pixels=8_700_000) for index in range(8)
    ]

    assert [
        len(chunk.entries)
        for chunk in partition_batches(entries, BatchBudget.ocr_default())
    ] == [7, 1]


def test_item_limit_and_order_are_stable() -> None:
    entries = [BatchEntry(value=index, encoded_bytes=1, pixels=1) for index in range(5)]
    budget = BatchBudget(max_items=2, max_encoded_bytes=100, max_pixels=100)

    assert _values(entries, budget) == [[0, 1], [2, 3], [4]]


def test_encoded_byte_and_pixel_limits_are_independent() -> None:
    budget = BatchBudget(max_items=10, max_encoded_bytes=5, max_pixels=100)
    byte_entries = [
        BatchEntry(value="a", encoded_bytes=3, pixels=10),
        BatchEntry(value="b", encoded_bytes=2, pixels=10),
        BatchEntry(value="c", encoded_bytes=1, pixels=10),
    ]
    assert _values(byte_entries, budget) == [["a", "b"], ["c"]]

    pixel_entries = [
        BatchEntry(value="a", encoded_bytes=1, pixels=60),
        BatchEntry(value="b", encoded_bytes=1, pixels=50),
        BatchEntry(value="c", encoded_bytes=1, pixels=10),
    ]
    assert _values(pixel_entries, budget) == [["a"], ["b", "c"]]


def test_unknown_pixels_fall_back_to_item_and_byte_limits() -> None:
    entries = [
        BatchEntry(value="a", encoded_bytes=3, pixels=None),
        BatchEntry(value="b", encoded_bytes=3, pixels=None),
    ]
    budget = BatchBudget(max_items=10, max_encoded_bytes=5, max_pixels=1)

    assert _values(entries, budget) == [["a"], ["b"]]


def test_oversized_single_always_enters_one_batch() -> None:
    entries = [
        BatchEntry(value="huge", encoded_bytes=101, pixels=101),
        BatchEntry(value="small", encoded_bytes=1, pixels=1),
    ]
    budget = BatchBudget(max_items=2, max_encoded_bytes=10, max_pixels=10)

    chunks = partition_batches(entries, budget)

    assert [chunk.values for chunk in chunks] == [["huge"], ["small"]]
    assert chunks[0].oversized_single is True
    assert chunks[1].oversized_single is False


@pytest.mark.parametrize(
    ("max_items", "max_encoded_bytes", "max_pixels"),
    [(0, 100, 100), (1, -1, 100), (1, 100, 0)],
)
def test_batch_budget_rejects_non_positive_limits(
    max_items: int, max_encoded_bytes: int, max_pixels: int
) -> None:
    with pytest.raises(ValueError, match="positive"):
        BatchBudget(
            max_items=max_items,
            max_encoded_bytes=max_encoded_bytes,
            max_pixels=max_pixels,
        )


def test_image_pixel_count_reads_header_from_bytes() -> None:
    image = Image.new("RGB", (10, 20), "red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    assert image_pixel_count(buffer.getvalue()) == 200


def test_image_pixel_count_reads_header_from_path(tmp_path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGB", (4, 5), "blue").save(path, format="PNG")

    assert image_pixel_count(path) == 20


def test_image_pixel_count_returns_none_on_garbage() -> None:
    assert image_pixel_count(b"not an image") is None
    assert image_pixel_count(b"") is None


def test_partition_batches_empty_entries_returns_empty() -> None:
    budget = BatchBudget(max_items=2, max_encoded_bytes=100, max_pixels=100)

    assert partition_batches([], budget) == []
    assert partition_batches(iter([]), budget) == []
