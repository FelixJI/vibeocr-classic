"""Classic-owned PDF workspace projected from Protocol wire DTOs.

The Runtime owns the mutable PDF and all PyMuPDF state.  Classic keeps only
the view state required by Qt plus frontend-only loading and OCR counters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vibeocr.runtime_contracts.pdf import (
    PdfDocumentMirror,
    PdfModelDiff,
    PdfPageInfoMirror,
    TextBlockMirror,
    TextLayerInfoMirror,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TextLayerInfo:
    """One text-layer entry displayed by Classic."""

    index: int
    text_preview: str
    char_count: int
    bbox: tuple[float, float, float, float]
    color_id: int


@dataclass(slots=True)
class TextBlock:
    """Frontend projection of a Protocol PDF text block."""

    text: str
    score: float
    bbox: tuple[float, float, float, float] | None = None
    polygon: tuple[float, ...] | None = None
    page_idx: int | None = None
    is_manually_edited: bool = False
    label: str = "text"
    order: int = -1


@dataclass(slots=True)
class PdfPageInfo:
    """One page of frontend display state."""

    page_index: int
    rotation: int = 0
    has_text_layer: bool = False
    text_layers: list[TextLayerInfo] = field(default_factory=list)
    is_scanned: bool = False
    thumbnail: object | None = None
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    ocr_text_blocks: list[TextBlock] = field(default_factory=list)
    ocr_preproc_angle: int = 0
    deskewed: bool = False


@dataclass(slots=True)
class PdfDocument:
    """Frontend display projection of a Runtime-owned PDF document."""

    file_path: str | None = None
    pages: list[PdfPageInfo] = field(default_factory=list)
    is_modified: bool = False
    has_structural_change: bool = False
    render_dpi: int = 300
    thumbnail_dpi: int = 96

    def get_page(self, index: int | None) -> PdfPageInfo | None:
        if index is None:
            return None
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(slots=True)
class PdfSession:
    """Frontend-only state for one Runtime PDF session."""

    file_path: str
    session_id: str = ""
    pdf_document: PdfDocument = field(default_factory=PdfDocument)
    loaded_pages: set[int] = field(default_factory=set)
    _ocr_stats: dict[str, int] = field(
        default_factory=lambda: {"written": 0, "skipped": 0}, repr=False
    )

    @property
    def is_modified(self) -> bool:
        return self.pdf_document.is_modified

    @property
    def load_progress(self) -> float:
        total = self.pdf_document.page_count
        if total == 0:
            return 1.0
        return len(self.loaded_pages) / total

    @property
    def ocr_stats(self) -> dict[str, int]:
        return self._ocr_stats

    def reset_ocr_stats(self) -> None:
        self._ocr_stats = {"written": 0, "skipped": 0}

    def add_ocr_stats(self, written: int, skipped: int) -> None:
        self._ocr_stats["written"] += written
        self._ocr_stats["skipped"] += skipped


def text_layer_from_mirror(mirror: TextLayerInfoMirror) -> TextLayerInfo:
    return TextLayerInfo(
        index=mirror.index,
        text_preview=mirror.text_preview,
        char_count=mirror.char_count,
        bbox=mirror.bbox,
        color_id=mirror.color_id,
    )


def text_block_from_mirror(mirror: TextBlockMirror) -> TextBlock:
    return TextBlock(
        text=mirror.text,
        score=mirror.score,
        bbox=mirror.bbox,
        polygon=mirror.polygon,
        page_idx=mirror.page_idx,
        is_manually_edited=mirror.is_manually_edited,
        label=mirror.label,
        order=mirror.order,
    )


def page_from_mirror(mirror: PdfPageInfoMirror) -> PdfPageInfo:
    return PdfPageInfo(
        page_index=mirror.page_index,
        rotation=mirror.rotation,
        has_text_layer=mirror.has_text_layer,
        text_layers=[text_layer_from_mirror(item) for item in mirror.text_layers],
        is_scanned=mirror.is_scanned,
        rect=mirror.rect,
        ocr_text_blocks=[
            text_block_from_mirror(item) for item in mirror.ocr_text_blocks
        ],
        ocr_preproc_angle=mirror.ocr_preproc_angle,
        deskewed=mirror.deskewed,
    )


def document_from_mirror(mirror: PdfDocumentMirror) -> PdfDocument:
    return PdfDocument(
        file_path=mirror.file_path,
        pages=[page_from_mirror(page) for page in mirror.pages],
        is_modified=mirror.is_modified,
        has_structural_change=mirror.has_structural_change,
        render_dpi=mirror.render_dpi,
        thumbnail_dpi=mirror.thumbnail_dpi,
    )


def coerce_document_mirror(value: Any) -> PdfDocumentMirror:
    """Normalize Protocol payloads and transitional legacy adapters at the seam."""

    if isinstance(value, PdfDocumentMirror):
        return value
    if isinstance(value, dict):
        return PdfDocumentMirror.from_payload(value)
    to_payload = getattr(value, "to_payload", None)
    if callable(to_payload):
        return PdfDocumentMirror.from_payload(to_payload())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return PdfDocumentMirror.from_payload(model_dump(mode="json"))
    raise TypeError("PDF document mirror must be a Protocol DTO or JSON object")


def apply_model_diff(document: PdfDocument, diff: PdfModelDiff) -> list[int]:
    """Apply one Runtime diff and return invalidated thumbnail indices."""

    invalidated = list(diff.invalidated_thumbnails)
    if diff.full_model is not None:
        replacement = document_from_mirror(diff.full_model)
        document.file_path = replacement.file_path
        document.pages = replacement.pages
        document.is_modified = replacement.is_modified
        document.has_structural_change = replacement.has_structural_change
        document.render_dpi = replacement.render_dpi
        document.thumbnail_dpi = replacement.thumbnail_dpi
        if not invalidated:
            invalidated = list(range(len(document.pages)))
    else:
        for page_mirror in diff.replaced_pages:
            index = page_mirror.page_index
            if 0 <= index < len(document.pages):
                document.pages[index] = page_from_mirror(page_mirror)
            else:
                logger.warning(
                    "[pdf_workspace] replaced_pages index out of range: %d", index
                )

    if diff.modified_flag is not None:
        document.is_modified = diff.modified_flag
    if diff.structural_flag is not None:
        document.has_structural_change = diff.structural_flag
    return invalidated


__all__ = [
    "PdfDocument",
    "PdfPageInfo",
    "PdfSession",
    "TextBlock",
    "TextLayerInfo",
    "apply_model_diff",
    "coerce_document_mirror",
    "document_from_mirror",
    "page_from_mirror",
    "text_block_from_mirror",
    "text_layer_from_mirror",
]
