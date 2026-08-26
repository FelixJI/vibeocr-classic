# src/vibeocr/widgets/pdf_options_widget.py
"""PDF OCR 选项组件

包含管道选择（锁定为文档类）、管道选项和 PDF 全局设置。
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.recognition_settings import PdfGlobalSettings
from vibeocr.classic.widgets.preprocess_options_widget import PreprocessOptionsWidget


class PdfOptionsWidget(QWidget):
    """PDF OCR 选项组件。

    组合了 PreprocessOptionsWidget（管道选项，锁定为文档类管道）
    和 PDF 全局设置（DPI、内存、字号等）。
    """

    settings_changed = Signal(object)  # PdfGlobalSettings

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_settings = PdfGlobalSettings()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 管道选项（复用 PreprocessOptionsWidget，初始化后锁定管道）
        self._pipeline_options = PreprocessOptionsWidget()
        # PDF 文字层仅支持能正确返回 preproc_angle 的文本类管道
        # （MinerU/VL 文档理解模型不适合用于嵌入隐形文字层）
        from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

        self._pipeline_options.lock_to_pipelines(
            {
                OCRPipeline.OCR,
                OCRPipeline.TABLE_RECOGNITION,
                OCRPipeline.FORMULA_RECOGNITION,
            },
            reason="PDF 文字层",
            default=OCRPipeline.OCR,
        )
        layout.addWidget(self._pipeline_options)

        # PDF 全局设置
        settings_group = QGroupBox("PDF 渲染设置")
        settings_layout = QVBoxLayout(settings_group)

        # DPI
        dpi_layout = QHBoxLayout()
        dpi_layout.addWidget(QLabel("渲染 DPI:"))
        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 600)
        self._dpi_spin.setValue(300)
        self._dpi_spin.setToolTip("PDF 页面渲染分辨率，越高越清晰但内存占用越大")
        dpi_layout.addWidget(self._dpi_spin)
        dpi_layout.addStretch()
        settings_layout.addLayout(dpi_layout)

        # 最大像素
        pixels_layout = QHBoxLayout()
        pixels_layout.addWidget(QLabel("单页像素上限:"))
        self._max_pixels_spin = QSpinBox()
        self._max_pixels_spin.setRange(1_000_000, 100_000_000)
        self._max_pixels_spin.setValue(16_000_000)
        self._max_pixels_spin.setSingleStep(1_000_000)
        self._max_pixels_spin.setToolTip("超过此限制时自动降低渲染 DPI")
        pixels_layout.addWidget(self._max_pixels_spin)
        pixels_layout.addStretch()
        settings_layout.addLayout(pixels_layout)

        # 字号比例
        font_layout = QHBoxLayout()
        font_layout.addWidget(QLabel("字号比例:"))
        self._font_ratio_spin = QDoubleSpinBox()
        self._font_ratio_spin.setRange(0.1, 1.0)
        self._font_ratio_spin.setSingleStep(0.05)
        self._font_ratio_spin.setValue(0.8)
        self._font_ratio_spin.setToolTip("字号 = 文字块高度 × 此比例")
        font_layout.addWidget(self._font_ratio_spin)
        font_layout.addStretch()
        settings_layout.addLayout(font_layout)

        # 重试次数
        retry_layout = QHBoxLayout()
        retry_layout.addWidget(QLabel("字号重试次数:"))
        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(1, 20)
        self._retry_spin.setValue(5)
        self._retry_spin.setToolTip("文字溢出时缩小字号重试的最大次数")
        retry_layout.addWidget(self._retry_spin)
        retry_layout.addStretch()
        settings_layout.addLayout(retry_layout)

        # 缩放因子
        shrink_layout = QHBoxLayout()
        shrink_layout.addWidget(QLabel("缩放因子:"))
        self._shrink_spin = QDoubleSpinBox()
        self._shrink_spin.setRange(0.1, 1.0)
        self._shrink_spin.setSingleStep(0.05)
        self._shrink_spin.setValue(0.75)
        self._shrink_spin.setToolTip("每次重试字号乘以此因子")
        shrink_layout.addWidget(self._shrink_spin)
        shrink_layout.addStretch()
        settings_layout.addLayout(shrink_layout)

        # 最小字号
        minfont_layout = QHBoxLayout()
        minfont_layout.addWidget(QLabel("最小字号:"))
        self._minfont_spin = QDoubleSpinBox()
        self._minfont_spin.setRange(1.0, 24.0)
        self._minfont_spin.setSingleStep(0.5)
        self._minfont_spin.setValue(4.0)
        self._minfont_spin.setToolTip("矮行/窄框时字号下限，保证隐形文字可被提取")
        minfont_layout.addWidget(self._minfont_spin)
        minfont_layout.addStretch()
        settings_layout.addLayout(minfont_layout)

        # 文字层可见
        self._visible_cb = QCheckBox("文字层可见（调试用）")
        self._visible_cb.setToolTip("启用后写入可见文字，方便调试 bbox 位置")
        self._visible_cb.setChecked(False)
        settings_layout.addWidget(self._visible_cb)

        layout.addWidget(settings_group)
        layout.addStretch()

        # 连接信号
        self._dpi_spin.valueChanged.connect(self._on_settings_changed)
        self._max_pixels_spin.valueChanged.connect(self._on_settings_changed)
        self._font_ratio_spin.valueChanged.connect(self._on_settings_changed)
        self._retry_spin.valueChanged.connect(self._on_settings_changed)
        self._shrink_spin.valueChanged.connect(self._on_settings_changed)
        self._minfont_spin.valueChanged.connect(self._on_settings_changed)
        self._visible_cb.toggled.connect(self._on_settings_changed)

    def set_recognition_catalog(self, catalog) -> None:
        """PDF 文字层只投影能返回文字层输入的 mode。"""
        self._pipeline_options.set_recognition_catalog(catalog)

    def _on_settings_changed(self):
        settings = self.get_settings()
        self._current_settings = settings
        self.settings_changed.emit(settings)

    @property
    def pipeline_options(self) -> PreprocessOptionsWidget:
        """获取底层管道选项组件。"""
        return self._pipeline_options

    def get_settings(self) -> PdfGlobalSettings:
        return PdfGlobalSettings(
            render_dpi=self._dpi_spin.value(),
            max_pixels=self._max_pixels_spin.value(),
            font_size_ratio=self._font_ratio_spin.value(),
            text_layer_visible=self._visible_cb.isChecked(),
            font_size_retry_count=self._retry_spin.value(),
            font_size_shrink_factor=self._shrink_spin.value(),
            min_font_size=self._minfont_spin.value(),
            # 这两个保存策略尚未暴露为控件，但从偏好设置载入后不能在用户
            # 调整 DPI/字号时悄悄重置为默认值。
            compress_on_save=self._current_settings.compress_on_save,
            clean_on_save=self._current_settings.clean_on_save,
        )

    def set_settings(self, settings: PdfGlobalSettings):
        """设置全局参数（不触发信号）。"""
        self._current_settings = settings
        widgets = [
            self._dpi_spin,
            self._max_pixels_spin,
            self._font_ratio_spin,
            self._retry_spin,
            self._shrink_spin,
            self._minfont_spin,
            self._visible_cb,
        ]
        for w in widgets:
            w.blockSignals(True)

        self._dpi_spin.setValue(settings.render_dpi)
        self._max_pixels_spin.setValue(settings.max_pixels)
        self._font_ratio_spin.setValue(settings.font_size_ratio)
        self._retry_spin.setValue(settings.font_size_retry_count)
        self._shrink_spin.setValue(settings.font_size_shrink_factor)
        self._minfont_spin.setValue(settings.min_font_size)
        self._visible_cb.setChecked(settings.text_layer_visible)

        for w in widgets:
            w.blockSignals(False)
