# tests/widgets/editor/test_tool_properties_bar_color_dialog.py
"""颜色选择对话框背景修复回归测试。

父窗口是截图覆盖层（WA_TranslucentBackground/WA_NoSystemBackground），该属性
会传播到 QColorDialog 顶层窗口导致黑底。修复手段：强制非原生对话框 + 清除对话框
透明属性 + 浅色不透明 QSS。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.editor.tool_properties_bar import ToolPropertiesBar


class TestColorDialogNonNative:
    def test_dialog_uses_non_native(self, qapp):
        """颜色对话框必须使用 Qt 自绘（非原生），否则在透明父窗口下黑底。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        assert dialog.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)

    def test_dialog_preserves_initial_color(self, qapp):
        """对话框应携带初始颜色值。"""
        bar = ToolPropertiesBar()
        initial = QColor(10, 20, 30)
        dialog = bar._make_color_dialog(initial)
        selected = dialog.currentColor()
        assert (selected.red(), selected.green(), selected.blue()) == (10, 20, 30)


class TestColorDialogOpaqueBackground:
    """回归：对话框必须清除透明属性并启用样式背景，避免黑底。"""

    def test_dialog_clears_translucent_attribute(self, qapp):
        """对话框不应继承父覆盖层的 WA_TranslucentBackground。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_dialog_clears_no_system_background_attribute(self, qapp):
        """对话框不应继承父覆盖层的 WA_NoSystemBackground。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def test_dialog_uses_styled_background(self, qapp):
        """对话框应启用 WA_StyledBackground，让背景由样式表填充。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def test_dialog_has_light_opaque_stylesheet(self, qapp):
        """对话框应带浅色不透明背景 QSS，与浅色主题一致。"""
        bar = ToolPropertiesBar()
        dialog = bar._make_color_dialog(QColor(255, 0, 0))
        qss = dialog.styleSheet()
        assert theme.Colors.surface.lower() in qss.lower()
        assert theme.Colors.text.lower() in qss.lower()


class TestPropertiesBarButtonsNoTooltip:
    """回归：截图覆盖层内按钮不得带 tooltip（黑底问题已通过移除 tooltip 解决）。"""

    def test_buttons_have_no_tooltip(self, qapp):
        from PySide6.QtWidgets import QToolButton

        bar = ToolPropertiesBar()
        for btn in bar.findChildren(QToolButton):
            assert btn.toolTip() == "", f"{btn.objectName() or btn.text()} 仍有 tooltip"
