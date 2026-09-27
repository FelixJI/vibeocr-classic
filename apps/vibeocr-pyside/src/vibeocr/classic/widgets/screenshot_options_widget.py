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

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.runtime_selection import (
    ENGINE_AVAILABILITY_UNAVAILABLE,
    RuntimeSelectionCatalog,
    gpu_gating_applies,
)
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

# 需 GPU 计算设备的重 VLM 管道（与 PreprocessOptionsWidget._GPU_REQUIRED_PIPELINES
# 一致）。仅在未协商识别模式目录的旧 Backend 上作为保守兼容门控；目录存在时
# 由 mode availability 权威决定。此处仅 PaddleOCR-VL 会生成预处理块；MinerU
# 无块，不在此处体现。
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
        # GPU 门控禁用的管道集合。仅在未协商识别模式目录时生效：
        # 无 GPU / CPU 计算设备 = _GPU_REQUIRED_PIPELINES，否则为空集。
        self._gpu_disabled_pipelines: set[OCRPipeline] = set()
        self._gpu_capability: bool | None = None
        self._recognition_catalog: RuntimeSelectionCatalog | None = None
        # 持久化抑制标志：批量回填 checkbox 时避免触发 options_changed。
        self._loading = False

        self._setup_ui()
        self._connect_signals()
        self.load()

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QLabel("识别类型由截图工具栏按钮决定，此处仅配置各管道的预处理参数。")
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

        self._groups[pipeline] = _PipelineGroup(
            pipeline=pipeline, box=box, checks=checks
        )
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

            base = (
                OCRPreferences.instance()
                .get_pipeline_options("screenshot", pipeline)
                .to_dict()
            )
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

    def set_recognition_catalog(
        self, catalog: RuntimeSelectionCatalog | None
    ) -> None:
        """目录到达后 mode availability 成为权威；重新评估各管道块可用性。"""

        self._recognition_catalog = catalog
        self._apply_gpu_gating_state()

    def apply_gpu_gating(self, has_gpu: bool) -> None:
        """记录探测结果并重算门控；语义见 _apply_gpu_gating_state。

        由 MainWindow 在依赖检测完成后及懒加载构造后显式广播。

        Args:
            has_gpu: 运行时是否使用 GPU 计算设备。
        """
        self._gpu_capability = bool(has_gpu)
        self._apply_gpu_gating_state()

    def _unavailable_pipelines(self) -> set[OCRPipeline]:
        """目录声明且全部模式 unavailable 的管道（无目录时为空集）。

        与识别下拉同规则：任一模式可选（ready / preparation_required）
        即保持可用，仅全部不可用时禁用整块。
        """
        catalog = self._recognition_catalog
        if gpu_gating_applies(catalog):
            return set()
        by_pipeline: dict[OCRPipeline, list] = {}
        for mode in catalog.modes:
            try:
                pipeline = OCRPipeline(mode.pipeline_id)
            except ValueError:
                continue
            by_pipeline.setdefault(pipeline, []).append(mode)
        return {
            pipeline
            for pipeline, modes in by_pipeline.items()
            if modes
            and all(mode.availability == ENGINE_AVAILABILITY_UNAVAILABLE for mode in modes)
        }

    def _apply_gpu_gating_state(self) -> None:
        """按目录 availability 权威与 GPU 探测重算各管道块可用性。

        目录存在时，管道全部声明模式均 unavailable 则禁用对应块；旧 GPU
        布尔不再覆盖。无目录（旧 Backend / health 未达）保持历史保守门控：
        无 GPU / CPU 计算设备时禁用重管道块。
        """
        self._gpu_disabled_pipelines = (
            set(_GPU_REQUIRED_PIPELINES)
            if self._gpu_capability is False
            and gpu_gating_applies(self._recognition_catalog)
            else set()
        )
        unavailable = self._unavailable_pipelines()
        for pipeline, group in self._groups.items():
            gpu_blocked = pipeline in self._gpu_disabled_pipelines
            mode_blocked = pipeline in unavailable
            group.box.setEnabled(not gpu_blocked and not mode_blocked)
            if gpu_blocked:
                group.box.setToolTip(
                    "当前 Runtime 版本未声明此管道的能力目录，"
                    "仅支持在 GPU 计算设备下使用。\n"
                    "如需使用，请在设置页切换计算设备后重启。"
                )
            elif mode_blocked:
                group.box.setToolTip("当前运行环境未提供此识别模式。")
            else:
                group.box.setToolTip("")
