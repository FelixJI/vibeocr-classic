"""Main window view logic"""

from __future__ import annotations

import logging
import os
import time
from importlib import import_module
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QPoint,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.app_paths import get_state_root
from vibeocr.classic.machine_cache import is_cache_valid
from vibeocr.classic.managers.config_manager import ConfigManager
from vibeocr.classic.managers.dependency_manager import DependencyManager
from vibeocr.classic.managers.layout_manager import LayoutManager
from vibeocr.classic.managers.subprocess_manager import SubprocessManager
from vibeocr.classic.runtime_status_messages import (
    format_runtime_unavailable,
    supervisor_start_failure_message,
)
from vibeocr.classic.services.log_service import setup_logging
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.utils.image_jobs import GenerationImageJobs, decode_image_file
from vibeocr.classic.utils.shutdown_jobs import ExternalShutdownJob
from vibeocr.classic.views.background_tasks import FunctionTask
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.classic.views.tabs.single_recognition_tab import SingleRecognitionTab
from vibeocr.classic.widgets.runtime_status_bar import RuntimeStatusBar
from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay
from vibeocr.classic.widgets.toast_widget import show_toast
from vibeocr.classic.widgets.toolbar import EdgeToolbar

# 延迟导入: OCR 服务模块导入很慢（~33s），延迟到首次使用时导入


class MainWindow(QMainWindow):
    """主窗口"""

    # 状态更新信号（用于线程安全的状态栏更新）
    _status_update_signal = Signal(str)
    _SHUTDOWN_POLL_INTERVAL_MS = 25
    _SHUTDOWN_UX_BUDGET_MS = 5000

    def __init__(self) -> None:
        super().__init__()
        self._project_root = get_state_root()
        self._ocr_ready = False
        self._dependency_check_complete = False  # 依赖检测是否完成

        self._closing = False  # 是否正在关闭（防止关闭时重复启动 Worker）
        self._force_quit = False  # 是否强制退出（而非最小化到托盘）
        self._tray_icon = None  # 系统托盘图标
        self._ocr_status_callback_fn: Any = None  # OCR 状态回调
        self._app_settings = None  # 应用设置
        self._runtime_gpu_capability: bool | None = None
        self._recognition_catalog = None
        self._machine_cache_data: dict | None = None
        self._machine_cache_tasks: set[FunctionTask] = set()
        self._machine_cache_generation = 0
        self._machine_cache_running = False
        self._image_load_jobs = GenerationImageJobs(self)
        self._image_load_jobs.completed.connect(self._on_image_file_loaded)
        self._image_load_jobs.failed.connect(self._on_image_file_load_failed)

        # 懒加载 Tab：批量/二维码/PDF 在启动期仅插占位空页，首次切换时才真正构造，
        # 把 MainWindow 构造耗时从 ~1.5s 砍到 <0.5s（首屏仅需单次识别 Tab）。
        # 构造后属性由 None 变为真实 widget；下游已用 hasattr/getattr 防御 None。
        self._batch_tab: Any = None
        self._qrcode_tab: Any = None
        self._pdf_tab: Any = None
        # 占位页 -> 构造方法 的映射，供 currentChanged 触发懒构造
        self._lazy_tab_builders: dict[int, tuple[str, Any]] = {}
        self._lazy_tab_generation = 0
        self._lazy_tab_inflight: tuple[int, int, FunctionTask] | None = None
        self._lazy_tab_tasks: set[FunctionTask] = set()
        self._lazy_tab_pending_index: int | None = None
        self._lazy_tab_build_scheduled: tuple[int, int] | None = None
        self._shutdown_phase = "idle"
        self._shutdown_stage = "idle"
        self._shutdown_external_job: Any = None
        self._shutdown_started_at = 0.0
        self._shutdown_timed_out = False
        self._shutdown_gui_probes: tuple[tuple[str, Any], ...] = ()
        self._startup_update_task: Any = None
        self._pending_maintenance_dialog: Any = None
        self._shutdown_poll_timer = QTimer(self)
        self._shutdown_poll_timer.setInterval(self._SHUTDOWN_POLL_INTERVAL_MS)
        self._shutdown_poll_timer.timeout.connect(self._poll_shutdown_state)

        # 依赖管理器
        self._dependency_manager = DependencyManager(self._project_root, self)
        self._dependency_manager.check_completed.connect(
            self._on_dependency_check_finished
        )

        # 布局管理器
        self._layout_manager = LayoutManager(ConfigManager.instance())

        # 子进程管理器
        self._subprocess_manager = SubprocessManager(self._project_root, self)
        self._subprocess_manager.service_ready.connect(self._on_supervisor_ready)
        self._subprocess_manager.progress_update.connect(self._on_subprocess_progress)

        self._setup_ui()

        # 恢复布局
        self._restore_layout()
        self._setup_console()
        self._init_about_tab()
        self._connect_signals()

        # 创建边缘工具栏
        self._edge_toolbar = EdgeToolbar()
        self._edge_toolbar.screenshot_requested.connect(self._on_screenshot)
        self._edge_toolbar.show_main_requested.connect(self._show_main_window)
        self._edge_toolbar.position_changed.connect(self._on_toolbar_position_changed)
        self._edge_toolbar.pipeline_screenshot_requested.connect(
            self._on_pipeline_screenshot
        )

        # 设置 OCRService 状态回调（用于显示模型下载进度）
        self._setup_ocr_status_callback()

        if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
            # 发布包门只验证冻结入口 -> Supervisor ready 的真实链路。
            # OCR/Paddle/模型环境由独立安装流程覆盖，不能让其拖慢或掩盖此门。
            QTimer.singleShot(0, self._start_supervisor_self_test)
        else:
            # 启动时立即读取缓存，如果有有效缓存则直接更新状态
            self._try_load_cache()
            # 异步检查嵌入式依赖（在UI显示后）
            QTimer.singleShot(100, self._check_embedded_dependencies)
            # GPU 探测由设置页 worker 在后台完成，结果通过
            # gpu_capability_callback 回传并广播给所有选项组件。

    def _start_supervisor_self_test(self) -> None:
        """在发布包门中跳过 OCR 环境探测，直接验证 Supervisor 握手。"""
        self._runtime_gpu_capability = False
        self._ocr_ready = True
        self._start_supervisor()

    @Slot(bool)
    def _apply_gpu_gating_to_all(self, has_gpu: bool) -> None:
        """在 GUI 线程将已探测的运行时 GPU 能力广播到选项组件。"""
        if self._closing:
            return

        from vibeocr.classic.widgets.preprocess_options_widget import (
            PreprocessOptionsWidget,
        )
        from vibeocr.classic.widgets.screenshot_options_widget import (
            ScreenshotOptionsWidget,
        )

        for widget in self.findChildren(PreprocessOptionsWidget):
            widget.apply_gpu_gating(has_gpu)
        for widget in self.findChildren(ScreenshotOptionsWidget):
            widget.apply_gpu_gating(has_gpu)
        batch_tab = getattr(self, "_batch_tab", None)
        if batch_tab is not None:
            refresh = getattr(batch_tab, "refresh_gpu_capability", None)
            if callable(refresh):
                refresh()

    @Slot(bool)
    def _on_gpu_capability_resolved(self, has_gpu: bool) -> None:
        """消费设置页既有后台 GPU 探测结果，不在 GUI 线程二次 shell out。"""
        if self._closing:
            return
        self._runtime_gpu_capability = bool(has_gpu)
        self._apply_gpu_gating_to_all(bool(has_gpu))

    def _setup_ocr_status_callback(self) -> None:
        """设置 OCR 状态回调，用于在状态栏显示模型下载进度"""

        def on_ocr_status(stage: str, message: str) -> None:
            """OCR 状态回调（可能从后台线程调用）"""
            self._status_update_signal.emit(message)

        self._status_update_signal.connect(self._on_status_update)
        # 延迟到首次使用时才 import OCRService（避免启动时 ~0.1s 的 import 开销）
        self._ocr_status_callback_fn = on_ocr_status

    def _ensure_ocr_status_callback(self) -> None:
        """Supervisor 通过 typed events 报告状态；不注册进程内 OCR 回调。"""
        self._ocr_status_callback_fn = None

    @Slot(str)
    def _on_status_update(self, message: str) -> None:
        """状态更新槽（线程安全）"""
        self._statusbar.showMessage(message)

    @Slot(str)
    def _on_log_status_update(self, message: str) -> None:
        """日志状态更新槽（用于显示 Worker 节点输出）"""
        # 只在 Supervisor 未就绪时显示启动期节点消息。
        if not self._subprocess_manager.is_ready:
            self._statusbar.showMessage(message)

    def _show_background_runtime_status(self, message: str) -> None:
        """把模型预加载/驻留事实写入独立区，不与前台任务竞争。"""
        if self._closing:
            return
        self._statusbar.set_residency(message)

    def _setup_ui(self) -> None:
        """设置UI"""
        # QMainWindow 需要一个 centralWidget 来放置主内容
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)

        # 使用预编译的 Python UI 文件，设置到 centralWidget 上
        self._ui = Ui_MainWindowWidget()
        self._ui.setupUi(self._central_widget)

        # 用 SingleRecognitionTab 替换 tabOCR
        self._single_tab = SingleRecognitionTab()
        tab_index = self._ui.tabWidget.indexOf(self._ui.tabOCR)
        self._ui.tabWidget.removeTab(tab_index)
        self._ui.tabWidget.insertTab(tab_index, self._single_tab, "单次识别")

        # 设置 tabSettings 的 sizePolicy，使其可以缩小
        # 这样 TabWidget 不会因为设置页面的内容太多而变得很大
        self._ui.tabSettings.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )

        # 设置窗口属性
        self.setWindowTitle("VibeOCR")
        self.resize(900, 600)

        # 创建状态栏
        self._statusbar = RuntimeStatusBar(self)
        self.setStatusBar(self._statusbar)
        # 启动初期即提示，避免状态栏空白让用户以为程序无响应
        self._statusbar.showMessage("正在检测 OCR 环境...")

        # 初始化 OCR 预设下拉框（包含截图组件和复制提示的初始化）
        self._init_preset_combo()

        # 批量/二维码/PDF 标签页：先插占位空页，首次切换时才真正构造（懒加载，
        # 避免启动期同步构建三个重型 Tab 拖慢窗口出现）。
        self._add_lazy_tab("批量识别", "batch", self._build_batch_tab)
        self._add_lazy_tab("二维码", "qrcode", self._build_qrcode_tab)
        self._add_lazy_tab("PDF 处理", "pdf", self._build_pdf_tab)

        # 将设置标签页移到最后
        self._move_settings_tab_to_end()

        # removeTab(当前tab) 后 Qt 自动选中 tabSettings，需要重置回第一个 tab
        self._ui.tabWidget.setCurrentIndex(0)

    def _move_settings_tab_to_end(self) -> None:
        """将设置标签页移动到最后位置"""
        tab_widget = self._ui.tabWidget
        settings_tab = self._ui.tabSettings

        # 获取设置标签页的当前索引
        settings_index = tab_widget.indexOf(settings_tab)
        if settings_index >= 0:
            # 使用 tabBar().moveTab 将设置标签页移到最后
            tab_widget.tabBar().moveTab(settings_index, tab_widget.count() - 1)
            logging.debug("设置标签页已移到最后")

    def _restore_layout(self) -> None:
        """恢复窗口和分割器布局"""
        # 恢复主窗口几何信息
        geometry = self._layout_manager.get_main_window_geometry()
        if geometry:
            self.restoreGeometry(geometry)
            logging.debug("已恢复窗口布局")

        # 恢复上次选中的标签页
        tab_index = self._layout_manager.get_tab_index()
        if tab_index is not None and 0 <= tab_index < self._ui.tabWidget.count():
            self._ui.tabWidget.setCurrentIndex(tab_index)
            logging.debug(f"已恢复标签页索引: {tab_index}")

        # 恢复单次识别标签页分割器状态
        if hasattr(self, "_single_tab") and hasattr(self._single_tab, "_splitter"):
            state = self._layout_manager.get_splitter_state("ocr_tab")
            if state:
                self._single_tab._splitter.restoreState(state)
                logging.debug("已恢复 OCR 分割器状态")
            else:
                total_width = self._single_tab._splitter.width()
                if total_width > 0:
                    self._single_tab._splitter.setSizes([400, total_width - 400])
                else:
                    self._single_tab._splitter.setSizes([400, 500])

        # 恢复二维码生成标签页分割器状态
        if hasattr(self, "_qrcode_tab") and self._qrcode_tab:
            state = self._layout_manager.get_splitter_state("qrcode_tab")
            if state:
                self._qrcode_tab._splitter.restoreState(state)
                logging.debug("已恢复二维码分割器状态")

    def _save_layout(self) -> None:
        """保存窗口和分割器布局"""
        # 保存主窗口几何信息
        self._layout_manager.set_main_window_geometry(self.saveGeometry())

        # 保存单次识别标签页分割器状态
        if hasattr(self, "_single_tab") and hasattr(self._single_tab, "_splitter"):
            self._layout_manager.set_splitter_state(
                "ocr_tab", self._single_tab._splitter.saveState()
            )

        # 保存批量识别标签页分割器状态
        if hasattr(self, "_batch_tab") and self._batch_tab:
            self._batch_tab.save_layout()

        # 保存二维码生成标签页分割器状态
        if hasattr(self, "_qrcode_tab") and self._qrcode_tab:
            self._layout_manager.set_splitter_state(
                "qrcode_tab", self._qrcode_tab._splitter.saveState()
            )

        # 保存当前标签页索引
        self._layout_manager.set_tab_index(self._ui.tabWidget.currentIndex())

        # 保存到文件
        self._layout_manager.save()

    def _init_preset_combo(self) -> None:
        """初始化截图组件"""
        self._overlay = ScreenCaptureOverlay()
        self._retired_overlays: set[ScreenCaptureOverlay] = set()
        # 记录截图开始前主窗口的最小化状态，用于截图结束后恢复窗口状态。
        self._main_window_minimized_before_capture = False

    def _add_lazy_tab(
        self, title: str, role: str, builder: Any, at_end: bool = False
    ) -> None:
        """插入一个占位空页，注册懒构造回调。

        启动期只插一个空 QWidget（零成本），首次切换到该页时由
        ``_on_lazy_tab_changed`` 触发 ``builder`` 真正构造内容并替换占位页。

        - ``at_end=False``（默认）：占位页插在设置页之前，保持功能页顺序
          （批量→二维码→PDF→设置）。
        - ``at_end=True``：占位页插到末尾（设置页之后），用于关于页（关于页
          居末尾，符合「关于」居末的惯例）。

        Args:
            title: 标签页标题。
            role: 角色标识（"batch"/"qrcode"/"pdf"/"about"），用于构造后回填属性名。
            builder: 无参可调用，返回真实 tab widget。
            at_end: 是否插到末尾（设置页之后）。
        """
        placeholder = QWidget()
        placeholder.setObjectName(f"lazySkeleton_{role}")
        skeleton_layout = QVBoxLayout(placeholder)
        skeleton_label = QLabel("正在准备页面…", placeholder)
        skeleton_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        skeleton_layout.addWidget(skeleton_label)
        tw = self._ui.tabWidget
        if at_end:
            insert_at = tw.count()
        else:
            settings_idx = tw.indexOf(self._ui.tabSettings)
            insert_at = settings_idx if settings_idx >= 0 else tw.count()
        idx = tw.insertTab(insert_at, placeholder, title)
        self._lazy_tab_builders[idx] = (role, builder)

    def _build_batch_tab(self) -> Any:
        """构造批量识别标签页（懒加载时调用）。

        import 延迟到此处：BatchRecognitionTab 模块顶层拉起 pdf_session_manager
        → pydantic/httpx 等重链，启动期不需要，避免拖慢 main_window 模块加载。
        """
        from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab

        tab = BatchRecognitionTab()
        tab.set_layout_manager(self._layout_manager)
        self._configure_recognition_mode_widgets(tab)
        return tab

    def _build_qrcode_tab(self) -> Any:
        """构造二维码标签页（懒加载时调用）。"""
        from vibeocr.classic.views.tabs.qrcode_tab import QrcodeTab

        return QrcodeTab()

    def _build_pdf_tab(self) -> Any:
        """构造 PDF 处理标签页（懒加载时调用）。

        import 延迟到此处：PdfTab 顶层拉起 pdf_session_manager → pydantic/httpx，
        启动期不需要。
        """
        from vibeocr.classic.views.tabs.pdf_tab import PdfTab

        tab = PdfTab()
        tab.task_status_changed.connect(self._statusbar.showMessage)
        tab.result_status_changed.connect(self._statusbar.finish_task)
        self._configure_recognition_mode_widgets(tab)
        return tab

    def _configure_recognition_mode_widgets(self, host) -> None:
        """新建的懒加载入口继承当前 health 模式目录。"""
        catalog = self._recognition_catalog
        if catalog is None:
            return
        from vibeocr.classic.widgets.preprocess_options_widget import (
            PreprocessOptionsWidget,
        )

        for widget in host.findChildren(PreprocessOptionsWidget):
            widget.set_recognition_catalog(catalog)
            widget.set_advanced_mode_install_callback(
                self._settings_controller.install_recognition_mode
            )

    def _on_lazy_tab_changed(self, index: int) -> None:
        """显示 skeleton，并 single-flight 启动纯数据/模块定位预热。"""
        if self._closing:
            return
        inflight = self._lazy_tab_inflight
        if inflight is not None and inflight[0] == index:
            self._lazy_tab_pending_index = index
            return

        self._lazy_tab_generation += 1
        self._lazy_tab_pending_index = (
            index if index in self._lazy_tab_builders else None
        )
        if inflight is not None:
            return
        self._start_lazy_tab_prewarm(index)

    @staticmethod
    def _prewarm_lazy_tab(role: str) -> object:
        """Import the tab module off-GUI; QWidget construction remains on GUI."""
        module_names = {
            "batch": "vibeocr.classic.views.batch_recognition_tab",
            "qrcode": "vibeocr.classic.views.tabs.qrcode_tab",
            "pdf": "vibeocr.classic.views.tabs.pdf_tab",
            "about": "vibeocr.classic.views.tabs.about_tab",
        }
        module_name = module_names.get(role)
        if module_name is None:
            return None
        # Importing defines classes and warms pure dependencies (httpx/pydantic,
        # update metadata); no QWidget is instantiated or transferred here.
        return import_module(module_name).__name__

    def _start_lazy_tab_prewarm(self, index: int) -> None:
        if self._closing or self._lazy_tab_inflight is not None:
            return
        entry = self._lazy_tab_builders.get(index)
        if entry is None or self._ui.tabWidget.currentIndex() != index:
            return
        role, _builder = entry
        generation = self._lazy_tab_generation
        task = FunctionTask(lambda role=role: self._prewarm_lazy_tab(role))
        self._lazy_tab_tasks.add(task)
        self._lazy_tab_inflight = (index, generation, task)
        task.signals.finished.connect(
            lambda result, idx=index, gen=generation, current=task: (
                self._on_lazy_prewarm_done(idx, gen, current, result)
            )
        )
        task.signals.error.connect(
            lambda error, idx=index, gen=generation, current=task: (
                self._on_lazy_prewarm_failed(idx, gen, current, error)
            )
        )
        QThreadPool.globalInstance().start(task)

    def _on_lazy_prewarm_done(
        self, index: int, generation: int, task: FunctionTask, _result: object
    ) -> None:
        self._lazy_tab_tasks.discard(task)
        if self._lazy_tab_inflight is not None and self._lazy_tab_inflight[2] is task:
            self._lazy_tab_inflight = None
        if (
            self._closing
            or generation != self._lazy_tab_generation
            or self._ui.tabWidget.currentIndex() != index
            or index not in self._lazy_tab_builders
        ):
            self._resume_pending_lazy_tab()
            return
        self._lazy_tab_build_scheduled = (index, generation)
        # 这是 GUI 事件循环切片，不是后台构造：先让 skeleton 获得一次绘制机会，
        # 下一轮才在 GUI 线程创建 QWidget。
        QTimer.singleShot(0, lambda: self._build_lazy_tab_on_gui(index, generation))

    def _on_lazy_prewarm_failed(
        self, index: int, generation: int, task: FunctionTask, error: str
    ) -> None:
        logging.warning("[懒加载] %s 预热失败: %s", index, error)
        self._on_lazy_prewarm_done(index, generation, task, None)

    def _resume_pending_lazy_tab(self) -> None:
        index = self._lazy_tab_pending_index
        if (
            not self._closing
            and index is not None
            and self._ui.tabWidget.currentIndex() == index
        ):
            self._start_lazy_tab_prewarm(index)

    def _build_lazy_tab_on_gui(self, index: int, generation: int) -> None:
        if self._lazy_tab_build_scheduled == (index, generation):
            self._lazy_tab_build_scheduled = None
        if (
            self._closing
            or generation != self._lazy_tab_generation
            or self._ui.tabWidget.currentIndex() != index
        ):
            return
        entry = self._lazy_tab_builders.get(index)
        if entry is None:
            return
        role, builder = entry
        assert QThread.currentThread() is self.thread(), "QWidget 必须在 GUI 线程构造"
        try:
            widget = builder()
        except Exception:
            logging.exception(f"[懒加载] 构造 {role} 标签页失败")
            return

        self._lazy_tab_builders.pop(index, None)

        # 回填属性，使下游 hasattr/getattr 防御逻辑生效
        attr_map = {
            "batch": "_batch_tab",
            "qrcode": "_qrcode_tab",
            "pdf": "_pdf_tab",
            "about": "_about_tab",
        }
        attr = attr_map.get(role)
        if attr:
            setattr(self, attr, widget)

        # 用真实 widget 替换占位页（保持同一 index 与标题）。
        # 替换期间临时阻塞信号：removeTab/insertTab 会改变 currentIndex 从而
        # 再次触发 currentChanged，避免误触发其他懒加载页或重复进入本回调。
        tab_widget = self._ui.tabWidget
        title = tab_widget.tabText(index)
        prev_blocked = tab_widget.blockSignals(True)
        try:
            tab_widget.removeTab(index)
            tab_widget.insertTab(index, widget, title)
            tab_widget.setCurrentIndex(index)
        finally:
            tab_widget.blockSignals(prev_blocked)
        logging.debug(f"懒加载标签页已构造: {role}")

        # 构造后恢复分割器布局（与 _restore_layout 逻辑对齐）
        self._restore_lazy_tab_layout(role, widget)

        if self._runtime_gpu_capability is not None:
            self._apply_gpu_gating_to_all(self._runtime_gpu_capability)

    def _restore_lazy_tab_layout(self, role: str, widget: Any) -> None:
        """懒构造的 tab 在替换占位页后恢复其分割器布局。

        batch 的分割器恢复由 set_layout_manager 内部完成（构造时已调用），
        故此处仅处理 qrcode。pdf 无需恢复分割器。
        """
        try:
            if role == "qrcode" and hasattr(widget, "_splitter"):
                state = self._layout_manager.get_splitter_state("qrcode_tab")
                if state:
                    widget._splitter.restoreState(state)
        except Exception:
            logging.debug(f"[懒加载] 恢复 {role} 布局失败（忽略）", exc_info=True)

    def _init_about_tab(self) -> None:
        """初始化关于标签页（懒加载：首次切换到关于页才构造）。

        关于页置于末尾（设置页之后），符合「关于」居末的惯例。AboutTab 构造会同步
        读取 CHANGELOG.md 并解析 Markdown（QTextBrowser.setMarkdown），是启动期可省的
        CPU 开销，延迟到用户真正查看关于页时再构造。
        """
        self._about_tab: Any = None
        self._add_lazy_tab("关于", "about", self._build_about_tab, at_end=True)

    def _build_about_tab(self) -> Any:
        """构造关于标签页（懒加载时调用）。"""
        from vibeocr.classic.views.tabs.about_tab import AboutTab

        return AboutTab(status_callback=self._statusbar.showMessage)

    def _setup_console(self) -> None:
        """初始化日志"""
        self._log_handler = setup_logging(ConfigManager.instance().get_log_level())
        self._log_handler.status_signal.connect(self._on_log_status_update)
        logging.info("VibeOCR 启动")

    def _connect_signals(self) -> None:
        """连接信号槽"""
        # 懒加载 Tab：用户切换到占位页时触发真实构造
        self._ui.tabWidget.currentChanged.connect(self._on_lazy_tab_changed)
        # _restore_layout 可能在信号连接前已 setCurrentIndex 到懒加载占位页，
        # 此时 currentChanged 不会重发。这里补一次：若当前页仍是未构造的占位页，
        # 立即触发构造，确保恢复的标签页可见、可用。
        cur = self._ui.tabWidget.currentIndex()
        if cur in self._lazy_tab_builders:
            self._on_lazy_tab_changed(cur)

        # 快捷键（替代已删除的菜单）
        self._shortcut_open = QShortcut(QKeySequence("Ctrl+O"), self)
        self._shortcut_open.activated.connect(self._on_open_image)

        self._shortcut_screenshot = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_screenshot.activated.connect(self._on_screenshot)

        self._shortcut_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._shortcut_quit.activated.connect(self.close)

        # 截图组件
        overlay = self._overlay
        if overlay is not None:
            self._connect_overlay_signals(overlay)

        # 单次识别 Tab 的截图/文件请求由 MainWindow 处理
        self._single_tab.screenshot_requested.connect(self._on_screenshot)
        # 截图来源识别完成时，重新把主窗口提到前台（见 _bring_main_window_to_front）
        self._single_tab.bring_to_front_requested.connect(
            self._bring_main_window_to_front
        )
        self._single_tab.task_status_changed.connect(self._statusbar.showMessage)
        self._single_tab.result_status_changed.connect(self._statusbar.finish_task)

        # 设置页面控制器
        self._settings_controller = SettingsPageController(
            ui=self,
            project_root=self._project_root,
            status_callback=self._statusbar.showMessage,
            runtime_status_callback=self._show_background_runtime_status,
            ocr_ready_callback=lambda: self._ocr_ready,
            subprocess_manager=self._subprocess_manager,
            # 设置页重装/补装依赖成功后联动重新检测（Bug A 修复）：
            # 旧逻辑设置页装完只刷新表格，不联动 _ocr_ready/Worker，截图界面
            # 仍提示"未就绪"。现复用 dependency_manager.check_dependencies，
            # 检测完成回调（_on_dependency_check_finished）自动设 _ocr_ready、
            # 启动子进程 Worker，与首启路径行为一致。
            install_succeeded_callback=self._on_settings_install_succeeded,
            gpu_capability_callback=self._on_gpu_capability_resolved,
            recognition_catalog_callback=self._on_recognition_catalog_loaded,
            defer_machine_cache_status=True,
        )
        self._settings_controller.connect_signals()

    def _on_recognition_catalog_loaded(self, catalog) -> None:
        """把 health 中协商的识别模式投影到所有本地作业入口。"""
        self._recognition_catalog = catalog
        self._edge_toolbar.set_recognition_catalog(catalog)
        from vibeocr.classic.widgets.preprocess_options_widget import (
            PreprocessOptionsWidget,
        )

        for widget in self.findChildren(PreprocessOptionsWidget):
            widget.set_recognition_catalog(catalog)
            widget.set_advanced_mode_install_callback(
                self._settings_controller.install_recognition_mode
            )
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            setter = getattr(overlay, "set_recognition_catalog", None)
            if callable(setter):
                setter(catalog)
            install_callback_setter = getattr(
                overlay, "set_advanced_mode_install_callback", None
            )
            if callable(install_callback_setter):
                install_callback_setter(
                    self._settings_controller.install_recognition_mode
                )

    def _on_settings_install_succeeded(self) -> None:
        """设置页重装/补装依赖成功后的联动回调（Bug A 修复）

        由 SettingsPageController._open_reinstall_dialog 在对话框 emit
        install_succeeded 时调用。复用 DependencyManager.check_dependencies
        重新检测便携环境——检测完成回调（_on_dependency_check_finished）会
        自动设置 _ocr_ready 并启动子进程 Worker，使
        截图界面立即生效，无需重启程序。

        不直接设 _ocr_ready=True：让真实检测（双层 _probe_module）正确反映
        "装了但 mineru 间接依赖没装完"等异常状态，避免假就绪。
        """
        if self._closing:
            return
        logging.info("[设置安装] 依赖安装成功，重新检测以联动截图功能")
        # reset 确保重入安全（若上一次检测仍在进行，避免 _is_checking 短路）
        self._dependency_manager.check_dependencies()

    def _try_load_cache(self) -> None:
        """后台读取 Classic 诊断缓存；结果不参与 Runtime readiness。"""
        self._request_machine_cache_load()

    def _request_machine_cache_load(self) -> None:
        """读取一份经过机器码校验、仅供设置页展示的诊断快照。"""
        if self._closing:
            return
        if self._machine_cache_running:
            return

        self._machine_cache_running = True
        self._machine_cache_generation += 1
        generation = self._machine_cache_generation
        task = FunctionTask(lambda: is_cache_valid(self._project_root))
        self._machine_cache_tasks.add(task)

        def finished(result: object) -> None:
            self._machine_cache_tasks.discard(task)
            self._machine_cache_running = False
            if self._closing or generation != self._machine_cache_generation:
                return
            valid, data = result if isinstance(result, tuple) else (False, None)
            self._machine_cache_data = (
                data if valid and isinstance(data, dict) else None
            )
            controller = getattr(self, "_settings_controller", None)
            if controller is not None:
                controller.apply_deferred_machine_cache_status(bool(valid))

        def failed(error: str) -> None:
            logging.warning("[缓存] 后台机器缓存校验失败: %s", error)
            finished((False, None))

        task.signals.finished.connect(finished)
        task.signals.error.connect(failed)
        QThreadPool.globalInstance().start(task)

    def _check_embedded_dependencies(self) -> None:
        """异步检查嵌入式OCR依赖"""
        if self._closing:
            return
        self._dependency_manager.check_dependencies()

    @Slot(bool, list)
    def _on_dependency_check_finished(self, ready: bool, missing: list) -> None:
        """依赖检查完成"""
        if self._closing:
            return
        # 清理旧版 updater 遗留的逐包同步标记。组件升级现在由
        # component-lock 与 Runtime Installer 整组处理，然后继续常规依赖检查。
        if self._check_pending_sync():
            return

        already_ready = self._ocr_ready
        self._dependency_check_complete = True
        if ready:
            self._ocr_ready = True
            self._statusbar.set_service("运行环境可用 · Supervisor 未连接")
            # 仅在未从缓存设置过就绪状态时更新状态栏（避免覆盖缓存提示）
            if not already_ready:
                self._statusbar.showMessage("准备 Supervisor")
            logging.info("OCR 运行环境可用")

            # Runtime Installer inspect 是唯一 readiness 权威；Classic 诊断缓存
            # 即使仍在后台加载，也不得阻塞或提前放行 Supervisor 启动。
            self._continue_ready_startup()
        else:
            self._ocr_ready = False
            self._statusbar.set_service("运行环境不可用")
            self._statusbar.set_residency("不可用")
            self._statusbar.set_result(format_runtime_unavailable(missing))
            self._statusbar.clearMessage()

            # Runtime 未就绪时主动弹出首启安装引导。DependencyManager 现在
            # 返回 Installer integrity（例如 ``cpu: not-installed``），不能再
            # 依赖旧版“Python 运行时”文案判断首启。
            # 用 singleShot 延迟，避免在依赖检查回调线程上下文直接弹模态对话框。
            if missing:
                QTimer.singleShot(300, self._start_install)

    def _continue_ready_startup(self) -> None:
        """Runtime inspect 成功后的启动编排（仅在 GUI 线程应用结果）。"""
        if self._closing or not self._ocr_ready:
            return

        self._start_supervisor()

    def _check_pending_sync(self) -> bool:
        """清理旧逐包同步标记；组件升级不再接管启动流程。"""
        # Phase 3：组件只能随产品 component-lock 整组升级。旧逐包同步标记
        # 不再消费；清理遗留文件后让 Runtime Installer 按新绑定 ensure。
        self._delete_pending_sync()
        return False

    def _delete_pending_sync(self) -> None:
        """删除新旧目录中遗留的逐包同步标记。"""
        pending_paths = (
            self._project_root / "data" / "cache" / "update" / "pending_sync.json",
            self._project_root / "data" / "settings" / "pending_sync.json",
        )
        for pending_path in pending_paths:
            try:
                pending_path.unlink(missing_ok=True)
            except Exception as e:
                logging.warning("[依赖同步] 删除 %s 失败: %s", pending_path, e)

    def _start_supervisor(self) -> None:
        """依赖检测完成后启动唯一的 PySide Supervisor 会话。"""
        if self._closing:
            logging.debug("[MainWindow] 应用程序正在关闭，跳过启动 Supervisor")
            return

        logging.debug("[MainWindow] 正在启动共享 Supervisor...")
        self._statusbar.showMessage("Supervisor 启动中")

        # Supervisor 的进程启动、ready 握手和 typed client 初始化可能耗时；
        # 交给 SubprocessManager 的线程池，完成后通过 service_ready 回到 Qt 主线程。
        self._subprocess_manager.start_supervisor()

    @Slot(bool)
    def _on_supervisor_ready(self, success: bool) -> None:
        """Supervisor ready envelope 回调。"""
        if self._closing:
            logging.debug("[MainWindow] 忽略关闭后的 Supervisor ready 结果")
            return
        if success:
            logging.debug("[MainWindow] Supervisor 已就绪")
            self._statusbar.set_service("Supervisor 已连接")
            # 启动里程碑 T4：Supervisor ready
            from vibeocr.classic.startup_metrics import StartupEvent, record_startup

            record_startup(StartupEvent.SUPERVISOR_READY)
            self._ensure_ocr_status_callback()

            # 进程 ready envelope 与已启动的 typed adapter 共同构成唯一就绪条件。
            from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

            adapter = get_supervisor_adapter()
            if not adapter.is_started:
                logging.error("[MainWindow] Supervisor ready 但 v2 适配器未启动")
                self._on_supervisor_ready(False)
                return
            settings_controller = getattr(self, "_settings_controller", None)
            # 先发布基础 ready；若启用了自动预加载，设置控制器随后会用更具体的
            # “正在预加载 N 个模型”覆盖它。
            self._statusbar.clearMessage()
            self._statusbar.set_residency("按需加载 · 尚未确认驻留")
            if settings_controller is not None:
                settings_controller.on_supervisor_ready()
            self._record_supervisor_ready()
        else:
            logging.warning("[MainWindow] Supervisor 子进程启动失败")
            self._statusbar.set_service("Supervisor 启动失败")
            self._statusbar.set_residency("不可用")
            self._statusbar.set_result("OCR 暂不可用")
            self._statusbar.clearMessage()
            if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
                from vibeocr.classic.startup_metrics import flush_startup

                flush_startup()
                os._exit(1)
                return
            # 显示错误提示
            QMessageBox.warning(
                self,
                "Supervisor 启动失败",
                supervisor_start_failure_message(),
            )

    @Slot(str)
    def _on_subprocess_progress(self, stage: str) -> None:
        """子进程启动进度回调"""
        self._statusbar.set_service("Supervisor 启动中")
        self._statusbar.showMessage(stage)

    def _record_supervisor_ready(self) -> None:
        """记录 Supervisor 已可交互；模型驻留不参与进程 readiness。"""
        from vibeocr.classic.startup_metrics import (
            StartupEvent,
            flush_startup,
            record_startup,
        )

        record_startup(StartupEvent.BACKEND_READY)
        record_startup(StartupEvent.INTERACTIVE)
        flush_startup()
        if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
            os._exit(0)

    def _run_after_supervisor_invalidated(self, continuation) -> None:
        """维护对话框只能在旧 Supervisor 确认退出后创建。"""

        if self._closing:
            return
        if self._pending_maintenance_dialog is not None:
            self._statusbar.showMessage("正在停止 OCR Supervisor，请稍候...")
            return

        self._pending_maintenance_dialog = continuation
        manager = self._subprocess_manager
        manager.invalidation_finished.connect(
            self._on_supervisor_invalidated_for_maintenance
        )
        started = manager.invalidate_supervisor()
        if not started:
            self._cancel_pending_maintenance_dialog()
            if manager.is_invalidating:
                self._statusbar.showMessage(
                    "已有 Supervisor 维护准备正在进行，请稍候..."
                )
                return
            QMessageBox.warning(
                self,
                "无法开始维护",
                "OCR Supervisor 无法安全停止，安装未开始。",
            )
            return
        self._statusbar.showMessage("正在停止 OCR Supervisor...")

    @Slot(bool, str)
    def _on_supervisor_invalidated_for_maintenance(
        self, success: bool, error: str
    ) -> None:
        continuation = self._pending_maintenance_dialog
        self._cancel_pending_maintenance_dialog()
        if self._closing or continuation is None:
            return
        if not success:
            QMessageBox.warning(
                self,
                "无法开始维护",
                f"OCR Supervisor 未能安全停止，安装未开始。\n{error}",
            )
            return
        continuation()

    def _cancel_pending_maintenance_dialog(self) -> None:
        if self._pending_maintenance_dialog is None:
            return
        self._pending_maintenance_dialog = None
        try:
            self._subprocess_manager.invalidation_finished.disconnect(
                self._on_supervisor_invalidated_for_maintenance
            )
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _show_install_dialog(self, missing: list) -> None:
        """自动准备 Base Runtime；高级组件只在用户选择相应模式后安装。"""
        if self._closing:
            return
        self._run_after_supervisor_invalidated(
            lambda: self._show_install_dialog_after_invalidation(missing)
        )

    def _show_install_dialog_after_invalidation(self, missing: list) -> None:
        """旧 Supervisor 已退出后显示安装对话框。"""
        if self._closing:
            return
        from vibeocr.classic.widgets.install_dialog import InstallDialog

        # ``()`` 是 capability-protected 的“仅 Base Runtime”意图：不向用户
        # 询问 CPU/GPU，也不把 Paddle/MinerU 当作工作台可用性的前置条件。
        dialog = InstallDialog(
            self._project_root,
            self,
            install_component_ids=(),
        )
        dialog.finished.connect(self._on_install_finished)
        dialog.install_succeeded.connect(self._on_install_succeeded)
        dialog.exec()

    def _start_install(self) -> None:
        """开始安装依赖（保留入口，直接走首启合并对话框）"""
        if self._closing:
            return
        self._show_install_dialog([])

    @Slot(int)
    def _on_install_finished(self, result: int) -> None:
        """安装完成"""
        if self._closing:
            return
        if result == 1:
            self._statusbar.showMessage("OCR依赖安装成功")
            # 安装成功后启动子进程 Worker
            self._start_supervisor()
            # 双保险刷新设置页（覆盖只发 finished 不发 install_succeeded 的路径）
            self._refresh_settings_env_state()
        else:
            self._statusbar.showMessage("OCR依赖安装失败")

    @Slot()
    def _on_install_succeeded(self) -> None:
        """安装成功后标记就绪"""
        if self._closing:
            return
        self._ocr_ready = True
        self._statusbar.showMessage("Base Runtime 已准备，快速 OCR 可直接使用")
        # 安装完成后 Python 运行时状态已变，刷新设置页环境维护区 label
        # （首启时 label 在 Python 未装时写下"未安装"，此处避免重启才更新）
        self._refresh_settings_env_state()

    def _refresh_settings_env_state(self) -> None:
        """刷新设置页"环境维护区"状态（Python 路径/就绪）。

        安装/同步/重装成功后调用，避免 label 停留在启动时计算的"未安装"，
        导致用户必须重启程序才看到正确状态。
        """
        controller = getattr(self, "_settings_controller", None)
        if controller is not None:
            try:
                controller.refresh_runtime_state()
            except Exception as e:
                logging.warning("[MainWindow] 刷新设置页环境状态失败: %s", e)

    @Slot()
    def _on_open_image(self) -> None:
        """打开图片文件"""
        if not self._check_ocr_ready():
            return
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        logging.debug("打开图片文件对话框")

        from vibeocr.classic.utils.mime_types import (
            FILE_FILTER_DOCUMENTS,
            FILE_FILTER_IMAGES,
        )

        self._open_file_dialog_and_dispatch(
            "打开文件",
            f"{FILE_FILTER_IMAGES};;{FILE_FILTER_DOCUMENTS};;所有文件 (*)",
        )

    @Slot()
    def _on_open_file_from_preview(self) -> None:
        """从预览区域打开文件（支持图片和 PDF）"""
        if not self._check_ocr_ready():
            return
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        logging.debug("打开文件对话框（图片/PDF）")

        from vibeocr.classic.utils.mime_types import FILE_FILTER_ALL

        self._open_file_dialog_and_dispatch(
            "选择文件", f"{FILE_FILTER_ALL};;所有文件 (*)"
        )

    def _open_file_dialog_and_dispatch(self, title: str, file_filter: str) -> None:
        """统一三处文件入口；图片后台解码，文档沿现有异步识别路径。"""
        from vibeocr.classic.utils.mime_types import is_document_file

        file_path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if not file_path:
            return

        if is_document_file(file_path):
            self._single_tab._preview_widget.clear()
            self._single_tab.process_file(file_path)
            return
        self._request_image_load(file_path)

    @Slot(str)
    def _request_image_load(self, file_path: str) -> None:
        """提交可被新请求取代的图片解码任务。"""
        if self._closing:
            return
        self._statusbar.showMessage(f"正在读取图片：{Path(file_path).name}...")
        self._image_load_jobs.submit(
            lambda cancel_event: (
                file_path,
                decode_image_file(file_path, cancel_event),
            )
        )

    @Slot(int, object)
    def _on_image_file_loaded(self, _generation: int, result: object) -> None:
        """GUI 线程只负责 QImage→QPixmap 与控件赋值。"""
        if self._closing or not isinstance(result, tuple) or len(result) != 2:
            return
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，已忽略迟到的图片", 3000)
            return
        file_path, image = result
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_image_file_load_failed(_generation, f"无法显示图片：{file_path}")
            return
        self._single_tab.set_image_for_recognition(pixmap)
        self._single_tab.set_pixmap(pixmap)
        self._single_tab.run_ocr(pixmap)

    @Slot(int, str)
    def _on_image_file_load_failed(self, _generation: int, error: str) -> None:
        if not self._closing:
            self._statusbar.showMessage(f"图片读取失败：{error}", 5000)

    def _check_ocr_ready(self) -> bool:
        """检查OCR功能是否可用"""
        if not self._dependency_check_complete:
            QMessageBox.information(
                self,
                "正在检测依赖",
                "OCR依赖检测中，请稍候...\n\n检测完成后才能使用截图识别功能。",
            )
            return False

        if not self._ocr_ready:
            reply = QMessageBox.question(
                self,
                "OCR功能未就绪",
                "OCR功能需要安装依赖才能使用。\n\n是否现在安装？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._start_install()
            return False

        # 依赖已就绪，但 Supervisor 尚未完成就绪握手 —— 拦截截图。
        # 模型预加载不参与本条件；Supervisor 可接单后即允许按需识别。
        if not self._subprocess_manager.is_ready:
            QMessageBox.information(
                self,
                "Supervisor 启动中",
                "OCR Supervisor 子进程正在启动并等待就绪握手，请稍候再试。",
            )
            return False
        return True

    @Slot()
    def _on_screenshot(self) -> None:
        """开始截图"""
        if self._closing:
            return
        if not self._check_ocr_ready():
            return

        # 记录截图前主窗口的最小化状态，截图结束后据此恢复（不抢焦点）。
        self._main_window_minimized_before_capture = self.isMinimized()
        self.showMinimized()
        # 延迟启动截图，让窗口有时间最小化
        QTimer.singleShot(200, self._start_fresh_overlay_capture)

    @Slot(str)
    def _on_pipeline_screenshot(self, pipeline_name: str) -> None:
        """从工具栏快捷管道按钮触发截图识别

        与 _on_screenshot 相同，但预先设置管道名称，截图选区完成后
        直接进入对应管道识别，跳过截图编辑界面。
        """
        if self._closing or not self._check_ocr_ready():
            return

        self._main_window_minimized_before_capture = self.isMinimized()
        self.showMinimized()
        # 预设管道，截图选区完成后自动用该管道识别
        QTimer.singleShot(200, lambda: self._start_fresh_overlay_capture(pipeline_name))

    def _start_fresh_overlay_capture(self, pipeline_name: str | None = None) -> None:
        """每次截图创建全新的 ScreenCaptureOverlay 实例并启动。

        早期复用单个 overlay 实例（hide()/show() 之间），分层窗口
        （WA_TranslucentBackground）的后备存储在会话间保留上一轮画面，
        导致下次 show() 时「一闪而过上一次截图界面」。尝试在 show 前
        repaint() 清屏、show 后再 repaint() 均无效——分层窗口的合成像素
        不随隐藏窗口的 repaint 更新。

        根治方案：每次截图新建 overlay（新原生窗口、空后备存储），
        _cleanup 时 deleteLater() 释放。代价是每轮一次轻量窗口创建，
        远小于残留帧带来的体验问题。
        """
        if self._closing:
            return
        if pipeline_name is not None and self._recognition_catalog is not None:
            mode = self._recognition_catalog.mode(pipeline_name)
            if mode is not None:
                if mode.availability == "unavailable":
                    reason = f"（{mode.reason_code}）" if mode.reason_code else ""
                    self._statusbar.showMessage(
                        f"{mode.display_name} 当前不可用{reason}"
                    )
                    return
                if mode.availability == "preparation_required":
                    self._statusbar.showMessage(
                        f"正在准备 {mode.display_name} 所需组件"
                    )
                    self._settings_controller.install_recognition_mode(mode)
                    return
        # 释放上一轮（若未正常 _cleanup，防御性清理）
        if self._overlay is not None:
            previous = self._overlay
            try:
                previous.finish_capture()
                previous.request_save_shutdown()
            except Exception:
                logging.exception("清理旧截图覆盖层失败")
            if previous.drain_saves(0):
                previous.deleteLater()
            else:
                # 已确认保存不可因下一轮截图而取消；保留 QObject 到完成/失败
                # 通知送达，随后在 GUI 事件循环释放。
                self._retired_overlays.add(previous)
            self._overlay = None

        self._overlay = ScreenCaptureOverlay()
        self._connect_overlay_signals(self._overlay)
        if self._recognition_catalog is not None:
            self._overlay.set_recognition_catalog(self._recognition_catalog)
            self._overlay.set_advanced_mode_install_callback(
                self._settings_controller.install_recognition_mode
            )
        if self._runtime_gpu_capability is not None:
            self._apply_gpu_gating_to_all(self._runtime_gpu_capability)

        if pipeline_name is not None:
            catalog = self._recognition_catalog
            mode = catalog.mode(pipeline_name) if catalog is not None else None
            if mode is not None:
                self._overlay.set_pending_recognition_mode(mode.mode_id)
            else:
                # 旧 Backend 没有 mode catalog 时，toolbar 新语义仍需安全投影
                # 回已发布的 pipeline wire；旧 toolbar 传 pipeline 也保持兼容。
                from vibeocr.classic.runtime_selection import (
                    legacy_execution_projection,
                )

                projection = legacy_execution_projection(pipeline_name)
                self._overlay.set_pending_pipeline(
                    projection[0] if projection is not None else pipeline_name
                )
        self._overlay.start_capture()

    def _connect_overlay_signals(self, overlay: ScreenCaptureOverlay) -> None:
        overlay.confirmed.connect(self._on_overlay_confirmed)
        overlay.copied.connect(self._on_overlay_copied)
        overlay.saved.connect(
            lambda path, current=overlay: self._on_overlay_saved_for(current, path)
        )
        overlay.save_failed.connect(
            lambda error, current=overlay: self._on_overlay_save_failed_for(
                current, error
            )
        )
        overlay.cancelled.connect(self._on_overlay_cancelled)

    def _restore_main_window(self, *, activate: bool) -> None:
        """截图结束后恢复主窗口状态。

        静默操作（复制/保存/取消）：仅当截图前主窗口未被最小化时恢复可见，
        截图前已最小化则保持最小化（不抢焦点）。
        识别操作（activate=True）：用户明确想看结果，无论截图前是否最小化都恢复
        可见——否则工具栏/托盘触发截图后窗口永远不出现。

        Args:
            activate: True 时额外激活窗口并置顶（仅识别路径）。
        """
        if self._closing:
            return
        if activate or not self._main_window_minimized_before_capture:
            self.showNormal()
        if activate:
            self.activateWindow()
            self.raise_()

    def _bring_main_window_to_front(self) -> None:
        """识别完成后把主窗口重新提到前台。

        截图确认时（_on_overlay_confirmed）已激活过一次主窗口，但 OCR 是异步的，
        可能耗时数秒（首次还需下载模型）。这期间用户或系统切走窗口后，开始前
        那次激活已失效——表现为「识别后主界面不弹出」。SingleRecognitionTab 在
        截图来源识别完成时发出 bring_to_front_requested，本槽在结果就绪后再次
        showNormal + activateWindow + raise_，确保窗口真正前置。

        Windows 上 activateWindow 对非前台进程常只闪烁任务栏，故延迟一拍重试，
        规避 overlay 刚关闭导致前台锁丢失的竞态。
        """
        if self._closing:
            return
        self.showNormal()
        self.activateWindow()
        self.raise_()
        # 延迟重试一次：跨进程前台权限在 overlay/其它窗口刚关闭后可能尚未归还，
        # 立即 activateWindow 会失败；下一事件循环重试成功率更高。
        QTimer.singleShot(0, self.activateWindow)

    @Slot(QPixmap, object)
    def _on_overlay_confirmed(self, pixmap: QPixmap, options) -> None:
        """截图确认，执行 OCR

        options 来自截图面板（screenshot 源），首次识别保持用截图源选项；
        同时经 set_image_for_recognition 启用「重新识别」按钮——
        之后点「重新识别」会改用界面面板选项（main 源）。
        """
        # 识别需要立即展示 OCR 结果，故激活并置顶主窗口。
        self._restore_main_window(activate=True)
        # 异步化后事件循环在 OCR 期间照常转动，用户可能在上一次识别未完成时
        # 再次触发截图确认；此时静默忽略并提示，避免旧结果覆盖新图。
        if self._single_tab.is_processing:
            self._statusbar.showMessage("上一次识别尚未完成，请稍候", 2000)
            return
        if not pixmap.isNull():
            self._single_tab.set_image_for_recognition(pixmap)
            self._single_tab.set_pixmap(pixmap)
            # from_screenshot=True：识别完成时让 tab 发 bring_to_front_requested，
            # MainWindow 在结果就绪后再次前置（OCR 期间窗口可能被切走）。
            self._single_tab.run_ocr(pixmap, options, from_screenshot=True)

    @Slot(QPixmap)
    def _on_overlay_copied(self, pixmap: QPixmap) -> None:
        """截图复制完成"""
        # 复制即结束本次框选；主窗口保持截图时的最小化状态，避免遮挡用户
        # 正在粘贴内容的目标应用。
        self._statusbar.showMessage("图片已复制到剪贴板")

    @Slot(str)
    def _on_overlay_saved(self, file_path: str) -> None:
        """截图保存完成"""
        if self._closing:
            return
        # 保存即结束本次框选；不要因为保存完成重新弹出主窗口。
        self._statusbar.showMessage(f"图片已保存: {file_path}")

    def _on_overlay_saved_for(
        self, overlay: ScreenCaptureOverlay, file_path: str
    ) -> None:
        self._on_overlay_saved(file_path)
        QTimer.singleShot(0, lambda: self._release_retired_overlay(overlay))

    def _on_overlay_save_failed_for(
        self, overlay: ScreenCaptureOverlay, error: str
    ) -> None:
        if not self._closing:
            self._statusbar.showMessage(error, 5000)
        QTimer.singleShot(0, lambda: self._release_retired_overlay(overlay))

    def _release_retired_overlay(self, overlay: ScreenCaptureOverlay) -> None:
        if overlay not in self._retired_overlays or not overlay.drain_saves(0):
            return
        self._retired_overlays.discard(overlay)
        overlay.deleteLater()

    @Slot()
    def _on_overlay_cancelled(self) -> None:
        """截图取消"""
        # 取消为静默操作，仅恢复可见性、不抢焦点。
        self._restore_main_window(activate=False)

    def closeEvent(self, event) -> None:
        """两阶段关闭：首次仅冻结/请求取消，后台 drain 后第二次才 accept。"""
        phase = getattr(self, "_shutdown_phase", "idle")
        if phase == "ready":
            event.accept()
            logging.debug("应用程序已关闭")
            return
        if phase == "draining":
            event.ignore()
            return

        # 检查是否应最小化到托盘
        if (
            not self._force_quit
            and self._app_settings
            and self._app_settings.minimize_to_tray
            and self._tray_icon is not None
        ):
            event.ignore()
            self.hide()
            logging.debug("主窗口已最小化到系统托盘")
            return

        logging.debug("正在关闭应用程序...")
        event.ignore()
        self._shutdown_phase = "draining"
        self._shutdown_stage = "gui"
        self._shutdown_started_at = time.monotonic()
        self._shutdown_timed_out = False
        self._begin_shutdown_requests()
        self._shutdown_gui_probes = self._collect_shutdown_gui_probes()
        self._shutdown_poll_timer.start()
        self._poll_shutdown_state()

    def _begin_shutdown_requests(self) -> None:
        """GUI 阶段：冻结回调和控件，只发协作取消，不等待 worker。"""

        assert QThread.currentThread() is self.thread()

        # 从这里开始拒绝任何迟到回调重新启动共享 Supervisor。
        self._closing = True
        self._cancel_pending_maintenance_dialog()
        self._lazy_tab_generation = getattr(self, "_lazy_tab_generation", 0) + 1
        self._lazy_tab_pending_index = None
        self._lazy_tab_build_scheduled = None
        if hasattr(self, "setEnabled"):
            self.setEnabled(False)
        image_load_jobs = getattr(self, "_image_load_jobs", None)
        if image_load_jobs is not None:
            image_load_jobs.close()
        dependency_manager = getattr(self, "_dependency_manager", None)
        if dependency_manager is not None and hasattr(
            dependency_manager, "request_shutdown"
        ):
            dependency_manager.request_shutdown()
        startup_update_task = getattr(self, "_startup_update_task", None)
        if startup_update_task is not None:
            startup_update_task.request_shutdown()
        overlays = [getattr(self, "_overlay", None)]
        overlays.extend(tuple(getattr(self, "_retired_overlays", set())))
        for overlay in overlays:
            if overlay is None:
                continue
            if hasattr(overlay, "request_shutdown"):
                overlay.request_shutdown()
            elif hasattr(overlay, "request_save_shutdown"):
                overlay.request_save_shutdown()
        self._machine_cache_generation = (
            getattr(self, "_machine_cache_generation", 0) + 1
        )

        # 异步识别可能仍在 qasync loop 上运行；在清理任何 widget 之前先标记关闭态
        # 并取消进行中的识别 task，否则 _on_ocr_finished/_on_ocr_error 回调会在
        # _result_widget.cleanup() 之后写入已销毁的 web view。
        if hasattr(self, "_single_tab") and self._single_tab is not None:
            if hasattr(self._single_tab, "request_shutdown"):
                self._single_tab.request_shutdown()
            else:
                self._single_tab.set_closing(True)
        qrcode_tab = getattr(self, "_qrcode_tab", None)
        if qrcode_tab is not None:
            if hasattr(qrcode_tab, "request_shutdown"):
                qrcode_tab.request_shutdown()
            elif hasattr(qrcode_tab, "set_closing"):
                qrcode_tab.set_closing(True)

        # 批处理没有独立 request API；shutdown(0) 只请求取消并进行零等待探测。
        batch_tab = getattr(self, "_batch_tab", None)
        if batch_tab is not None:
            if hasattr(batch_tab, "request_shutdown"):
                batch_tab.request_shutdown()
            elif hasattr(batch_tab, "shutdown"):
                batch_tab.shutdown(timeout_ms=0)

        if hasattr(self, "_settings_controller"):
            if hasattr(self._settings_controller, "request_shutdown"):
                self._settings_controller.request_shutdown()

        # PDF 页签只发取消请求，实际等待纳入下方统一 wall-clock 预算。
        if hasattr(self, "_pdf_tab") and self._pdf_tab:
            if hasattr(self._pdf_tab, "request_shutdown"):
                self._pdf_tab.request_shutdown()

        subprocess_manager = getattr(self, "_subprocess_manager", None)
        if subprocess_manager is not None and hasattr(
            subprocess_manager, "request_shutdown"
        ):
            subprocess_manager.request_shutdown()

        from vibeocr.classic.utils.dialog_workers import request_dialog_workers_shutdown

        request_dialog_workers_shutdown()

        try:
            from vibeocr.classic.utils.qt_async import get_async_runner

            runner = get_async_runner()
            if runner.active_count > 0:
                runner.cancel_all()
        except Exception:
            logging.debug("请求取消 async runner 失败", exc_info=True)

        # 关闭边缘工具栏
        if hasattr(self, "_edge_toolbar") and self._edge_toolbar:
            self._edge_toolbar.close()

        # 保存应用设置
        if self._app_settings:
            self._app_settings.save()

        # 保存布局
        self._save_layout()

    def _collect_shutdown_gui_probes(self) -> tuple[tuple[str, Any], ...]:
        """Snapshot side-effect-free probes; every callable runs on the GUI owner."""
        assert QThread.currentThread() is self.thread()
        probes: list[tuple[str, Any]] = []

        def add_method(name: str, owner: object | None, method_name: str) -> None:
            method = getattr(owner, method_name, None)
            if callable(method):
                probes.append((name, method))

        add_method(
            "dependency_manager",
            getattr(self, "_dependency_manager", None),
            "is_drained",
        )
        add_method(
            "startup_update",
            getattr(self, "_startup_update_task", None),
            "is_drained",
        )
        add_method(
            "settings",
            getattr(self, "_settings_controller", None),
            "is_drained",
        )
        for name, attr in (
            ("single", "_single_tab"),
            ("qrcode", "_qrcode_tab"),
            ("batch", "_batch_tab"),
            ("pdf", "_pdf_tab"),
        ):
            add_method(name, getattr(self, attr, None), "is_drained")

        image_jobs = getattr(self, "_image_load_jobs", None)
        if image_jobs is not None:
            probes.append(("main_image_load", lambda jobs=image_jobs: jobs.drain(0)))

        probes.append(
            (
                "machine_cache",
                lambda: not getattr(self, "_machine_cache_tasks", set()),
            )
        )
        probes.append(
            ("lazy_tabs", lambda: not getattr(self, "_lazy_tab_tasks", set()))
        )

        overlays = [getattr(self, "_overlay", None)]
        overlays.extend(tuple(getattr(self, "_retired_overlays", set())))
        for index, overlay in enumerate(overlays):
            if overlay is not None:
                if hasattr(overlay, "is_drained"):
                    probes.append((f"overlay_{index}", overlay.is_drained))
                elif hasattr(overlay, "drain_saves"):
                    probes.append(
                        (
                            f"overlay_{index}",
                            lambda current=overlay: current.drain_saves(0),
                        )
                    )

        add_method(
            "subprocess",
            getattr(self, "_subprocess_manager", None),
            "is_drained",
        )
        from vibeocr.classic.utils.dialog_workers import are_dialog_workers_drained

        probes.append(("dialog_workers", are_dialog_workers_drained))
        try:
            from vibeocr.classic.utils.qt_async import (
                are_tracked_native_jobs_drained,
                get_async_runner,
            )

            runner = get_async_runner()
            probes.append(("async_runner", lambda: runner.active_count == 0))
            probes.append(("async_native", are_tracked_native_jobs_drained))
        except Exception:
            logging.debug(
                "Unable to snapshot async runner during shutdown", exc_info=True
            )
        return tuple(probes)

    @Slot()
    def _poll_shutdown_state(self) -> None:
        """Advance shutdown without blocking the Qt event loop."""
        if getattr(self, "_shutdown_phase", "idle") != "draining":
            return
        assert QThread.currentThread() is self.thread()

        elapsed_ms = (time.monotonic() - self._shutdown_started_at) * 1000
        if elapsed_ms >= self._SHUTDOWN_UX_BUDGET_MS and not self._shutdown_timed_out:
            self._shutdown_timed_out = True
            logging.warning(
                "Shutdown exceeded the %d ms UX budget; keeping owners alive until drained",
                self._SHUTDOWN_UX_BUDGET_MS,
            )

        if self._shutdown_stage != "gui":
            return

        pending: list[str] = []
        for name, probe in self._shutdown_gui_probes:
            try:
                if not bool(probe()):
                    pending.append(name)
            except Exception:
                pending.append(name)
                logging.exception("Shutdown probe failed: %s", name)
        if pending:
            return
        self._start_external_shutdown()

    def _start_external_shutdown(self) -> None:
        """Detach plain-Python resources only after every Qt owner is drained."""
        assert QThread.currentThread() is self.thread()
        if self._shutdown_stage != "gui":
            return

        from vibeocr.classic.client import shutdown_backend_client

        operations: list[tuple[str, Any]] = [
            ("backend_session", shutdown_backend_client)
        ]
        subprocess_manager = getattr(self, "_subprocess_manager", None)
        if subprocess_manager is not None and hasattr(
            subprocess_manager, "take_shutdown_callable"
        ):
            service_shutdown = subprocess_manager.take_shutdown_callable()
            if callable(service_shutdown):
                operations.append(("subprocess_service", service_shutdown))

        job = ExternalShutdownJob(tuple(operations), self)
        self._shutdown_external_job = job
        self._shutdown_stage = "external"
        job.finished.connect(self._on_external_shutdown_finished)
        job.start()

    @Slot()
    def _on_external_shutdown_finished(self) -> None:
        assert QThread.currentThread() is self.thread()
        job = self._shutdown_external_job
        if job is None:
            return
        self._shutdown_external_job = None
        if job.errors:
            logging.warning("External shutdown operations failed: %s", job.errors)
        job.deleteLater()
        self._finalize_shutdown()

    def _finalize_shutdown(self) -> None:
        """Destroy GUI resources only after native and external work has returned."""
        assert QThread.currentThread() is self.thread()
        self._shutdown_poll_timer.stop()
        self._cleanup_overlay_widgets()
        self._cleanup_webengine_widgets()
        self._shutdown_stage = "ready"
        self._shutdown_phase = "ready"
        self.close()

    def _cleanup_overlay_widgets(self) -> None:
        """保存 drain 成功后才在 GUI 线程释放 current/retired overlay。"""
        assert QThread.currentThread() is self.thread()
        overlays = [getattr(self, "_overlay", None)]
        overlays.extend(tuple(getattr(self, "_retired_overlays", set())))
        self._overlay = None
        self._retired_overlays.clear()
        for overlay in overlays:
            if overlay is not None:
                overlay.deleteLater()

    def _cleanup_webengine_widgets(self) -> None:
        """只能在 GUI 线程、所有成功 drain 之后调用。"""
        assert QThread.currentThread() is self.thread()

        # 所有会写结果的后台任务已请求取消/尽力 drain 后，再销毁 WebEngine。
        for tab in (
            getattr(self, "_single_tab", None),
            getattr(self, "_batch_tab", None),
        ):
            if tab and hasattr(tab, "_result_widget") and tab._result_widget:
                tab._result_widget.cleanup()

    # ============================================================
    # 系统托盘与边缘工具栏集成
    # ============================================================

    def set_app_settings(self, app_settings) -> None:
        """设置应用设置对象（由 main.py 调用）"""

        self._app_settings = app_settings  # type: ignore[assignment]
        self._init_app_settings_ui()
        self.apply_app_settings()

    def set_tray_icon(self, tray_icon) -> None:
        """设置系统托盘图标（由 main.py 调用）"""
        self._tray_icon = tray_icon

    def prewarm_result_webengine(self) -> None:
        """预热单次识别结果页 WebEngine，避免首次截图结果前主界面闪烁。

        由 ``main.py:launch_application`` 在窗口 ``show()`` + splash 收尾后经
        ``QTimer.singleShot(0, ...)`` 调度，在启动空闲片段触发。转发到
        ``SingleRecognitionTab._result_widget.prewarm_webengine``，把 Chromium
        冷启动成本从「首次结果渲染时」前移到「用户已看到界面、尚未首次截图」。
        ``_closing`` 为真时跳过；``_result_widget`` 缺失时静默返回（防御性 getattr）。
        """
        if self._closing:
            return
        rw = getattr(
            getattr(self._single_tab, "_result_widget", None), "prewarm_webengine", None
        )
        if rw is not None:
            rw()

    def apply_app_settings(self) -> None:
        """应用当前设置到工具栏等组件"""
        if not self._app_settings:
            return
        # 工具栏显示/隐藏
        if self._app_settings.show_toolbar:
            pos = self._app_settings.toolbar_pos
            if pos and "x" in pos and "y" in pos:
                self._edge_toolbar.move(pos["x"], pos["y"])
            else:
                self._edge_toolbar.set_initial_position()
            self._edge_toolbar._detect_edge()
            self._edge_toolbar.show()
        else:
            self._edge_toolbar.hide()
        # 自动隐藏和延迟
        self._edge_toolbar.set_auto_hide(self._app_settings.auto_hide_toolbar)
        self._edge_toolbar.set_hide_delay(self._app_settings.hide_delay_ms)
        # 更新设置页面复选框
        self._sync_app_settings_ui()

    def _init_app_settings_ui(self) -> None:
        """初始化设置页面中的应用设置复选框"""
        self._chk_show_toolbar = self.findChild(QCheckBox, "chkShowToolbar")
        self._chk_auto_hide = self.findChild(QCheckBox, "chkAutoHideToolbar")
        self._chk_tray = self.findChild(QCheckBox, "chkMinimizeToTray")
        self._chk_autostart = self.findChild(QCheckBox, "chkAutoStart")
        self._spin_hide_delay = self.findChild(QSpinBox, "spinHideDelay")

        if self._chk_show_toolbar:
            self._chk_show_toolbar.toggled.connect(self._on_show_toolbar_toggled)
        if self._chk_auto_hide:
            self._chk_auto_hide.toggled.connect(self._on_auto_hide_toggled)
        if self._chk_tray:
            self._chk_tray.toggled.connect(self._on_minimize_to_tray_toggled)
        if self._chk_autostart:
            self._chk_autostart.toggled.connect(self._on_autostart_toggled)
        if self._spin_hide_delay:
            self._spin_hide_delay.valueChanged.connect(self._on_hide_delay_changed)

        self._save_delay_timer = QTimer(self)
        self._save_delay_timer.setSingleShot(True)
        self._save_delay_timer.timeout.connect(self._do_save_hide_delay)

        self._save_pos_timer = QTimer(self)
        self._save_pos_timer.setSingleShot(True)
        self._save_pos_timer.timeout.connect(self._do_save_toolbar_pos)

        self._sync_app_settings_ui()

    def _sync_app_settings_ui(self) -> None:
        """将当前设置值同步到设置页面 UI"""
        if not self._app_settings:
            return

        show = self._app_settings.show_toolbar
        auto_hide = self._app_settings.auto_hide_toolbar

        if self._chk_show_toolbar:
            self._chk_show_toolbar.blockSignals(True)
            self._chk_show_toolbar.setChecked(show)
            self._chk_show_toolbar.blockSignals(False)
        if self._chk_auto_hide:
            self._chk_auto_hide.blockSignals(True)
            self._chk_auto_hide.setChecked(auto_hide)
            self._chk_auto_hide.setEnabled(show)
            self._chk_auto_hide.blockSignals(False)
        if self._spin_hide_delay:
            self._spin_hide_delay.blockSignals(True)
            self._spin_hide_delay.setValue(self._app_settings.hide_delay_ms)
            self._spin_hide_delay.setEnabled(show and auto_hide)
            self._spin_hide_delay.blockSignals(False)
        if self._chk_tray:
            self._chk_tray.blockSignals(True)
            self._chk_tray.setChecked(self._app_settings.minimize_to_tray)
            self._chk_tray.blockSignals(False)
        if self._chk_autostart:
            self._chk_autostart.blockSignals(True)
            self._chk_autostart.setChecked(self._app_settings.auto_start)
            self._chk_autostart.blockSignals(False)

    @Slot(bool)
    def _on_auto_hide_toggled(self, checked: bool) -> None:
        """自动隐藏复选框切换"""
        if self._app_settings:
            self._app_settings.auto_hide_toolbar = checked
            self._app_settings.save()
        self._edge_toolbar.set_auto_hide(checked)
        if self._spin_hide_delay:
            self._spin_hide_delay.setEnabled(checked)
        show_toast(self, "保存成功")
        logging.debug(f"自动隐藏工具栏: {'启用' if checked else '禁用'}")

    @Slot(int)
    def _on_hide_delay_changed(self, value: int) -> None:
        """隐藏延迟值改变（防抖保存）"""
        if self._app_settings:
            self._app_settings.hide_delay_ms = value
        self._edge_toolbar.set_hide_delay(value)
        self._save_delay_timer.start(300)

    def _do_save_hide_delay(self) -> None:
        """防抖延迟后实际保存设置"""
        if self._app_settings:
            self._app_settings.save()
            show_toast(self, "保存成功")
            logging.debug(f"工具栏隐藏延迟: {self._app_settings.hide_delay_ms}ms")

    @Slot(bool)
    def _on_show_toolbar_toggled(self, checked: bool) -> None:
        """显示工具栏复选框切换"""
        if self._app_settings:
            self._app_settings.show_toolbar = checked
            self._app_settings.save()
        if checked:
            pos = self._app_settings.toolbar_pos if self._app_settings else None
            if pos and "x" in pos and "y" in pos:
                self._edge_toolbar.move(pos["x"], pos["y"])
            else:
                self._edge_toolbar.set_initial_position()
            self._edge_toolbar.show()
        else:
            self._edge_toolbar.hide()
        if self._chk_auto_hide:
            self._chk_auto_hide.setEnabled(checked)
        if self._spin_hide_delay and self._app_settings:
            self._spin_hide_delay.setEnabled(
                checked and self._app_settings.auto_hide_toolbar
            )
        show_toast(self, "保存成功")
        logging.debug(f"显示边缘工具栏: {'启用' if checked else '禁用'}")

    @Slot(QPoint)
    def _on_toolbar_position_changed(self, pos: QPoint) -> None:
        """工具栏拖拽位置变更（防抖保存）"""
        if self._app_settings:
            self._app_settings.toolbar_pos = {"x": pos.x(), "y": pos.y()}
            self._save_pos_timer.start(500)

    def _do_save_toolbar_pos(self) -> None:
        """防抖延迟后实际保存工具栏位置"""
        if self._app_settings:
            self._app_settings.save()
            logging.debug(f"工具栏位置已保存: {self._app_settings.toolbar_pos}")

    @Slot(bool)
    def _on_minimize_to_tray_toggled(self, checked: bool) -> None:
        """最小化到托盘复选框切换"""
        if self._app_settings:
            self._app_settings.minimize_to_tray = checked
            self._app_settings.save()
        # 动态更新关闭窗口时是否退出程序
        from PySide6.QtWidgets import QApplication

        QApplication.setQuitOnLastWindowClosed(not checked)
        show_toast(self, "保存成功")
        logging.debug(f"最小化到系统托盘: {'启用' if checked else '禁用'}")

    @Slot(bool)
    def _on_autostart_toggled(self, checked: bool) -> None:
        """开机自启动复选框切换"""
        from vibeocr.classic.utils.autostart import set_autostart

        success = set_autostart(checked)
        if success and self._app_settings:
            self._app_settings.auto_start = checked
            self._app_settings.save()
            show_toast(self, "保存成功")
            logging.debug(f"开机自启动: {'启用' if checked else '禁用'}")
        elif not success:
            logging.warning("设置开机自启动失败")
            # 恢复复选框状态
            if self._chk_autostart:
                self._chk_autostart.blockSignals(True)
                self._chk_autostart.setChecked(not checked)
                self._chk_autostart.blockSignals(False)
            QMessageBox.warning(
                self, "设置失败", "设置开机自启动失败，请检查系统权限。"
            )

    def _show_main_window(self) -> None:
        """显示并激活主窗口（由工具栏触发）"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def bring_to_front(self) -> None:
        """将主窗口提到前台（由单实例守卫触发）。

        第二实例启动时检测到已运行实例，通过 SingleInstanceGuard 通知本实例
        调用此方法。复用 _show_main_window 的恢复逻辑（含最小化到托盘场景：
        showNormal 会取消最小化并 show 隐藏窗口）。
        """
        self._show_main_window()
