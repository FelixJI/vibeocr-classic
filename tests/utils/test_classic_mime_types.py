"""Classic 文件选择与文档路由所需的 MIME helper 契约。"""

from vibeocr.classic.utils.mime_types import (
    FILE_FILTER_ALL,
    FILE_FILTER_DOCUMENTS,
    FILE_FILTER_IMAGES,
    is_document_file,
)


def test_file_filters_cover_supported_frontend_inputs() -> None:
    assert FILE_FILTER_IMAGES == (
        "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
    )
    assert FILE_FILTER_DOCUMENTS == "文档 (*.pdf *.docx *.pptx *.xlsx)"
    assert FILE_FILTER_ALL == (
        "所有支持的格式 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp "
        "*.jp2 *.pdf *.docx *.pptx *.xlsx)"
    )


def test_is_document_file_is_case_insensitive() -> None:
    assert is_document_file("C:/documents/report.PDF") is True
    assert is_document_file("C:/documents/slides.pptx") is True
    assert is_document_file("C:/images/scan.png") is False
