# src/vibeocr/widgets/screen_coordinate_mapper.py
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPixmap


@dataclass
class ScreenInfo:
    geometry: QRect
    dpr: float
    grab: QPixmap
    offset: QPoint


class ScreenCoordinateMapper:
    def __init__(
        self, screens: list[ScreenInfo], screenshot_dpr: float | None = None
    ) -> None:
        self._screens = screens
        self._screenshot_dpr = (
            screenshot_dpr if screenshot_dpr is not None else self.max_dpr
        )
        if screens:
            vg = screens[0].geometry
            for s in screens[1:]:
                vg = vg.united(s.geometry)
            self._virtual_geometry = vg
        else:
            self._virtual_geometry = QRect()

    @property
    def virtual_geometry(self) -> QRect:
        return self._virtual_geometry

    @property
    def max_dpr(self) -> float:
        if not self._screens:
            return 1.0
        return max(s.dpr for s in self._screens)

    def screen_at(self, logical_pos: QPoint) -> ScreenInfo | None:
        best = None
        best_area = -1
        for s in self._screens:
            if s.geometry.contains(logical_pos):
                area = s.geometry.width() * s.geometry.height()
                if best is None or area < best_area:
                    best = s
                    best_area = area
        return best

    def dpr_at(self, logical_pos: QPoint) -> float:
        info = self.screen_at(logical_pos)
        if info is not None:
            return info.dpr
        return self.max_dpr

    def logical_to_physical(self, pos: QPoint) -> QPoint:
        dpr = self.dpr_at(pos)
        return QPoint(round(pos.x() * dpr), round(pos.y() * dpr))

    def physical_to_logical(self, phys_pos: QPoint, dpr: float) -> QPointF:
        return QPointF(phys_pos.x() / dpr, phys_pos.y() / dpr)

    def logical_rect_to_physical(self, rect: QRect) -> QRect:
        dpr = self.dpr_at(rect.topLeft())
        px = round(rect.x() * dpr)
        py = round(rect.y() * dpr)
        pw = round(rect.width() * dpr)
        ph = round(rect.height() * dpr)
        return QRect(px, py, pw, ph)

    @property
    def screenshot_dpr(self) -> float:
        return self._screenshot_dpr

    def logical_to_screenshot_physical(self, rect: QRect) -> QRect:
        """逻辑坐标 → 合并截图的物理像素坐标（统一使用 screenshot_dpr）"""
        dpr = self._screenshot_dpr
        return QRect(
            round(rect.x() * dpr),
            round(rect.y() * dpr),
            round(rect.width() * dpr),
            round(rect.height() * dpr),
        )

    def logical_to_screenshot_physical_point(self, pos: QPoint) -> QPoint:
        """单点逻辑坐标 → 合并截图的物理像素坐标（统一使用 screenshot_dpr）。

        合并截图在 ``start_capture`` 中统一按 ``max_dpr`` 渲染，每块屏的像素被
        等比例拉伸到 ``max_dpr`` 空间，因此**任何屏幕上**的点都必须用统一的
        ``screenshot_dpr`` 换算，而非 per-screen dpr（否则在混合 DPR 多屏下，
        低 DPR 屏上的坐标会被映射到错误的物理位置）。与处理单屏 grab 的
        :meth:`logical_to_physical`（per-screen）区分使用。
        """
        dpr = self._screenshot_dpr
        return QPoint(round(pos.x() * dpr), round(pos.y() * dpr))

    def sample_pixel(self, logical_pos: QPoint) -> QColor:
        info = self.screen_at(logical_pos)
        if info is None:
            return QColor(0, 0, 0)
        local_x = logical_pos.x() - info.offset.x()
        local_y = logical_pos.y() - info.offset.y()
        phys_x = round(local_x * info.dpr)
        phys_y = round(local_y * info.dpr)
        image: QImage = info.grab.toImage()
        if 0 <= phys_x < image.width() and 0 <= phys_y < image.height():
            return QColor(image.pixelColor(phys_x, phys_y))
        return QColor(0, 0, 0)

    def clip_to_virtual(self, rect: QRect) -> QRect:
        return rect.intersected(self._virtual_geometry)
