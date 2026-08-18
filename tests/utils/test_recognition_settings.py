"""Classic-owned recognition settings 的公开行为契约。"""

from __future__ import annotations

from vibeocr.classic.recognition_settings import (
    ExportSettings,
    LINE_MODE_MERGE,
    LINE_MODE_SMART,
    OCROptions,
    PdfGlobalSettings,
    TextBlockOptions,
)
from vibeocr.runtime_contracts.contracts.mineru import (
    MINERU_BACKEND_DEFAULT,
    MINERU_EFFORT_DEFAULT,
)
from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline


def test_ocr_options_keep_preferences_but_emit_only_supported_wire_fields() -> None:
    options = OCROptions(
        pipeline=OCRPipeline.TABLE_RECOGNITION,
        use_doc_orientation_classify=False,
        use_doc_unwarping=True,
        use_table_orientation_classify=False,
        use_ocr_results_with_table_cells=True,
        formula_recognition_model_name="preference-only",
    )

    restored = OCROptions.from_dict(options.to_dict())
    selection = restored.to_pipeline_selection()

    assert restored.pipeline is OCRPipeline.TABLE_RECOGNITION
    assert restored.formula_recognition_model_name == "preference-only"
    assert restored.backend == MINERU_BACKEND_DEFAULT
    assert restored.effort == MINERU_EFFORT_DEFAULT
    assert selection.pipeline_id == "TABLE_RECOGNITION"
    assert selection.options == {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": True,
        "use_table_orientation_classify": False,
        "use_ocr_results_with_table_cells": True,
    }


def test_ocr_options_ignore_legacy_backend_only_preference_keys() -> None:
    options = OCROptions.from_dict(
        {
            "pipeline": "TABLE_RECOGNITION",
            "use_wireless_table": False,
            "use_e2e_wired_table_rec_model": True,
            "text_det_thresh": 0.3,
            "use_table_orientation_classify": False,
        }
    )

    assert "use_wireless_table" not in options.to_dict()
    assert "use_e2e_wired_table_rec_model" not in options.to_dict()
    assert "text_det_thresh" not in options.to_dict()
    assert options.use_table_orientation_classify is False


def test_ocr_options_map_legacy_pipeline_member_name_to_protocol_value() -> None:
    options = OCROptions.from_dict({"pipeline": "DOCUMENT_PARSING"})

    assert options.pipeline is OCRPipeline.DOCUMENT_PARSING
    assert options.to_pipeline_selection().pipeline_id == "MinerU"


def test_ocr_options_fallback_for_non_string_pipeline_values() -> None:
    for pipeline_value in (None, 42, True, [], {}):
        options = OCROptions.from_dict({"pipeline": pipeline_value})

        assert options.pipeline is OCRPipeline.OCR
        assert options.to_dict()["pipeline"] == OCRPipeline.OCR.value


def test_text_block_options_roundtrip_and_fallback_invalid_line_mode() -> None:
    options = TextBlockOptions.from_dict(
        {
            "line_mode": LINE_MODE_SMART,
            "block_join_space": 1,
            "chinese_indent": True,
            "drop_blank_blocks": 0,
        }
    )

    assert options.to_dict() == {
        "line_mode": "smart",
        "block_join_space": True,
        "chinese_indent": True,
        "drop_blank_blocks": False,
    }
    assert TextBlockOptions.from_dict({"line_mode": "invalid"}).line_mode == (
        LINE_MODE_MERGE
    )


def test_pdf_global_settings_preserve_save_flags_and_adjust_dpi() -> None:
    settings = PdfGlobalSettings.from_dict(
        {
            "render_dpi": 300,
            "max_pixels": 1_000_000,
            "compress_on_save": False,
            "clean_on_save": True,
        }
    )

    assert settings.adjust_dpi(612, 792) == 103
    assert settings.compress_on_save is False
    assert settings.clean_on_save is True
    assert settings.to_wire_payload() == settings.to_dict()
    assert PdfGlobalSettings(max_pixels=10).adjust_dpi(612, 792) == 72


def test_export_settings_remain_mutable_with_stable_labels_and_extensions() -> None:
    settings = ExportSettings()

    settings.format = "xlsx"
    settings.location_mode = "custom"
    settings.custom_directory = "C:/exports"

    assert settings.get_extension() == ".xlsx"
    assert settings.get_label() == "Excel 表格 (.xlsx)"
    assert settings.location_mode == "custom"
    assert settings.custom_directory == "C:/exports"


def test_ocr_pipeline_selection_sends_task_engine_override() -> None:
    selection = OCROptions(
        pipeline=OCRPipeline.OCR, engine="windows"
    ).to_pipeline_selection()

    assert selection.engine is not None
    assert selection.engine.value == "windows"
    assert selection.to_payload()["engine"] == "windows"


def test_ocr_pipeline_selection_uses_global_default_without_override() -> None:
    selection = OCROptions(pipeline=OCRPipeline.OCR).to_pipeline_selection(
        default_engine="paddleocr"
    )

    assert selection.engine is not None
    assert selection.engine.value == "paddleocr"


def test_ocr_pipeline_selection_omits_engine_when_unresolved() -> None:
    selection = OCROptions(pipeline=OCRPipeline.OCR).to_pipeline_selection()

    assert selection.engine is None
    assert "engine" not in selection.to_payload()

    unresolved_legacy = OCROptions(
        pipeline=OCRPipeline.OCR, engine="legacy-unknown"
    ).to_pipeline_selection(default_engine="legacy-unknown")

    assert unresolved_legacy.engine is None
    assert "engine" not in unresolved_legacy.to_payload()


def test_non_ocr_pipelines_never_send_engine() -> None:
    for pipeline in (
        OCRPipeline.PP_STRUCTURE_V3,
        OCRPipeline.PADDLEOCR_VL,
        OCRPipeline.DOCUMENT_PARSING,
    ):
        selection = OCROptions(
            pipeline=pipeline, engine="windows"
        ).to_pipeline_selection(default_engine="paddleocr")

        assert selection.engine is None
        assert "engine" not in selection.to_payload()


def test_engine_override_round_trips_through_preferences() -> None:
    options = OCROptions(pipeline=OCRPipeline.OCR, engine="windows")

    restored = OCROptions.from_dict(options.to_dict())

    assert restored.engine == "windows"
    assert restored.to_pipeline_selection().engine is not None
