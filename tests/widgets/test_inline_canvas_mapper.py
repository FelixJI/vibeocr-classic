from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QPixmap

from vibeocr.classic.widgets.inline_edit_canvas import InlineEditCanvas
from vibeocr.classic.widgets.screen_coordinate_mapper import (
    ScreenCoordinateMapper,
    ScreenInfo,
)


def _make_mapper(dpr=2.0):
    grab = QPixmap(int(200 * dpr), int(100 * dpr))
    grab.setDevicePixelRatio(dpr)
    grab.fill()
    info = ScreenInfo(
        geometry=QRect(0, 0, 200, 100),
        dpr=dpr,
        grab=grab,
        offset=QPoint(0, 0),
    )
    return ScreenCoordinateMapper([info])


class TestCanvasAcceptsMapper:
    def test_set_background_with_mapper(self, qapp):
        canvas = InlineEditCanvas()
        mapper = _make_mapper(dpr=2.0)
        pixmap = QPixmap(200, 100)
        pixmap.setDevicePixelRatio(2.0)
        canvas.set_background(pixmap, QPointF(0, 0), mapper)
        assert canvas._background_item is not None
        assert canvas._mapper is mapper

    def test_set_background_without_mapper(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        canvas.set_background(pixmap, QPointF(0, 0))
        assert canvas._background_item is not None
        assert canvas._mapper is None

    def test_update_crop_region_with_mapper(self, qapp):
        canvas = InlineEditCanvas()
        mapper = _make_mapper(dpr=2.0)
        screen_pixmap = QPixmap((200 * 2), (100 * 2))
        screen_pixmap.setDevicePixelRatio(2.0)
        canvas.set_background(screen_pixmap, QPointF(0, 0), mapper)
        # Should not crash
        canvas.update_crop_region(screen_pixmap, QRect(10, 10, 50, 30), mapper)
        assert canvas._background_item is not None
