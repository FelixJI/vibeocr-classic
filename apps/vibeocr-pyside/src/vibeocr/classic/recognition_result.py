"""Classic-owned mutable OCR presentation model and Protocol adapter."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from vibeocr.runtime_contracts import (
    OcrResultV1,
    OcrTextBlockV1,
    parse_ocr_result_payload,
)

DISCARDED_BLOCK_TYPES = frozenset(
    {"header", "footer", "page_number", "page_footnote", "aside_text"}
)


def normalize_bbox(
    bbox_raw: list[float] | tuple[float, ...],
    img_w: int = 0,
    img_h: int = 0,
) -> tuple[float, float, float, float]:
    """Normalize UI geometry to the existing ``[0, 1000]`` coordinate space."""

    values = (
        float(bbox_raw[0]),
        float(bbox_raw[1]),
        float(bbox_raw[2]),
        float(bbox_raw[3]),
    )
    max_value = max(values)
    if max_value < 1.1:
        return (
            values[0] * 1000,
            values[1] * 1000,
            values[2] * 1000,
            values[3] * 1000,
        )
    if max_value > 1001 and img_w > 0 and img_h > 0:
        return (
            values[0] / img_w * 1000,
            values[1] / img_h * 1000,
            values[2] / img_w * 1000,
            values[3] / img_h * 1000,
        )
    return values


@dataclass
class TextBlock:
    """One mutable text block used by Classic editing and preview widgets."""

    text: str
    score: float
    bbox: tuple[float, float, float, float] | None
    polygon: tuple[float, ...] | None = None
    page_idx: int | None = None
    is_manually_edited: bool = False
    content_index: int | None = None
    content_id: str | None = None
    label: str = "text"
    order: int = -1


@dataclass
class OCRResult:
    """Mutable recognition state presented and edited by Classic UI."""

    raw_text: str = ""
    markdown_text: str = ""
    html_text: str = ""
    text_with_scores: list[tuple[str, float]] = field(default_factory=list)
    avg_score: float = 0.0
    low_confidence_items: list[tuple[str, float]] = field(default_factory=list)
    pipeline_type: str = "OCR"
    images: dict[str, Any] = field(default_factory=dict)
    content_list: list[dict[str, Any]] = field(default_factory=list)
    text_blocks: list[TextBlock] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    preproc_angle: int = 0
    preprocessed_image: bytes | None = None
    preproc_img_w: int = 0
    preproc_img_h: int = 0

    @property
    def has_rich_content(self) -> bool:
        return bool(self.html_text and self.html_text != self.raw_text)

    @property
    def has_content_list(self) -> bool:
        return bool(self.content_list)

    @property
    def display_text(self) -> str:
        return self.html_text if self.has_rich_content else self.raw_text

    @property
    def copy_text(self) -> str:
        return self.markdown_text if self.markdown_text else self.raw_text


def _text_block_from_dto(block: OcrTextBlockV1) -> TextBlock:
    return TextBlock(
        text=block.text,
        score=block.score,
        bbox=block.bbox,
        polygon=block.polygon,
        page_idx=block.page_idx,
        is_manually_edited=block.is_manually_edited,
        content_index=block.content_index,
        content_id=block.content_id,
        label=block.label,
        order=block.order,
    )


def ocr_result_from_payload(payload_type: str, payload: object) -> OCRResult:
    """Parse one ``ocr.v1`` wire payload into mutable Classic presentation state."""

    dto: OcrResultV1 = parse_ocr_result_payload(payload_type, payload)
    return OCRResult(
        raw_text=dto.raw_text,
        markdown_text=dto.markdown_text,
        html_text=dto.html_text,
        text_with_scores=list(dto.text_with_scores),
        avg_score=dto.avg_score,
        low_confidence_items=list(dto.low_confidence_items),
        pipeline_type=dto.pipeline_type,
        images=deepcopy(dict(dto.images)),
        content_list=deepcopy(list(dto.content_list)),
        text_blocks=[_text_block_from_dto(block) for block in dto.text_blocks],
        image_width=dto.image_width,
        image_height=dto.image_height,
        preproc_angle=dto.preproc_angle,
    )


__all__ = [
    "DISCARDED_BLOCK_TYPES",
    "OCRResult",
    "TextBlock",
    "normalize_bbox",
    "ocr_result_from_payload",
]
