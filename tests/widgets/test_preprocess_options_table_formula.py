# tests/widgets/test_preprocess_options_table_formula.py
"""测试 PreprocessOptionsWidget 对 TABLE_RECOGNITION 和 FORMULA_RECOGNITION 管道的支持"""

import sys

import pytest

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

# PySide6 可能在 CI 中不可用，需要跳过
pyside6 = pytest.importorskip("PySide6")

from vibeocr.classic.widgets.preprocess_options_widget import (  # noqa: E402
    PreprocessOptionsWidget,
)


@pytest.fixture
def app(qtbot):
    """创建 QApplication"""
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication(sys.argv)
    return instance


@pytest.fixture
def widget(app, qtbot):
    """创建 PreprocessOptionsWidget"""
    w = PreprocessOptionsWidget()
    qtbot.addWidget(w)
    w.show()
    return w


def _select_pipeline(widget, pipeline: OCRPipeline):
    """选择指定管道"""
    combo = widget._pipeline_combo
    for i in range(combo.count()):
        if combo.itemData(i) == pipeline.value:
            combo.setCurrentIndex(i)
            return
    raise ValueError(f"Pipeline {pipeline} not found in combo box")


# ── 管道下拉框 ──


class TestPipelineCombo:
    def test_pipeline_combo_includes_table_recognition(self, widget):
        """管道下拉框应包含 TABLE_RECOGNITION"""
        combo = widget._pipeline_combo
        values = [combo.itemData(i) for i in range(combo.count())]
        assert "TABLE_RECOGNITION" in values

    def test_pipeline_combo_includes_formula_recognition(self, widget):
        """管道下拉框应包含 FORMULA_RECOGNITION"""
        combo = widget._pipeline_combo
        values = [combo.itemData(i) for i in range(combo.count())]
        assert "FORMULA_RECOGNITION" in values


# ── 选项组可见性 ──


class TestGroupVisibility:
    def test_table_group_visible_for_table_recognition(self, widget):
        """选择 TABLE_RECOGNITION 时，表格识别选项组可见"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)
        # 使用 isHidden() 而非 isVisible()，因为 isVisible() 依赖父级链在
        # headless 环境中可能全部不可见
        assert not widget._table_recognition_group.isHidden()

    def test_table_group_hidden_for_ocr(self, widget):
        """选择 OCR 时，表格识别选项组不可见"""
        _select_pipeline(widget, OCRPipeline.OCR)
        assert widget._table_recognition_group.isHidden()

    def test_formula_group_visible_for_formula_recognition(self, widget):
        """选择 FORMULA_RECOGNITION 时，公式识别选项组可见"""
        _select_pipeline(widget, OCRPipeline.FORMULA_RECOGNITION)
        assert not widget._formula_recognition_group.isHidden()

    def test_formula_group_hidden_for_ocr(self, widget):
        """选择 OCR 时，公式识别选项组不可见"""
        _select_pipeline(widget, OCRPipeline.OCR)
        assert widget._formula_recognition_group.isHidden()

    def test_preprocess_tab_visible_for_table(self, widget):
        """选择 TABLE_RECOGNITION 时，预处理选项卡可见"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)
        assert widget._tab_widget.isTabVisible(0)

    def test_preprocess_tab_visible_for_formula(self, widget):
        """选择 FORMULA_RECOGNITION 时，预处理选项卡可见"""
        _select_pipeline(widget, OCRPipeline.FORMULA_RECOGNITION)
        assert widget._tab_widget.isTabVisible(0)


# ── get_options ──


class TestGetOptions:
    def test_table_recognition_options_defaults(self, widget):
        """TABLE_RECOGNITION 默认选项"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)
        opts = widget.get_options()
        assert opts.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert opts.use_table_orientation_classify is True
        assert opts.use_ocr_results_with_table_cells is True
        assert opts.use_doc_orientation_classify is True
        assert opts.use_doc_unwarping is True

    def test_formula_recognition_options_defaults(self, widget):
        """FORMULA_RECOGNITION 默认选项"""
        _select_pipeline(widget, OCRPipeline.FORMULA_RECOGNITION)
        opts = widget.get_options()
        assert opts.pipeline == OCRPipeline.FORMULA_RECOGNITION
        assert opts.formula_recognition_batch_size == 1
        assert opts.use_doc_orientation_classify is True
        assert opts.use_doc_unwarping is True

    def test_table_recognition_options_modified(self, widget):
        """修改 TABLE_RECOGNITION 选项后 get_options 反映变化"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)

        widget._use_table_orientation_classify_cb.setChecked(False)
        widget._use_ocr_with_table_cells_cb.setChecked(False)
        widget._doc_orientation_cb.setChecked(False)

        opts = widget.get_options()
        assert opts.use_table_orientation_classify is False
        assert opts.use_ocr_results_with_table_cells is False
        assert opts.use_doc_orientation_classify is False

    def test_formula_recognition_options_modified(self, widget):
        """修改 FORMULA_RECOGNITION 选项后 get_options 反映变化"""
        _select_pipeline(widget, OCRPipeline.FORMULA_RECOGNITION)

        widget._formula_batch_size_spin.setValue(8)
        widget._doc_unwarping_cb.setChecked(False)

        opts = widget.get_options()
        assert opts.formula_recognition_batch_size == 8
        assert opts.use_doc_unwarping is False


# ── set_options ──


class TestSetOptions:
    def test_set_table_recognition_options(self, widget):
        """set_options 能恢复 TABLE_RECOGNITION 设置"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
            use_doc_orientation_classify=False,
        )
        widget.set_options(options)

        assert widget._use_table_orientation_classify_cb.isChecked() is False
        assert widget._use_ocr_with_table_cells_cb.isChecked() is False
        assert widget._doc_orientation_cb.isChecked() is False

    def test_set_formula_recognition_options(self, widget):
        """set_options 能恢复 FORMULA_RECOGNITION 设置"""
        _select_pipeline(widget, OCRPipeline.FORMULA_RECOGNITION)

        options = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=16,
            use_doc_unwarping=False,
        )
        widget.set_options(options)

        assert widget._formula_batch_size_spin.value() == 16
        assert widget._doc_unwarping_cb.isChecked() is False

    def test_set_options_does_not_emit_signal(self, widget, qtbot):
        """set_options 不应触发 options_changed 信号"""
        _select_pipeline(widget, OCRPipeline.TABLE_RECOGNITION)

        emitted = []

        def on_changed(opts):
            emitted.append(opts)

        widget.options_changed.connect(on_changed)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
        )
        widget.set_options(options)

        assert len(emitted) == 0

    def test_set_options_roundtrip_table(self, widget):
        """set_options -> get_options 往返测试（TABLE_RECOGNITION）"""
        original = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        widget.set_options(original)
        restored = widget.get_options()

        assert restored.pipeline == original.pipeline
        assert (
            restored.use_table_orientation_classify
            == original.use_table_orientation_classify
        )
        assert (
            restored.use_ocr_results_with_table_cells
            == original.use_ocr_results_with_table_cells
        )
        assert (
            restored.use_doc_orientation_classify
            == original.use_doc_orientation_classify
        )
        assert restored.use_doc_unwarping == original.use_doc_unwarping

    def test_set_options_roundtrip_formula(self, widget):
        """set_options -> get_options 往返测试（FORMULA_RECOGNITION）"""
        original = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=12,
            use_doc_orientation_classify=False,
        )
        widget.set_options(original)
        restored = widget.get_options()

        assert restored.pipeline == original.pipeline
        assert (
            restored.formula_recognition_batch_size
            == original.formula_recognition_batch_size
        )
        assert (
            restored.use_doc_orientation_classify
            == original.use_doc_orientation_classify
        )


# ── OCROptions 序列化 ──


class TestOCROptionsSerialization:
    def test_table_options_to_dict(self):
        """表格识别选项序列化为字典"""
        opts = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
        )
        d = opts.to_dict()
        assert d["pipeline"] == "TABLE_RECOGNITION"
        assert d["use_table_orientation_classify"] is False
        assert d["use_ocr_results_with_table_cells"] is False

    def test_formula_options_to_dict(self):
        """公式识别选项序列化为字典"""
        opts = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=8,
        )
        d = opts.to_dict()
        assert d["pipeline"] == "FORMULA_RECOGNITION"
        assert d["formula_recognition_batch_size"] == 8

    def test_table_options_from_dict(self):
        """从字典反序列化表格识别选项"""
        d = {
            "pipeline": "TABLE_RECOGNITION",
            "use_table_orientation_classify": False,
            "use_ocr_results_with_table_cells": False,
        }
        opts = OCROptions.from_dict(d)
        assert opts.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert opts.use_table_orientation_classify is False
        assert opts.use_ocr_results_with_table_cells is False

    def test_formula_options_from_dict(self):
        """从字典反序列化公式识别选项"""
        d = {
            "pipeline": "FORMULA_RECOGNITION",
            "formula_recognition_batch_size": 16,
        }
        opts = OCROptions.from_dict(d)
        assert opts.pipeline == OCRPipeline.FORMULA_RECOGNITION
        assert opts.formula_recognition_batch_size == 16

    def test_dict_roundtrip_table(self):
        """表格识别选项 to_dict -> from_dict 往返"""
        original = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
        )
        restored = OCROptions.from_dict(original.to_dict())
        assert restored.pipeline == original.pipeline
        assert (
            restored.use_table_orientation_classify
            == original.use_table_orientation_classify
        )
        assert (
            restored.use_ocr_results_with_table_cells
            == original.use_ocr_results_with_table_cells
        )

    def test_dict_roundtrip_formula(self):
        """公式识别选项 to_dict -> from_dict 往返"""
        original = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=20,
        )
        restored = OCROptions.from_dict(original.to_dict())
        assert restored.pipeline == original.pipeline
        assert (
            restored.formula_recognition_batch_size
            == original.formula_recognition_batch_size
        )
