# src/vibeocr/widgets/inline_toolbar.py
"""内联编辑工具栏

毛玻璃浅色主题的浮动工具栏，包含工具按钮、属性条和操作按钮。
所有按钮使用纯文字标签。
属性条作为独立工具栏显示在主工具栏下方。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.editor.annotation_items import EditTool
from vibeocr.classic.widgets.editor.tool_properties_bar import ToolPropertiesBar

# 工具按钮定义：(label, EditTool)
_TOOL_DEFS: list[tuple[str, EditTool]] = [
    ("选择", EditTool.SELECT),
    ("打码", EditTool.MOSAIC),
    ("模糊", EditTool.BLUR),
    ("矩形", EditTool.RECT),
    ("椭圆", EditTool.ELLIPSE),
    ("箭头", EditTool.ARROW),
    ("文字", EditTool.TEXT),
]


class InlineToolbar(QWidget):
    """内联编辑工具栏（毛玻璃浅色主题）

    上方为主工具栏（工具按钮 + 操作按钮），下方为独立的属性条工具栏。

    Signals:
        tool_changed(EditTool): 当前工具切换
        undo_requested(): 撤销请求
        redo_requested(): 重做请求
        save_requested(): 另存为请求
        copy_requested(): 复制请求
        confirm_requested(): 确认识别请求
        cancel_requested(): 取消请求
    """

    tool_changed = Signal(object)
    undo_requested = Signal()
    redo_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inlineToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._current_tool: EditTool | None = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"""
            #inlineToolbar {{
                background-color: {theme.Colors.surface};
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # --- 上方：主工具栏 ---
        self._top_bar = QWidget()
        self._top_bar.setObjectName("topBar")
        self._top_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._top_bar.setFixedHeight(theme.Layout.toolbar_height)
        self._top_bar.setStyleSheet(
            f"QWidget {{ background: {theme.Colors.surface};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.lg}px; }}"
        )

        top_layout = QHBoxLayout(self._top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)
        top_layout.setSpacing(4)

        # 工具按钮组（exclusive）
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        tool_style = theme.toolbar_button_qss()

        self._tool_buttons: dict[EditTool, QToolButton] = {}
        for label, tool in _TOOL_DEFS:
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setStyleSheet(tool_style)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            top_layout.addWidget(btn)

        # 弹性空间
        top_layout.addStretch()

        # 操作按钮
        action_style = theme.toolbar_button_qss()

        self._btn_undo = self._make_action_btn("撤销", action_style)
        self._btn_undo.setEnabled(False)
        top_layout.addWidget(self._btn_undo)

        self._btn_redo = self._make_action_btn("重做", action_style)
        self._btn_redo.setEnabled(False)
        top_layout.addWidget(self._btn_redo)

        top_layout.addWidget(self._create_separator())

        self._btn_save = self._make_action_btn("保存", action_style)
        top_layout.addWidget(self._btn_save)

        self._btn_copy = self._make_action_btn("复制", action_style)
        top_layout.addWidget(self._btn_copy)

        self._btn_cancel = self._make_action_btn(
            "取消",
            f"QToolButton {{ background: transparent; color: {theme.Colors.text};"
            f" border: none; border-radius: {theme.Radius.sm}px; padding: 4px 6px; }}"
            f" QToolButton:hover {{ background: {theme.Colors.danger_hover};"
            f" color: {theme.Colors.danger}; }}",
        )
        top_layout.addWidget(self._btn_cancel)

        outer.addWidget(self._top_bar)

        # --- 下方：属性条工具栏（初始隐藏） ---
        self._props_panel = QWidget()
        self._props_panel.setObjectName("propsPanel")
        self._props_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._props_panel.setStyleSheet(
            f"QWidget#propsPanel {{ background: {theme.Colors.surface};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.lg}px; }}"
            f" #propsPanel QWidget {{ background: transparent; }}"
            f" #propsPanel QLabel {{ color: {theme.Colors.text};"
            f" font-size: {theme.Typography.caption}px; }}"
            f" #propsPanel QSpinBox, #propsPanel QFontComboBox, #propsPanel QPushButton {{"
            f" background: {theme.Colors.surface}; color: {theme.Colors.text};"
            f" border: 1px solid {theme.Colors.border_strong};"
            f" border-radius: {theme.Radius.sm}px; padding: 1px 4px;"
            f" max-height: 26px; }}"
            f" #propsPanel QSlider::groove:horizontal {{"
            f" background: {theme.Colors.border_strong}; height: 4px;"
            f" border-radius: 2px; }}"
            f" #propsPanel QSlider::handle:horizontal {{"
            f" background: {theme.Colors.accent}; width: 14px; height: 14px;"
            f" margin: -5px 0; border-radius: 7px; }}"
            f" #propsPanel QCheckBox {{ color: {theme.Colors.text};"
            f" font-size: {theme.Typography.caption}px; spacing: 4px; }}"
            f" #propsPanel QCheckBox::indicator {{ width: 14px; height: 14px;"
            f" border: 1px solid {theme.Colors.border_strong};"
            f" border-radius: 3px; background: {theme.Colors.surface}; }}"
            f" #propsPanel QCheckBox::indicator:checked {{"
            f" background: {theme.Colors.accent};"
            f" border-color: {theme.Colors.accent}; }}"
        )
        self._props_panel.hide()

        props_layout = QHBoxLayout(self._props_panel)
        props_layout.setContentsMargins(8, 4, 8, 4)
        props_layout.setSpacing(6)

        self._properties_bar = ToolPropertiesBar()
        self._properties_bar.setStyleSheet("")  # 清空暗色主题样式，使用面板的浅色样式
        props_layout.addWidget(self._properties_bar)

        outer.addWidget(self._props_panel)

    def _make_action_btn(self, text: str, style: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setStyleSheet(style)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _create_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme.Colors.overlay};")
        return sep

    def _connect_signals(self) -> None:
        for tool, btn in self._tool_buttons.items():
            btn.clicked.connect(lambda _, t=tool: self._on_tool_clicked(t))

        self._btn_undo.clicked.connect(self.undo_requested.emit)
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        self._btn_save.clicked.connect(self.save_requested.emit)
        self._btn_copy.clicked.connect(self.copy_requested.emit)
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)

    def _on_tool_clicked(self, tool: EditTool) -> None:
        self._current_tool = tool
        has_props = tool != EditTool.SELECT
        self._props_panel.setVisible(has_props)
        if has_props:
            self._properties_bar.update_for_tool(tool)
        self.tool_changed.emit(tool)

    @property
    def properties_bar(self) -> ToolPropertiesBar:
        return self._properties_bar

    def set_undo_enabled(self, enabled: bool) -> None:
        self._btn_undo.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        self._btn_redo.setEnabled(enabled)
