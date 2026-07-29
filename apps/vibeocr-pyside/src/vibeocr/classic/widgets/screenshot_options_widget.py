# src/vibeocr/widgets/screenshot_options_widget.py
"""截图选项组件 - 按管道分组展示预处理参数。

与 PreprocessOptionsWidget（主界面识别面板，含管道下拉框）不同，本组件
专用于「设置 → 截图选项」页：识别类型由截图工具栏按钮唯一决定，此处
仅按管道分组配置各管道的预处理参数（方向分类/扭曲矫正/文本行方向），
彻底消除"选择截图默认识别类型"的语义歧义。

每个支持预处理参数的管道各占一个 QGroupBox，块内按该管道的支持矩阵
动态生成 checkbox。MinerU 不支持任何预处理参数，故不生成块。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend.models.ocr_options import OCROptions
from vibeocr.classic.ui import theme
from vibeocr.runtime_contracts.contracts.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_display_name,
    is_option_supported,
)

# 预处理参数 → 显示名（与 inline_recognition_panel 的 _OPTION_DISPLAY_NAMES 对齐）
_PREPROCESS_OPTIONS: list[tuple[str, str, bool]] = [
    # (字段名, 显示名, 默认值) —— 默认值与 OCROptions dataclass 默认一致
    ("use_doc_orientation_classify", "文档方向分类", True),
    ("use_doc_unwarping", "文档扭曲矫正", False),
    ("use_textline_orientation", "文本行方向分类", False),
]

# 需 GPU 后端的重 VLM 管道（与 PreprocessOptionsWidget._GPU_REQUIRED_PIPELINES 一致）。
# 此处仅 PaddleOCR-VL 会生成预处理块；MinerU 无块，不在此处体现。
_GPU_REQUIRED_PIPELINES = {OCRPipeline.DOCUMENT_PARSING, OCRPipeline.PADDLEOCR_VL}


@dataclass
class _PipelineGroup:
    """单个管道预处理块的状态句柄。"""

    pipeline: OCRPipeline
    box: QGroupBox
    checks: dict[str, QCheckBox]  # 字段名 → checkbox


class ScreenshotOptionsWidget(QWidget):
    """截图选项组件 - 按管道分组配置预处理参数。

    无管道下拉框：6 个管道（实为 5 个，MinerU 无预处理参数）的参数块同时
    常驻可见。各块 checkbox 变化经 options_changed 信号上报，由控制器持久化
    到 OCRPreferences 的 "screenshot" 源对应管道 key。
    """

    options_changed = Signal(object)  # OCROptions

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._groups: dict[OCRPipeline, _PipelineGroup] = {}
        # GPU 门控禁用的管道集合。无 GPU/CPU 后端时 = _GPU_REQUIRED_PIPELINES。
        self._gpu_disabled_pipelines: set[OCRPipeline] = set()
        # 持久化抑制标志：批量回填 checkbox 时避免触发 options_changed。
        self._loading = False

        self._setup_ui()
        self._connect_signals()
        self.load()

        # 进程级 GPU 能力缓存已被 main_window 在启动时算出 → 立即应用门控
        # （无 nvidia-smi 开销，瞬时完成）。缓存未就绪时跳过，由 main_window
        # 算完后通过 apply_gpu_gating 广播补齐。
        from vibeocr.backend.env_manager import _runtime_gpu_capability_cache

        if _runtime_gpu_capability_cache is not None:
            self.apply_gpu_gating(_runtime_gpu_capability_cache)

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel(
            "识别类型由截图工具栏按钮决定，此处仅配置各管道的预处理参数。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {theme.Colors.text_muted};"
            f" font-size: {theme.Typography.caption}px;"
        )
        layout.addWidget(hint)

        for pipeline in get_all_pipelines():
            supported = [
                (field, label, default)
                for field, label, default in _PREPROCESS_OPTIONS
                if is_option_supported(pipeline, field)
            ]
            if not supported:
                # MinerU 等不支持预处理参数的管道不生成块
                continue
            layout.addWidget(self._build_group(pipeline, supported))

        layout.addStretch()

    def _build_group(
        self,
        pipeline: OCRPipeline,
        supported: list[tuple[str, str, bool]],
    ) -> QGroupBox:
        box = QGroupBox(get_pipeline_display_name(pipeline))
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(6)

        checks: dict[str, QCheckBox] = {}
        for field, label, default in supported:
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setProperty("field", field)
            box_layout.addWidget(cb)
            checks[field] = cb

        self._groups[pipeline] = _PipelineGroup(pipeline=pipeline, box=box, checks=checks)
        return box

    def _connect_signals(self) -> None:
        for group in self._groups.values():
            for cb in group.checks.values():
                cb.toggled.connect(self._on_option_changed)

    # ── 加载 / 上报 ──

    def load(self) -> None:
        """从 OCRPreferences 的 screenshot 源回填各管道块的预处理参数。"""
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
        except RuntimeError:
            return

        self._loading = True
        try:
            for pipeline, group in self._groups.items():
                opts = prefs.get_pipeline_options("screenshot", pipeline)
                for field, cb in group.checks.items():
                    cb.setChecked(getattr(opts, field))
        finally:
            self._loading = False

    def _on_option_changed(self) -> None:
        """某管道块的 checkbox 变化 → 持久化该管道的预处理参数。

        以 screenshot 源已存值为基础，仅覆盖该块暴露的预处理字段，
        避免覆盖同管道已配置的其他参数。
        """
        if self._loading:
            return
        sender = self.sender()
        if not isinstance(sender, QCheckBox):
            return
        # 通过信号发送者定位发生变化的管道块
        for pipeline, group in self._groups.items():
            if sender in group.checks.values():
                options = self._build_options(pipeline)
                self._persist(pipeline, options)
                self.options_changed.emit(options)
                return

    def _build_options(self, pipeline: OCRPipeline) -> OCROptions:
        """根据指定管道块的 checkbox 构造 OCROptions。

        以 screenshot 源已存值为基础（保留其他字段），仅覆盖该块暴露的
        预处理字段，确保 .pipeline 与该块管道一致。
        """
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            base = OCRPreferences.instance().get_pipeline_options(
                "screenshot", pipeline
            ).to_dict()
        except RuntimeError:
            base = OCROptions(pipeline=pipeline).to_dict()
        # 强制 pipeline 与该块一致（识别类型权威性）
        base["pipeline"] = pipeline.value
        group = self._groups[pipeline]
        for field, cb in group.checks.items():
            base[field] = cb.isChecked()
        return OCROptions.from_dict(base)

    def _persist(self, pipeline: OCRPipeline, options: OCROptions) -> None:
        """持久化到 OCRPreferences 的 screenshot 源对应管道 key。"""
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pipeline_options(
                "screenshot", pipeline, options
            )
        except RuntimeError:
            pass

    # ── GPU 门控（正交于参数配置） ──

    def apply_gpu_gating(self, has_gpu: bool) -> None:
        """根据运行时是否使用 GPU 后端，禁用/启用需 GPU 的重管道块。

        无 GPU 或 CPU 后端时禁用 PaddleOCR-VL 块（MinerU 无块）；
        有 GPU 后端时恢复可配置。由 MainWindow 在启动时（依赖检测完成后）
        调用一次，或在构造时从进程级缓存读取。

        Args:
            has_gpu: 运行时是否使用 GPU 后端。
        """
        self._gpu_disabled_pipelines = (
            set() if has_gpu else set(_GPU_REQUIRED_PIPELINES)
        )
        for pipeline, group in self._groups.items():
            disabled = pipeline in self._gpu_disabled_pipelines
            group.box.setEnabled(not disabled)
            if disabled:
                group.box.setToolTip(
                    "未检测到 CUDA GPU 或当前为 CPU 后端，此管道不可用。\n"
                    "如需使用，请在设置页切换到 GPU 后端后重启。"
                )
            else:
                group.box.setToolTip("")
