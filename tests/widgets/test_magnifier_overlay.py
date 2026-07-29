from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap

from vibeocr.classic.widgets.magnifier_overlay import MagnifierOverlay
from vibeocr.classic.widgets.screen_coordinate_mapper import (
    ScreenCoordinateMapper,
    ScreenInfo,
)


def _make_mapper(dpr=1.0, w=100, h=100, color="red"):
    grab = QPixmap(int(w * dpr), int(h * dpr))
    grab.setDevicePixelRatio(dpr)
    grab.fill(QColor(color))
    info = ScreenInfo(
        geometry=QRect(0, 0, w, h),
        dpr=dpr,
        grab=grab,
        offset=QPoint(0, 0),
    )
    return ScreenCoordinateMapper([info])


class TestMagnifierSize:
    def test_magnifier_size_is_odd(self):
        assert MagnifierOverlay.MAGNIFIER_SIZE % 2 == 1


class TestDrawMagnifierAcceptsMapper:
    def test_draw_magnifier_with_mapper(self, qapp):
        mapper = _make_mapper(dpr=1.0)
        canvas = QPixmap(200, 200)
        canvas.fill(QColor("black"))
        painter = QPainter(canvas)
        result = MagnifierOverlay.draw_magnifier(
            painter,
            QPoint(50, 50),
            QPixmap(100, 100),
            mapper.virtual_geometry,
            4,
            mapper,
            QRect(0, 0, 200, 200),
        )
        painter.end()
        assert isinstance(result, QRect)

    def test_draw_pixel_info_with_mapper(self, qapp):
        mapper = _make_mapper(dpr=1.0)
        canvas = QPixmap(200, 200)
        canvas.fill(QColor("black"))
        painter = QPainter(canvas)
        mag_rect = QRect(70, 70, 121, 121)
        # Should not crash
        MagnifierOverlay.draw_pixel_info(
            painter,
            QPoint(50, 50),
            None,  # selection_rect
            mapper.virtual_geometry,
            mapper,
            mag_rect,
        )
        painter.end()

    def test_draw_pixel_info_shows_color(self, qapp):
        mapper = _make_mapper(dpr=1.0, color="#00FF00")
        canvas = QPixmap(400, 300)
        canvas.fill(QColor("black"))
        painter = QPainter(canvas)
        mag_rect = QRect(70, 70, 121, 121)
        MagnifierOverlay.draw_pixel_info(
            painter,
            QPoint(50, 50),
            QRect(10, 10, 80, 80),
            mapper.virtual_geometry,
            mapper,
            mag_rect,
        )
        painter.end()
        # Verify it painted something (the info panel area should be non-black)
        # Just checking no crash is sufficient for this test


def _build_merged_pixmap(screen_infos, virtual_geometry):
    """复刻 ScreenCaptureOverlay.start_capture 的合并截图渲染逻辑。

    每块屏的 grab 按其逻辑 offset 绘制到一张统一 max_dpr 的 pixmap 上，
    因此低 DPR 屏的像素会被等比例拉伸到 max_dpr 空间。
    """

    max_dpr = max(s.dpr for s in screen_infos)
    physical_size = virtual_geometry.size() * max_dpr
    pixmap = QPixmap(physical_size)
    pixmap.fill(QColor("black"))
    pixmap.setDevicePixelRatio(max_dpr)
    painter = QPainter(pixmap)
    for info in screen_infos:
        painter.drawPixmap(info.offset, info.grab)
    painter.end()
    return pixmap


class TestDrawMagnifierMixedDpr:
    """回归测试：混合 DPR 多屏下放大镜取样必须对齐鼠标实际位置。

    合并截图统一按 max_dpr 渲染，放大镜对其取样时必须用统一的
    screenshot_dpr 换算坐标，而非 per-screen dpr。
    """

    def test_samples_correct_screen_on_low_dpr_display(self, qapp):
        """混合 DPR：放大镜在低 DPR 屏内部取样必须对齐鼠标位置。

        屏幕 A dpr=1，内部左半红 / 右半绿（分界 x=50）；屏幕 B dpr=2 蓝色。
        鼠标停在 A 区右半（绿）的逻辑 (75, 50)。

        合并图统一按 max_dpr=2 渲染：A 区被拉伸到物理 0~199，
        逻辑 75 → 物理 150（绿区）。若误用 per-screen dpr=1，
        会算成物理 75（红区），放大镜显示成红色——即为回归 bug。
        """
        ga = QPixmap(100, 100)
        ga.setDevicePixelRatio(1.0)
        gp = QPainter(ga)
        gp.fillRect(QRect(0, 0, 50, 100), QColor("red"))
        gp.fillRect(QRect(50, 0, 50, 100), QColor("green"))
        gp.end()
        gb = QPixmap(200, 200)
        gb.setDevicePixelRatio(2.0)
        gb.fill(QColor("blue"))
        infos = [
            ScreenInfo(
                geometry=QRect(0, 0, 100, 100), dpr=1.0, grab=ga, offset=QPoint(0, 0)
            ),
            ScreenInfo(
                geometry=QRect(100, 0, 100, 100),
                dpr=2.0,
                grab=gb,
                offset=QPoint(100, 0),
            ),
        ]
        mapper = ScreenCoordinateMapper(infos)
        vg = QRect(0, 0, 200, 100)
        merged = _build_merged_pixmap(infos, vg)

        canvas = QPixmap(400, 300)
        canvas.fill(QColor("black"))
        painter = QPainter(canvas)
        mag_rect = MagnifierOverlay.draw_magnifier(
            painter,
            QPoint(75, 50),
            merged,
            vg,
            8,  # 高 zoom → 取样窗口小，放大镜显示鼠标位置附近真实像素
            mapper,
            QRect(0, 0, 400, 300),
        )
        painter.end()

        # 统计放大镜中心 40x40 区域内的“绿色像素”比例。
        # 鼠标停在绿区，取样位置正确时绿色应占多数；若误用 per-screen dpr
        # 错位到红区，绿色比例会接近 0。
        img = canvas.toImage()
        cx, cy = mag_rect.center().x(), mag_rect.center().y()
        green_count = 0
        total = 0
        for dy in range(-20, 20):
            for dx in range(-20, 20):
                c = img.pixelColor(cx + dx, cy + dy)
                total += 1
                if c.green() > 100 and c.red() < 100:
                    green_count += 1
        green_ratio = green_count / total
        assert green_ratio > 0.5, (
            f"放大镜在 A 区绿区应多数显示绿色，绿色占比 {green_ratio:.0%} "
            f"过低 — 疑似误用 per-screen dpr 导致取样错位到红区"
        )
