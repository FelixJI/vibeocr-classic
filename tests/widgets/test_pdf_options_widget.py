# tests/widgets/test_pdf_options_widget.py
"""PdfOptionsWidget 组件测试"""

import pytest

from vibeocr.classic.recognition_settings import PdfGlobalSettings
from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline
from vibeocr.classic.widgets.pdf_options_widget import PdfOptionsWidget


@pytest.fixture
def widget(qtbot):
    """创建 PdfOptionsWidget。"""
    w = PdfOptionsWidget()
    qtbot.addWidget(w)
    return w


class TestPdfOptionsWidget:
    """PdfOptionsWidget 组件测试"""

    def test_pipeline_locked_to_text_pipelines(self, widget):
        """PDF 文字层仅允许通用 OCR / 表格 / 公式管道。"""
        assert widget.pipeline_options.is_pipeline_locked is True
        current = widget.pipeline_options.get_current_pipeline()
        allowed = {
            OCRPipeline.OCR,
            OCRPipeline.TABLE_RECOGNITION,
            OCRPipeline.FORMULA_RECOGNITION,
        }
        assert current in allowed
        # 下拉项中只有这 3 个启用，其余禁用
        combo = widget.pipeline_options._pipeline_combo
        for i in range(combo.count()):
            p = OCRPipeline(combo.itemData(i))
            assert combo.model().item(i).isEnabled() is (p in allowed)

    def test_pipeline_default_is_ocr(self, widget):
        """首次构造默认选中通用 OCR 管道。"""
        assert widget.pipeline_options.get_current_pipeline() == OCRPipeline.OCR

    def test_catalog_filters_pdf_to_supported_recognition_modes(self, widget):
        from vibeocr.classic.runtime_selection import (
            RecognitionModeEntry,
            RecognitionModeLifecycle,
            RuntimeSelectionCatalog,
        )

        modes = (
            ("rapid_text", "OCR"),
            ("paddle_table", "TABLE_RECOGNITION"),
            ("paddle_formula", "FORMULA_RECOGNITION"),
            ("mineru_document", "MinerU"),
        )
        catalog = RuntimeSelectionCatalog(
            modes=tuple(
                RecognitionModeEntry(
                    mode_id=mode_id,
                    family="text",
                    pipeline_id=pipeline_id,
                    engine="rapidocr" if pipeline_id == "OCR" else None,
                    provisioning="base_runtime",
                    availability="ready",
                    lifecycle=RecognitionModeLifecycle(
                        "unmanaged", False, False, False, False
                    ),
                    supported_options=(),
                )
                for mode_id, pipeline_id in modes
            ),
            has_recognition_mode_catalog=True,
        )

        widget.set_recognition_catalog(catalog)
        combo = widget.pipeline_options._pipeline_combo
        assert [combo.itemData(i) for i in range(combo.count())] == [
            "rapid_text",
            "paddle_table",
            "paddle_formula",
        ]

    def test_default_settings(self, widget):
        """get_settings 默认值应与 PdfGlobalSettings 默认一致。"""
        s = widget.get_settings()
        assert s.render_dpi == 300
        assert s.font_size_ratio == 0.8
        assert s.text_layer_visible is False

    def test_set_settings_round_trip(self, widget):
        """set_settings 后 get_settings 应返回相同值。"""
        custom = PdfGlobalSettings(
            render_dpi=200,
            max_pixels=8_000_000,
            font_size_ratio=0.6,
            text_layer_visible=True,
            font_size_retry_count=3,
            font_size_shrink_factor=0.5,
            min_font_size=6.0,
            compress_on_save=False,
            clean_on_save=True,
        )
        widget.set_settings(custom)
        loaded = widget.get_settings()
        assert loaded.render_dpi == 200
        assert loaded.max_pixels == 8_000_000
        assert loaded.font_size_ratio == 0.6
        assert loaded.text_layer_visible is True
        assert loaded.font_size_retry_count == 3
        assert loaded.font_size_shrink_factor == 0.5
        assert loaded.min_font_size == 6.0
        assert loaded.compress_on_save is False
        assert loaded.clean_on_save is True

    def test_settings_changed_signal(self, widget, qtbot):
        """修改 spinbox 应触发 settings_changed 信号。"""
        with qtbot.waitSignal(widget.settings_changed, timeout=1000) as blocker:
            widget._dpi_spin.setValue(150)
        assert blocker.args[0].render_dpi == 150

    def test_set_settings_does_not_emit(self, widget, qtbot):
        """set_settings 应阻塞信号，不触发 settings_changed。"""
        emitted = []
        widget.settings_changed.connect(lambda s: emitted.append(s))
        widget.set_settings(PdfGlobalSettings(render_dpi=100))
        assert emitted == []
