"""Tests for annotation items — MosaicItem and BlurItem update_background."""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPixmap, Qt

from vibeocr.classic.widgets.editor.annotation_items import (
    BlurItem,
    EllipseAnnotation,
    MosaicItem,
    RectAnnotation,
)


def _make_pixmap(w: int = 200, h: int = 200) -> QPixmap:
    px = QPixmap(w, h)
    px.fill(QColor(100, 150, 200))
    return px


class TestMosaicItemUpdateBackground:
    def test_update_background_regenerates(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(10, 10, 80, 80), bg, strength=8)

        new_bg = _make_pixmap(300, 300)
        new_bg.fill(QColor(200, 50, 50))
        item.update_background(new_bg)

        assert item._background_pixmap is new_bg
        assert item._cached_mosaic is not None


class TestBlurItemUpdateBackground:
    def test_update_background_regenerates(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(10, 10, 80, 80), bg, radius=10)

        new_bg = _make_pixmap(300, 300)
        new_bg.fill(QColor(200, 50, 50))
        item.update_background(new_bg)

        assert item._background_pixmap is new_bg
        assert item._cached_blur is not None


class TestRectAnnotationSetters:
    def test_set_pen_color(self, qapp):
        item = RectAnnotation(QRectF(0, 0, 100, 80), pen_color=QColor(255, 0, 0))
        item.set_pen_color(QColor(0, 255, 0))
        assert item.pen().color() == QColor(0, 255, 0)

    def test_set_pen_width(self, qapp):
        item = RectAnnotation(QRectF(0, 0, 100, 80), pen_width=2)
        item.set_pen_width(5)
        assert item.pen().width() == 5

    def test_set_fill_enabled(self, qapp):
        item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=False)
        item.set_fill_enabled(True, QColor(0, 0, 255, 50))
        # alpha=50 → opacity=round(50*100/255)=20 → alpha=int(20*255/100)=51
        brush_color = item.brush().color()
        assert brush_color.red() == 0
        assert brush_color.green() == 0
        assert brush_color.blue() == 255
        assert brush_color.alpha() == int(round(50 * 100 / 255) * 255 / 100)

    def test_set_fill_disabled(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(0, 0, 255, 50)
        )
        item.set_fill_enabled(False)
        assert item.brush().style() == Qt.BrushStyle.NoBrush

    def test_fill_opacity_default(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(0, 0, 255, 50)
        )
        assert item._fill_opacity == 20  # 50/255 ≈ 20%

    def test_set_fill_color(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50)
        )
        item.set_fill_color(QColor(0, 128, 255))
        brush_color = item.brush().color()
        assert brush_color.red() == 0
        assert brush_color.green() == 128
        assert brush_color.blue() == 255
        # 透明度不变
        assert brush_color.alpha() == item._computed_fill_color().alpha()

    def test_set_fill_opacity(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50)
        )
        item.set_fill_opacity(80)
        assert item._fill_opacity == 80
        brush_color = item.brush().color()
        assert brush_color.alpha() == int(80 * 255 / 100)

    def test_fill_opacity_constructor(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80),
            fill_enabled=True,
            fill_color=QColor(0, 128, 0),
            fill_opacity=60,
        )
        assert item._fill_opacity == 60
        assert item.brush().color().alpha() == int(60 * 255 / 100)

    def test_fill_disabled_preserves_state(self, qapp):
        item = RectAnnotation(
            QRectF(0, 0, 100, 80),
            fill_enabled=True,
            fill_color=QColor(0, 128, 0),
            fill_opacity=60,
        )
        item.set_fill_enabled(False)
        assert item._fill_opacity == 60
        assert item._fill_color == QColor(0, 128, 0)


class TestEllipseAnnotationSetters:
    def test_set_pen_color(self, qapp):
        item = EllipseAnnotation(QRectF(0, 0, 100, 80), pen_color=QColor(255, 0, 0))
        item.set_pen_color(QColor(0, 255, 0))
        assert item.pen().color() == QColor(0, 255, 0)

    def test_set_pen_width(self, qapp):
        item = EllipseAnnotation(QRectF(0, 0, 100, 80), pen_width=2)
        item.set_pen_width(5)
        assert item.pen().width() == 5

    def test_set_fill_enabled(self, qapp):
        item = EllipseAnnotation(QRectF(0, 0, 100, 80))
        item.set_fill_enabled(True, QColor(255, 0, 0, 50))
        # alpha=50 → opacity=round(50*100/255)=20 → alpha=int(20*255/100)=51
        brush_color = item.brush().color()
        assert brush_color.red() == 255
        assert brush_color.green() == 0
        assert brush_color.blue() == 0
        assert brush_color.alpha() == int(round(50 * 100 / 255) * 255 / 100)

    def test_set_fill_color(self, qapp):
        item = EllipseAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50)
        )
        item.set_fill_color(QColor(0, 128, 255))
        brush_color = item.brush().color()
        assert brush_color.red() == 0
        assert brush_color.green() == 128
        assert brush_color.blue() == 255

    def test_set_fill_opacity(self, qapp):
        item = EllipseAnnotation(
            QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50)
        )
        item.set_fill_opacity(80)
        assert item._fill_opacity == 80
        brush_color = item.brush().color()
        assert brush_color.alpha() == int(80 * 255 / 100)


class TestMosaicItemResize:
    def test_resizing_flag_hides_effect(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(10, 10, 80, 80), bg, strength=8)
        assert item._cached_mosaic is not None
        item.set_resizing(True)
        assert item._resizing is True
        item.set_resizing(False)
        assert item._cached_mosaic is not None

    def test_regenerate_after_resize(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(10, 10, 80, 80), bg, strength=8)
        item.setRect(QRectF(10, 10, 120, 120))
        item.regenerate()
        assert item._cached_mosaic is not None


class TestBlurItemResize:
    def test_resizing_flag(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(10, 10, 80, 80), bg, radius=10)
        item.set_resizing(True)
        assert item._resizing is True

    def test_regenerate_after_resize(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(10, 10, 80, 80), bg, radius=10)
        item.setRect(QRectF(10, 10, 120, 120))
        item.regenerate()
        assert item._cached_blur is not None
