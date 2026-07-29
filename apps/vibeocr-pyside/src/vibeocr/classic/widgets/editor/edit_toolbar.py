"""底部编辑工具栏

包含工具按钮组、属性条、操作按钮。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.editor.annotation_items import EditTool
from vibeocr.classic.widgets.editor.tool_properties_bar import ToolPropertiesBar


class EditorToolbar(QWidget):
    """底部编辑工具栏"""

    tool_changed = Signal(object)  # EditTool
    undo_requested = Signal()
    redo_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editorToolbar")
        self.setFixedHeight(theme.Layout.toolbar_height)
        self.setStyleSheet(
            f"QWidget#editorToolbar {{ background: {theme.Colors.surface};"
            f" border-top: 1px solid {theme.Colors.border}; }}"
        )

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 工具按钮组
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        tool_style = theme.toolbar_button_qss()

        tools = [
            ("选择", EditTool.SELECT),
            ("马赛克", EditTool.MOSAIC),
            ("模糊", EditTool.BLUR),
            ("矩形", EditTool.RECT),
            ("圆形", EditTool.ELLIPSE),
            ("箭头", EditTool.ARROW),
            ("文字", EditTool.TEXT),
        ]

        self._tool_buttons: dict[EditTool, QToolButton] = {}
        for text, tool in tools:
            btn = QToolButton()
            btn.setText(text)
            btn.setCheckable(True)
            btn.setStyleSheet(tool_style)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            layout.addWidget(btn)

        # 默认选中"选择"工具
        self._tool_buttons[EditTool.SELECT].setChecked(True)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 工具属性条
        self._properties_bar = ToolPropertiesBar()
        layout.addWidget(self._properties_bar)

        # 弹性空间
        layout.addStretch()

        # 撤销/重做
        action_style = theme.toolbar_button_qss()

        self._btn_undo = QPushButton("撤销")
        self._btn_undo.setStyleSheet(action_style)
        self._btn_undo.setEnabled(False)
        layout.addWidget(self._btn_undo)

        self._btn_redo = QPushButton("重做")
        self._btn_redo.setStyleSheet(action_style)
        self._btn_redo.setEnabled(False)
        layout.addWidget(self._btn_redo)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 操作按钮
        self._btn_save = QPushButton("另存为")
        self._btn_save.setStyleSheet(action_style)
        layout.addWidget(self._btn_save)

        self._btn_copy = QPushButton("复制")
        self._btn_copy.setStyleSheet(action_style)
        layout.addWidget(self._btn_copy)

        self._btn_confirm = QPushButton("确认识别")
        self._btn_confirm.setStyleSheet(theme.button_qss("primary"))
        layout.addWidget(self._btn_confirm)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setStyleSheet(theme.button_qss("danger"))
        layout.addWidget(self._btn_cancel)

    def _create_separator(self) -> QFrame:
        """创建垂直分隔线"""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme.Colors.border};")
        return sep

    def _connect_signals(self) -> None:
        # 工具按钮
        for tool, btn in self._tool_buttons.items():
            btn.clicked.connect(lambda checked, t=tool: self._on_tool_clicked(t))

        # 操作按钮
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        self._btn_save.clicked.connect(self.save_requested.emit)
        self._btn_copy.clicked.connect(self.copy_requested.emit)
        self._btn_confirm.clicked.connect(self.confirm_requested.emit)
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)

    def _on_tool_clicked(self, tool: EditTool) -> None:
        """工具按钮点击"""
        self._properties_bar.update_for_tool(tool)
        self.tool_changed.emit(tool)

    @property
    def properties_bar(self) -> ToolPropertiesBar:
        return self._properties_bar

    def set_undo_enabled(self, enabled: bool) -> None:
        self._btn_undo.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        self._btn_redo.setEnabled(enabled)
