"""Tests for SelectionResizeFrame — handle detection, cursor, resize, and constraints."""

from PySide6.QtCore import QPoint, QRect, Qt

from vibeocr.classic.widgets.selection_resize_frame import (
    HandlePosition,
    _apply_resize,
    _constrain_rect,
    _cursor_for_handle,
    _hit_test,
)


class TestHandleDetection:
    def test_no_handle_when_outside(self):
        rect = QRect(100, 100, 400, 300)
        pos = QPoint(50, 50)
        assert _hit_test(pos, rect) == HandlePosition.NONE

    def test_top_left_corner(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(102, 102), rect) == HandlePosition.TOP_LEFT

    def test_top_right_corner(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(498, 102), rect) == HandlePosition.TOP_RIGHT

    def test_bottom_left_corner(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(102, 398), rect) == HandlePosition.BOTTOM_LEFT

    def test_bottom_right_corner(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(498, 398), rect) == HandlePosition.BOTTOM_RIGHT

    def test_top_edge(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(300, 102), rect) == HandlePosition.TOP

    def test_bottom_edge(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(300, 398), rect) == HandlePosition.BOTTOM

    def test_left_edge(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(102, 250), rect) == HandlePosition.LEFT

    def test_right_edge(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(498, 250), rect) == HandlePosition.RIGHT

    def test_inside_returns_none_for_move(self):
        rect = QRect(100, 100, 400, 300)
        assert _hit_test(QPoint(300, 250), rect) == HandlePosition.NONE


class TestCursorMapping:
    def test_top_left_cursor(self):
        c = _cursor_for_handle(HandlePosition.TOP_LEFT)
        assert c == Qt.CursorShape.SizeFDiagCursor

    def test_bottom_right_cursor(self):
        c = _cursor_for_handle(HandlePosition.BOTTOM_RIGHT)
        assert c == Qt.CursorShape.SizeFDiagCursor

    def test_top_right_cursor(self):
        c = _cursor_for_handle(HandlePosition.TOP_RIGHT)
        assert c == Qt.CursorShape.SizeBDiagCursor

    def test_move_cursor(self):
        c = _cursor_for_handle(HandlePosition.MOVE)
        assert c == Qt.CursorShape.SizeAllCursor


class TestApplyResize:
    def test_drag_bottom_right(self):
        original = QRect(100, 100, 200, 150)
        delta = QPoint(50, 30)
        result = _apply_resize(original, HandlePosition.BOTTOM_RIGHT, delta)
        assert result == QRect(100, 100, 250, 180)

    def test_drag_top_left(self):
        original = QRect(100, 100, 200, 150)
        delta = QPoint(20, 10)
        result = _apply_resize(original, HandlePosition.TOP_LEFT, delta)
        assert result == QRect(120, 110, 180, 140)

    def test_drag_right_edge(self):
        original = QRect(100, 100, 200, 150)
        delta = QPoint(40, 0)
        result = _apply_resize(original, HandlePosition.RIGHT, delta)
        assert result == QRect(100, 100, 240, 150)

    def test_move_entire_rect(self):
        original = QRect(100, 100, 200, 150)
        delta = QPoint(30, -20)
        result = _apply_resize(original, HandlePosition.MOVE, delta)
        assert result == QRect(130, 80, 200, 150)


class TestConstrainRect:
    def test_within_bounds_unchanged(self):
        bounds = QRect(0, 0, 1920, 1080)
        rect = QRect(100, 100, 400, 300)
        assert _constrain_rect(rect, bounds, 50) == rect

    def test_clamped_to_bounds(self):
        bounds = QRect(0, 0, 1920, 1080)
        rect = QRect(1800, 1000, 200, 150)
        result = _constrain_rect(rect, bounds, 50)
        assert result.right() <= 1920
        assert result.bottom() <= 1080

    def test_min_size_enforced(self):
        bounds = QRect(0, 0, 1920, 1080)
        rect = QRect(100, 100, 20, 20)
        result = _constrain_rect(rect, bounds, 50)
        assert result.width() >= 50
        assert result.height() >= 50
