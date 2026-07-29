# tests/widgets/test_screen_coordinate_mapper.py
from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QColor, QPixmap

from vibeocr.classic.widgets.screen_coordinate_mapper import (
    ScreenCoordinateMapper,
    ScreenInfo,
)


def _make_screen_info(x=0, y=0, w=1920, h=1080, dpr=1.0, color=None) -> ScreenInfo:
    geometry = QRect(x, y, w, h)
    grab = QPixmap(int(w * dpr), int(h * dpr))
    grab.setDevicePixelRatio(dpr)
    grab.fill(QColor(color if color else "black"))
    return ScreenInfo(
        geometry=geometry,
        dpr=dpr,
        grab=grab,
        offset=QPoint(x, y),
    )


class TestVirtualGeometry:
    def test_single_screen(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.virtual_geometry == QRect(0, 0, 1920, 1080)

    def test_dual_screen_horizontal(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080),
            _make_screen_info(1920, 0, 1920, 1080),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.virtual_geometry == QRect(0, 0, 3840, 1080)

    def test_mixed_dpr_max(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080, dpr=1.0),
            _make_screen_info(1920, 0, 1920, 1080, dpr=2.0),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.max_dpr == 2.0


class TestScreenAt:
    def test_single_screen(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.screen_at(QPoint(100, 100)) is screens[0]

    def test_dual_screen_right(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080),
            _make_screen_info(1920, 0, 1920, 1080),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.screen_at(QPoint(2000, 100)) is screens[1]

    def test_outside_returns_none(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.screen_at(QPoint(3000, 3000)) is None


class TestDprAt:
    def test_mixed_dpr_first_screen(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080, dpr=1.0),
            _make_screen_info(1920, 0, 1920, 1080, dpr=2.0),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.dpr_at(QPoint(100, 100)) == 1.0

    def test_mixed_dpr_second_screen(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080, dpr=1.0),
            _make_screen_info(1920, 0, 1920, 1080, dpr=2.0),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.dpr_at(QPoint(2000, 100)) == 2.0

    def test_outside_returns_max_dpr(self, qapp):
        screens = [
            _make_screen_info(0, 0, 1920, 1080, dpr=1.0),
            _make_screen_info(1920, 0, 1920, 1080, dpr=2.0),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.dpr_at(QPoint(9999, 9999)) == 2.0


class TestLogicalToPhysical:
    def test_dpr_1(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=1.0)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.logical_to_physical(QPoint(100, 200))
        assert result == QPoint(100, 200)

    def test_dpr_2(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=2.0)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.logical_to_physical(QPoint(100, 200))
        assert result == QPoint(200, 400)

    def test_qround_not_truncation(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=1.5)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.logical_to_physical(QPoint(1, 1))
        assert result == QPoint(2, 2)  # qRound(1.5) == 2, not int(1.5) == 1


class TestPhysicalToLogical:
    def test_dpr_2(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=2.0)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.physical_to_logical(QPoint(200, 400), 2.0)
        assert result == QPointF(100.0, 200.0)


class TestLogicalRectToPhysical:
    def test_dpr_2(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=2.0)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.logical_rect_to_physical(QRect(10, 20, 100, 50))
        assert result == QRect(20, 40, 200, 100)


class TestLogicalToScreenshotPhysicalPoint:
    """单点逻辑坐标 → 合并截图物理坐标（统一使用 screenshot_dpr）。

    合并截图统一按 max_dpr 渲染，每块屏的像素被等比例拉伸到 max_dpr 空间，
    因此任何屏幕上的点都必须用统一的 screenshot_dpr 换算，而非 per-screen dpr。
    """

    def test_single_screen_dpr_2(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=2.0)]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.logical_to_screenshot_physical_point(QPoint(100, 200)) == QPoint(
            200, 400
        )

    def test_round_not_truncation(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080, dpr=1.5)]
        mapper = ScreenCoordinateMapper(screens)
        # screenshot_dpr == max_dpr == 1.5 → 1*1.5=1.5 → round 为 2
        assert mapper.logical_to_screenshot_physical_point(QPoint(1, 1)) == QPoint(2, 2)

    def test_mixed_dpr_uses_unified_screenshot_dpr(self, qapp):
        """混合 DPR：低 DPR 屏上的点也按统一的 max_dpr 换算。

        屏幕 A dpr=1 @ (0,0)，屏幕 B dpr=2 @ (1920,0)。
        合并图 max_dpr=2.0，故 A 区逻辑 (100,100) → 物理 (200,200)，
        B 区逻辑 (2000,100) → 物理 (4000,200)。
        """
        screens = [
            _make_screen_info(0, 0, 1920, 1080, dpr=1.0),
            _make_screen_info(1920, 0, 1920, 1080, dpr=2.0),
        ]
        mapper = ScreenCoordinateMapper(screens)
        assert mapper.screenshot_dpr == 2.0
        # A 区：不能用 per-screen dpr=1 算成 (100,100)
        assert mapper.logical_to_screenshot_physical_point(QPoint(100, 100)) == QPoint(
            200, 200
        )
        # B 区
        assert mapper.logical_to_screenshot_physical_point(QPoint(2000, 100)) == QPoint(
            4000, 200
        )


class TestSamplePixel:
    def test_returns_pixel_color_from_grab(self, qapp):
        screens = [_make_screen_info(0, 0, 100, 100, dpr=1.0, color="#FF0000")]
        mapper = ScreenCoordinateMapper(screens)
        color = mapper.sample_pixel(QPoint(50, 50))
        assert color.red() == 255
        assert color.green() == 0
        assert color.blue() == 0

    def test_mixed_dpr_samples_from_correct_screen(self, qapp):
        screens = [
            _make_screen_info(0, 0, 100, 100, dpr=1.0, color="#00FF00"),
            _make_screen_info(100, 0, 100, 100, dpr=2.0, color="#0000FF"),
        ]
        mapper = ScreenCoordinateMapper(screens)
        c1 = mapper.sample_pixel(QPoint(50, 50))
        c2 = mapper.sample_pixel(QPoint(150, 50))
        assert c1.green() == 255
        assert c2.blue() == 255


class TestClipToVirtual:
    def test_clips_overlapping_rect(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.clip_to_virtual(QRect(-10, -10, 2000, 1100))
        assert result == QRect(0, 0, 1920, 1080)

    def test_already_inside(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        rect = QRect(100, 100, 200, 200)
        assert mapper.clip_to_virtual(rect) == rect

    def test_completely_outside(self, qapp):
        screens = [_make_screen_info(0, 0, 1920, 1080)]
        mapper = ScreenCoordinateMapper(screens)
        result = mapper.clip_to_virtual(QRect(2000, 2000, 100, 100))
        assert result.isEmpty()
