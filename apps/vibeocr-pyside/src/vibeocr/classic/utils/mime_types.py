"""Classic 文件选择与文档路由所需的文件类型 helper。"""

from pathlib import Path

FILE_FILTER_IMAGES = "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
FILE_FILTER_DOCUMENTS = "文档 (*.pdf *.docx *.pptx *.xlsx)"
FILE_FILTER_ALL = (
    "所有支持的格式 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2 "
    "*.pdf *.docx *.pptx *.xlsx)"
)

_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})


def is_document_file(path_or_name: str | Path) -> bool:
    """判断路径是否是 Classic 支持的 PDF 或 Office 文档。"""
    return Path(path_or_name).suffix.lower() in _DOCUMENT_EXTENSIONS
