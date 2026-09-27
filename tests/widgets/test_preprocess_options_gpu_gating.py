# tests/widgets/test_preprocess_options_gpu_gating.py
"""PreprocessOptionsWidget 的 GPU 门控测试。

覆盖需求：无 CUDA GPU（或用户选了 CPU 后端）时，禁用文档解析(MinerU)与
PaddleOCR-VL 两个重管道；门控与上下文锁定正交、不被 unlock_pipeline 冲掉。
协商到识别模式目录后，mode availability 是唯一权威，旧 GPU 布尔不再覆盖。
"""

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline
from vibeocr.classic.runtime_selection import (
    ENGINE_AVAILABILITY_READY,
    ENGINE_AVAILABILITY_UNAVAILABLE,
    RecognitionModeEntry,
    RecognitionModeLifecycle,
    RuntimeSelectionCatalog,
)
from vibeocr.classic.widgets.preprocess_options_widget import PreprocessOptionsWidget


@pytest.fixture
def app(qtbot):
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app, qtbot):
    """新构造组件保持 unknown，等待 MainWindow 显式广播门控。"""
    w = PreprocessOptionsWidget()
    qtbot.addWidget(w)
    return w


def _enabled_map(widget):
    """返回 {pipeline: enabled} 映射（目录态 itemData 是 mode id，需经 widget 解析）。"""
    result = {}
    for i in range(widget._pipeline_combo.count()):
        p = widget._pipeline_for_item_data(widget._pipeline_combo.itemData(i))
        if p is not None:
            result[p] = widget._pipeline_combo.model().item(i).isEnabled()
    return result


GPU_PIPELINES = {OCRPipeline.DOCUMENT_PARSING, OCRPipeline.PADDLEOCR_VL}


def _mode(mode_id, family, pipeline_id, provisioning, availability, lifecycle):
    return RecognitionModeEntry(
        mode_id,
        family,
        pipeline_id,
        None,
        provisioning,
        availability,
        RecognitionModeLifecycle(*lifecycle),
    )


def _mode_catalog(mineru_availability="ready", vl_availability="ready"):
    """构造仅含文档类模式的目录；其余模式与门控无关。"""
    return RuntimeSelectionCatalog(
        modes=(
            _mode(
                "rapid_text",
                "text",
                "OCR",
                "base_runtime",
                "ready",
                ("unmanaged", False, False, False, False),
            ),
            _mode(
                "mineru_document",
                "document",
                "MinerU",
                "advanced_component",
                mineru_availability,
                ("process_keep_alive", True, True, False, True),
            ),
            _mode(
                "paddle_document_vl",
                "document",
                "PaddleOCR-VL",
                "advanced_component",
                vl_availability,
                ("model_residency", True, True, True, True),
            ),
        ),
        has_recognition_mode_catalog=True,
    )


class TestGpuGating:
    def test_capability_is_tristate_until_gating_result_arrives(self, widget):
        assert widget.gpu_capability is None

        widget.apply_gpu_gating(False)
        assert widget.gpu_capability is False

        widget.apply_gpu_gating(True)
        assert widget.gpu_capability is True

    def test_no_gpu_disables_document_and_vl(self, widget):
        """无 GPU 时文档解析与 VL 禁用，其余启用。"""
        widget.apply_gpu_gating(False)
        em = _enabled_map(widget)
        for p in GPU_PIPELINES:
            assert em[p] is False, f"{p} 应被禁用"
        # 其余管道仍可选
        assert em[OCRPipeline.OCR] is True
        assert em[OCRPipeline.PP_STRUCTURE_V3] is True
        assert em[OCRPipeline.TABLE_RECOGNITION] is True
        assert em[OCRPipeline.FORMULA_RECOGNITION] is True

    def test_with_gpu_all_enabled(self, widget):
        """有 GPU 时全部可选。"""
        widget.apply_gpu_gating(False)
        widget.apply_gpu_gating(True)
        em = _enabled_map(widget)
        for p in em:
            assert em[p] is True

    def test_unlock_keeps_gpu_gating(self, widget):
        """unlock_pipeline 不应冲掉 GPU 门控（核心难点修复）。"""
        widget.apply_gpu_gating(False)
        widget.lock_to_pipelines({OCRPipeline.OCR})
        widget.unlock_pipeline()
        em = _enabled_map(widget)
        # 文档/VL 在 unlock 后仍被 GPU 门控禁用
        for p in GPU_PIPELINES:
            assert em[p] is False, f"{p} 在 unlock 后不应被恢复"

    def test_gating_orthogonal_to_context_lock(self, widget):
        """GPU 门控与上下文锁定取并集禁用，二者独立。"""
        widget.apply_gpu_gating(False)
        # 上下文锁定只允许 OCR（其余被锁禁用）
        widget.lock_to_pipelines({OCRPipeline.OCR}, reason="测试")
        em = _enabled_map(widget)
        # OCR 启用；其余全部禁用（含被 GPU 门控禁用的文档/VL，和被锁禁用的其他）
        assert em[OCRPipeline.OCR] is True
        for p in GPU_PIPELINES:
            assert em[p] is False
        assert em[OCRPipeline.PP_STRUCTURE_V3] is False  # 被上下文锁禁用

    def test_gating_switches_current_off_disabled(self, widget):
        """当前选中管道被门控禁用时，应回退到第一个可选项。"""
        # 先选中文档解析
        idx = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(idx)
        assert widget.get_current_pipeline() == OCRPipeline.DOCUMENT_PARSING

        widget.apply_gpu_gating(False)
        # 当前管道已回退到非禁用项
        assert widget.get_current_pipeline() not in GPU_PIPELINES

    def test_new_widget_waits_for_explicit_gpu_state(self, app, qtbot):
        """构造不猜测 GPU 状态；后续显式广播可以反复更新。"""
        w = PreprocessOptionsWidget()
        qtbot.addWidget(w)
        assert w.gpu_capability is None

        w.apply_gpu_gating(False)
        assert w.gpu_capability is False
        em_map = _enabled_map(w)
        for p in GPU_PIPELINES:
            assert em_map[p] is False

        w.apply_gpu_gating(True)
        assert w.gpu_capability is True
        assert all(_enabled_map(w).values())


class TestModeCatalogAuthority:
    """目录存在时不被旧 gpu flag 覆盖；无目录保持旧三态兼容。"""

    def test_ready_mode_overrides_legacy_gpu_disable(self, widget):
        """catalog 已就绪的文档/VL 模式在 CPU 计算设备下仍可选。"""
        widget.set_recognition_catalog(_mode_catalog())
        widget.apply_gpu_gating(False)
        em = _enabled_map(widget)
        assert em[OCRPipeline.DOCUMENT_PARSING] is True
        assert em[OCRPipeline.PADDLEOCR_VL] is True
        # 探测结果仍被如实记录，供其他展示层使用。
        assert widget.gpu_capability is False

    def test_catalog_arrival_re_enables_previously_gated_items(self, widget):
        """GPU 探测先回 False、catalog 后到时，必须重新评估门控。"""
        widget.apply_gpu_gating(False)
        assert _enabled_map(widget)[OCRPipeline.DOCUMENT_PARSING] is False
        widget.set_recognition_catalog(_mode_catalog())
        assert _enabled_map(widget)[OCRPipeline.DOCUMENT_PARSING] is True

    def test_gate_reports_ready_for_cpu_mineru(self, widget):
        widget.set_recognition_catalog(_mode_catalog())
        widget.apply_gpu_gating(False)
        assert widget.document_parsing_availability() == ENGINE_AVAILABILITY_READY

    def test_gate_fails_closed_on_unavailable_mode(self, widget):
        """未就绪/不可用不得被无条件放行。"""
        widget.set_recognition_catalog(_mode_catalog(mineru_availability="unavailable"))
        widget.apply_gpu_gating(False)
        assert _enabled_map(widget)[OCRPipeline.DOCUMENT_PARSING] is False
        assert widget.document_parsing_availability() == ENGINE_AVAILABILITY_UNAVAILABLE
