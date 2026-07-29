"""Qt-only PDF rendering helpers for the Classic frontend."""

from __future__ import annotations

import fitz
from PySide6.QtGui import QImage, QPixmap


def render_page_pixmap(
    doc: fitz.Document, page_index: int, dpi: int = 96
) -> QPixmap:
    """Render one page as a detached QPixmap in the UI process."""
    page = doc[page_index]
    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = QImage(
        pixmap.samples,
        pixmap.width,
        pixmap.height,
        pixmap.stride,
        QImage.Format.Format_RGB888,
    )
    return QPixmap.fromImage(image.copy())


__all__ = ["render_page_pixmap"]
