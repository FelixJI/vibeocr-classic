# src/vibeocr/widgets/preprocess_options_widget.py
"""预处理选项组件 - 选项卡式布局"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.ui import theme
from vibeocr.classic.widgets.collapsible_group_box import CollapsibleGroupBox
from vibeocr.runtime_contracts.contracts.mineru import (
    MINERU_BACKEND_LABELS,
    MINERU_EFFORT_LABELS,
)
from vibeocr.runtime_contracts.contracts.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_display_name,
)


class PreprocessOptionsWidget(CollapsibleGroupBox):
    """预处理选项组件

    选项卡式布局，根据管道动态显示选项。
    """

    options_changed = Signal(object)  # OCROptions
    pipeline_switching = Signal(
        object, object
    )  # (old_pipeline: OCRPipeline, OCROptions)
    pipeline_switched = Signal(object)  # (new_pipeline: OCRPipeline)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("识别选项", parent)
        self._current_options = OCROptions()
        self._pipeline_locked = False
        # 上下文锁定允许的管道集合（仅 _pipeline_locked 为 True 时有效）。
        # 由 lock_to_pipelines 写入，_apply_pipeline_enabled_states 读取。
        self._locked_allowed: set[OCRPipeline] = set()
        # GPU 门控禁用的管道集合（正交于上下文锁定）。
        # 无 GPU 或 CPU 后端时 = _GPU_REQUIRED_PIPELINES，有 GPU 后端时为空集。
        # apply_gpu_gating 写入，_apply_pipeline_enabled_states 读取；
        # 与上下文锁定取并集禁用，且不被 unlock_pipeline 冲掉。
        self._gpu_disabled_pipelines: set[OCRPipeline] = set()
        self._gpu_capability: bool | None = None
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = self.contentLayout()
        layout.setSpacing(8)

        # 管道选择
        pipeline_layout = QHBoxLayout()
        pipeline_layout.addWidget(QLabel("管道:"))
        self._pipeline_combo = QComboBox()
        self._populate_pipeline_combo()
        pipeline_layout.addWidget(self._pipeline_combo)

        self._pipeline_lock_label = QLabel()
        self._pipeline_lock_label.setStyleSheet(
            f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption}px;"
        )
        self._pipeline_lock_label.setVisible(False)
        pipeline_layout.addWidget(self._pipeline_lock_label)

        pipeline_layout.addStretch()
        layout.addLayout(pipeline_layout)

        # 选项卡
        self._tab_widget = QTabWidget()
        layout.addWidget(self._tab_widget)

        # 预处理选项卡
        self._preprocess_tab = self._create_preprocess_tab()
        self._tab_widget.addTab(self._preprocess_tab, "预处理")

        # 高级选项卡
        self._advanced_tab = self._create_advanced_tab()
        self._tab_widget.addTab(self._advanced_tab, "高级")

        # 常驻提示：说明各来源选项归属
        self._source_hint_label = QLabel(
            "面板选项用于：粘贴 / 导入文件 / 重新识别；"
            "截图预处理请在「设置 → 截图选项」中配置。"
        )
        self._source_hint_label.setWordWrap(True)
        self._source_hint_label.setStyleSheet(
            f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption}px;"
        )
        layout.addWidget(self._source_hint_label)

        # 初始更新可见性
        self._update_tab_visibility()

    def _populate_pipeline_combo(self):
        """填充管道下拉框"""
        self._pipeline_combo.clear()
        for pipeline in get_all_pipelines():
            self._pipeline_combo.addItem(
                get_pipeline_display_name(pipeline),
                pipeline.value,
            )
        # 首启即应用 GPU 门控（apply_gpu_gating 未调用前为空集，全启用）。
        self._apply_pipeline_enabled_states()

    def _create_preprocess_tab(self) -> QWidget:
        """创建预处理选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._doc_orientation_cb = QCheckBox("文档方向分类")
        self._doc_orientation_cb.setToolTip("自动检测并矫正文档方向 (0/90/180/270度)")
        self._doc_orientation_cb.setChecked(True)
        layout.addWidget(self._doc_orientation_cb)

        self._doc_unwarping_cb = QCheckBox("文档扭曲矫正")
        self._doc_unwarping_cb.setToolTip("矫正文档的扭曲、倾斜、透视变形")
        self._doc_unwarping_cb.setChecked(True)
        layout.addWidget(self._doc_unwarping_cb)

        self._textline_orientation_cb = QCheckBox("文本行方向分类")
        self._textline_orientation_cb.setToolTip("检测文本行方向 (0/180度)")
        self._textline_orientation_cb.setChecked(False)
        layout.addWidget(self._textline_orientation_cb)

        layout.addStretch()
        return widget

    def _create_advanced_tab(self) -> QWidget:
        """创建高级选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # MineRU 文档解析选项组
        self._mineru_group = self._create_mineru_group()
        layout.addWidget(self._mineru_group)

        # PP-StructureV3 选项组
        self._pp_structure_group = self._create_pp_structure_group()
        layout.addWidget(self._pp_structure_group)

        # PaddleOCR-VL 文档解析选项组
        self._paddlocr_vl_group = self._create_paddlocr_vl_group()
        layout.addWidget(self._paddlocr_vl_group)

        # 表格识别选项组
        self._table_recognition_group = self._create_table_recognition_group()
        layout.addWidget(self._table_recognition_group)

        # 公式识别选项组
        self._formula_recognition_group = self._create_formula_recognition_group()
        layout.addWidget(self._formula_recognition_group)

        layout.addStretch()
        return widget

    def _create_pp_structure_group(self) -> QGroupBox:
        """创建 PP-StructureV3 选项组"""
        group = QGroupBox("PP-StructureV3 选项")
        layout = QVBoxLayout(group)

        self._use_table_cb = QCheckBox("表格识别")
        self._use_table_cb.setToolTip("启用表格结构识别（HTML 输出）")
        self._use_table_cb.setChecked(True)
        layout.addWidget(self._use_table_cb)

        self._use_formula_cb = QCheckBox("公式识别")
        self._use_formula_cb.setToolTip("启用数学公式识别（LaTeX 输出）")
        self._use_formula_cb.setChecked(True)
        layout.addWidget(self._use_formula_cb)

        self._use_seal_cb = QCheckBox("印章识别")
        self._use_seal_cb.setToolTip("启用印章文字识别")
        self._use_seal_cb.setChecked(False)
        layout.addWidget(self._use_seal_cb)

        self._use_chart_cb = QCheckBox("图表识别")
        self._use_chart_cb.setToolTip("启用图表内容识别")
        self._use_chart_cb.setChecked(False)
        layout.addWidget(self._use_chart_cb)

        return group

    def _create_mineru_group(self) -> QGroupBox:
        """创建 MineRU 文档解析选项组"""
        group = QGroupBox("文档解析选项")
        layout = QVBoxLayout(group)

        # 后端选择
        backend_layout = QHBoxLayout()
        backend_layout.addWidget(QLabel("解析后端:"))
        self._backend_combo = QComboBox()
        for value, label in MINERU_BACKEND_LABELS.items():
            self._backend_combo.addItem(label, value)
        self._backend_combo.setToolTip(
            "VLM 智能引擎：使用视觉语言模型，效果最佳（失败自动回退混合引擎）\n"
            "混合引擎：兼顾兼容性和效果\n"
            "传统流水线：纯 CPU 可用，效果一般"
        )
        backend_layout.addWidget(self._backend_combo)
        backend_layout.addStretch()
        layout.addLayout(backend_layout)

        # 解析强度（仅对混合引擎 hybrid-engine 生效）
        effort_layout = QHBoxLayout()
        effort_layout.addWidget(QLabel("解析强度:"))
        self._effort_combo = QComboBox()
        for value, label in MINERU_EFFORT_LABELS.items():
            self._effort_combo.addItem(label, value)
        self._effort_combo.setToolTip(
            "仅对混合引擎（hybrid-engine）生效\n"
            "标准：更快，但关闭图片/图表分析\n"
            "高精度：启用图片/图表分析，更慢"
        )
        effort_layout.addWidget(self._effort_combo)
        effort_layout.addStretch()
        layout.addLayout(effort_layout)

        # 解析方法
        parse_method_layout = QHBoxLayout()
        parse_method_layout.addWidget(QLabel("解析方法:"))
        self._parse_method_combo = QComboBox()
        self._parse_method_combo.addItem("自动（提取 + 识别）", "auto")
        self._parse_method_combo.addItem("纯文本提取", "txt")
        self._parse_method_combo.addItem("强制 OCR 识别", "ocr")
        self._parse_method_combo.setToolTip(
            "自动：智能选择最佳方式\n"
            "纯文本提取：直接提取 PDF 内嵌文字，速度快\n"
            "强制 OCR 识别：将每页视为图片进行识别，适用于扫描件"
        )
        parse_method_layout.addWidget(self._parse_method_combo)
        parse_method_layout.addStretch()
        layout.addLayout(parse_method_layout)

        self._enable_formula_cb = QCheckBox("公式识别")
        self._enable_formula_cb.setToolTip("启用数学公式识别（LaTeX 输出）")
        self._enable_formula_cb.setChecked(True)
        layout.addWidget(self._enable_formula_cb)

        self._enable_table_cb = QCheckBox("表格识别")
        self._enable_table_cb.setToolTip("启用表格结构识别（HTML 输出）")
        self._enable_table_cb.setChecked(True)
        layout.addWidget(self._enable_table_cb)

        # 语言选择
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("文档语言:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("自动检测", "")
        self._lang_combo.addItem("中文", "zh")
        self._lang_combo.addItem("英文", "en")
        self._lang_combo.addItem("中英混合", "zh,en")
        self._lang_combo.addItem("日文", "ja")
        self._lang_combo.addItem("韩文", "ko")
        self._lang_combo.setToolTip("文档主要语言，自动检测适用于混合语言")
        lang_layout.addWidget(self._lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # 页码范围
        page_layout = QHBoxLayout()
        page_layout.addWidget(QLabel("起始页:"))
        self._start_page_spin = QSpinBox()
        self._start_page_spin.setRange(0, 99999)
        self._start_page_spin.setValue(0)
        self._start_page_spin.setToolTip("起始页（从 0 开始）")
        page_layout.addWidget(self._start_page_spin)
        page_layout.addWidget(QLabel("结束页:"))
        self._end_page_check = QCheckBox("限制")
        self._end_page_spin = QSpinBox()
        self._end_page_spin.setRange(0, 99999)
        self._end_page_spin.setValue(99999)
        self._end_page_spin.setEnabled(False)
        self._end_page_check.toggled.connect(self._end_page_spin.setEnabled)
        page_layout.addWidget(self._end_page_check)
        page_layout.addWidget(self._end_page_spin)
        page_layout.addStretch()
        layout.addLayout(page_layout)

        return group

    def _create_paddlocr_vl_group(self) -> QGroupBox:
        """创建 PaddleOCR-VL 文档解析选项组"""
        group = QGroupBox("PaddleOCR-VL 选项")
        layout = QVBoxLayout(group)

        self._vl_use_layout_cb = QCheckBox("版面检测")
        self._vl_use_layout_cb.setToolTip(
            "启用版面布局检测，识别文档中的文本、表格、图片等区域"
        )
        self._vl_use_layout_cb.setChecked(True)
        layout.addWidget(self._vl_use_layout_cb)

        self._vl_use_chart_cb = QCheckBox("图表识别")
        self._vl_use_chart_cb.setToolTip("启用图表内容识别（需版面检测）")
        self._vl_use_chart_cb.setChecked(False)
        layout.addWidget(self._vl_use_chart_cb)

        self._vl_use_seal_cb = QCheckBox("印章识别")
        self._vl_use_seal_cb.setToolTip("启用印章文字识别（需版面检测）")
        self._vl_use_seal_cb.setChecked(False)
        layout.addWidget(self._vl_use_seal_cb)

        self._vl_use_ocr_for_image_cb = QCheckBox("图片文字识别")
        self._vl_use_ocr_for_image_cb.setToolTip("对检测到的图片区域进行 OCR 文字识别")
        self._vl_use_ocr_for_image_cb.setChecked(False)
        layout.addWidget(self._vl_use_ocr_for_image_cb)

        return group

    def _create_table_recognition_group(self) -> QGroupBox:
        """创建表格识别选项组"""
        group = QGroupBox("表格识别选项")
        layout = QVBoxLayout(group)

        self._use_table_orientation_classify_cb = QCheckBox("表格方向分类")
        self._use_table_orientation_classify_cb.setToolTip(
            "自动检测并矫正表格方向 (0/90/180/270度)"
        )
        self._use_table_orientation_classify_cb.setChecked(True)
        layout.addWidget(self._use_table_orientation_classify_cb)

        self._use_ocr_with_table_cells_cb = QCheckBox("单元格文字识别")
        self._use_ocr_with_table_cells_cb.setToolTip(
            "识别表格结构后，对每个单元格内的文字进行 OCR"
        )
        self._use_ocr_with_table_cells_cb.setChecked(True)
        layout.addWidget(self._use_ocr_with_table_cells_cb)

        return group

    def _create_formula_recognition_group(self) -> QGroupBox:
        """创建公式识别选项组"""
        group = QGroupBox("公式识别选项")
        layout = QVBoxLayout(group)

        # 公式批量大小
        batch_layout = QHBoxLayout()
        batch_layout.addWidget(QLabel("批量大小:"))
        self._formula_batch_size_spin = QSpinBox()
        self._formula_batch_size_spin.setRange(1, 32)
        self._formula_batch_size_spin.setValue(1)
        batch_layout.addWidget(self._formula_batch_size_spin)
        batch_layout.addStretch()
        layout.addLayout(batch_layout)

        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("模型名称:"))
        self._formula_model_name_edit = QLineEdit()
        self._formula_model_name_edit.setPlaceholderText("留空使用默认")
        model_layout.addWidget(self._formula_model_name_edit)
        model_layout.addStretch()
        layout.addLayout(model_layout)

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("模型路径:"))
        self._formula_model_dir_edit = QLineEdit()
        self._formula_model_dir_edit.setPlaceholderText("留空使用默认")
        dir_layout.addWidget(self._formula_model_dir_edit)
        dir_layout.addStretch()
        layout.addLayout(dir_layout)

        return group

    def _connect_signals(self):
        """连接信号"""
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)

        # 预处理选项
        self._doc_orientation_cb.toggled.connect(self._on_option_changed)
        self._doc_unwarping_cb.toggled.connect(self._on_option_changed)
        self._textline_orientation_cb.toggled.connect(self._on_option_changed)

        # MineRU 选项
        self._enable_formula_cb.toggled.connect(self._on_option_changed)
        self._enable_table_cb.toggled.connect(self._on_option_changed)
        self._backend_combo.currentIndexChanged.connect(self._on_option_changed)
        self._effort_combo.currentIndexChanged.connect(self._on_option_changed)
        self._parse_method_combo.currentIndexChanged.connect(self._on_option_changed)
        self._lang_combo.currentIndexChanged.connect(self._on_option_changed)
        self._start_page_spin.valueChanged.connect(self._on_option_changed)
        self._end_page_check.toggled.connect(self._on_option_changed)
        self._end_page_spin.valueChanged.connect(self._on_option_changed)

        # PaddleOCR-VL 选项
        self._vl_use_layout_cb.toggled.connect(self._on_option_changed)
        self._vl_use_chart_cb.toggled.connect(self._on_option_changed)
        self._vl_use_seal_cb.toggled.connect(self._on_option_changed)
        self._vl_use_ocr_for_image_cb.toggled.connect(self._on_option_changed)

        # PP-StructureV3 选项
        self._use_table_cb.toggled.connect(self._on_option_changed)
        self._use_formula_cb.toggled.connect(self._on_option_changed)
        self._use_seal_cb.toggled.connect(self._on_option_changed)
        self._use_chart_cb.toggled.connect(self._on_option_changed)

        # 表格识别选项
        self._use_table_orientation_classify_cb.toggled.connect(self._on_option_changed)
        self._use_ocr_with_table_cells_cb.toggled.connect(self._on_option_changed)

        # 公式识别选项
        self._formula_batch_size_spin.valueChanged.connect(self._on_option_changed)

        # 公式识别新增选项
        self._formula_model_name_edit.textChanged.connect(self._on_option_changed)
        self._formula_model_dir_edit.textChanged.connect(self._on_option_changed)

    def _on_pipeline_changed(self):
        """管道选择变更 — 通知调用方保存旧管道、加载新管道"""
        old_pipeline = self._current_options.pipeline
        self.pipeline_switching.emit(old_pipeline, self._current_options)

        self._update_tab_visibility()
        self._current_options = self.get_options()

        new_pipeline = self.get_current_pipeline()
        self.pipeline_switched.emit(new_pipeline)

    def _update_tab_visibility(self):
        """根据管道更新选项卡可见性"""
        pipeline = self.get_current_pipeline()
        supported = self._get_supported_options(pipeline)

        # 预处理选项卡
        has_preprocess = any(
            opt in supported
            for opt in [
                "use_doc_orientation_classify",
                "use_doc_unwarping",
                "use_textline_orientation",
            ]
        )

        # 高级选项卡
        has_advanced = any(
            opt in supported
            for opt in [
                "parse_method",
                "backend",
                "effort",
                "enable_formula",
                "enable_table",
                "lang_list",
                "start_page_id",
                "end_page_id",
                "use_table_recognition",
                "use_formula_recognition",
                "use_seal_recognition",
                "use_chart_recognition",
                "vl_use_layout_detection",
                "vl_use_chart_recognition",
                "vl_use_seal_recognition",
                "use_ocr_for_image_block",
                "use_table_orientation_classify",
                "use_ocr_results_with_table_cells",
                "formula_recognition_batch_size",
                "formula_recognition_model_name",
                "formula_recognition_model_dir",
            ]
        )

        # 设置选项卡可见性
        self._tab_widget.setTabVisible(0, has_preprocess)
        self._tab_widget.setTabVisible(1, has_advanced)

        # 设置 MineRU 组可见性
        mineru_opts = [
            "parse_method",
            "backend",
            "effort",
            "enable_formula",
            "enable_table",
            "lang_list",
            "start_page_id",
            "end_page_id",
        ]
        self._mineru_group.setVisible(any(opt in supported for opt in mineru_opts))

        # PaddleOCR-VL 选项组可见性
        vl_opts = [
            "vl_use_layout_detection",
            "vl_use_chart_recognition",
            "vl_use_seal_recognition",
            "use_ocr_for_image_block",
        ]
        self._paddlocr_vl_group.setVisible(any(opt in supported for opt in vl_opts))

        # PP-StructureV3 选项组可见性
        pp_struct_opts = [
            "use_table_recognition",
            "use_formula_recognition",
            "use_seal_recognition",
            "use_chart_recognition",
        ]
        self._pp_structure_group.setVisible(
            any(opt in supported for opt in pp_struct_opts)
        )

        # 表格识别选项组可见性
        table_opts = [
            "use_table_orientation_classify",
            "use_ocr_results_with_table_cells",
        ]
        self._table_recognition_group.setVisible(
            any(opt in supported for opt in table_opts)
        )

        # 公式识别选项组可见性
        formula_opts = [
            "formula_recognition_batch_size",
            "formula_recognition_model_name",
            "formula_recognition_model_dir",
        ]
        self._formula_recognition_group.setVisible(
            any(opt in supported for opt in formula_opts)
        )

        # 如果当前选项卡不可见，切换到第一个可见的
        for i in range(self._tab_widget.count()):
            if self._tab_widget.isTabVisible(i):
                self._tab_widget.setCurrentIndex(i)
                break

    def _get_supported_options(self, pipeline: OCRPipeline) -> list[str]:
        """获取管道支持的选项列表"""
        from vibeocr.runtime_contracts.contracts.pipelines import (
            get_pipeline_supported_options,
        )

        return get_pipeline_supported_options(pipeline)

    def _on_option_changed(self):
        """选项变更"""
        options = self.get_options()
        self._current_options = options
        self.options_changed.emit(options)

    def get_current_pipeline(self) -> OCRPipeline:
        """获取当前选择的管道"""
        value = self._pipeline_combo.currentData()
        return OCRPipeline(value)

    def get_options(self) -> OCROptions:
        """获取当前选项（仅包含当前管道支持的选项）"""
        from vibeocr.runtime_contracts.contracts.pipelines import is_option_supported

        pipeline = self.get_current_pipeline()

        kwargs: dict = {"pipeline": pipeline}

        if is_option_supported(pipeline, "use_doc_orientation_classify"):
            kwargs["use_doc_orientation_classify"] = (
                self._doc_orientation_cb.isChecked()
            )
        if is_option_supported(pipeline, "use_doc_unwarping"):
            kwargs["use_doc_unwarping"] = self._doc_unwarping_cb.isChecked()
        if is_option_supported(pipeline, "use_textline_orientation"):
            kwargs["use_textline_orientation"] = (
                self._textline_orientation_cb.isChecked()
            )
        if is_option_supported(pipeline, "enable_formula"):
            kwargs["enable_formula"] = self._enable_formula_cb.isChecked()
        if is_option_supported(pipeline, "enable_table"):
            kwargs["enable_table"] = self._enable_table_cb.isChecked()
        if is_option_supported(pipeline, "backend"):
            kwargs["backend"] = self._backend_combo.currentData()
        if is_option_supported(pipeline, "effort"):
            kwargs["effort"] = self._effort_combo.currentData()
        if is_option_supported(pipeline, "parse_method"):
            kwargs["parse_method"] = self._parse_method_combo.currentData()
        if is_option_supported(pipeline, "lang_list"):
            lang_data = self._lang_combo.currentData()
            if lang_data:
                kwargs["lang_list"] = lang_data.split(",")
            else:
                kwargs["lang_list"] = []
        if is_option_supported(pipeline, "vl_use_layout_detection"):
            kwargs["vl_use_layout_detection"] = self._vl_use_layout_cb.isChecked()
        if is_option_supported(pipeline, "vl_use_chart_recognition"):
            kwargs["vl_use_chart_recognition"] = self._vl_use_chart_cb.isChecked()
        if is_option_supported(pipeline, "vl_use_seal_recognition"):
            kwargs["vl_use_seal_recognition"] = self._vl_use_seal_cb.isChecked()
        if is_option_supported(pipeline, "use_ocr_for_image_block"):
            kwargs["use_ocr_for_image_block"] = (
                self._vl_use_ocr_for_image_cb.isChecked()
            )
        if is_option_supported(pipeline, "use_table_recognition"):
            kwargs["use_table_recognition"] = self._use_table_cb.isChecked()
        if is_option_supported(pipeline, "use_formula_recognition"):
            kwargs["use_formula_recognition"] = self._use_formula_cb.isChecked()
        if is_option_supported(pipeline, "use_seal_recognition"):
            kwargs["use_seal_recognition"] = self._use_seal_cb.isChecked()
        if is_option_supported(pipeline, "use_chart_recognition"):
            kwargs["use_chart_recognition"] = self._use_chart_cb.isChecked()
        if is_option_supported(pipeline, "use_table_orientation_classify"):
            kwargs["use_table_orientation_classify"] = (
                self._use_table_orientation_classify_cb.isChecked()
            )
        if is_option_supported(pipeline, "use_ocr_results_with_table_cells"):
            kwargs["use_ocr_results_with_table_cells"] = (
                self._use_ocr_with_table_cells_cb.isChecked()
            )
        if is_option_supported(pipeline, "formula_recognition_batch_size"):
            kwargs["formula_recognition_batch_size"] = (
                self._formula_batch_size_spin.value()
            )
        if is_option_supported(pipeline, "formula_recognition_model_name"):
            text = self._formula_model_name_edit.text().strip()
            kwargs["formula_recognition_model_name"] = text or None
        if is_option_supported(pipeline, "formula_recognition_model_dir"):
            text = self._formula_model_dir_edit.text().strip()
            kwargs["formula_recognition_model_dir"] = text or None
        if is_option_supported(pipeline, "start_page_id"):
            kwargs["start_page_id"] = self._start_page_spin.value()
        if is_option_supported(pipeline, "end_page_id"):
            if self._end_page_check.isChecked():
                kwargs["end_page_id"] = self._end_page_spin.value()
            else:
                kwargs["end_page_id"] = None

        return OCROptions(**kwargs)

    def set_options(self, options: OCROptions):
        """设置选项（不触发 options_changed 信号）"""
        self._current_options = options

        # 阻止所有控件信号，防止级联触发
        widgets = [
            self._pipeline_combo,
            self._doc_orientation_cb,
            self._doc_unwarping_cb,
            self._textline_orientation_cb,
            self._enable_formula_cb,
            self._enable_table_cb,
            self._backend_combo,
            self._effort_combo,
            self._parse_method_combo,
            self._lang_combo,
            self._start_page_spin,
            self._end_page_check,
            self._end_page_spin,
            self._vl_use_layout_cb,
            self._vl_use_chart_cb,
            self._vl_use_seal_cb,
            self._vl_use_ocr_for_image_cb,
            self._use_table_cb,
            self._use_formula_cb,
            self._use_seal_cb,
            self._use_chart_cb,
            self._use_table_orientation_classify_cb,
            self._use_ocr_with_table_cells_cb,
            self._formula_batch_size_spin,
            self._formula_model_name_edit,
            self._formula_model_dir_edit,
        ]
        for w in widgets:
            w.blockSignals(True)

        # 设置管道
        index = self._pipeline_combo.findData(options.pipeline.value)
        if index >= 0:
            self._pipeline_combo.setCurrentIndex(index)

        # 设置预处理选项
        self._doc_orientation_cb.setChecked(options.use_doc_orientation_classify)
        self._doc_unwarping_cb.setChecked(options.use_doc_unwarping)
        self._textline_orientation_cb.setChecked(options.use_textline_orientation)

        # 设置 MineRU 选项
        self._enable_formula_cb.setChecked(options.enable_formula)
        self._enable_table_cb.setChecked(options.enable_table)

        # 设置 backend
        backend_idx = self._backend_combo.findData(options.backend)
        if backend_idx >= 0:
            self._backend_combo.setCurrentIndex(backend_idx)

        # 设置 effort
        effort_idx = self._effort_combo.findData(getattr(options, "effort", "medium"))
        if effort_idx >= 0:
            self._effort_combo.setCurrentIndex(effort_idx)

        parse_method_idx = self._parse_method_combo.findData(options.parse_method)
        if parse_method_idx >= 0:
            self._parse_method_combo.setCurrentIndex(parse_method_idx)

        # 设置语言
        if options.lang_list:
            lang_str = ",".join(options.lang_list)
            lang_idx = self._lang_combo.findData(lang_str)
            if lang_idx >= 0:
                self._lang_combo.setCurrentIndex(lang_idx)
        else:
            self._lang_combo.setCurrentIndex(0)

        # 设置页码范围
        self._start_page_spin.setValue(options.start_page_id)
        if options.end_page_id is not None:
            self._end_page_check.setChecked(True)
            self._end_page_spin.setValue(options.end_page_id)
        else:
            self._end_page_check.setChecked(False)

        # 设置 PaddleOCR-VL 选项
        self._vl_use_layout_cb.setChecked(options.vl_use_layout_detection)
        self._vl_use_chart_cb.setChecked(options.vl_use_chart_recognition)
        self._vl_use_seal_cb.setChecked(options.vl_use_seal_recognition)
        self._vl_use_ocr_for_image_cb.setChecked(options.use_ocr_for_image_block)

        # 设置 PP-StructureV3 选项
        self._use_table_cb.setChecked(options.use_table_recognition)
        self._use_formula_cb.setChecked(options.use_formula_recognition)
        self._use_seal_cb.setChecked(options.use_seal_recognition)
        self._use_chart_cb.setChecked(options.use_chart_recognition)

        # 设置表格识别选项
        self._use_table_orientation_classify_cb.setChecked(
            options.use_table_orientation_classify
        )
        self._use_ocr_with_table_cells_cb.setChecked(
            options.use_ocr_results_with_table_cells
        )

        # 设置公式识别选项
        self._formula_batch_size_spin.setValue(options.formula_recognition_batch_size)
        self._formula_model_name_edit.setText(
            options.formula_recognition_model_name or ""
        )
        self._formula_model_dir_edit.setText(
            options.formula_recognition_model_dir or ""
        )

        # 恢复信号
        for w in widgets:
            w.blockSignals(False)

        self._update_tab_visibility()

    # ── 管道锁定 ──

    _DOCUMENT_PIPELINES = {OCRPipeline.DOCUMENT_PARSING, OCRPipeline.PADDLEOCR_VL}

    # 需 GPU 后端的重 VLM 管道：MinerU（VLM 引擎依赖 vLLM/lmdeploy，CUDA-only）、
    # PaddleOCR-VL（重 VLM 模型）。CPU 后端下禁用以避免不可用/体验极差。
    _GPU_REQUIRED_PIPELINES = {OCRPipeline.DOCUMENT_PARSING, OCRPipeline.PADDLEOCR_VL}

    def lock_to_pipelines(
        self,
        allowed: set[OCRPipeline],
        reason: str = "",
        default: OCRPipeline | None = None,
    ) -> None:
        """锁定管道下拉项，仅保留 allowed 集合内的管道可选。

        Args:
            allowed: 允许的管道集合。
            reason: 锁定原因，显示在管道旁边的提示标签。
            default: 若当前管道不在 allowed 内，切到此管道；
                     为 None 时切到 allowed 内下拉框第一个出现的管道。
        """
        if self._pipeline_locked:
            return
        self._pipeline_locked = True
        self._locked_allowed = set(allowed)

        # 应用启用状态：上下文锁定 + GPU 门控取并集禁用（统一逻辑，
        # 确保 GPU 约束在锁定后仍生效）。
        self._apply_pipeline_enabled_states()

        # 若当前管道不在 allowed 内，切到指定默认（或 allowed 内第一个）
        current = self.get_current_pipeline()
        if current not in allowed:
            fallback = default
            if fallback is None or fallback not in allowed:
                # 找 allowed 内下拉框中第一个出现的管道
                fallback = next(
                    (
                        OCRPipeline(self._pipeline_combo.itemData(i))
                        for i in range(self._pipeline_combo.count())
                        if OCRPipeline(self._pipeline_combo.itemData(i)) in allowed
                    ),
                    current,
                )
            idx = self._pipeline_combo.findData(fallback.value)
            if idx >= 0:
                self._pipeline_combo.blockSignals(True)
                self._pipeline_combo.setCurrentIndex(idx)
                self._pipeline_combo.blockSignals(False)

        self._pipeline_lock_label.setText(f"({reason})" if reason else "(已锁定)")
        self._pipeline_lock_label.setVisible(True)

        self._update_tab_visibility()

    def lock_to_document_parsing(self, reason: str = "") -> None:
        """锁定管道为文档类管道（MineRU / PaddleOCR-VL 可选），禁用其他管道。

        向后兼容包装：等价于
        ``lock_to_pipelines({DOCUMENT_PARSING, PADDLEOCR_VL}, reason, DOCUMENT_PARSING)``。

        Args:
            reason: 锁定原因，显示在管道旁边
        """
        self.lock_to_pipelines(
            self._DOCUMENT_PIPELINES,
            reason=reason or "仅文档解析",
            default=OCRPipeline.DOCUMENT_PARSING,
        )

    def unlock_pipeline(self) -> None:
        """解除管道锁定，恢复自由选择。

        注意：解除上下文锁定后仍会重新应用 GPU 门控（CPU 后端下文档解析/VL
        保持禁用），不会无条件全部恢复。
        """
        if not self._pipeline_locked:
            return
        self._pipeline_locked = False
        self._locked_allowed = set()

        # 重新应用启用状态：上下文锁定已解除，但 GPU 门控仍生效。
        self._apply_pipeline_enabled_states()

        self._pipeline_lock_label.setVisible(False)

    @property
    def is_pipeline_locked(self) -> bool:
        return self._pipeline_locked

    # ── GPU 门控（正交于上下文锁定） ──

    @property
    def gpu_capability(self) -> bool | None:
        """返回异步运行时探测结果；``None`` 表示探测尚未完成。"""
        return self._gpu_capability

    def apply_gpu_gating(self, has_gpu: bool) -> None:
        """根据运行时是否使用 GPU 后端，禁用/启用需 GPU 的重管道。

        与上下文锁定（lock_to_pipelines/unlock_pipeline）正交：二者取并集禁用，
        且 GPU 门控不会被 unlock_pipeline 冲掉（unlock 后仍重新应用）。

        无 GPU 或 CPU 后端时禁用 MinerU(文档解析)/PaddleOCR-VL；有 GPU 后端时
        恢复可选。由 MainWindow 在依赖检测完成后及懒加载构造后显式广播。

        Args:
            has_gpu: 运行时是否使用 GPU 后端。
        """
        self._gpu_capability = bool(has_gpu)
        self._gpu_disabled_pipelines = (
            set() if has_gpu else set(self._GPU_REQUIRED_PIPELINES)
        )
        self._apply_pipeline_enabled_states()

        # 若当前选中的管道被 GPU 门控禁用，回退到下拉框中第一个可选项，
        # 避免停留在灰色不可用项上（与 lock_to_pipelines 的回退逻辑一致）。
        if not has_gpu:
            current = self.get_current_pipeline()
            if current in self._gpu_disabled_pipelines:
                fallback = self._first_enabled_pipeline()
                if fallback is not None and fallback != current:
                    idx = self._pipeline_combo.findData(fallback.value)
                    if idx >= 0:
                        self._pipeline_combo.blockSignals(True)
                        self._pipeline_combo.setCurrentIndex(idx)
                        self._pipeline_combo.blockSignals(False)
                        self._update_tab_visibility()

    def _first_enabled_pipeline(self) -> OCRPipeline | None:
        """返回下拉框中第一个启用的管道（用于 GPU 门控回退）。"""
        for i in range(self._pipeline_combo.count()):
            item = self._pipeline_combo.model().item(i)
            if item.isEnabled():
                data = self._pipeline_combo.itemData(i)
                try:
                    return OCRPipeline(data)
                except ValueError:
                    continue
        return None

    def _apply_pipeline_enabled_states(self) -> None:
        """统一重算下拉项启用状态（上下文锁定 ∪ GPU 门控）

        每项 pipeline 的启用条件 = 同时满足：
        - 不在 GPU 门控禁用集合内（_gpu_disabled_pipelines）
        - 上下文锁定未激活，或在锁定允许集合内（_locked_allowed）

        二者任一为禁用则禁用。被 GPU 门控禁用的项附加说明性 tooltip。
        """
        for i in range(self._pipeline_combo.count()):
            data = self._pipeline_combo.itemData(i)
            try:
                pipeline = OCRPipeline(data)
            except ValueError:
                continue

            item = self._pipeline_combo.model().item(i)
            gpu_disabled = pipeline in self._gpu_disabled_pipelines
            # 上下文锁定未激活时视为"允许所有"（仅由 GPU 门控决定）
            context_disabled = (
                self._pipeline_locked and pipeline not in self._locked_allowed
            )

            if gpu_disabled:
                item.setEnabled(False)
                item.setToolTip(
                    "未检测到 CUDA GPU 或当前为 CPU 后端，此管道不可用。\n"
                    "如需使用，请在设置页切换到 GPU 后端后重启。"
                )
            elif context_disabled:
                item.setEnabled(False)
                item.setToolTip("")
            else:
                item.setEnabled(True)
                item.setToolTip("")
