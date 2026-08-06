"""PDF bbox 到 Classic 预览像素坐标的纯数学转换。"""

from __future__ import annotations

from typing import Protocol, cast


class _PageRectSize(Protocol):
    width: float
    height: float


def bbox_to_pixel(
    bbox: tuple[float, float, float, float],
    page_rect: tuple[float, float, float, float] | _PageRectSize,
    render_dpi: int,
    source: str = "pdf",
    rotation: int = 0,
    mediabox: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """将 PDF points 或归一化 bbox 转换为预览图像像素坐标。"""
    page_width = getattr(page_rect, "width", None)
    if page_width is None:
        page_rect = cast(tuple[float, float, float, float], page_rect)
        page_width = page_rect[2] - page_rect[0]
        page_height = page_rect[3] - page_rect[1]
    else:
        page_height = cast(_PageRectSize, page_rect).height

    if source == "normalized":
        x0 = bbox[0] / 1000 * page_width
        y0 = bbox[1] / 1000 * page_height
        x1 = bbox[2] / 1000 * page_width
        y1 = bbox[3] / 1000 * page_height
    else:
        if mediabox is not None:
            media_width = mediabox[2] - mediabox[0]
            media_height = mediabox[3] - mediabox[1]
        elif rotation in (90, 270):
            media_width = page_height
            media_height = page_width
        else:
            media_width = page_width
            media_height = page_height

        bx0, by0, bx1, by1 = bbox
        if rotation == 90:
            x0, y0 = media_height - by1, bx0
            x1, y1 = media_height - by0, bx1
        elif rotation == 180:
            x0, y0 = media_width - bx1, media_height - by1
            x1, y1 = media_width - bx0, media_height - by0
        elif rotation == 270:
            x0, y0 = by0, media_width - bx1
            x1, y1 = by1, media_width - bx0
        else:
            x0, y0, x1, y1 = bx0, by0, bx1, by1

    scale = render_dpi / 72.0
    return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
