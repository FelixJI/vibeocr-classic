"""右侧 OCR 识别设置面板

复用 PreprocessOptionsWidget，提供管道选择、预处理选项和参数配置。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.preprocess_options_widget import PreprocessOptionsWidget


class RecognitionPanel(QWidget):
    """右侧识别设置面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recognitionPanel")
        self.setFixedWidth(theme.Layout.panel_width)
        self.setStyleSheet(theme.panel_qss())
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 标题
        title = QLabel("识别设置")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        # 预处理选项组件（复用已有组件）
        self._options_widget = PreprocessOptionsWidget()
        scroll.setWidget(self._options_widget)

        layout.addWidget(scroll, 1)

    def get_options(self) -> OCROptions:
        """获取当前 OCR 选项"""
        return self._options_widget.get_options()

    def set_options(self, options: OCROptions) -> None:
        """设置 OCR 选项"""
        self._options_widget.set_options(options)
