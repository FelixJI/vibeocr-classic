"""导出设置组件

提供导出格式选择、导出位置选择和导出按钮。
"""

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend.models.export_settings import ExportSettings

logger = logging.getLogger(__name__)


class ExportSettingsWidget(QWidget):
    """导出设置组件

    支持 5 种导出格式、两种位置模式和自定义路径记忆。
    """

    export_requested = Signal(str, object)  # format, OCRResult
    export_all_requested = Signal(str)  # format

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = ExportSettings()
        self._current_result = None

        self._setup_ui()
        self._load_settings()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(4, 4, 4, 4)

        # 导出格式
        format_layout = QHBoxLayout()
        format_label = QLabel("格式:")
        self._format_combo = QComboBox()
        for key, label in ExportSettings.FORMAT_LABELS.items():
            self._format_combo.addItem(label, key)

        format_layout.addWidget(format_label)
        format_layout.addWidget(self._format_combo, stretch=1)
        layout.addLayout(format_layout)

        # 导出位置
        self._same_radio = QRadioButton("与源文件相同目录")
        self._custom_radio = QRadioButton("自定义目录:")
        self._same_radio.setChecked(True)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("选择导出目录...")
        self._path_edit.setEnabled(False)

        self._browse_btn = QPushButton("浏览")
        self._browse_btn.setFixedWidth(50)
        self._browse_btn.setEnabled(False)

        path_layout = QHBoxLayout()
        path_layout.addWidget(self._path_edit, stretch=1)
        path_layout.addWidget(self._browse_btn)

        layout.addWidget(self._same_radio)
        layout.addWidget(self._custom_radio)
        layout.addLayout(path_layout)

        # 导出按钮
        btn_layout = QHBoxLayout()
        self._export_btn = QPushButton("导出当前")
        self._export_all_btn = QPushButton("导出全部")
        # 初始无结果，「导出当前」禁用（set_current_result 会随结果切换）
        self._export_btn.setEnabled(False)
        btn_layout.addWidget(self._export_btn)
        btn_layout.addWidget(self._export_all_btn)
        layout.addLayout(btn_layout)

        self._connect_signals()

    def _connect_signals(self) -> None:
        self._same_radio.toggled.connect(self._on_location_mode_changed)
        self._custom_radio.toggled.connect(self._on_location_mode_changed)
        self._browse_btn.clicked.connect(self._on_browse)
        self._format_combo.currentIndexChanged.connect(self._on_settings_changed)
        self._export_btn.clicked.connect(self._on_export)
        self._export_all_btn.clicked.connect(self._on_export_all)

    def _load_settings(self) -> None:
        """从 ConfigManager 加载导出设置"""
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            config = ConfigManager.instance()
            data = config.get_export_settings()

            self._settings.format = data.get("format", "markdown")
            self._settings.location_mode = data.get("location_mode", "same_as_source")
            self._settings.custom_directory = data.get("custom_directory", "")
            self._settings.last_custom_directory = data.get("last_custom_directory", "")

            # 应用到 UI
            idx = self._format_combo.findData(self._settings.format)
            if idx >= 0:
                self._format_combo.setCurrentIndex(idx)

            if self._settings.location_mode == "custom":
                self._custom_radio.setChecked(True)
                self._path_edit.setText(self._settings.custom_directory)
                self._path_edit.setEnabled(True)
                self._browse_btn.setEnabled(True)
            else:
                self._same_radio.setChecked(True)
        except Exception as e:
            logger.warning("加载导出设置失败: %s", e)

    def _save_settings(self) -> None:
        """保存导出设置到 ConfigManager"""
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            config = ConfigManager.instance()
            config.save_export_settings(
                {
                    "format": self._settings.format,
                    "location_mode": self._settings.location_mode,
                    "custom_directory": self._settings.custom_directory,
                    "last_custom_directory": self._settings.last_custom_directory,
                }
            )
        except Exception as e:
            logger.warning("保存导出设置失败: %s", e)

    def _on_location_mode_changed(self) -> None:
        is_custom = self._custom_radio.isChecked()
        self._path_edit.setEnabled(is_custom)
        self._browse_btn.setEnabled(is_custom)
        self._settings.location_mode = "custom" if is_custom else "same_as_source"

        if is_custom and self._settings.last_custom_directory:
            self._path_edit.setText(self._settings.last_custom_directory)
            self._settings.custom_directory = self._settings.last_custom_directory

        self._save_settings()

    def _on_browse(self) -> None:
        """选择自定义导出目录"""
        start_dir = (
            self._settings.custom_directory or self._settings.last_custom_directory
        )
        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录", start_dir)
        if dir_path:
            self._path_edit.setText(dir_path)
            self._settings.custom_directory = dir_path
            self._settings.last_custom_directory = dir_path
            self._save_settings()

    def _on_settings_changed(self) -> None:
        self._settings.format = self._format_combo.currentData() or "markdown"
        self._save_settings()

    def _on_export(self) -> None:
        """导出当前文件"""
        self._on_settings_changed()
        if self._current_result:
            self.export_requested.emit(self._settings.format, self._current_result)

    def _on_export_all(self) -> None:
        """导出全部文件"""
        self._on_settings_changed()
        self.export_all_requested.emit(self._settings.format)

    def set_current_result(self, result) -> None:
        """设置当前显示的结果。

        同时据此启用/禁用「导出当前」按钮：无结果时禁用，避免点击后静默无反应。
        （「导出全部」不依赖单个结果，始终保持可用。）
        """
        self._current_result = result
        self._export_btn.setEnabled(result is not None)

    def get_export_dir(self, source_path: str = "") -> str:
        """获取导出目录"""
        if self._settings.location_mode == "custom" and self._settings.custom_directory:
            return self._settings.custom_directory
        if source_path:
            from pathlib import Path

            return str(Path(source_path).parent)
        return self._settings.last_custom_directory or ""

    def get_settings(self) -> ExportSettings:
        return self._settings
