"""OCR 选项持久化管理器

模块级单例，作为所有界面的 OCR 选项统一数据源。
支持按管道独立存储，区分 main 和 screenshot 两个数据源。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from vibeocr.backend.core.pipelines import OCRPipeline
from vibeocr.backend.models.ocr_options import OCROptions
from vibeocr.backend.models.text_block_options import TextBlockOptions

if TYPE_CHECKING:
    from vibeocr.backend.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.classic.managers.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "ocr_preferences.json"
_CONFIG_VERSION = 4

_instance: OCRPreferences | None = None


class OCRPreferences(QObject):
    """OCR 选项持久化管理器

    所有 OCR 选项的统一数据源，提供跨界面同步和持久化。
    支持按管道独立存储，区分 "main"（主界面）、"screenshot"（截图面板）、
    "pdf"（PDF 处理）三个数据源。

    Usage:
        prefs = OCRPreferences.instance(config_manager)
        options = prefs.get_pipeline_options("main", OCRPipeline.OCR)
        prefs.set_pipeline_options("main", OCRPipeline.OCR, new_options)
    """

    options_changed = Signal(object)  # OCROptions (legacy)
    batch_options_changed = Signal(object)  # OCROptions
    pipeline_options_changed = Signal(str, object)  # (source, OCROptions)

    def __init__(self, config_manager: ConfigManager | Path) -> None:
        super().__init__()
        if isinstance(config_manager, Path):
            self._cm = None
            self._config_dir = config_manager
            self._config_path = config_manager / _CONFIG_FILENAME
        else:
            self._cm = config_manager
            self._config_dir = config_manager.config_dir
            self._config_path = self._config_dir / _CONFIG_FILENAME

        self._per_pipeline: dict[str, dict[str, OCROptions]] = {
            "main": {},
            "screenshot": {},
            "pdf": {},
        }
        self._batch_options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        self._last_main_pipeline: OCRPipeline = OCRPipeline.OCR
        self._pdf_settings: dict = {}  # PdfGlobalSettings raw dict
        self._pdf_splitter_state: bytes | None = None
        self._pdf_right_splitter_state: bytes | None = None
        self._last_pdf_pipeline: OCRPipeline = OCRPipeline.OCR
        # 文本块处理选项（与 OCR 引擎/管道无关，不按管道/来源分，独立顶层字段）
        self._text_block_options = TextBlockOptions()
        self._load()

    @staticmethod
    def instance(
        config_manager: ConfigManager | Path | None = None,
    ) -> OCRPreferences:
        global _instance
        if _instance is None:
            if config_manager is None:
                raise RuntimeError("OCRPreferences 首次创建必须传入 config_manager")
            _instance = OCRPreferences(config_manager)
        return _instance

    @staticmethod
    def reset_instance() -> None:
        global _instance
        _instance = None

    def _load(self) -> None:
        if self._cm is not None:
            data = self._cm._load_json(_CONFIG_FILENAME)
        else:
            if not self._config_path.exists():
                return
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"加载 OCR 选项失败: {e}")
                return

        if not data:
            return

        version = data.get("version", 1)

        if version < 2:
            pipeline_name = data.get("pipeline", "OCR")
            self._per_pipeline["main"][pipeline_name] = OCROptions.from_dict(data)
            try:
                self._last_main_pipeline = OCRPipeline(pipeline_name)
            except ValueError:
                self._last_main_pipeline = OCRPipeline.OCR
        else:
            for source in ("main", "screenshot", "pdf"):
                source_data = data.get(source, {})
                for pipeline_name, opts_dict in source_data.items():
                    self._per_pipeline.setdefault(source, {})[pipeline_name] = (
                        OCROptions.from_dict(opts_dict)
                    )
            last = data.get("last_main_pipeline", "OCR")
            try:
                self._last_main_pipeline = OCRPipeline(last)
            except ValueError:
                self._last_main_pipeline = OCRPipeline.OCR
            last_pdf = data.get("last_pdf_pipeline", "OCR")
            try:
                self._last_pdf_pipeline = OCRPipeline(last_pdf)
            except ValueError:
                self._last_pdf_pipeline = OCRPipeline.OCR

        batch_data = data.get("batch_options")
        if batch_data:
            self._batch_options = OCROptions.from_dict(batch_data)

        pdf_settings_data = data.get("pdf_settings")
        if pdf_settings_data and isinstance(pdf_settings_data, dict):
            self._pdf_settings = pdf_settings_data

        splitter_b64 = data.get("pdf_splitter_state")
        if splitter_b64 and isinstance(splitter_b64, str):
            self._pdf_splitter_state = base64.b64decode(splitter_b64)
        else:
            self._pdf_splitter_state = None

        right_b64 = data.get("pdf_right_splitter_state")
        if right_b64 and isinstance(right_b64, str):
            self._pdf_right_splitter_state = base64.b64decode(right_b64)
        else:
            self._pdf_right_splitter_state = None

        # 文本块处理选项（版本 <4 的旧配置无此字段，from_dict({}) 走默认值）
        self._text_block_options = TextBlockOptions.from_dict(
            data.get("text_block_options")
        )

        logger.debug("OCR 选项已加载")

    def get_pipeline_options(self, source: str, pipeline: OCRPipeline) -> OCROptions:
        """读取指定区域指定管道的选项，不存在则返回默认"""
        opts = self._per_pipeline.get(source, {}).get(pipeline.value)
        if opts:
            return OCROptions.from_dict(opts.to_dict())
        return OCROptions(pipeline=pipeline)

    def set_pipeline_options(
        self, source: str, pipeline: OCRPipeline, options: OCROptions
    ) -> None:
        """保存到指定区域并持久化"""
        if source not in self._per_pipeline:
            self._per_pipeline[source] = {}
        self._per_pipeline[source][pipeline.value] = OCROptions.from_dict(
            options.to_dict()
        )
        if source == "main":
            self._last_main_pipeline = pipeline
        elif source == "pdf":
            self._last_pdf_pipeline = pipeline
        self.save()
        self.pipeline_options_changed.emit(source, options)

    def get_options(self) -> OCROptions:
        """获取主界面最后使用的管道选项（向后兼容）"""
        return self.get_pipeline_options("main", self._last_main_pipeline)

    def set_options(self, options: OCROptions) -> None:
        """设置主界面选项（向后兼容）"""
        self._last_main_pipeline = options.pipeline
        self._per_pipeline.setdefault("main", {})[options.pipeline.value] = (
            OCROptions.from_dict(options.to_dict())
        )
        self.save()
        self.options_changed.emit(options)

    def get_batch_options(self) -> OCROptions:
        return self._batch_options

    def set_batch_options(self, options: OCROptions) -> None:
        self._batch_options = OCROptions.from_dict(options.to_dict())
        self.save()
        self.batch_options_changed.emit(self._batch_options)

    # ---- 文本块处理选项 ----

    def get_text_options(self) -> TextBlockOptions:
        """获取文本块处理选项（与管道/来源无关的顶层设置）。"""
        return TextBlockOptions.from_dict(self._text_block_options.to_dict())

    def set_text_options(self, options: TextBlockOptions) -> None:
        """保存文本块处理选项并持久化。"""
        self._text_block_options = TextBlockOptions.from_dict(options.to_dict())
        self.save()

    def save(self) -> bool:
        save_data = {
            "version": _CONFIG_VERSION,
            "last_main_pipeline": self._last_main_pipeline.value,
            "last_pdf_pipeline": self._last_pdf_pipeline.value,
            "main": {
                k: v.to_dict() for k, v in self._per_pipeline.get("main", {}).items()
            },
            "screenshot": {
                k: v.to_dict()
                for k, v in self._per_pipeline.get("screenshot", {}).items()
            },
            "pdf": {
                k: v.to_dict() for k, v in self._per_pipeline.get("pdf", {}).items()
            },
            "pdf_settings": self._pdf_settings,
            "pdf_splitter_state": (
                base64.b64encode(self._pdf_splitter_state).decode("ascii")
                if self._pdf_splitter_state is not None
                else None
            ),
            "pdf_right_splitter_state": (
                base64.b64encode(self._pdf_right_splitter_state).decode("ascii")
                if self._pdf_right_splitter_state is not None
                else None
            ),
            "batch_options": self._batch_options.to_dict(),
            "text_block_options": self._text_block_options.to_dict(),
        }
        if self._cm is not None:
            return self._cm._save_json(_CONFIG_FILENAME, save_data)
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存 OCR 选项失败: {e}")
            return False

    # ---- PDF 全局设置 ----

    def get_pdf_settings(self) -> PdfGlobalSettings:
        """获取 PDF 全局设置。"""
        from vibeocr.backend.models.pdf_ocr_options import PdfGlobalSettings

        return PdfGlobalSettings.from_dict(self._pdf_settings)

    def set_pdf_settings(self, settings: PdfGlobalSettings) -> None:
        """保存 PDF 全局设置。"""
        self._pdf_settings = settings.to_dict()
        self.save()

    def get_pdf_splitter_state(self) -> bytes | None:
        """获取 PDF 主 splitter 布局状态（QSplitter.saveState().data() 的 bytes）。"""
        return self._pdf_splitter_state

    def set_pdf_splitter_state(self, state: bytes | None) -> None:
        """保存 PDF 主 splitter 布局状态并持久化（None 表示清除）。"""
        self._pdf_splitter_state = state
        self.save()

    def get_pdf_right_splitter_state(self) -> bytes | None:
        """获取 PDF 右侧（纵向）splitter 布局状态。"""
        return self._pdf_right_splitter_state

    def set_pdf_right_splitter_state(self, state: bytes | None) -> None:
        """保存 PDF 右侧（纵向）splitter 布局状态并持久化。"""
        self._pdf_right_splitter_state = state
        self.save()

    def set_pdf_splitter_states(self, main: bytes | None, right: bytes | None) -> None:
        """一次性保存两个 splitter 布局状态并持久化（避免连续两次落盘）。"""
        self._pdf_splitter_state = main
        self._pdf_right_splitter_state = right
        self.save()

    def get_pdf_pipeline_options(self) -> OCROptions:
        """读取 PDF 末次使用管道的选项，不存在则返回默认。

        封装 `get_pipeline_options("pdf", self._last_pdf_pipeline)`，
        供 PdfTab 调用，避免外部访问私有字段。
        """
        return self.get_pipeline_options("pdf", self._last_pdf_pipeline)

    def set_pdf_pipeline_options(self, options: OCROptions) -> None:
        """保存 PDF 管道选项（更新末次管道并持久化）。"""
        self.set_pipeline_options("pdf", options.pipeline, options)
