"""选区边界拖拽手柄框架

在 EDITING 阶段覆盖在画布周围的控件，提供 8 个拖拽手柄
用于调整选区大小和位置。

控件始终覆盖整个虚拟桌面，内部用 _selection_rect 记录选区位置，
避免拖拽时改变控件几何导致的闪烁。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QApplication, QWidget


class HandlePosition(Enum):
    NONE = "none"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    MOVE = "move"


# 手柄尺寸（半边长，即检测半径）
_HANDLE_HALF = 5

# 边框区域宽度：鼠标在此范围内才触发 MOVE
_BORDER_WIDTH = 8


def _handle_positions(rect: QRect) -> dict[HandlePosition, QPoint]:
    """返回 8 个手柄的中心坐标"""
    return {
        HandlePosition.TOP_LEFT: rect.topLeft(),
        HandlePosition.TOP_RIGHT: rect.topRight(),
        HandlePosition.BOTTOM_LEFT: rect.bottomLeft(),
        HandlePosition.BOTTOM_RIGHT: rect.bottomRight(),
        HandlePosition.TOP: QPoint(rect.x() + rect.width() // 2, rect.top()),
        HandlePosition.BOTTOM: QPoint(rect.x() + rect.width() // 2, rect.bottom()),
        HandlePosition.LEFT: QPoint(rect.left(), rect.y() + rect.height() // 2),
        HandlePosition.RIGHT: QPoint(rect.right(), rect.y() + rect.height() // 2),
    }


def _is_in_border_zone(pos: QPoint, rect: QRect, border: int = _BORDER_WIDTH) -> bool:
    """判断 pos 是否在 rect 的边框环形区域内（非内部深层区域）"""
    if not rect.contains(pos):
        return False
    inner = rect.adjusted(border, border, -border, -border)
    if inner.width() <= 0 or inner.height() <= 0:
        return True  # 选区太小，整个区域都算边框
    return not inner.contains(pos)


def _hit_test(pos: QPoint, rect: QRect) -> HandlePosition:
    """检测 pos 命中了哪个手柄"""
    handles = _handle_positions(rect)
    for hp, center in handles.items():
        if (
            abs(pos.x() - center.x()) <= _HANDLE_HALF
            and abs(pos.y() - center.y()) <= _HANDLE_HALF
        ):
            return hp
    return HandlePosition.NONE


def _cursor_for_handle(handle: HandlePosition) -> Qt.CursorShape:
    """返回手柄对应的光标形状"""
    mapping = {
        HandlePosition.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
        HandlePosition.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
        HandlePosition.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
        HandlePosition.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
        HandlePosition.TOP: Qt.CursorShape.SizeVerCursor,
        HandlePosition.BOTTOM: Qt.CursorShape.SizeVerCursor,
        HandlePosition.LEFT: Qt.CursorShape.SizeHorCursor,
        HandlePosition.RIGHT: Qt.CursorShape.SizeHorCursor,
        HandlePosition.MOVE: Qt.CursorShape.SizeAllCursor,
        HandlePosition.NONE: Qt.CursorShape.ArrowCursor,
    }
    return mapping.get(handle, Qt.CursorShape.ArrowCursor)


def _apply_resize(original: QRect, handle: HandlePosition, delta: QPoint) -> QRect:
    """根据手柄位置和鼠标 delta 计算新矩形"""
    r = QRect(original)
    if handle == HandlePosition.MOVE:
        r.translate(delta)
    elif handle == HandlePosition.TOP_LEFT:
        r.setTopLeft(r.topLeft() + delta)
    elif handle == HandlePosition.TOP_RIGHT:
        r.setTopRight(r.topRight() + delta)
    elif handle == HandlePosition.BOTTOM_LEFT:
        r.setBottomLeft(r.bottomLeft() + delta)
    elif handle == HandlePosition.BOTTOM_RIGHT:
        r.setBottomRight(r.bottomRight() + delta)
    elif handle == HandlePosition.TOP:
        r.setTop(r.top() + delta.y())
    elif handle == HandlePosition.BOTTOM:
        r.setBottom(r.bottom() + delta.y())
    elif handle == HandlePosition.LEFT:
        r.setLeft(r.left() + delta.x())
    elif handle == HandlePosition.RIGHT:
        r.setRight(r.right() + delta.x())
    return r.normalized()


def _constrain_rect(rect: QRect, bounds: QRect, min_size: int) -> QRect:
    """约束矩形在边界内并保证最小尺寸（边界优先）"""
    r = QRect(rect)

    # 1. 先约束到边界
    if r.left() < bounds.left():
        r.setLeft(bounds.left())
    if r.top() < bounds.top():
        r.setTop(bounds.top())
    if r.right() > bounds.right():
        r.setRight(bounds.right())
    if r.bottom() > bounds.bottom():
        r.setBottom(bounds.bottom())

    # 2. 再尝试保证最小尺寸（不超出边界）
    if r.width() < min_size:
        available_right = bounds.right() - r.left()
        if available_right >= min_size:
            r.setWidth(min_size)
        else:
            r.setRight(bounds.right())

    if r.height() < min_size:
        available_bottom = bounds.bottom() - r.top()
        if available_bottom >= min_size:
            r.setHeight(min_size)
        else:
            r.setBottom(bounds.bottom())

    return r.normalized()


class SelectionResizeFrame(QWidget):
    """选区边界拖拽手柄框架

    控件始终覆盖整个虚拟桌面，通过 _selection_rect 记录当前选区位置。
    拖拽时只更新 _selection_rect 并重绘，不改变控件几何，避免闪烁。
    """

    selection_changed = Signal(QRect)
    selection_finalized = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        virtual_geometry: QRect | None = None,
        min_size: int = 50,
        forward_target: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._virtual_geometry = virtual_geometry or QRect()
        self._min_size = min_size
        self._forward_target = forward_target
        self._selection_rect = QRect()
        self._active_handle = HandlePosition.NONE
        self._drag_start_pos: QPoint | None = None
        self._drag_start_rect: QRect | None = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 覆盖整个虚拟桌面
        if virtual_geometry:
            self.setGeometry(virtual_geometry)

    def set_initial_selection(self, rect: QRect) -> None:
        """设置初始选区并显示控件"""
        self._selection_rect = QRect(rect)
        self.update()

    def sync_selection(self, rect: QRect) -> None:
        """外部调用：将选区同步到新位置（不改变控件几何）"""
        self._selection_rect = QRect(rect)
        self.update()

    def _forward_event(self, event: QMouseEvent) -> None:
        """将鼠标事件转发到下层画布，坐标映射到目标控件的局部坐标系"""
        if not self._forward_target:
            event.ignore()
            return

        # 将事件坐标从本控件映射到目标控件
        local_pos = self._forward_target.mapFrom(self, event.pos())
        new_event = QMouseEvent(
            event.type(),
            QPointF(local_pos),
            event.globalPosition(),
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        # 发送到 viewport 而非 QGraphicsView 本身，因为 QAbstractScrollArea
        # 的事件路由要求鼠标事件经过 viewport 才能到达 mousePressEvent
        target = self._forward_target
        from PySide6.QtWidgets import QAbstractScrollArea

        if isinstance(target, QAbstractScrollArea):
            target = target.viewport()
        QApplication.sendEvent(target, new_event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._selection_rect.isEmpty():
            return

        painter = QPainter(self)
        sr = self._selection_rect

        # 边框：白色半透明虚线
        pen = QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(sr.adjusted(0, 0, -1, -1))

        # 8 个手柄
        handles = _handle_positions(sr)
        handle_pen = QPen(QColor(255, 255, 255), 1)
        handle_brush = QColor(0, 120, 215)
        for pos in handles.values():
            painter.setPen(handle_pen)
            painter.setBrush(handle_brush)
            painter.drawRect(
                pos.x() - _HANDLE_HALF,
                pos.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2,
                _HANDLE_HALF * 2,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            self._forward_event(event)
            return

        pos = event.pos()
        handle = _hit_test(pos, self._selection_rect)
        if handle != HandlePosition.NONE:
            self._active_handle = handle
        elif _is_in_border_zone(pos, self._selection_rect):
            self._active_handle = HandlePosition.MOVE
        else:
            self._forward_event(event)
            return

        self._drag_start_pos = pos
        self._drag_start_rect = QRect(self._selection_rect)
        self.setCursor(_cursor_for_handle(self._active_handle))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active_handle == HandlePosition.NONE:
            handle = _hit_test(event.pos(), self._selection_rect)
            if handle == HandlePosition.NONE and _is_in_border_zone(
                event.pos(), self._selection_rect
            ):
                handle = HandlePosition.MOVE
            if handle == HandlePosition.NONE and self._forward_target:
                self.setCursor(self._forward_target.cursor())
            else:
                self.setCursor(_cursor_for_handle(handle))
            self._forward_event(event)
            return

        if not self._drag_start_pos or not self._drag_start_rect:
            return

        delta = event.pos() - self._drag_start_pos
        new_rect = _apply_resize(self._drag_start_rect, self._active_handle, delta)
        new_rect = _constrain_rect(new_rect, self._virtual_geometry, self._min_size)
        self.selection_changed.emit(new_rect)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._active_handle != HandlePosition.NONE
        ):
            self._active_handle = HandlePosition.NONE
            self._drag_start_pos = None
            self._drag_start_rect = None
            self.selection_finalized.emit()
            return
        self._forward_event(event)
