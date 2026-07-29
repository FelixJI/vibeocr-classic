"""Tests for WindowDetector."""

import ctypes.wintypes
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPoint, QRect

from vibeocr.classic.widgets.window_detector import WindowDetector


def _make_mapper(dpr: float = 1.0, virtual_offset: QPoint | None = None) -> MagicMock:
    mapper = MagicMock()
    mapper.dpr_at.return_value = dpr
    mapper.virtual_geometry = QRect(virtual_offset or QPoint(0, 0), QPoint(9999, 9999))
    mapper.clip_to_virtual.side_effect = lambda r: r
    return mapper


@pytest.fixture
def detector(qapp):
    overlay_hwnd = 12345
    return WindowDetector(overlay_hwnd)


class _MockWin32:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def WindowFromPoint(self, point):
        return self._kwargs.get("window_from_point_result", 0)

    def GetAncestor(self, hwnd, flags):
        return self._kwargs.get("ancestor_result", hwnd)

    def IsWindowVisible(self, hwnd):
        return self._kwargs.get("is_visible", False)

    def GetWindowRect(self, hwnd, rect):
        result = self._kwargs.get("get_window_rect_result")
        if result is None:
            return False
        rect.left = result.left
        rect.top = result.top
        rect.right = result.right
        rect.bottom = result.bottom
        return True


class TestWindowDetectorInit:
    def test_stores_overlay_hwnd(self, detector):
        assert detector._overlay_hwnd == 12345

    def test_initial_cache_is_none(self, detector):
        assert detector._cached_hwnd is None
        assert detector._cached_rect is None


class TestHitTest:
    def test_returns_none_when_no_window(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.classic.widgets.window_detector._win",
            _MockWin32(window_from_point_result=0),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_filters_overlay_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.classic.widgets.window_detector._win",
            _MockWin32(window_from_point_result=12345, ancestor_result=12345),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_returns_root_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.classic.widgets.window_detector._win",
            _MockWin32(
                window_from_point_result=999, ancestor_result=888, is_visible=True
            ),
        )
        result = detector._hit_test((100, 200))
        assert result == 888


class TestGetControlRect:
    def test_returns_accessible_result(self, detector):
        detector._try_accessible = lambda pos: QRect(110, 210, 180, 120)
        result = detector._get_control_rect(888, (200, 250))
        assert result == QRect(110, 210, 180, 120)

    def test_falls_back_to_enum_children(self, detector):
        detector._try_accessible = lambda pos: None
        detector._try_enum_children = lambda hwnd, pos: QRect(160, 230, 120, 110)
        result = detector._get_control_rect(888, (200, 250))
        assert result == QRect(160, 230, 120, 110)

    def test_returns_none_when_both_fail(self, detector):
        detector._try_accessible = lambda pos: None
        detector._try_enum_children = lambda hwnd, pos: None
        result = detector._get_control_rect(888, (200, 250))
        assert result is None


class TestGetWindowRect:
    def test_returns_rect_for_valid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.classic.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=ctypes.wintypes.RECT(100, 200, 500, 400)),
        )
        result = detector._get_window_rect(888)
        assert result == QRect(100, 200, 400, 200)

    def test_returns_none_for_invalid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.classic.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=None),
        )
        result = detector._get_window_rect(0)
        assert result is None


class TestDetectAt:
    def test_returns_logical_rect_with_dpr_and_offset(self, detector, monkeypatch):
        detector._try_accessible = lambda pos: QRect(200, 400, 400, 400)
        detector._hit_test = lambda pos: 888
        pos = QPoint(50, 100)
        result = detector.detect_at(pos, _make_mapper(dpr=2.0))
        assert result is not None
        assert result.x() == 100
        assert result.y() == 200
        assert result.width() == 200
        assert result.height() == 200

    def test_returns_none_when_no_window(self, detector, monkeypatch):
        detector._hit_test = lambda pos: None
        result = detector.detect_at(QPoint(50, 50), _make_mapper())
        assert result is None


class TestDetectAtCache:
    def test_caches_result(self, detector, monkeypatch):
        detector._try_accessible = lambda pos: QRect(100, 200, 400, 200)
        detector._hit_test = lambda pos: 888
        pos = QPoint(50, 50)
        r1 = detector.detect_at(pos, _make_mapper())
        assert detector._cached_hwnd == 888
        assert detector._cached_rect == r1

    def test_cache_cleared_on_miss(self, detector):
        detector._cached_hwnd = 999
        detector._cached_rect = QRect(10, 10, 100, 100)
        detector._hit_test = lambda pos: None
        detector.detect_at(QPoint(50, 50), _make_mapper())
        assert detector._cached_hwnd is None
        assert detector._cached_rect is None
