"""工具属性条

根据当前工具动态切换显示颜色、线宽、填充、字体等属性控件。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.editor.annotation_items import EditTool


class ToolPropertiesBar(QWidget):
    """工具属性条"""

    color_changed = Signal(QColor)
    line_width_changed = Signal(int)
    fill_enabled_changed = Signal(bool)
    fill_color_changed = Signal(QColor)
    fill_opacity_changed = Signal(int)
    fill_linked_changed = Signal(bool)
    font_changed = Signal(QFont)
    font_size_changed = Signal(int)
    bold_changed = Signal(bool)
    italic_changed = Signal(bool)
    mosaic_strength_changed = Signal(int)
    blur_radius_changed = Signal(int)

    # 面板索引
    _EMPTY_PAGE = 0
    _SHAPE_PAGE = 1
    _TEXT_PAGE = 2
    _MOSAIC_PAGE = 3
    _BLUR_PAGE = 4
    _COMMON_PAGE = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("propertiesBar")
        self.setStyleSheet(
            f"QWidget#propertiesBar {{ background: transparent; }}"
            f" QLabel {{ color: {theme.Colors.text};"
            f" font-size: {theme.Typography.caption}px; }}"
        )

        self._current_color = QColor(255, 0, 0)
        self._fill_color = QColor(255, 0, 0)
        self._setup_ui()
        self._connect_signals()
        self._last_tool: EditTool = EditTool.SELECT

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # 页面 0：空白（SELECT 等无属性工具）
        self._stack.addWidget(QWidget())

        # 页面 1：图形属性（矩形/圆/箭头）
        self._stack.addWidget(self._create_shape_page())

        # 页面 2：文字属性
        self._stack.addWidget(self._create_text_page())

        # 页面 3：马赛克属性
        self._stack.addWidget(self._create_mosaic_page())

        # 页面 4：模糊属性
        self._stack.addWidget(self._create_blur_page())

        # 页面 5：通用属性（选中矩形/椭圆/箭头时）
        self._stack.addWidget(self._create_common_page())

    def _create_color_button(self) -> QPushButton:
        """创建颜色选择按钮"""
        btn = QPushButton()
        btn.setObjectName("colorPickButton")
        btn.setFixedSize(24, 24)
        self._apply_color_style(btn)
        btn.clicked.connect(self._on_color_pick)
        return btn

    # ==================== 填充色辅助方法 ====================

    def _create_fill_color_button(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("fillColorPickButton")
        btn.setFixedSize(24, 24)
        self._apply_fill_color_style(btn)
        btn.clicked.connect(self._on_fill_color_pick)
        return btn

    def _apply_fill_color_style(self, btn: QPushButton) -> None:
        btn.setStyleSheet(
            f"QPushButton#fillColorPickButton {{ background-color: {self._fill_color.name()}; "
            f"border: 1px solid {theme.Colors.text_muted}; border-radius: 3px; }}"
        )

    def _update_fill_color_buttons(self) -> None:
        if hasattr(self, "_fill_color_btn"):
            self._apply_fill_color_style(self._fill_color_btn)
        if hasattr(self, "_common_fill_color_btn"):
            self._apply_fill_color_style(self._common_fill_color_btn)

    def _on_fill_color_pick(self) -> None:
        dialog = self._make_color_dialog(self._fill_color)
        if dialog.exec() == QColorDialog.DialogCode.Accepted:
            color = dialog.selectedColor()
            if color.isValid():
                self._fill_color = QColor(color.red(), color.green(), color.blue())
                self._update_fill_color_buttons()
                self.fill_color_changed.emit(self._fill_color)

    def _set_fill_sub_controls_visible(
        self, is_shape_page: bool, visible: bool
    ) -> None:
        if is_shape_page:
            for w in (
                self._fill_color_btn,
                self._fill_link_btn,
                self._fill_opacity_title,
                self._fill_opacity_slider,
                self._fill_opacity_label,
            ):
                w.setVisible(visible)
        else:
            for w in (
                self._common_fill_color_btn,
                self._common_fill_link_btn,
                self._common_fill_opacity_title,
                self._common_fill_opacity_slider,
                self._common_fill_opacity_label,
            ):
                w.setVisible(visible)

    def _update_fill_color_btn_state(
        self, link_btn: QToolButton, color_btn: QPushButton
    ) -> None:
        color_btn.setEnabled(not link_btn.isChecked())

    # ==================== 页面创建 ====================

    def _create_shape_page(self) -> QWidget:
        """创建图形属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # 颜色
        layout.addWidget(QLabel("颜色"))
        self._shape_color_btn = self._create_color_button()
        layout.addWidget(self._shape_color_btn)

        # 线宽
        layout.addWidget(QLabel("线宽"))
        self._line_width_spin = QSpinBox()
        self._line_width_spin.setRange(1, 10)
        self._line_width_spin.setValue(2)
        layout.addWidget(self._line_width_spin)

        # 填充
        self._fill_cb = QCheckBox("填充")
        layout.addWidget(self._fill_cb)

        # 填充色按钮
        self._fill_color_btn = self._create_fill_color_button()
        layout.addWidget(self._fill_color_btn)

        # 链接按钮
        self._fill_link_btn = QToolButton()
        self._fill_link_btn.setText("🔗")
        self._fill_link_btn.setCheckable(True)
        self._fill_link_btn.setChecked(True)
        self._fill_link_btn.setFixedSize(24, 24)
        self._fill_link_btn.setStyleSheet(
            "QToolButton { font-size: 14px; }"
            f"QToolButton:checked {{ background-color: {theme.Colors.accent}; color: white; }}"
        )
        layout.addWidget(self._fill_link_btn)

        # 透明度
        self._fill_opacity_title = QLabel("透明度")
        layout.addWidget(self._fill_opacity_title)
        self._fill_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._fill_opacity_slider.setRange(0, 100)
        self._fill_opacity_slider.setValue(20)
        self._fill_opacity_slider.setFixedWidth(80)
        layout.addWidget(self._fill_opacity_slider)
        self._fill_opacity_label = QLabel("20%")
        self._fill_opacity_label.setFixedWidth(30)
        layout.addWidget(self._fill_opacity_label)

        # 初始隐藏填充子控件
        self._set_fill_sub_controls_visible(is_shape_page=True, visible=False)

        return page

    def _create_text_page(self) -> QWidget:
        """创建文字属性页（增强版：带粗体/斜体按钮）"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # 颜色
        layout.addWidget(QLabel("颜色"))
        self._text_color_btn = self._create_color_button()
        layout.addWidget(self._text_color_btn)

        # 字体
        layout.addWidget(QLabel("字体"))
        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentFont(QFont("Microsoft YaHei"))
        layout.addWidget(self._font_combo)

        # 字号
        layout.addWidget(QLabel("字号"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 72)
        self._font_size_spin.setValue(14)
        layout.addWidget(self._font_size_spin)

        # 粗体按钮
        self._bold_btn = QToolButton()
        self._bold_btn.setText("B")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setStyleSheet(
            "QToolButton { font-weight: bold; min-width: 24px; min-height: 24px; }"
            f"QToolButton:checked {{ background-color: {theme.Colors.accent}; color: white; }}"
        )
        layout.addWidget(self._bold_btn)

        # 斜体按钮
        self._italic_btn = QToolButton()
        self._italic_btn.setText("I")
        self._italic_btn.setCheckable(True)
        self._italic_btn.setStyleSheet(
            "QToolButton { font-style: italic; min-width: 24px; min-height: 24px; }"
            f"QToolButton:checked {{ background-color: {theme.Colors.accent}; color: white; }}"
        )
        layout.addWidget(self._italic_btn)

        return page

    def _create_mosaic_page(self) -> QWidget:
        """创建马赛克属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("强度"))
        self._mosaic_slider = QSlider(Qt.Orientation.Horizontal)
        self._mosaic_slider.setRange(2, 20)
        self._mosaic_slider.setValue(10)
        self._mosaic_slider.setFixedWidth(100)
        layout.addWidget(self._mosaic_slider)

        self._mosaic_label = QLabel("10")
        layout.addWidget(self._mosaic_label)

        return page

    def _create_blur_page(self) -> QWidget:
        """创建模糊属性页"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("半径"))
        self._blur_slider = QSlider(Qt.Orientation.Horizontal)
        self._blur_slider.setRange(2, 30)
        self._blur_slider.setValue(10)
        self._blur_slider.setFixedWidth(100)
        layout.addWidget(self._blur_slider)

        self._blur_label = QLabel("10")
        layout.addWidget(self._blur_label)

        return page

    def _create_common_page(self) -> QWidget:
        """创建通用属性页（颜色+线宽+填充）"""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("颜色"))
        self._common_color_btn = self._create_color_button()
        layout.addWidget(self._common_color_btn)

        layout.addWidget(QLabel("线宽"))
        self._common_line_width_spin = QSpinBox()
        self._common_line_width_spin.setRange(1, 10)
        self._common_line_width_spin.setValue(2)
        layout.addWidget(self._common_line_width_spin)

        self._common_fill_cb = QCheckBox("填充")
        layout.addWidget(self._common_fill_cb)

        # 填充色按钮
        self._common_fill_color_btn = self._create_fill_color_button()
        layout.addWidget(self._common_fill_color_btn)

        # 链接按钮
        self._common_fill_link_btn = QToolButton()
        self._common_fill_link_btn.setText("🔗")
        self._common_fill_link_btn.setCheckable(True)
        self._common_fill_link_btn.setChecked(True)
        self._common_fill_link_btn.setFixedSize(24, 24)
        self._common_fill_link_btn.setStyleSheet(
            "QToolButton { font-size: 14px; }"
            f"QToolButton:checked {{ background-color: {theme.Colors.accent}; color: white; }}"
        )
        layout.addWidget(self._common_fill_link_btn)

        # 透明度
        self._common_fill_opacity_title = QLabel("透明度")
        layout.addWidget(self._common_fill_opacity_title)
        self._common_fill_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._common_fill_opacity_slider.setRange(0, 100)
        self._common_fill_opacity_slider.setValue(20)
        self._common_fill_opacity_slider.setFixedWidth(80)
        layout.addWidget(self._common_fill_opacity_slider)
        self._common_fill_opacity_label = QLabel("20%")
        self._common_fill_opacity_label.setFixedWidth(30)
        layout.addWidget(self._common_fill_opacity_label)

        # 初始隐藏
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=False)

        return page

    # ==================== 信号连接 ====================

    def _connect_signals(self) -> None:
        self._line_width_spin.valueChanged.connect(self.line_width_changed.emit)
        self._fill_cb.toggled.connect(self._on_fill_toggled_shape)
        self._common_fill_cb.toggled.connect(self._on_fill_toggled_common)
        self._fill_link_btn.toggled.connect(self._on_fill_link_toggled_shape)
        self._common_fill_link_btn.toggled.connect(self._on_fill_link_toggled_common)
        self._fill_opacity_slider.valueChanged.connect(
            self._on_fill_opacity_changed_shape
        )
        self._common_fill_opacity_slider.valueChanged.connect(
            self._on_fill_opacity_changed_common
        )
        self._font_combo.currentFontChanged.connect(self.font_changed.emit)
        self._font_size_spin.valueChanged.connect(self.font_size_changed.emit)
        self._bold_btn.toggled.connect(self.bold_changed.emit)
        self._italic_btn.toggled.connect(self.italic_changed.emit)
        self._mosaic_slider.valueChanged.connect(self._on_mosaic_changed)
        self._blur_slider.valueChanged.connect(self._on_blur_changed)

    def _on_fill_toggled_shape(self, checked: bool) -> None:
        self._set_fill_sub_controls_visible(is_shape_page=True, visible=checked)
        self._update_fill_color_btn_state(self._fill_link_btn, self._fill_color_btn)
        self.fill_enabled_changed.emit(checked)

    def _on_fill_toggled_common(self, checked: bool) -> None:
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=checked)
        self._update_fill_color_btn_state(
            self._common_fill_link_btn, self._common_fill_color_btn
        )
        self.fill_enabled_changed.emit(checked)

    def _on_fill_link_toggled_shape(self, checked: bool) -> None:
        if checked:
            self._fill_color = QColor(
                self._current_color.red(),
                self._current_color.green(),
                self._current_color.blue(),
            )
            self._update_fill_color_buttons()
        self._update_fill_color_btn_state(self._fill_link_btn, self._fill_color_btn)
        self.fill_linked_changed.emit(checked)

    def _on_fill_link_toggled_common(self, checked: bool) -> None:
        if checked:
            self._fill_color = QColor(
                self._current_color.red(),
                self._current_color.green(),
                self._current_color.blue(),
            )
            self._update_fill_color_buttons()
        self._update_fill_color_btn_state(
            self._common_fill_link_btn, self._common_fill_color_btn
        )
        self.fill_linked_changed.emit(checked)

    def _on_fill_opacity_changed_shape(self, value: int) -> None:
        self._fill_opacity_label.setText(f"{value}%")
        self.fill_opacity_changed.emit(value)

    def _on_fill_opacity_changed_common(self, value: int) -> None:
        self._common_fill_opacity_label.setText(f"{value}%")
        self.fill_opacity_changed.emit(value)

    # ==================== 颜色处理 ====================

    def _make_color_dialog(self, color: QColor) -> QColorDialog:
        """创建颜色选择对话框（Qt 自绘、不透明）。

        父窗口是截图覆盖层（WA_TranslucentBackground/WA_NoSystemBackground），
        该属性会传播到对话框顶层窗口：原生对话框直接整窗黑底，Qt 自绘对话框
        若继承透明属性同样会出现黑底。因此除强制非原生外，还需：
        - 清除对话框的 WA_TranslucentBackground/WA_NoSystemBackground，
          置位 WA_StyledBackground，让背景由样式表填充；
        - 设浅色不透明 QSS（背景 surface、文字 text），与浅色主题一致。
        仅作用于本对话框，不影响其它界面。
        """
        dialog = QColorDialog(color, self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        # 脱离父覆盖层的透明属性，确保对话框背景不透明（规避黑底）
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dialog.setStyleSheet(
            f"QColorDialog {{ background-color: {theme.Colors.surface};"
            f" color: {theme.Colors.text}; }}"
        )
        return dialog

    def _on_color_pick(self) -> None:
        """打开颜色选择对话框"""
        dialog = self._make_color_dialog(self._current_color)
        if dialog.exec() == QColorDialog.DialogCode.Accepted:
            color = dialog.selectedColor()
            if color.isValid():
                self._current_color = color
                self._update_color_buttons()
                self.color_changed.emit(color)
                if (
                    self._fill_link_btn.isChecked()
                    or self._common_fill_link_btn.isChecked()
                ):
                    self._fill_color = QColor(color.red(), color.green(), color.blue())
                    self._update_fill_color_buttons()

    def _apply_color_style(self, btn: QPushButton) -> None:
        """设置颜色按钮样式（使用 objectName 选择器避免被父级样式覆盖）"""
        btn.setStyleSheet(
            f"QPushButton#colorPickButton {{ background-color: {self._current_color.name()}; "
            f"border: 1px solid {theme.Colors.text_muted}; border-radius: 3px; }}"
        )

    def _update_color_buttons(self) -> None:
        """更新所有颜色按钮的背景色"""
        if hasattr(self, "_shape_color_btn"):
            self._apply_color_style(self._shape_color_btn)
        if hasattr(self, "_text_color_btn"):
            self._apply_color_style(self._text_color_btn)
        if hasattr(self, "_common_color_btn"):
            self._apply_color_style(self._common_color_btn)

    def _on_mosaic_changed(self, value: int) -> None:
        self._mosaic_label.setText(str(value))
        self.mosaic_strength_changed.emit(value)

    def _on_blur_changed(self, value: int) -> None:
        self._blur_label.setText(str(value))
        self.blur_radius_changed.emit(value)

    # ==================== 工具/选中切换 ====================

    def update_for_tool(self, tool: EditTool) -> None:
        """根据工具切换属性面板"""
        self._last_tool = tool
        if tool in (EditTool.RECT, EditTool.ELLIPSE, EditTool.ARROW):
            self._stack.setCurrentIndex(self._SHAPE_PAGE)
        elif tool == EditTool.TEXT:
            self._stack.setCurrentIndex(self._TEXT_PAGE)
        elif tool == EditTool.MOSAIC:
            self._stack.setCurrentIndex(self._MOSAIC_PAGE)
        elif tool == EditTool.BLUR:
            self._stack.setCurrentIndex(self._BLUR_PAGE)
        else:
            self._stack.setCurrentIndex(self._EMPTY_PAGE)

    def update_for_selection(self, item) -> None:
        """根据选中标注项切换属性面板，并同步控件值"""
        from vibeocr.classic.widgets.editor.annotation_items import (
            ArrowAnnotation,
            BlurItem,
            EllipseAnnotation,
            MosaicItem,
            RectAnnotation,
            TextAnnotation,
        )

        if isinstance(item, (RectAnnotation, EllipseAnnotation)):
            self._sync_common_page(item)
            self._common_fill_cb.show()
            self._set_fill_sub_controls_visible(
                is_shape_page=False, visible=getattr(item, "_fill_enabled", False)
            )
            self._stack.setCurrentIndex(self._COMMON_PAGE)
        elif isinstance(item, ArrowAnnotation):
            self._sync_common_page(item)
            self._hide_common_fill_controls()
            self._stack.setCurrentIndex(self._COMMON_PAGE)
        elif isinstance(item, TextAnnotation):
            self._sync_text_page(item)
            self._stack.setCurrentIndex(self._TEXT_PAGE)
        elif isinstance(item, MosaicItem):
            self._mosaic_slider.setValue(item._strength)
            self._stack.setCurrentIndex(self._MOSAIC_PAGE)
        elif isinstance(item, BlurItem):
            self._blur_slider.setValue(item._radius)
            self._stack.setCurrentIndex(self._BLUR_PAGE)
        else:
            self.clear_selection()

    def clear_selection(self) -> None:
        """清除选中态，恢复当前工具的属性页"""
        if hasattr(self, "_common_fill_cb"):
            self._common_fill_cb.show()
        self.update_for_tool(self._last_tool)

    def _sync_common_page(self, item) -> None:
        """同步通用属性页控件值"""
        self._common_line_width_spin.blockSignals(True)
        self._common_line_width_spin.setValue(item._pen_width)
        self._common_line_width_spin.blockSignals(False)
        self._current_color = item._pen_color
        self._update_color_buttons()

        # 同步填充属性
        fill_enabled = getattr(item, "_fill_enabled", False)
        fill_color = getattr(
            item,
            "_fill_color",
            QColor(
                item._pen_color.red(), item._pen_color.green(), item._pen_color.blue()
            ),
        )
        fill_opacity = getattr(item, "_fill_opacity", 20)

        is_linked = (
            fill_color.red() == item._pen_color.red()
            and fill_color.green() == item._pen_color.green()
            and fill_color.blue() == item._pen_color.blue()
        )

        self._common_fill_cb.blockSignals(True)
        self._common_fill_cb.setChecked(fill_enabled)
        self._common_fill_cb.blockSignals(False)

        self._common_fill_link_btn.blockSignals(True)
        self._common_fill_link_btn.setChecked(is_linked)
        self._common_fill_link_btn.blockSignals(False)

        self._fill_color = QColor(
            fill_color.red(), fill_color.green(), fill_color.blue()
        )
        self._update_fill_color_buttons()

        self._common_fill_opacity_slider.blockSignals(True)
        self._common_fill_opacity_slider.setValue(fill_opacity)
        self._common_fill_opacity_slider.blockSignals(False)
        self._common_fill_opacity_label.setText(f"{fill_opacity}%")

        self._update_fill_color_btn_state(
            self._common_fill_link_btn, self._common_fill_color_btn
        )

    def _hide_common_fill_controls(self) -> None:
        self._common_fill_cb.hide()
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=False)

    def _sync_text_page(self, item) -> None:
        """同步文字属性页控件值"""
        self._font_size_spin.blockSignals(True)
        self._font_size_spin.setValue(item.font().pointSize())
        self._font_size_spin.blockSignals(False)
        self._font_combo.blockSignals(True)
        self._font_combo.setCurrentFont(item.font())
        self._font_combo.blockSignals(False)
        self._current_color = item._text_color
        self._update_color_buttons()
