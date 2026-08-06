"""Classic-owned text block layout behavior."""

from dataclasses import dataclass

import pytest

from vibeocr.classic.recognition_settings import (
    LINE_MODE_KEEP,
    LINE_MODE_MERGE,
    LINE_MODE_SMART,
    TextBlockOptions,
)
from vibeocr.classic.text_layout import TextBlockProcessor


@dataclass
class _Block:
    text: str
    score: float
    bbox: tuple[float, float, float, float] | None = None
    order: int = -1


def test_keep_mode_preserves_one_block_per_line() -> None:
    blocks = [_Block(text="甲", score=0.9), _Block(text="乙", score=0.9)]
    options = TextBlockOptions(line_mode=LINE_MODE_KEEP)

    assert TextBlockProcessor.process(blocks, options) == "甲\n乙"


def test_merge_mode_joins_blocks_with_space_and_indent() -> None:
    blocks = [_Block(text="第一", score=0.9), _Block(text="第二", score=0.9)]
    options = TextBlockOptions(
        line_mode=LINE_MODE_MERGE,
        block_join_space=True,
        chinese_indent=True,
    )

    assert TextBlockProcessor.process(blocks, options) == "\u3000\u3000第一 第二"


def test_smart_mode_splits_paragraphs_at_large_vertical_gap() -> None:
    blocks = [
        _Block("段一", 0.9, bbox=(0, 100, 100, 200)),
        _Block("续行", 0.9, bbox=(0, 210, 100, 310)),
        _Block("段二", 0.9, bbox=(0, 511, 100, 611)),
    ]
    options = TextBlockOptions(line_mode=LINE_MODE_SMART, block_join_space=True)

    assert TextBlockProcessor.process(blocks, options) == "段一 续行\n\n段二"


def test_layout_sorts_by_explicit_order_before_joining() -> None:
    blocks = [
        _Block("丙", 0.9, order=3),
        _Block("甲", 0.9, order=1),
        _Block("乙", 0.9, order=2),
    ]
    options = TextBlockOptions(line_mode=LINE_MODE_MERGE)

    assert TextBlockProcessor.process(blocks, options) == "甲乙丙"


def test_indexed_layout_preserves_original_indices_across_sort_and_split() -> None:
    indexed = [
        (7, _Block("段二", 0.9, bbox=(0, 300, 100, 400), order=2)),
        (3, _Block("段一", 0.9, bbox=(0, 0, 100, 100), order=1)),
    ]

    ordered = TextBlockProcessor._sort_indexed(indexed)
    segments = TextBlockProcessor._split_indexed_into_segments(ordered)

    assert [[index for index, _block in segment] for segment in segments] == [
        [3],
        [7],
    ]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (TextBlockOptions(line_mode=LINE_MODE_KEEP), "甲\n乙"),
        (
            TextBlockOptions(line_mode=LINE_MODE_KEEP, chinese_indent=True),
            "甲\n乙",
        ),
        (TextBlockOptions(line_mode=LINE_MODE_MERGE), "甲乙"),
        (
            TextBlockOptions(line_mode=LINE_MODE_MERGE, block_join_space=True),
            "甲 乙",
        ),
    ],
)
def test_layout_modes_preserve_existing_text_options_behavior(
    options: TextBlockOptions,
    expected: str,
) -> None:
    assert (
        TextBlockProcessor.process([_Block("甲", 0.9), _Block("乙", 0.9)], options)
        == expected
    )


def test_smart_mode_honors_strict_gap_boundary_and_indents_each_paragraph() -> None:
    blocks = [
        _Block("甲", 0.9, bbox=(0, 0, 100, 100)),
        _Block("乙", 0.9, bbox=(0, 250, 100, 350)),
        _Block("丙", 0.9, bbox=(0, 501, 100, 601)),
    ]
    options = TextBlockOptions(
        line_mode=LINE_MODE_SMART,
        block_join_space=True,
        chinese_indent=True,
    )

    assert TextBlockProcessor.process(blocks, options) == (
        "\u3000\u3000甲 乙\n\n\u3000\u3000丙"
    )


def test_drop_blank_blocks_can_be_enabled_or_disabled() -> None:
    blocks = [_Block("甲", 0.9), _Block("   ", 0.9), _Block("乙", 0.9)]

    assert (
        TextBlockProcessor.process(
            blocks,
            TextBlockOptions(line_mode=LINE_MODE_KEEP, drop_blank_blocks=True),
        )
        == "甲\n乙"
    )
    assert (
        TextBlockProcessor.process(
            blocks,
            TextBlockOptions(line_mode=LINE_MODE_KEEP, drop_blank_blocks=False),
        )
        == "甲\n   \n乙"
    )


@pytest.mark.parametrize(
    "blocks",
    [
        [],
        [_Block("", 0.9), _Block("   ", 0.9)],
    ],
)
def test_empty_or_all_blank_input_returns_empty(blocks: list[_Block]) -> None:
    assert TextBlockProcessor.process(blocks, TextBlockOptions()) == ""


def test_bbox_fallback_sorts_by_vertical_then_horizontal_position() -> None:
    blocks = [
        _Block("右", 0.9, bbox=(500, 100, 600, 200)),
        _Block("下", 0.9, bbox=(0, 500, 100, 600)),
        _Block("左", 0.9, bbox=(0, 100, 100, 200)),
    ]

    assert (
        TextBlockProcessor.process(
            blocks,
            TextBlockOptions(line_mode=LINE_MODE_MERGE),
        )
        == "左右下"
    )


@pytest.mark.parametrize(
    "blocks",
    [
        [
            _Block("甲", 0.9, bbox=(0, 0, 100, 100)),
            _Block("乙", 0.9, bbox=None),
        ],
        [
            _Block("甲", 0.9, bbox=(0, 100, 100, 100)),
            _Block("乙", 0.9, bbox=(0, 500, 100, 600)),
        ],
    ],
)
def test_smart_mode_keeps_unmeasurable_gaps_in_same_segment(
    blocks: list[_Block],
) -> None:
    assert (
        TextBlockProcessor.process(
            blocks,
            TextBlockOptions(line_mode=LINE_MODE_SMART, block_join_space=True),
        )
        == "甲 乙"
    )
