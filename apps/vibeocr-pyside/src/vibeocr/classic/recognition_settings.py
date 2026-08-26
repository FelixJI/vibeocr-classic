"""Classic-owned recognition, presentation, PDF, and export settings.

The Protocol package owns pipeline identifiers and supported wire fields.
Classic owns user preferences and converts them to the narrow Protocol DTO at
the submission seam; no Backend Python model is part of this interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from vibeocr.runtime_contracts import PipelineSelection
from vibeocr.runtime_contracts.dtos import OcrEngine
from vibeocr.runtime_contracts.contracts.mineru import (
    MINERU_BACKEND_DEFAULT,
    MINERU_EFFORT_DEFAULT,
)
from vibeocr.runtime_contracts.contracts.pipelines import (
    OCRPipeline,
    get_pipeline_supported_options,
)

LINE_MODE_KEEP = "keep"
LINE_MODE_MERGE = "merge"
LINE_MODE_SMART = "smart"
_VALID_LINE_MODES = frozenset({LINE_MODE_KEEP, LINE_MODE_MERGE, LINE_MODE_SMART})


@dataclass
class OCROptions:
    """User-semantic recognition settings for one Protocol pipeline."""

    pipeline: OCRPipeline = OCRPipeline.OCR
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = False
    use_table_recognition: bool = True
    use_formula_recognition: bool = True
    use_seal_recognition: bool = False
    use_chart_recognition: bool = False
    vl_use_layout_detection: bool = True
    vl_use_chart_recognition: bool = False
    vl_use_seal_recognition: bool = False
    use_ocr_for_image_block: bool = False
    parse_method: str = "auto"
    backend: str = MINERU_BACKEND_DEFAULT
    effort: str = MINERU_EFFORT_DEFAULT
    enable_formula: bool = True
    enable_table: bool = True
    lang_list: list[str] = field(default_factory=list)
    start_page_id: int = 0
    end_page_id: int | None = None
    use_table_orientation_classify: bool = True
    use_ocr_results_with_table_cells: bool = True
    formula_recognition_batch_size: int = 1
    formula_recognition_model_name: str | None = None
    formula_recognition_model_dir: str | None = None
    # 任务级引擎覆盖：None 表示沿用全局默认；仅纯文本 OCR pipeline 有效。
    engine: str | None = None
    # 用户识别模式只在 Classic 内持久化；当前正式 SDK 尚未接收此请求字段。
    recognition_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline.value,
            "use_doc_orientation_classify": self.use_doc_orientation_classify,
            "use_doc_unwarping": self.use_doc_unwarping,
            "use_textline_orientation": self.use_textline_orientation,
            "use_table_recognition": self.use_table_recognition,
            "use_formula_recognition": self.use_formula_recognition,
            "use_seal_recognition": self.use_seal_recognition,
            "use_chart_recognition": self.use_chart_recognition,
            "vl_use_layout_detection": self.vl_use_layout_detection,
            "vl_use_chart_recognition": self.vl_use_chart_recognition,
            "vl_use_seal_recognition": self.vl_use_seal_recognition,
            "use_ocr_for_image_block": self.use_ocr_for_image_block,
            "parse_method": self.parse_method,
            "backend": self.backend,
            "effort": self.effort,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "lang_list": self.lang_list,
            "start_page_id": self.start_page_id,
            "end_page_id": self.end_page_id,
            "use_table_orientation_classify": self.use_table_orientation_classify,
            "use_ocr_results_with_table_cells": self.use_ocr_results_with_table_cells,
            "formula_recognition_batch_size": self.formula_recognition_batch_size,
            "formula_recognition_model_name": self.formula_recognition_model_name,
            "formula_recognition_model_dir": self.formula_recognition_model_dir,
            "engine": self.engine,
            "recognition_mode": self.recognition_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OCROptions:
        pipeline_value = data.get("pipeline", OCRPipeline.OCR.value)
        if isinstance(pipeline_value, str):
            try:
                pipeline = OCRPipeline(pipeline_value)
            except ValueError:
                pipeline = OCRPipeline.__members__.get(pipeline_value.upper())
                if pipeline is None:
                    pipeline = next(
                        (
                            candidate
                            for candidate in OCRPipeline
                            if candidate.value.lower() == pipeline_value.lower()
                        ),
                        OCRPipeline.OCR,
                    )
        elif isinstance(pipeline_value, OCRPipeline):
            pipeline = pipeline_value
        else:
            pipeline = OCRPipeline.OCR
        defaults = cls(pipeline=pipeline)
        values = defaults.to_dict()
        values.update(data)
        values["pipeline"] = pipeline
        return cls(**{name: values[name] for name in cls.__dataclass_fields__})

    def copy(self, **updates: Any) -> OCROptions:
        data = self.to_dict()
        data.update(updates)
        return OCROptions.from_dict(data)

    def to_pipeline_selection(
        self, default_engine: str | None = None
    ) -> PipelineSelection:
        """投影到 Protocol PipelineSelection。

        ``engine`` 只对纯文本 OCR pipeline 发送：任务 override 优先，其次
        是全局默认 ``default_engine``；两者都不是稳定 engine id 时省略
        字段，交给 Backend 默认。其他 pipeline 不携带 engine。
        """

        pipeline = self.pipeline
        engine_override = self.engine
        # 所有入口（主界面、截图、批量、PDF）最终都经过本方法。模式只在
        # Classic 内解析，随后仍构造现有的严格 PipelineSelection wire。
        from vibeocr.classic.runtime_selection import (
            execution_projection_for_mode,
            recognition_mode_for_engine,
        )

        mode_id = self.recognition_mode
        if mode_id is None and pipeline is OCRPipeline.OCR:
            mode_id = recognition_mode_for_engine(engine_override or default_engine)
        if mode_id is not None:
            projection = execution_projection_for_mode(mode_id)
            if projection is None:
                from vibeocr.classic.runtime_selection import RuntimeSelectionError

                raise RuntimeSelectionError(f"未知 recognition mode: {mode_id}")
            projected_pipeline, projected_engine = projection
            try:
                pipeline = OCRPipeline(projected_pipeline)
            except ValueError as exc:
                # 协商目录必须与本地 SDK 的已发布 pipeline enum 一致。
                from vibeocr.classic.runtime_selection import RuntimeSelectionError

                raise RuntimeSelectionError(
                    f"recognition mode 投影了未知 pipeline: {mode_id}"
                ) from exc
            engine_override = projected_engine

        allowed = set(get_pipeline_supported_options(pipeline))
        options = {
            key: value
            for key, value in self.to_dict().items()
            if key in allowed and value is not None
        }
        engine: OcrEngine | None = None
        if pipeline is OCRPipeline.OCR:
            from vibeocr.classic.runtime_selection import resolve_engine_id

            resolved = resolve_engine_id(engine_override, default_engine)
            if resolved is not None:
                engine = OcrEngine(resolved)
        return PipelineSelection(pipeline.value, options=options, engine=engine)


@dataclass
class TextBlockOptions:
    """Classic-only OCR text layout preferences."""

    line_mode: str = LINE_MODE_MERGE
    block_join_space: bool = False
    chinese_indent: bool = False
    drop_blank_blocks: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_mode": self.line_mode,
            "block_join_space": self.block_join_space,
            "chinese_indent": self.chinese_indent,
            "drop_blank_blocks": self.drop_blank_blocks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TextBlockOptions:
        if not data:
            return cls()
        line_mode = data.get("line_mode", LINE_MODE_MERGE)
        if line_mode not in _VALID_LINE_MODES:
            line_mode = LINE_MODE_MERGE
        return cls(
            line_mode=line_mode,
            block_join_space=bool(data.get("block_join_space", False)),
            chinese_indent=bool(data.get("chinese_indent", False)),
            drop_blank_blocks=bool(data.get("drop_blank_blocks", True)),
        )


@dataclass
class PdfGlobalSettings:
    """Classic PDF rendering preferences and their Runtime wire projection."""

    render_dpi: int = 300
    max_pixels: int = 16_000_000
    font_size_ratio: float = 0.8
    text_layer_visible: bool = False
    font_size_retry_count: int = 5
    font_size_shrink_factor: float = 0.75
    min_font_size: float = 4.0
    compress_on_save: bool = True
    clean_on_save: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "render_dpi": self.render_dpi,
            "max_pixels": self.max_pixels,
            "font_size_ratio": self.font_size_ratio,
            "text_layer_visible": self.text_layer_visible,
            "font_size_retry_count": self.font_size_retry_count,
            "font_size_shrink_factor": self.font_size_shrink_factor,
            "min_font_size": self.min_font_size,
            "compress_on_save": self.compress_on_save,
            "clean_on_save": self.clean_on_save,
        }

    def to_wire_payload(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PdfGlobalSettings:
        if not data:
            return cls()
        defaults = cls().to_dict()
        return cls(
            **{name: data.get(name, default) for name, default in defaults.items()}
        )

    def adjust_dpi(self, page_width: float, page_height: float) -> int:
        pixel_width = page_width / 72.0 * self.render_dpi
        pixel_height = page_height / 72.0 * self.render_dpi
        total_pixels = pixel_width * pixel_height
        if total_pixels <= self.max_pixels:
            return self.render_dpi
        adjusted = int(self.render_dpi * math.sqrt(self.max_pixels / total_pixels))
        return max(72, adjusted)


@dataclass
class ExportSettings:
    """Mutable export form state owned by the Classic widget."""

    format: str = "markdown"
    location_mode: str = "same_as_source"
    custom_directory: str = ""
    last_custom_directory: str = ""

    FORMAT_EXTENSIONS: ClassVar[dict[str, str]] = {
        "markdown": ".md",
        "html": ".html",
        "docx": ".docx",
        "xlsx": ".xlsx",
        "txt": ".txt",
    }
    FORMAT_LABELS: ClassVar[dict[str, str]] = {
        "markdown": "Markdown 文档 (.md)",
        "html": "HTML 网页 (.html)",
        "docx": "Word 文档 (.docx)",
        "xlsx": "Excel 表格 (.xlsx)",
        "txt": "纯文本 (.txt)",
    }

    def get_extension(self) -> str:
        return self.FORMAT_EXTENSIONS.get(self.format, ".txt")

    def get_label(self) -> str:
        return self.FORMAT_LABELS.get(self.format, "未知格式")


__all__ = [
    "ExportSettings",
    "LINE_MODE_KEEP",
    "LINE_MODE_MERGE",
    "LINE_MODE_SMART",
    "OCROptions",
    "PdfGlobalSettings",
    "TextBlockOptions",
]
