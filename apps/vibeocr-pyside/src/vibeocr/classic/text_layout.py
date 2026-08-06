"""Classic-owned OCR text block layout."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from vibeocr.classic.recognition_settings import (
    LINE_MODE_KEEP,
    LINE_MODE_MERGE,
    TextBlockOptions,
)

_CJK_INDENT = "\u3000\u3000"
_PARAGRAPH_GAP_FACTOR = 1.5

if TYPE_CHECKING:
    from vibeocr.classic.recognition_result import TextBlock


class TextBlockProcessor:
    """Apply Classic text layout preferences to OCR text blocks."""

    @staticmethod
    def process(
        text_blocks: list[TextBlock],
        options: TextBlockOptions,
        image_height: int = 0,
    ) -> str:
        """Return the text blocks laid out for Classic presentation."""
        del image_height
        if not text_blocks:
            return ""
        blocks = text_blocks
        if options.drop_blank_blocks:
            blocks = [block for block in blocks if block.text and block.text.strip()]
        if not blocks:
            return ""
        blocks = TextBlockProcessor._sort_blocks(blocks)
        if options.line_mode == LINE_MODE_KEEP:
            return "\n".join(block.text for block in blocks)
        if options.line_mode == LINE_MODE_MERGE:
            text = TextBlockProcessor._join_segment(blocks, options.block_join_space)
            if options.chinese_indent:
                text = _CJK_INDENT + text
            return text
        segments = TextBlockProcessor._split_into_segments(blocks)
        parts = [
            TextBlockProcessor._join_segment(segment, options.block_join_space)
            for segment in segments
        ]
        if options.chinese_indent:
            parts = [_CJK_INDENT + part for part in parts]
        return "\n\n".join(parts)

    @staticmethod
    def _split_into_segments(blocks: list[TextBlock]) -> list[list[TextBlock]]:
        segments: list[list[TextBlock]] = [[blocks[0]]]
        for previous, current in itertools.pairwise(blocks):
            if TextBlockProcessor._is_paragraph_break(previous, current):
                segments.append([current])
            else:
                segments[-1].append(current)
        return segments

    @staticmethod
    def _sort_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
        if any(block.order != -1 for block in blocks):
            return sorted(
                blocks,
                key=lambda block: (block.order, _bbox_sort_key(block)),
            )
        return sorted(blocks, key=_bbox_sort_key)

    @staticmethod
    def _sort_indexed(
        indexed: list[tuple[int, TextBlock]],
    ) -> list[tuple[int, TextBlock]]:
        if any(block.order != -1 for _, block in indexed):
            return sorted(
                indexed,
                key=lambda item: (item[1].order, _bbox_sort_key(item[1])),
            )
        return sorted(indexed, key=lambda item: _bbox_sort_key(item[1]))

    @staticmethod
    def _split_indexed_into_segments(
        indexed: list[tuple[int, TextBlock]],
    ) -> list[list[tuple[int, TextBlock]]]:
        segments: list[list[tuple[int, TextBlock]]] = [[indexed[0]]]
        for (_, previous), (index, current) in itertools.pairwise(indexed):
            if TextBlockProcessor._is_paragraph_break(previous, current):
                segments.append([(index, current)])
            else:
                segments[-1].append((index, current))
        return segments

    @staticmethod
    def _is_paragraph_break(previous: TextBlock, current: TextBlock) -> bool:
        if previous.bbox is None or current.bbox is None:
            return False
        _, previous_y1, _, previous_y2 = previous.bbox
        _, current_y1, _, _ = current.bbox
        previous_height = previous_y2 - previous_y1
        if previous_height <= 0:
            return False
        return current_y1 - previous_y2 > _PARAGRAPH_GAP_FACTOR * previous_height

    @staticmethod
    def _join_segment(blocks: list[TextBlock], join_space: bool) -> str:
        separator = " " if join_space else ""
        return separator.join(block.text for block in blocks if block.text)


def _bbox_sort_key(block: TextBlock) -> tuple[float, float]:
    if block.bbox is None:
        return (float("inf"), float("inf"))
    x1, y1, _, _ = block.bbox
    return (y1, x1)


__all__ = ["TextBlockProcessor"]
