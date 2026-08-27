"""内联识别面板 - 快速选择识别类型"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.runtime_selection import RuntimeSelectionCatalog
from vibeocr.classic.ui import theme
from vibeocr.runtime_contracts.contracts.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_short_name,
)


class InlineRecognitionPanel(QWidget):
    """内联识别面板

    从管道注册表动态生成按钮，点击直接触发识别。

    识别类型（pipeline）由按钮唯一决定；OCRPreferences 的 "screenshot" 源
    仅提供该管道的参数默认值（预处理/子选项），不会覆盖按钮选定的识别类型。
    """

    recognize_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._pipeline_buttons: dict[OCRPipeline, QPushButton] = {}
        self._mode_buttons: dict[str, QPushButton] = {}
        self._recognition_catalog: RuntimeSelectionCatalog | None = None
        self._mode_entries = {}
        self._advanced_mode_install_callback = None
        self._current_pipeline: OCRPipeline = OCRPipeline.OCR
        self._current_options: OCROptions = OCROptions(pipeline=OCRPipeline.OCR)

        self._setup_ui()
        self._apply_styles()
        self._load_pipeline_options(self._current_pipeline)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        for pipeline in get_all_pipelines():
            label = get_pipeline_short_name(pipeline)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("pipeline", pipeline)
            btn.clicked.connect(
                lambda _checked, p=pipeline: self._on_pipeline_clicked(p)
            )
            layout.addWidget(btn)
            self._pipeline_buttons[pipeline] = btn

    def set_recognition_catalog(self, catalog: RuntimeSelectionCatalog | None) -> None:
        """将截图中的识别入口从 legacy pipeline 切换为 mode 语义。"""
        self._recognition_catalog = catalog
        if catalog is None or not catalog.has_recognition_mode_catalog:
            return
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._pipeline_buttons = {}
        self._mode_buttons = {}
        self._mode_entries = {mode.mode_id: mode for mode in catalog.modes}
        for mode in catalog.modes:
            button = QPushButton(mode.display_name)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("recognition_mode", mode.mode_id)
            button.setEnabled(mode.availability != "unavailable")
            button.clicked.connect(
                lambda _checked, mode_id=mode.mode_id: self._on_mode_clicked(mode_id)
            )
            layout.addWidget(button)
            self._mode_buttons[mode.mode_id] = button
        self._apply_styles()
        current_mode = self._current_options.recognition_mode
        if current_mode in self._mode_entries:
            self._on_mode_clicked(current_mode)
        elif catalog.modes:
            self._on_mode_clicked(catalog.modes[0].mode_id)

    def set_advanced_mode_install_callback(self, callback) -> None:
        self._advanced_mode_install_callback = callback

    def _apply_styles(self):
        self.setStyleSheet(
            f"QWidget {{ background: {theme.Colors.surface};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.lg}px; }}"
        )
        for btn in (*self._pipeline_buttons.values(), *self._mode_buttons.values()):
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.Colors.text};"
                f" border: none; border-radius: {theme.Radius.sm}px; padding: 6px;"
                f" text-align: left; }}"
                f" QPushButton:hover {{ background: {theme.Colors.hover_bg}; }}"
                f" QPushButton:checked {{ background: {theme.Colors.accent};"
                f" color: white; }}"
            )

    def _load_pipeline_options(self, pipeline: OCRPipeline) -> None:
        """从 OCRPreferences 的 screenshot 源加载指定管道的选项"""
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            self._current_options = prefs.get_pipeline_options("screenshot", pipeline)
        except RuntimeError:
            self._current_options = OCROptions(pipeline=pipeline)

    def _on_pipeline_clicked(self, pipeline: OCRPipeline):
        self._current_pipeline = pipeline
        self._load_pipeline_options(pipeline)
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == pipeline)
        self.recognize_requested.emit()

    def _on_mode_clicked(self, mode_id: str) -> None:
        mode = self._mode_entries.get(mode_id)
        if mode is None:
            return
        try:
            pipeline = OCRPipeline(mode.pipeline_id)
        except ValueError:
            return
        self._current_pipeline = pipeline
        self._load_pipeline_options(pipeline)
        self._current_options = self._current_options.copy(recognition_mode=mode_id)
        for candidate, button in self._mode_buttons.items():
            button.setChecked(candidate == mode_id)
        if (
            mode.availability == "preparation_required"
            and self._advanced_mode_install_callback is not None
        ):
            self._advanced_mode_install_callback(mode)
        self.recognize_requested.emit()

    def get_options(self) -> OCROptions:
        options = OCROptions.from_dict(self._current_options.to_dict())
        # 识别类型由按钮唯一决定，screenshot 源不可覆盖。
        # 即使 screenshot 源里该管道存入了 .pipeline 不一致的腐烂数据，
        # 按钮选什么就识别什么。
        return options.copy(
            pipeline=self._current_pipeline,
            recognition_mode=(
                self._current_options.recognition_mode if self._mode_entries else None
            ),
        )

    def set_options(self, options: OCROptions):
        self._current_options = OCROptions.from_dict(options.to_dict())
        self._current_pipeline = options.pipeline
        if options.recognition_mode in self._mode_buttons:
            self._on_mode_clicked(options.recognition_mode)
            return
        for p, btn in self._pipeline_buttons.items():
            btn.setChecked(p == options.pipeline)
