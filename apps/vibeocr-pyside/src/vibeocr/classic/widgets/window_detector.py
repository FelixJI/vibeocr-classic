"""WindowDetector — 通过 Win32 API 检测鼠标下的窗口和子控件边界。

仅 Windows 平台可用。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect

if TYPE_CHECKING:
    from vibeocr.classic.widgets.screen_coordinate_mapper import ScreenCoordinateMapper

if sys.platform != "win32":
    raise ImportError("WindowDetector is only available on Windows")

user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


GA_ROOT = 2


class _Win32Bindings:
    def WindowFromPoint(self, point: _POINT) -> int:
        return user32.WindowFromPoint(point)

    def GetAncestor(self, hwnd: int, flags: int) -> int:
        return user32.GetAncestor(hwnd, flags)

    def IsWindowVisible(self, hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))

    def GetWindowRect(self, hwnd: int, rect: ctypes.wintypes.RECT) -> bool:
        return bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))


_win = _Win32Bindings()


class WindowDetector:
    def __init__(self, overlay_hwnd: int) -> None:
        self._overlay_hwnd = overlay_hwnd
        self._cached_hwnd: int | None = None
        self._cached_rect: QRect | None = None

    def detect_at(
        self,
        pos: QPoint,
        mapper: ScreenCoordinateMapper,
    ) -> QRect | None:
        dpr = mapper.dpr_at(pos)
        vg = mapper.virtual_geometry
        physical_x = round(pos.x() * dpr) + round(vg.x() * dpr)
        physical_y = round(pos.y() * dpr) + round(vg.y() * dpr)
        hwnd = self._hit_test((physical_x, physical_y))
        if hwnd is None:
            self._cached_hwnd = None
            self._cached_rect = None
            return None

        rect = self._get_control_rect(hwnd, (physical_x, physical_y))
        if rect is None:
            rect = self._get_window_rect(hwnd)
        if rect is None:
            return None

        logical = QRect(
            round((rect.x() - vg.x()) / dpr),
            round((rect.y() - vg.y()) / dpr),
            round(rect.width() / dpr),
            round(rect.height() / dpr),
        )
        # 裁剪到虚拟桌面范围
        logical = mapper.clip_to_virtual(logical)
        if logical.isEmpty():
            return None

        self._cached_hwnd = hwnd
        self._cached_rect = logical
        return logical

    def _hit_test(self, physical_pos: tuple[int, int]) -> int | None:
        point = _POINT(physical_pos[0], physical_pos[1])
        hwnd = _win.WindowFromPoint(point)
        if hwnd == 0:
            return None

        root = _win.GetAncestor(hwnd, GA_ROOT)
        if root == 0:
            root = hwnd

        if root == self._overlay_hwnd:
            return None

        if not _win.IsWindowVisible(root):
            return None

        return root

    def _get_control_rect(
        self, hwnd: int, physical_pos: tuple[int, int]
    ) -> QRect | None:
        rect = self._try_accessible(physical_pos)
        if rect is not None:
            return rect
        return self._try_enum_children(hwnd, physical_pos)

    def _try_accessible(self, physical_pos: tuple[int, int]) -> QRect | None:
        try:
            import oleacc  # type: ignore[import-untyped]

            hr, accessible, _child_id = oleacc.AccessibleObjectFromPoint(
                physical_pos[0], physical_pos[1]
            )
            if hr != 0 or accessible is None:
                return None
            location = accessible.accLocation(0)
            if location is None:
                return None
            left, top, width, height = location
            if width <= 0 or height <= 0:
                return None
            return QRect(int(left), int(top), int(width), int(height))
        except Exception:
            return None

    def _try_enum_children(
        self, hwnd: int, physical_pos: tuple[int, int]
    ) -> QRect | None:
        children_rects: list[QRect] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_callback(child_hwnd: int, _lparam: int) -> bool:
            rect = ctypes.wintypes.RECT()
            if user32.GetWindowRect(child_hwnd, ctypes.byref(rect)):
                children_rects.append(
                    QRect(
                        rect.left,
                        rect.top,
                        rect.right - rect.left,
                        rect.bottom - rect.top,
                    )
                )
            return True

        user32.EnumChildWindows(hwnd, enum_callback, 0)

        if not children_rects:
            return None

        px, py = physical_pos
        smallest: QRect | None = None
        for qrect in children_rects:
            if qrect.contains(QPoint(px, py)):
                if smallest is None or (qrect.width() * qrect.height()) < (
                    smallest.width() * smallest.height()
                ):
                    smallest = qrect
        return smallest

    def _get_window_rect(self, hwnd: int) -> QRect | None:
        rect = ctypes.wintypes.RECT()
        result = _win.GetWindowRect(hwnd, rect)
        if not result:
            return None
        return QRect(
            rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        )
