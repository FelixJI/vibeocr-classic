# tests/widgets/test_preprocess_options_gpu_gating.py
"""PreprocessOptionsWidget 的 GPU 门控测试。

覆盖需求：无 CUDA GPU（或用户选了 CPU 后端）时，禁用文档解析(MinerU)与
PaddleOCR-VL 两个重管道；门控与上下文锁定正交、不被 unlock_pipeline 冲掉。
"""

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline
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
    """返回 {pipeline: enabled} 映射。"""
    result = {}
    for i in range(widget._pipeline_combo.count()):
        p = OCRPipeline(widget._pipeline_combo.itemData(i))
        result[p] = widget._pipeline_combo.model().item(i).isEnabled()
    return result


GPU_PIPELINES = {OCRPipeline.DOCUMENT_PARSING, OCRPipeline.PADDLEOCR_VL}


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
