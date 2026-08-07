from __future__ import annotations

from vibeocr.classic.pdf_workspace import (
    PdfDocument,
    PdfSession,
    apply_model_diff,
    coerce_document_mirror,
    document_from_mirror,
)
from vibeocr.runtime_contracts.pdf import PdfDocumentMirror, PdfModelDiff


def _mirror(*, page_count: int = 2) -> PdfDocumentMirror:
    return PdfDocumentMirror.from_payload(
        {
            "file_path": "C:/docs/input.pdf",
            "pages": [
                {
                    "page_index": index,
                    "rect": [0.0, 0.0, 612.0, 792.0],
                    "text_layers": [],
                    "ocr_text_blocks": [],
                }
                for index in range(page_count)
            ],
            "is_modified": False,
            "has_structural_change": False,
            "render_dpi": 300,
            "thumbnail_dpi": 96,
        }
    )


def test_document_from_mirror_owns_frontend_view_state() -> None:
    document = document_from_mirror(_mirror())

    assert document.page_count == 2
    assert document.get_page(0) is document.pages[0]
    assert document.get_page(99) is None
    document.pages[0].thumbnail = object()


def test_apply_model_diff_replaces_one_page_and_flags() -> None:
    document = document_from_mirror(_mirror())
    diff = PdfModelDiff.from_payload(
        {
            "replaced_pages": [
                {
                    "page_index": 1,
                    "rotation": 90,
                    "text_layers": [],
                    "ocr_text_blocks": [],
                }
            ],
            "modified_flag": True,
            "structural_flag": False,
            "invalidated_thumbnails": [1],
        }
    )

    invalidated = apply_model_diff(document, diff)

    assert document.pages[1].rotation == 90
    assert document.is_modified is True
    assert invalidated == [1]


def test_apply_model_diff_full_model_invalidates_all_pages() -> None:
    document = PdfDocument()
    diff = PdfModelDiff.from_payload({"full_model": _mirror(page_count=3).to_payload()})

    invalidated = apply_model_diff(document, diff)

    assert document.page_count == 3
    assert invalidated == [0, 1, 2]


def test_pdf_session_keeps_only_frontend_counters() -> None:
    session = PdfSession(
        file_path="C:/docs/input.pdf",
        session_id="session-1",
        pdf_document=document_from_mirror(_mirror()),
    )
    session.loaded_pages.add(0)
    session.add_ocr_stats(written=2, skipped=1)

    assert session.load_progress == 0.5
    assert session.ocr_stats == {"written": 2, "skipped": 1}

    session.reset_ocr_stats()
    assert session.ocr_stats == {"written": 0, "skipped": 0}


def test_coerce_document_mirror_accepts_transitional_payload_provider() -> None:
    class LegacyMirror:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return _mirror(page_count=1).to_payload()

    mirror = coerce_document_mirror(LegacyMirror())

    assert len(mirror.pages) == 1
