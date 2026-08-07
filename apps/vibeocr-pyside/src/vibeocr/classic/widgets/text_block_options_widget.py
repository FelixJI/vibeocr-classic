# src/vibeocr/widgets/text_block_options_widget.py
"""文本块处理选项组件

紧凑面板，控制 OCR 文本块的拼接排版策略（换行模式 / 块间空格 /
中文缩进 / 去空白块）。仅用于单识别标签页，与 OCR 引擎/管道无关。

变化即写盘：任一控件变化立即持久化到 OCRPreferences（仿
ScreenshotOptionsWidget._persist 的「变化即写盘」模式）。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from vibeocr.classic.recognition_settings import (
    LINE_MODE_KEEP,
    LINE_MODE_MERGE,
    LINE_MODE_SMART,
    TextBlockOptions,
)
from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.collapsible_group_box import CollapsibleGroupBox

# 换行模式下拉项：(显示名, line_mode 值)
_LINE_MODE_ITEMS: list[tuple[str, str]] = [
    ("保留原样", LINE_MODE_KEEP),
    ("合并成一段", LINE_MODE_MERGE),
    ("智能分段", LINE_MODE_SMART),
]


class TextBlockOptionsWidget(CollapsibleGroupBox):
    """文本块处理选项面板。"""

    options_changed = Signal(object)  # TextBlockOptions

    def __init__(self, parent: QWidget | None = None):
        super().__init__("文本块处理", parent)
        # 持久化抑制标志：批量回填控件时避免触发写盘。
        self._loading = False
        self._setup_ui()
        self._connect_signals()
        self.load()

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        layout = self.contentLayout()
        layout.setSpacing(6)

        # 换行模式下拉
        mode_row = QHBoxLayout()
        mode_label = QLabel("换行模式")
        mode_label.setStyleSheet(
            f"font-size: {theme.Typography.body}px;"
        )
        self._mode_combo = QComboBox()
        for display, value in _LINE_MODE_ITEMS:
            self._mode_combo.addItem(display, value)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._mode_combo, stretch=1)
        layout.addLayout(mode_row)

        # 三个开关
        self._join_space_cb = QCheckBox("块间加空格")
        self._join_space_cb.setToolTip("合并相关模式下，文本块之间插入半角空格")
        layout.addWidget(self._join_space_cb)

        self._indent_cb = QCheckBox("中文段落缩进")
        self._indent_cb.setToolTip("合并/智能分段模式下，每段首行加两个全角空格")
        layout.addWidget(self._indent_cb)

        self._drop_blank_cb = QCheckBox("去除空白块")
        self._drop_blank_cb.setToolTip("过滤掉内容为空或纯空白的文本块")
        layout.addWidget(self._drop_blank_cb)

    def _connect_signals(self) -> None:
        self._mode_combo.currentIndexChanged.connect(self._on_option_changed)
        self._join_space_cb.toggled.connect(self._on_option_changed)
        self._indent_cb.toggled.connect(self._on_option_changed)
        self._drop_blank_cb.toggled.connect(self._on_option_changed)

    # ── 加载 / 上报 ──

    def load(self) -> None:
        """从 OCRPreferences 回填控件状态。"""
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            opts = OCRPreferences.instance().get_text_options()
        except RuntimeError:
            return

        self._loading = True
        try:
            # 换行模式
            idx = self._mode_combo.findData(opts.line_mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
            self._join_space_cb.setChecked(opts.block_join_space)
            self._indent_cb.setChecked(opts.chinese_indent)
            self._drop_blank_cb.setChecked(opts.drop_blank_blocks)
        finally:
            self._loading = False

    def _on_option_changed(self) -> None:
        """任一控件变化 → 构造选项 → 持久化 → 上报。"""
        if self._loading:
            return
        opts = self.get_text_options()
        self._persist(opts)
        self.options_changed.emit(opts)

    def _persist(self, options: TextBlockOptions) -> None:
        """持久化到 OCRPreferences（变化即写盘）。"""
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_text_options(options)
        except RuntimeError:
            pass

    def get_text_options(self) -> TextBlockOptions:
        """根据当前控件状态构造 TextBlockOptions。"""
        return TextBlockOptions(
            line_mode=self._mode_combo.currentData(),
            block_join_space=self._join_space_cb.isChecked(),
            chinese_indent=self._indent_cb.isChecked(),
            drop_blank_blocks=self._drop_blank_cb.isChecked(),
        )
