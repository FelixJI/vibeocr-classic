# tests/widgets/test_edge_toolbar.py
"""Tests for EdgeToolbar (桌面边缘隐身悬浮操作栏)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from vibeocr.classic.widgets.toolbar import EdgeToolbar


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
