# tests/widgets/test_edge_toolbar.py
"""Tests for EdgeToolbar (桌面边缘隐身悬浮操作栏)."""

from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from vibeocr.classic.widgets import toolbar as toolbar_module
from vibeocr.classic.widgets.toolbar import EdgeSide, EdgeToolbar


class TestEdgeToolbar:
    def test_is_widget(self, qapp):
        tb = EdgeToolbar()
        assert isinstance(tb, QWidget)

    def test_styled_background_enabled(self, qapp):
        """浅色背景依赖 WA_StyledBackground：否则背景透明，样式表 background-color 失效。"""
        tb = EdgeToolbar()
        assert tb.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def test_paints_light_background(self, qapp):
        """浅色实体背景由 paintEvent 绘制（透明顶层窗口下 QSS 背景不可靠）。

        判定方式：渲染到 pixmap，确认画出了非透明像素（即主题 surface 浅色背景）。
        """
        from PySide6.QtGui import QColor, QImage

        from vibeocr.classic.ui import theme

        tb = EdgeToolbar()
        img = QImage(tb.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)  # 全透明基准
        tb.render(img)
        # 取中心点像素，应为不透明的主题浅色背景
        pixel = img.pixelColor(tb.width() // 2, tb.height() // 2)
        assert pixel.alpha() == 255
        assert pixel.red() > 200  # 接近 #ffffff
        assert pixel.green() > 200
        assert pixel.blue() > 200
        # 与 theme surface 一致
        assert pixel.name() == QColor(theme.Colors.surface).name().lower()

    def test_revealed_from_detection_margin_rehides_when_pointer_stays_outside(
        self, qapp, monkeypatch
    ):
        """外扩检测区误触发展开后，鼠标未进窗口也必须再次收回。"""
        tb = EdgeToolbar()
        screen_geo = qapp.primaryScreen().availableGeometry()
        visible_geo = QRect(
            screen_geo.center().x() - tb.width() // 2,
            screen_geo.top(),
            tb.width(),
            tb.height(),
        )
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)

        tb.set_hide_delay(100)
        tb._auto_hide_enabled = True
        tb._docked_side = EdgeSide.TOP
        tb._is_hidden = True
        tb.setGeometry(hidden_geo)

        cursor_pos = [QPoint(hidden_geo.left() - 5, screen_geo.top() + 1)]
        monkeypatch.setattr(
            toolbar_module,
            "QCursor",
            SimpleNamespace(pos=lambda: cursor_pos[0]),
        )
        tb._mouse_check_timer.start()

        # 鼠标位于隐藏窗口外、但落在额外 10px 检测区内，会触发展开。
        tb._check_mouse_position()
        assert not tb._is_hidden

        # 鼠标从未进入实际窗口，不会产生 leaveEvent；状态机仍须自行收回。
        cursor_pos[0] = QPoint(screen_geo.right(), screen_geo.bottom())
        QTest.qWait(350)

        assert tb._is_hidden
        tb.close()
