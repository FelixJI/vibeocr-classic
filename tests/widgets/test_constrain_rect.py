from PySide6.QtCore import QRect

from vibeocr.classic.widgets.selection_resize_frame import _constrain_rect


class TestConstrainRect:
    def test_normal_case_inside_bounds(self):
        bounds = QRect(0, 0, 1000, 1000)
        rect = QRect(10, 10, 30, 30)
        result = _constrain_rect(rect, bounds, min_size=10)
        assert result == rect

    def test_rect_exceeds_left_bound(self):
        bounds = QRect(0, 0, 1000, 1000)
        rect = QRect(-50, 10, 100, 50)
        result = _constrain_rect(rect, bounds, min_size=10)
        assert result.left() == 0
        assert result.width() >= 10

    def test_rect_exceeds_all_bounds(self):
        bounds = QRect(0, 0, 100, 100)
        rect = QRect(-10, -10, 200, 200)
        result = _constrain_rect(rect, bounds, min_size=10)
        assert result.left() >= 0
        assert result.top() >= 0
        assert result.right() <= 100
        assert result.bottom() <= 100

    def test_bounds_too_small_for_min_size(self):
        """当边界本身小于 min_size 时，边界优先"""
        bounds = QRect(0, 0, 5, 5)
        rect = QRect(-2, -2, 3, 3)
        result = _constrain_rect(rect, bounds, min_size=50)
        # 边界优先 — 结果不应超出 bounds
        assert result.left() >= 0
        assert result.top() >= 0
        assert result.right() <= 5
        assert result.bottom() <= 5

    def test_min_size_respected_when_space_available(self):
        bounds = QRect(0, 0, 1000, 1000)
        rect = QRect(500, 500, 3, 3)
        result = _constrain_rect(rect, bounds, min_size=10)
        assert result.width() >= 10
        assert result.height() >= 10
