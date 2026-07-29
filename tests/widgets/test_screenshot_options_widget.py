# tests/widgets/test_screenshot_options_widget.py
"""ScreenshotOptionsWidget 测试。

覆盖：
- 按管道分组：仅支持预处理参数的管道生成块；MinerU 无块
- 各块 checkbox 变化正确持久化到 screenshot 源对应 pipeline key
- 持久化数据的 .pipeline 字段与该块管道一致（识别类型不可被设置篡改）
- GPU 门控：无 GPU 时 PaddleOCR-VL 块灰显
- 无管道下拉框（消除"选识别类型"语义）
"""

import pytest
from PySide6.QtWidgets import QComboBox

from vibeocr.backend.core.pipelines import OCRPipeline
from vibeocr.classic.utils.ocr_preferences import OCRPreferences
from vibeocr.classic.widgets.screenshot_options_widget import ScreenshotOptionsWidget

# 支持预处理参数的管道（应有块）
_PIPELINES_WITH_PREPROCESS = {
    OCRPipeline.OCR,
    OCRPipeline.PP_STRUCTURE_V3,
    OCRPipeline.PADDLEOCR_VL,
    OCRPipeline.TABLE_RECOGNITION,
    OCRPipeline.FORMULA_RECOGNITION,
}


@pytest.fixture
def widget(qtbot, tmp_path, monkeypatch):
    """创建组件，并初始化 OCRPreferences（用 tmp_path 隔离）。

    强制 GPU 缓存未就绪，避免构造时自动门控干扰断言。
    """
    import vibeocr.backend.env_manager as em

    monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
    OCRPreferences.reset_instance()
    OCRPreferences.instance(tmp_path)
    w = ScreenshotOptionsWidget()
    qtbot.addWidget(w)
    yield w
    OCRPreferences.reset_instance()


class TestGroupStructure:
    def test_no_pipeline_combobox(self, widget):
        """组件不应包含管道下拉框（消除选识别类型语义）"""
        combos = widget.findChildren(QComboBox)
        assert combos == [], "截图选项页不应有管道下拉框"

    def test_groups_for_preprocess_pipelines(self, widget):
        """每个支持预处理参数的管道都有独立块"""
        for pipeline in _PIPELINES_WITH_PREPROCESS:
            assert pipeline in widget._groups, f"{pipeline} 应有预处理块"

    def test_mineru_has_no_group(self, widget):
        """MinerU 不支持预处理参数，不应生成块"""
        assert OCRPipeline.DOCUMENT_PARSING not in widget._groups

    def test_textline_orientation_only_for_ocr_and_structure(self, widget):
        """use_textline_orientation 仅 OCR / PP-StructureV3 支持"""
        for pipeline in (OCRPipeline.OCR, OCRPipeline.PP_STRUCTURE_V3):
            assert "use_textline_orientation" in widget._groups[pipeline].checks
        for pipeline in (
            OCRPipeline.PADDLEOCR_VL,
            OCRPipeline.TABLE_RECOGNITION,
            OCRPipeline.FORMULA_RECOGNITION,
        ):
            assert (
                "use_textline_orientation" not in widget._groups[pipeline].checks
            ), f"{pipeline} 不支持文本行方向"


class TestPersistence:
    def test_checkbox_change_persists_to_correct_pipeline(self, widget, tmp_path):
        """勾选某管道块的 checkbox → 持久化到 screenshot 源对应 pipeline key"""
        group = widget._groups[OCRPipeline.TABLE_RECOGNITION]
        cb = group.checks["use_doc_orientation_classify"]
        cb.setChecked(False)

        prefs = OCRPreferences.instance()
        stored = prefs.get_pipeline_options(
            "screenshot", OCRPipeline.TABLE_RECOGNITION
        )
        assert stored.use_doc_orientation_classify is False

    def test_persisted_pipeline_field_matches_group(self, widget):
        """持久化数据的 .pipeline 字段必须与该块管道一致（不可被篡改为 OCR）"""
        group = widget._groups[OCRPipeline.FORMULA_RECOGNITION]
        group.checks["use_doc_unwarping"].setChecked(True)

        prefs = OCRPreferences.instance()
        stored = prefs.get_pipeline_options(
            "screenshot", OCRPipeline.FORMULA_RECOGNITION
        )
        assert stored.pipeline == OCRPipeline.FORMULA_RECOGNITION

    def test_groups_independent(self, widget):
        """一个管道的 checkbox 变化不影响其他管道的持久化值"""
        widget._groups[OCRPipeline.OCR].checks[
            "use_doc_orientation_classify"
        ].setChecked(False)
        widget._groups[OCRPipeline.PP_STRUCTURE_V3].checks[
            "use_doc_orientation_classify"
        ].setChecked(True)

        prefs = OCRPreferences.instance()
        ocr_stored = prefs.get_pipeline_options("screenshot", OCRPipeline.OCR)
        struct_stored = prefs.get_pipeline_options(
            "screenshot", OCRPipeline.PP_STRUCTURE_V3
        )
        assert ocr_stored.use_doc_orientation_classify is False
        assert struct_stored.use_doc_orientation_classify is True

    def test_load_populates_from_persisted(self, tmp_path, monkeypatch):
        """构造后各块回填 screenshot 源已存的参数"""
        import vibeocr.backend.env_manager as em
        from vibeocr.backend.models.ocr_options import OCROptions

        monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
        OCRPreferences.reset_instance()
        prefs = OCRPreferences.instance(tmp_path)
        prefs.set_pipeline_options(
            "screenshot",
            OCRPipeline.OCR,
            OCROptions(
                pipeline=OCRPipeline.OCR,
                use_doc_orientation_classify=False,
                use_doc_unwarping=True,
            ),
        )
        try:
            w = ScreenshotOptionsWidget()
            group = w._groups[OCRPipeline.OCR]
            assert group.checks["use_doc_orientation_classify"].isChecked() is False
            assert group.checks["use_doc_unwarping"].isChecked() is True
        finally:
            OCRPreferences.reset_instance()


class TestGpuGating:
    def test_no_gpu_disables_paddlocr_vl_group(self, widget):
        """无 GPU 时 PaddleOCR-VL 块灰显"""
        widget.apply_gpu_gating(False)
        assert widget._groups[OCRPipeline.PADDLEOCR_VL].box.isEnabled() is False

    def test_with_gpu_enables_all_groups(self, widget):
        """有 GPU 时所有块可用"""
        widget.apply_gpu_gating(True)
        for pipeline, group in widget._groups.items():
            assert group.box.isEnabled() is True, f"{pipeline} 应可用"

    def test_non_gpu_pipelines_unaffected_by_gating(self, widget):
        """无 GPU 时 OCR / 表格 / 公式 / 结构块仍可用"""
        widget.apply_gpu_gating(False)
        for pipeline in (
            OCRPipeline.OCR,
            OCRPipeline.PP_STRUCTURE_V3,
            OCRPipeline.TABLE_RECOGNITION,
            OCRPipeline.FORMULA_RECOGNITION,
        ):
            assert (
                widget._groups[pipeline].box.isEnabled() is True
            ), f"{pipeline} 不应受 GPU 门控影响"
