"""设置页面控制器

处理设置页面的逻辑，包括预加载和缓存管理。
"""

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.app_paths import get_bundled_resources_dir
from vibeocr.classic.machine_cache import is_cache_valid
from vibeocr.classic.pyside import settings_runtime
from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter
from vibeocr.classic.runtime_installation import RuntimeInstallerClient
from vibeocr.classic.runtime_selection import (
    DOWNLOAD_SOURCES_CAPABILITY,
    ENGINE_AVAILABILITY_LABELS,
    ENGINE_DISPLAY_NAMES,
    VALID_ENGINE_IDS,
    RuntimeSelectionCatalog,
    RuntimeSelectionError,
    parse_capability_catalogs,
)
from vibeocr.classic.views.background_tasks import FunctionTask
from vibeocr.classic.widgets.backend_choice_dialog import BackendChoiceDialog
from vibeocr.runtime_contracts import (
    PipelineSpec,
    ResidencyKind,
    ResidencyStatus,
    SettingsSnapshot,
)

try:
    from vibeocr.runtime_contracts import parse_runtime_status
except ImportError:  # Protocol 2.0/2.1 compatibility: HTTP status arrived in 2.2.
    parse_runtime_status = None

logger = logging.getLogger(__name__)

# QRunnable 运行期间的进程级强引用。窗口可先于慢 WMIC/PowerShell/RPC 完成销毁；
# 保留 wrapper 到结果回调，避免 Qt 线程池仍持有 C++ runnable 时 Python 对象被回收。
_BACKGROUND_TASKS: set[object] = set()


def _is_bundled() -> bool:
    """检测当前是否为 PyInstaller 打包态。"""
    return bool(getattr(sys, "frozen", False))


def _resolve_shortcut_icon_path() -> str:
    """解析快捷方式图标路径（.ico），兼容开发态与打包态。"""
    icon = get_bundled_resources_dir() / "app_icon.ico"
    return str(icon) if icon.exists() else ""


class SettingsPageController:
    """设置页面控制器

    处理设置页面的所有逻辑，与 UI 控件通过 findChild 方式交互。
    """

    def __init__(
        self,
        ui: QWidget,
        project_root: Path,
        status_callback: Callable[[str], None],
        ocr_ready_callback: Callable[[], bool],
        subprocess_manager,
        install_succeeded_callback: Callable[[], None] | None = None,
        gpu_capability_callback: Callable[[bool], None] | None = None,
        defer_backend_initialization: bool = False,
        defer_machine_cache_status: bool = False,
        runtime_status_callback: Callable[[str], None] | None = None,
        runtime_installer_client: RuntimeInstallerClient | None = None,
    ) -> None:
        self._ui = ui
        self._project_root = project_root
        self._status_callback = status_callback
        self._runtime_status_callback = runtime_status_callback
        self._ocr_ready_callback = ocr_ready_callback
        self._subprocess_manager = subprocess_manager
        self._runtime_installer = runtime_installer_client or RuntimeInstallerClient(
            project_root
        )
        # 设置页重装/补装依赖成功后的联动回调（由 MainWindow 提供）。
        # 回归（Bug A）：旧逻辑设置页 BackendChoiceDialog 只连 finished 刷新表格，
        # 没联动 MainWindow._ocr_ready / 子进程 Worker，导致装完仍提示"未就绪"。
        # 现由 MainWindow 传入一个触发 dependency_manager.check_dependencies
        # 的回调，使设置页安装成功后与首启路径行为一致（检测完成回调里自动
        # 设 _ocr_ready + 启动 Worker）。
        self._install_succeeded_callback = install_succeeded_callback
        self._gpu_capability_callback = gpu_capability_callback
        self._runtime_has_gpu: bool | None = None
        self._defer_backend_initialization = defer_backend_initialization
        self._defer_machine_cache_status = defer_machine_cache_status
        self._pending_maintenance_dialog: Callable[[], None] | None = None
        self._runtime_adapter = None
        self._runtime_settings_snapshot: SettingsSnapshot | None = None
        self._selection_catalog: RuntimeSelectionCatalog | None = None
        self._selection_accelerator: str | None = None
        self._engine_combo_initialized = False
        self._source_combo_rows: dict[str, QComboBox] = {}
        self._source_combo_row_widgets: list[QWidget] = []
        self._runtime_action = ""
        self._pending_ttl_sync = False
        self._backend_options = None
        self._closing = False
        self._cache_tasks: set[object] = set()
        self._cache_generation = 0
        self._env_refresh_generation = 0
        self._machine_cache_generation = 0
        self._cache_refresh_running = False
        self._shortcut_running = False
        self._preload_selected: tuple[str, ...] = ()
        self._preload_loaded_count = 0
        self._preload_poll_timer = QTimer(ui)
        self._preload_poll_timer.setInterval(750)
        self._preload_poll_timer.timeout.connect(self._poll_preload_residency)
        self._ttl_sync_timer = QTimer(ui)
        self._ttl_sync_timer.setSingleShot(True)
        self._ttl_sync_timer.setInterval(300)
        self._ttl_sync_timer.timeout.connect(self._sync_configured_pipeline_ttls)
        # 非模态重装对话框引用：show() 后须持有，否则被 GC 立即销毁；
        # 对话框 finished 时从列表移除，允许再次打开。
        self._active_dialogs: list = []

        # 控制器不是 QObject，独立测试/嵌入场景可能不会显式调用 shutdown；
        # 宿主 widget 销毁时先冻结后台回调，避免迟到结果访问已释放的 Qt 对象。
        ui.destroyed.connect(self.request_shutdown)

    def request_shutdown(self) -> None:
        """Release background workers owned by settings-page widgets.

        取消手动预加载任务（协作取消）并断开 signal，避免迟到回调访问
        已销毁的 UI。再关闭 GPU 检测线程。
        """
        self._closing = True
        self._cancel_pending_maintenance_dialog()
        self._preload_poll_timer.stop()
        self._preload_selected = ()
        self._preload_loaded_count = 0
        self._ttl_sync_timer.stop()
        for dialog in tuple(self._active_dialogs):
            request_shutdown = getattr(dialog, "request_shutdown", None)
            if callable(request_shutdown):
                request_shutdown()
            else:
                close = getattr(dialog, "close", None)
                if callable(close):
                    close()
        # 不清空正在运行的 QRunnable 引用：QThreadPool 结束前销毁其 Python
        # wrapper/Signals 可能导致 use-after-free。完成回调会先 discard，再由
        # _closing 守卫跳过所有 UI 操作。

        self._disconnect_runtime_adapter()

        backend_options = self._backend_options
        if backend_options is not None:
            backend_options.request_gpu_detection_shutdown()

    def drain(self, timeout_ms: int) -> bool:
        """Compatibility drain covering every settings-owned native/UI task."""
        import time

        from PySide6.QtCore import QCoreApplication, QThread

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            if self.is_drained():
                return True
            if timeout_ms <= 0 or time.monotonic() >= deadline:
                return False
            # 独立调用场景没有 MainWindow poll timer；推进 queued completion，
            # 让 cache/dialog/update 引用在 owner GUI 线程上安全释放。
            QCoreApplication.processEvents()
            QThread.msleep(5)

    def is_drained(self) -> bool:
        """Poll all settings-owned native jobs without waiting on the GUI thread."""
        backend_options = self._backend_options
        gpu_drained = backend_options is None or bool(
            backend_options.is_gpu_detection_drained()
        )
        # The completion callback removes each task on the GUI thread.  Waiting
        # for the set to become empty also drains queued callbacks that capture UI.
        cache_drained = not self._cache_tasks
        from vibeocr.classic.utils.dialog_workers import are_dialog_workers_drained

        dialogs_drained = are_dialog_workers_drained()
        return gpu_drained and cache_drained and dialogs_drained

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Compatibility entry point for callers outside MainWindow."""
        self.request_shutdown()
        return self.drain(timeout_ms)

    def connect_signals(self) -> None:
        """连接设置页面的信号槽"""
        nav_list = self._ui.findChild(QListWidget, "settingsNavList")
        stacked = self._ui.findChild(QStackedWidget, "settingsStackedWidget")
        if nav_list and stacked:
            nav_list.currentRowChanged.connect(stacked.setCurrentIndex)

        self._connect_runtime_adapter()

        self._init_log_level_control()

        chk_enable_preload = self._ui.findChild(QCheckBox, "chkEnablePreload")
        if chk_enable_preload:
            chk_enable_preload.toggled.connect(self._on_enable_preload_toggled)

        btn_preload_now = self._ui.findChild(QPushButton, "btnPreloadNow")
        if btn_preload_now:
            btn_preload_now.clicked.connect(self._on_preload_now_clicked)
        for pipeline in self._get_preloadable_pipelines():
            checkbox = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if checkbox is not None:
                checkbox.toggled.connect(self._save_preload_pipelines_config)

        btn_refresh_cache = self._ui.findChild(QPushButton, "btnRefreshCache")
        if btn_refresh_cache:
            btn_refresh_cache.clicked.connect(self._on_refresh_cache_clicked)

        btn_clear_cache = self._ui.findChild(QPushButton, "btnClearCache")
        if btn_clear_cache:
            btn_clear_cache.clicked.connect(self._on_clear_cache_clicked)

        # --- 管道缓存生命周期管理 ---
        # 旧的 spinPipelineTtl / chkEnablePipelineTtl 已被每管道 TTL ComboBox
        # 取代（_init_pipeline_ttl_combos 在 _init_settings_page 内构造并接线）。
        btn_refresh_pipeline_cache = self._ui.findChild(
            QPushButton, "btnRefreshPipelineCache"
        )
        if btn_refresh_pipeline_cache:
            btn_refresh_pipeline_cache.clicked.connect(
                self._on_refresh_pipeline_cache_clicked
            )

        btn_release_heavy = self._ui.findChild(QPushButton, "btnReleaseHeavy")
        if btn_release_heavy:
            btn_release_heavy.setEnabled(False)
            btn_release_heavy.setToolTip(
                "Supervisor v2 暂不支持按“重模型”筛选；请释放全部闲置模型。"
            )

        btn_release_all = self._ui.findChild(QPushButton, "btnReleaseAll")
        if btn_release_all:
            btn_release_all.clicked.connect(self._on_release_all_clicked)

        # --- 环境维护：重装 Python 运行时 / 重装 OCR 依赖 / 补充安装缺失依赖 ---
        btn_reinstall_python = self._ui.findChild(QPushButton, "btnReinstallPython")
        if btn_reinstall_python:
            btn_reinstall_python.setText("重建完整 Runtime")
            btn_reinstall_python.setToolTip(
                "重新安装受产品绑定的 Python、Backend、Protocol 与当前推理 profile。"
            )
            btn_reinstall_python.clicked.connect(self._on_reinstall_python)

        btn_reinstall_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        if btn_reinstall_deps:
            btn_reinstall_deps.setText("选择并确保 Runtime profile")
            btn_reinstall_deps.setToolTip(
                "选择 CPU/GPU profile；确认后通过可见安装流程校验或切换。"
            )
            btn_reinstall_deps.clicked.connect(self._on_reinstall_deps)

        btn_install_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        if btn_install_missing:
            btn_install_missing.setText("补全当前 Runtime")
            btn_install_missing.setToolTip(
                "校验当前 profile，仅在缺失或损坏时下载并补全。"
            )
            btn_install_missing.clicked.connect(self._on_install_missing)

        btn_update_deps = self._ui.findChild(QPushButton, "btnUpdateDeps")
        if btn_update_deps:
            btn_update_deps.setText("刷新产品绑定状态")
            btn_update_deps.setToolTip(
                "Runtime 版本随 VibeOCR 产品更新统一升级；此处只刷新绑定状态。"
            )
            btn_update_deps.clicked.connect(self._on_update_deps)

        self._refresh_env_maintenance_state()

        self._init_shortcut_buttons()

        self._init_screenshot_options(nav_list, stacked)
        self._init_pdf_options(nav_list, stacked)
        if not self._defer_backend_initialization:
            self._init_backend_options_in_group()
        self._init_settings_page()

        # 所有子页（静态 .ui 页 + 动态插入页）就绪后统一包滚动条，
        # 规范设置界面滚动行为：内容超出窗口高度时出垂直滚动条而非被裁剪。
        self._wrap_settings_pages_in_scroll()
        self._on_refresh_pipeline_cache_clicked()

    def _connect_runtime_adapter(self):
        """绑定当前 supervisor adapter 的 typed runtime signals。"""
        adapter = get_supervisor_adapter()
        if adapter is self._runtime_adapter:
            return adapter
        self._disconnect_runtime_adapter()
        adapter.residency_status.connect(self._on_residency_status)
        adapter.residency_error.connect(self._on_residency_error)
        adapter.settings_updated.connect(self._on_settings_updated)
        adapter.settings_error.connect(self._on_settings_error)
        adapter.settings_loaded.connect(self._on_settings_loaded)
        adapter.health_loaded.connect(self._on_health_loaded)
        adapter.health_error.connect(self._on_health_error)
        adapter.preload_completed.connect(self._on_preload_completed)
        adapter.preload_error.connect(self._on_preload_error)
        self._runtime_adapter = adapter
        return adapter

    def _disconnect_runtime_adapter(self) -> None:
        adapter = self._runtime_adapter
        if adapter is None:
            return
        for signal, slot in (
            (adapter.residency_status, self._on_residency_status),
            (adapter.residency_error, self._on_residency_error),
            (adapter.settings_updated, self._on_settings_updated),
            (adapter.settings_error, self._on_settings_error),
            (adapter.settings_loaded, self._on_settings_loaded),
            (adapter.health_loaded, self._on_health_loaded),
            (adapter.health_error, self._on_health_error),
            (adapter.preload_completed, self._on_preload_completed),
            (adapter.preload_error, self._on_preload_error),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._runtime_adapter = None

    # ----------------------------------------------------------------
    # Toast 提示
    # ----------------------------------------------------------------

    def _show_settings_toast(self, text: str = "保存成功") -> None:
        """在所属窗口顶部居中显示 Toast 通知。"""
        try:
            from vibeocr.classic.widgets.toast_widget import show_toast

            # 查找顶层窗口作为 toast 父控件，避免被 Tab 裁剪
            window = self._ui
            if hasattr(window, "window"):
                window = window.window()
            show_toast(window, text)
        except Exception:
            # 不允许影响主流程，但必须记录完整堆栈——
            # 旧实现用 logger.debug 无 exc_info，吞掉真正的异常类型与 traceback，
            # 导致 [Toast] 显示失败 日志无法定位根因（用户实测：更新依赖按钮无反应，
            # 唯一线索就是这行被吞的异常）。现用 logger.exception 落盘完整堆栈。
            logger.exception("[Toast] 显示失败（不影响主流程，但请上报此堆栈）")

    # ----------------------------------------------------------------
    # 快捷方式创建
    # ----------------------------------------------------------------

    def _init_shortcut_buttons(self) -> None:
        """在「应用设置」分组底部动态添加快捷方式按钮。

        按钮仅在 Windows 打包态可点击；开发态灰显并提示。
        """
        group = self._ui.findChild(QWidget, "groupAppSettings")
        if group is None:
            return

        layout = group.layout()
        if layout is None:
            return

        # 水平按钮行
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self._btn_desktop = QPushButton("发送快捷方式到桌面")
        self._btn_desktop.setToolTip("在桌面上创建 VibeOCR 快捷方式")
        self._btn_desktop.clicked.connect(self._on_create_desktop_shortcut)
        row_layout.addWidget(self._btn_desktop)

        self._btn_startmenu = QPushButton("发送快捷方式到开始菜单")
        self._btn_startmenu.setToolTip("在开始菜单中创建 VibeOCR 快捷方式")
        self._btn_startmenu.clicked.connect(self._on_create_start_menu_shortcut)
        row_layout.addWidget(self._btn_startmenu)

        row_layout.addStretch()

        # 非打包态禁用按钮并修改文案
        if not _is_bundled():
            self._btn_desktop.setEnabled(False)
            self._btn_desktop.setToolTip("仅在打包版本中可用")
            self._btn_startmenu.setEnabled(False)
            self._btn_startmenu.setToolTip("仅在打包版本中可用")

        layout.addWidget(row)

    def _on_create_desktop_shortcut(self) -> None:
        """在桌面创建 VibeOCR 快捷方式。"""
        if not _is_bundled():
            self._show_settings_toast("仅在打包版本中可用")
            return

        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        lnk = str(desktop / "VibeOCR.lnk")
        target = sys.executable
        icon = _resolve_shortcut_icon_path()
        wd = str(Path(sys.executable).parent)

        self._start_shortcut_creation(
            target, lnk, icon, wd, success_text="桌面快捷方式已创建"
        )

    def _on_create_start_menu_shortcut(self) -> None:
        """在开始菜单创建 VibeOCR 快捷方式。"""
        if not _is_bundled():
            self._show_settings_toast("仅在打包版本中可用")
            return

        start_menu = (
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "VibeOCR"
        )
        lnk = str(start_menu / "VibeOCR.lnk")
        target = sys.executable
        icon = _resolve_shortcut_icon_path()
        wd = str(Path(sys.executable).parent)

        self._start_shortcut_creation(
            target, lnk, icon, wd, success_text="开始菜单快捷方式已创建"
        )

    def _start_shortcut_creation(
        self,
        target: str,
        shortcut_path: str,
        icon: str,
        working_dir: str,
        *,
        success_text: str,
    ) -> None:
        """在线程池创建快捷方式，同一时刻只允许一个 PowerShell 操作。"""
        if self._closing or self._shortcut_running:
            return
        self._shortcut_running = True
        self._set_shortcut_buttons_enabled(False)

        def operation() -> bool:
            from vibeocr.classic.utils.shortcuts import create_windows_shortcut

            return create_windows_shortcut(
                target, shortcut_path, "VibeOCR", icon, working_dir
            )

        def finished(success: bool) -> None:
            self._shortcut_running = False
            self._set_shortcut_buttons_enabled(True)
            if success:
                self._show_settings_toast(success_text)
            else:
                QMessageBox.warning(
                    None,
                    "创建失败",
                    "创建快捷方式失败或操作超时，请检查权限。",
                )

        def failed(error: str) -> None:
            logger.warning("创建快捷方式后台任务失败: %s", error)
            finished(False)

        self._run_cache_operation(operation, finished, failed)

    def _set_shortcut_buttons_enabled(self, enabled: bool) -> None:
        if not _is_bundled():
            enabled = False
        for name in ("_btn_desktop", "_btn_startmenu"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)

    # ----------------------------------------------------------------
    # 截图 / PDF 选项页初始化
    # ----------------------------------------------------------------

    def _init_screenshot_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化截图面板选项页面。

        按管道分组展示预处理参数（无管道下拉框）：识别类型由截图工具栏按钮
        唯一决定，此处仅配置各管道的预处理参数。
        """
        if not nav_list or not stacked:
            return

        from vibeocr.classic.widgets.screenshot_options_widget import (
            ScreenshotOptionsWidget,
        )

        # 添加导航项和页面
        nav_list.addItem("截图选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._screenshot_options = ScreenshotOptionsWidget()
        page_layout.addWidget(self._screenshot_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)

        stacked.addWidget(page)

        # 截图选项变更时弹出保存成功提示
        self._screenshot_options.options_changed.connect(
            lambda _: self._show_settings_toast()
        )

        # ScreenshotOptionsWidget 自管持久化（构造时 load、变更时直接写
        # screenshot 源），此处无需连接信号。

    def _init_pdf_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化 PDF 选项页面。"""
        if not nav_list or not stacked:
            return

        from vibeocr.classic.utils.ocr_preferences import OCRPreferences
        from vibeocr.classic.widgets.pdf_options_widget import PdfOptionsWidget

        nav_list.addItem("PDF 选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._pdf_options = PdfOptionsWidget()
        page_layout.addWidget(self._pdf_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)
        stacked.addWidget(page)

        # 恢复保存的设置
        try:
            prefs = OCRPreferences.instance()
            # 管道选项
            default_pipeline = self._pdf_options.pipeline_options.get_current_pipeline()
            self._pdf_options.pipeline_options.set_options(
                prefs.get_pipeline_options("pdf", default_pipeline)
            )
            # 全局设置
            self._pdf_options.set_settings(prefs.get_pdf_settings())
        except RuntimeError:
            pass

        # 连接管道选项信号
        self._pdf_switching = False
        self._pdf_options.pipeline_options.pipeline_switching.connect(
            self._on_pdf_pipeline_switching
        )
        self._pdf_options.pipeline_options.pipeline_switched.connect(
            self._on_pdf_pipeline_switched
        )
        self._pdf_options.pipeline_options.options_changed.connect(
            self._on_pdf_option_changed
        )

        # 连接全局设置信号
        self._pdf_options.settings_changed.connect(self._on_pdf_settings_changed)

    def _init_backend_options_in_group(self) -> None:
        """把推理后端组件放入「应用设置」页的「推理后端与依赖」分组内。

        推理后端（GPU/CPU 选择）与 OCR 依赖安装本质上是同一件事——后端决定
        要装哪些依赖，依赖表格/重装按钮负责查看与维护这些依赖。故合并到同一
        分组，不再单列导航项。
        """
        container = self._ui.findChild(QWidget, "backendOptionsContainer")
        if container is None or self._backend_options is not None:
            return

        from vibeocr.classic.widgets.backend_options_widget import BackendOptionsWidget

        self._backend_options = BackendOptionsWidget(
            self._project_root,
            gpu_capability_callback=self._on_gpu_capability_resolved,
        )
        layout = container.layout()
        if layout is not None:
            layout.addWidget(self._backend_options)

        self._backend_options.backend_change_requested.connect(
            self._on_backend_change_requested
        )

    def initialize_deferred_backend_options(self) -> None:
        """为显式延迟初始化的独立宿主构造 Runtime profile 组件。"""
        if not self._closing:
            self._init_backend_options_in_group()

    def apply_deferred_machine_cache_status(self, valid: bool) -> None:
        """应用 MainWindow 后台缓存校验结果，不再次触发机器码探测。"""
        if not self._closing:
            self._update_cache_status("缓存有效" if valid else "无有效缓存")

    def _on_gpu_capability_resolved(self, has_gpu: bool) -> None:
        """记录既有后台探测结果，并向 MainWindow 广播。"""
        if self._closing:
            return
        self._runtime_has_gpu = bool(has_gpu)
        if self._gpu_capability_callback is not None:
            self._gpu_capability_callback(bool(has_gpu))

    def _on_backend_change_requested(self, target: str) -> None:
        """二次确认后通过可见安装对话框切换完整 Runtime profile。"""
        backend_options = self._backend_options
        if target not in {"cpu", "gpu"}:
            if backend_options is not None:
                backend_options.set_change_in_progress(False)
            return
        name = "GPU（NVIDIA CUDA）" if target == "gpu" else "CPU"
        size = "通常需要数 GB" if target == "gpu" else "通常超过 1 GB"
        reply = QMessageBox.question(
            None,
            "确认切换推理后端",
            f"将停止当前 OCR Supervisor，并联网下载、安装完整的 {name} "
            f"Runtime profile（{size}，实际流量取决于已有缓存）。\n\n"
            "安装期间会显示进度并可取消。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            if backend_options is not None:
                backend_options.set_change_in_progress(False)
            return
        self._open_install_dialog(force_backend=target)

    def _on_pdf_pipeline_switching(self, old_pipeline, options) -> None:
        self._pdf_switching = True
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_pipeline_options(options)
        except RuntimeError:
            pass

    def _on_pdf_pipeline_switched(self, new_pipeline) -> None:
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            loaded = OCRPreferences.instance().get_pipeline_options("pdf", new_pipeline)
            self._pdf_options.pipeline_options.set_options(loaded)
        except RuntimeError:
            pass
        self._pdf_switching = False

    def _on_pdf_option_changed(self, options) -> None:
        if self._pdf_switching:
            return
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_pipeline_options(options)
            self._show_settings_toast()
        except RuntimeError:
            pass

    def _on_pdf_settings_changed(self, settings) -> None:
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_settings(settings)
            self._show_settings_toast()
        except RuntimeError:
            pass

    def _init_settings_page(self) -> None:
        """初始化设置页面状态"""
        if self._defer_machine_cache_status:
            self._update_cache_status("正在检查缓存...")
        else:
            # 独立嵌入场景没有 MainWindow 的共享启动快照；避免仅为初始文案
            # 主动触发 WMIC，用户点击"刷新缓存"时再走后台 operation。
            self._update_cache_status("缓存状态尚未刷新")
        self._update_preload_status()
        self._restore_preload_checkbox_state()
        # 引入拆分后的 labelPipelineCacheStatus（运行时层），再构造每管道 TTL
        # ComboBox。前者由 typed ResidencyStatus signal 写入，后者由用户操作触发。
        self._init_pipeline_cache_status_label()
        self._init_pipeline_ttl_combos()
        self._init_ocr_runtime_group()

    def _init_log_level_control(self) -> None:
        """在应用设置页加入持久化日志级别选择。"""
        if self._ui.findChild(QComboBox, "comboLogLevel") is not None:
            return
        layout = self._ui.findChild(QVBoxLayout, "appSettingsLayout")
        if layout is None:
            return
        row = QWidget(self._ui)
        row.setObjectName("logLevelRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("日志级别：", row)
        combo = QComboBox(row)
        combo.setObjectName("comboLogLevel")
        combo.addItem("普通（推荐）", "INFO")
        combo.addItem("调试（详细）", "DEBUG")
        combo.addItem("仅警告与错误", "WARNING")
        combo.setToolTip("普通模式会过滤 HTTP、模型框架等底层调试输出")
        row_layout.addWidget(label)
        row_layout.addWidget(combo)
        row_layout.addStretch(1)
        layout.addWidget(row)

        saved = settings_runtime.get_log_level()
        index = combo.findData(saved)
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(self._on_log_level_changed)

    def _on_log_level_changed(self) -> None:
        combo = self._ui.findChild(QComboBox, "comboLogLevel")
        if combo is None:
            return
        level = str(combo.currentData() or "INFO")
        if settings_runtime.set_log_level(level):
            self._show_settings_toast("日志级别已更新；WorkerHost 将在下次重连时应用")

    # ----------------------------------------------------------------
    # 每管道 TTL ComboBox（替代旧 spinPipelineTtl + chkEnablePipelineTtl）
    # ----------------------------------------------------------------

    #: TTL 预设档：显示文本 → 秒数。0 在 v2 schema 中转换为继承默认 TTL。
    _TTL_PRESETS: list[tuple[str, int]] = [
        ("继承默认 TTL", 0),
        ("持久驻留", -1),
        ("1 分钟", 60),
        ("3 分钟", 180),
        ("5 分钟", 300),
        ("10 分钟", 600),
        ("15 分钟", 900),
        ("30 分钟", 1800),
    ]

    def _init_pipeline_ttl_combos(self) -> None:
        """在「模型管理 → 运行时缓存」分组内追加每管道 TTL ComboBox。

        原型由 spinPipelineTtl + chkEnablePipelineTtl（单 TTL 适用于所有管道）改为
        6 个独立 ComboBox，分别对应 OCRPipeline 枚举的每一项。每个 ComboBox 携带
        相同的 7 档预设（_TTL_PRESETS），选中项经 ConfigManager.set_pipeline_ttl
        持久化，并通过 _sync_configured_pipeline_ttls 批量下发到 worker。

        幂等：重复调用时若已存在 comboTtl_OCR 则直接返回。
        """
        layout = self._ui.findChild(QVBoxLayout, "runtimeCacheLayout")
        if layout is None:
            logger.warning("[TTL Combos] runtimeCacheLayout 未找到，跳过 ComboBox 创建")
            return
        if self._ui.findChild(QComboBox, "comboTtl_OCR") is not None:
            logger.debug("[TTL Combos] 已存在 comboTtl_OCR，跳过（幂等）")
            return

        from vibeocr.classic.managers.config_manager import ConfigManager
        from vibeocr.runtime_contracts.contracts.pipelines import (
            OCRPipeline,
            get_pipeline_display_name,
        )

        ttls = ConfigManager.instance().get_pipeline_ttls()
        created_count = 0
        for pipeline in OCRPipeline:
            row = QWidget(self._ui)
            row.setObjectName(f"ttlRow_{pipeline.value}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            label = QLabel(get_pipeline_display_name(pipeline), row)
            combo = QComboBox(row)
            combo.setObjectName(f"comboTtl_{pipeline.value}")
            for display_text, secs in self._TTL_PRESETS:
                # 第二参数写入 UserRole data，_restore_pipeline_ttl_combos 用
                # findData 反查索引（比匹配显示文本更稳）。
                combo.addItem(display_text, secs)
            self._select_ttl_combo(combo, ttls.get(pipeline.value, 0))
            inherit_ttl_tip = "可继承默认 TTL、选择有限 TTL，或设置为持久驻留。"
            combo.setToolTip(inherit_ttl_tip)
            # MinerU 使用独立 API 进程；有限 TTL 到期会真实停止该进程。
            if pipeline == OCRPipeline.DOCUMENT_PARSING:
                mineru_tip = (
                    f"{inherit_ttl_tip}"
                    "设置有限 TTL 后，MinerU 闲置到期会停止 API 进程；"
                    "下次使用时会重新启动。"
                )
                label.setToolTip(mineru_tip)
                combo.setToolTip(mineru_tip)
            # 默认绑定 pipeline.value；lambda 显式捕获避免闭包晚绑定陷阱。
            combo.currentIndexChanged.connect(
                lambda _idx, name=pipeline.value, c=combo: (
                    self._on_pipeline_ttl_combo_changed(name, c)
                )
            )
            row_layout.addWidget(label)
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            layout.addWidget(row)
            created_count += 1
        logger.info(
            "[TTL Combos] 已创建 %d 个 ComboBox (layout count=%d)",
            created_count,
            layout.count(),
        )

    def _select_ttl_combo(self, combo: QComboBox, ttl: int) -> None:
        """根据 TTL 秒数选中 ComboBox 项（UserRole data 精确匹配，无匹配回退继承）。"""
        index = combo.findData(ttl)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _restore_pipeline_ttl_combos(self) -> None:
        """从配置恢复所有 TTL ComboBox 选中项（阻塞信号避免触发下发）。"""
        from vibeocr.classic.managers.config_manager import ConfigManager
        from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

        ttls = ConfigManager.instance().get_pipeline_ttls()
        for pipeline in OCRPipeline:
            combo = self._ui.findChild(QComboBox, f"comboTtl_{pipeline.value}")
            if combo is None:
                continue
            current_ttl = ttls.get(pipeline.value, 0)
            combo.blockSignals(True)
            self._select_ttl_combo(combo, current_ttl)
            combo.blockSignals(False)

    def _on_pipeline_ttl_combo_changed(
        self, pipeline_name: str, combo: QComboBox
    ) -> None:
        """单个管道 TTL 改变 → 写配置 + 防抖下发 worker + toast。

        UI 线程边界：本函数仅调用 ConfigManager（本地 JSON 读写，非阻塞）与
        _ttl_sync_timer.start（异步触发 _sync_configured_pipeline_ttls，后者再走
        _run_cache_operation 线程池）。**禁止**直接调用 env_manager.* 或同步 RPC。
        """
        idx = combo.currentIndex()
        if idx < 0 or idx >= len(self._TTL_PRESETS):
            return
        _display, ttl = self._TTL_PRESETS[idx]
        from vibeocr.classic.managers.config_manager import ConfigManager

        if not ConfigManager.instance().set_pipeline_ttl(pipeline_name, ttl):
            logger.warning("[TTL] 写入配置失败: %s=%d", pipeline_name, ttl)
            return
        self._show_settings_toast()
        # 防抖：连续切换档位时只下发最后一次到 worker（_ttl_sync_timer 在
        # __init__ 已 connect 到 _sync_configured_pipeline_ttls）。
        self._ttl_sync_timer.start()

    def _wrap_settings_pages_in_scroll(self) -> None:
        """把 settingsStackedWidget 的每个子页包进 QScrollArea。

        修复：原页面直接塞进 QStackedWidget 无滚动区，窗口高度不足时内容被裁剪
        （用户反馈"部分内容显示区域很矮/看不见"）。包一层 setWidgetResizable
        的 QScrollArea 后，垂直超出自动出滚动条，水平不滚动（宽度跟随窗口）。

        所有子页（pageModelManagement / pageAppSettings / 截图选项 / PDF 选项）
        统一处理。原页 widget 从 stacked 移除、用 scroll 替换占位，
        索引与导航行（currentRowChanged→setCurrentIndex）保持一一对应。
        """
        from PySide6.QtCore import Qt

        stacked = self._ui.findChild(QStackedWidget, "settingsStackedWidget")
        if stacked is None:
            return

        # 先 snapshot 所有原页（按索引顺序），再统一清空 + 按顺序回填 scroll。
        # 循环中边遍历边 insert/remove 会导致索引错位、widget(i) 返回 None
        # （AttributeError 根因）。snapshot 后先全部脱离 stacked，再顺序加回，
        # 保证索引与导航行（currentRowChanged→setCurrentIndex）一一对应。
        pages: list[QWidget] = []
        for i in range(stacked.count()):
            page = stacked.widget(i)
            if page is not None:
                pages.append(page)

        # 全部从 stacked 移除（setParent(None) 同时解除父子关系）
        for page in pages:
            stacked.removeWidget(page)

        # 按原顺序加回（每个包一层 scroll，已包裹的幂等跳过）
        for page in pages:
            if isinstance(page, QScrollArea):
                stacked.addWidget(page)
                continue
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)  # 内容宽度跟随 scroll，不出水平滚动条
            scroll.setFrameShape(QFrame.Shape.NoFrame)  # 无边框，视觉与原页一致
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page.setParent(None)  # 解除与原 stacked 的父子关系
            scroll.setWidget(page)
            page.setAutoFillBackground(False)
            stacked.addWidget(scroll)

    @staticmethod
    def _get_preloadable_pipelines():
        """获取可预加载的管道列表"""
        from vibeocr.runtime_contracts.contracts.pipelines import (
            get_preloadable_pipelines,
        )

        return get_preloadable_pipelines()

    def _restore_preload_checkbox_state(self) -> None:
        """恢复持久化选择并根据总开关启用 Supervisor 预加载控件。"""
        from vibeocr.classic.managers.config_manager import ConfigManager

        config = ConfigManager.instance()
        selected = set(config.get_preload_pipelines())
        for pipeline in self._get_preloadable_pipelines():
            checkbox = self._ui.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(pipeline.value in selected)
            checkbox.blockSignals(False)
        chk_enable = self._ui.findChild(QCheckBox, "chkEnablePreload")
        enabled = config.get_preload_enabled()
        if chk_enable is not None:
            chk_enable.blockSignals(True)
            chk_enable.setChecked(enabled)
            chk_enable.blockSignals(False)
        self._set_preload_controls_enabled(enabled)

    def _on_enable_preload_toggled(self, checked: bool) -> None:
        """启用或禁用手动 Supervisor 预加载选择。"""
        from vibeocr.classic.managers.config_manager import ConfigManager

        ConfigManager.instance().set_preload_enabled(checked)
        self._set_preload_controls_enabled(checked)

    def _set_preload_controls_enabled(self, enabled: bool) -> None:
        options = self._ui.findChild(QWidget, "preloadOptions")
        if options is not None:
            options.setEnabled(enabled)
        button = self._ui.findChild(QPushButton, "btnPreloadNow")
        if button is not None:
            button.setEnabled(enabled)
        self._update_preload_status(
            "请选择管道并通过 Supervisor 预加载" if enabled else "模型预加载已禁用"
        )

    def on_supervisor_ready(self) -> None:
        """Supervisor ready 后按持久配置异步预加载选中模型。"""
        from vibeocr.classic.managers.config_manager import ConfigManager

        if ConfigManager.instance().get_preload_enabled():
            self._on_preload_now_clicked()
        else:
            self._on_refresh_pipeline_cache_clicked()

    def _on_preload_now_clicked(self) -> None:
        """把选中的管道交给 Supervisor 顺序预加载。"""
        adapter = self._connect_runtime_adapter()
        if not adapter.is_started:
            self._update_preload_status("预加载失败：Supervisor 未连接")
            self._publish_runtime_status("Supervisor 未连接 · 无法预加载模型")
            return
        selected = tuple(
            pipeline.value
            for pipeline in self._get_preloadable_pipelines()
            if (
                (
                    checkbox := self._ui.findChild(
                        QCheckBox, f"chkPreload_{pipeline.name}"
                    )
                )
                is not None
                and checkbox.isChecked()
            )
        )
        if not selected:
            self._update_preload_status("请至少选择一个预加载管道")
            return
        button = self._ui.findChild(QPushButton, "btnPreloadNow")
        if button is not None:
            button.setEnabled(False)
        self._update_preload_status(
            f"正在通过 Supervisor 预加载 {len(selected)} 个管道..."
        )
        self._preload_selected = selected
        self._preload_loaded_count = 0
        self._publish_runtime_status(
            f"预加载中 · 0/{len(selected)} 驻留 · {'、'.join(selected)}"
        )
        self._poll_preload_residency()
        self._preload_poll_timer.start()
        adapter.preload(selected)

    def _poll_preload_residency(self) -> None:
        """预加载期间读取 Supervisor 的部分驻留快照。"""
        if self._closing or not self._preload_selected:
            self._preload_poll_timer.stop()
            return
        adapter = self._connect_runtime_adapter()
        if adapter.is_started:
            adapter.refresh_residency()

    def _on_preload_completed(self, status: object) -> None:
        if self._closing:
            return
        selected = self._preload_selected
        self._preload_poll_timer.stop()
        self._preload_selected = ()
        button = self._ui.findChild(QPushButton, "btnPreloadNow")
        if button is not None:
            enabled = self._ui.findChild(QCheckBox, "chkEnablePreload")
            button.setEnabled(bool(enabled is not None and enabled.isChecked()))
        if isinstance(status, ResidencyStatus):
            loaded_names = {
                entry.pipeline
                for entry in status.entries
                if entry.kind is not ResidencyKind.EVICTED
            }
            selected_loaded = [
                pipeline for pipeline in selected if pipeline in loaded_names
            ]
            loaded_detail = "、".join(selected_loaded) if selected_loaded else "无"
            self._update_preload_status(
                f"预加载完成；选中管道当前驻留 "
                f"{len(selected_loaded)}/{len(selected)}：{loaded_detail}"
            )
            self._publish_runtime_status(
                f"已驻留 {len(selected_loaded)}/{len(selected)} · {loaded_detail}"
            )
        else:
            self._update_preload_status("预加载完成")
            self._publish_runtime_status("预加载完成 · 正在刷新驻留快照")
        self._preload_loaded_count = 0
        if isinstance(status, ResidencyStatus):
            self._on_residency_status(status)

    def _on_preload_error(self, error: str) -> None:
        if self._closing:
            return
        selected_count = len(self._preload_selected)
        loaded_count = self._preload_loaded_count
        self._preload_poll_timer.stop()
        self._preload_selected = ()
        self._preload_loaded_count = 0
        button = self._ui.findChild(QPushButton, "btnPreloadNow")
        if button is not None:
            enabled = self._ui.findChild(QCheckBox, "chkEnablePreload")
            button.setEnabled(bool(enabled is not None and enabled.isChecked()))
        self._update_preload_status(f"预加载失败：{error}")
        if selected_count:
            self._publish_runtime_status(
                f"驻留未完成 · {loaded_count}/{selected_count} · 其余按需加载"
            )

    def _save_preload_pipelines_config(self) -> None:
        """持久化启动预加载管道；实际加载仍只经 Supervisor。"""
        from vibeocr.classic.managers.config_manager import ConfigManager

        selected = [
            pipeline.value
            for pipeline in self._get_preloadable_pipelines()
            if (
                (
                    checkbox := self._ui.findChild(
                        QCheckBox, f"chkPreload_{pipeline.name}"
                    )
                )
                is not None
                and checkbox.isChecked()
            )
        ]
        ConfigManager.instance().set_preload_pipelines(selected)

    def _update_preload_status(self, status: str | None = None) -> None:
        """更新预加载状态"""
        label = self._ui.findChild(QLabel, "labelPreloadStatus")
        if label:
            label.setText(status or "可通过 Supervisor 预加载选定模型")

    def _publish_runtime_status(self, status: str) -> None:
        """把模型运行时摘要发布到全局状态栏；详细错误仍保留在设置页。"""
        callback = self._runtime_status_callback
        if callback is not None:
            callback(status)

    def _disable_legacy_preload_controls(self) -> None:
        """禁用无法经 v2 job 表达的旧预加载 interface。"""
        message = "Supervisor v2 当前按需加载模型，暂不支持手动预加载。"
        for name in ("chkEnablePreload", "btnPreloadNow", "preloadOptions"):
            widget = self._ui.findChild(QWidget, name)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip(message)
        progress = self._ui.findChild(QProgressBar, "progressPreload")
        if progress is not None:
            progress.setVisible(False)
        self._update_preload_status("模型由 Supervisor 按需加载")

    # ============================================================
    # 缓存管理
    # ============================================================

    def _on_refresh_cache_clicked(self) -> None:
        """在线程池重新检查产品绑定的 Runtime。"""
        if self._closing or self._cache_refresh_running:
            return
        self._cache_refresh_running = True
        button = self._ui.findChild(QPushButton, "btnRefreshCache")
        if button:
            button.setEnabled(False)
        self._update_cache_status("正在验证 Runtime manifest 与组件状态...")
        self._machine_cache_generation += 1
        generation = self._machine_cache_generation

        def finished(result: tuple[bool, str]) -> None:
            self._cache_refresh_running = False
            if button:
                button.setEnabled(True)
            if generation != self._machine_cache_generation:
                return
            success, info = result
            if success:
                self._apply_cache_status(
                    generation,
                    True,
                    info,
                    f"Runtime 状态已刷新：{info}",
                )
                self._show_settings_toast("Runtime manifest 与组件状态已重新验证")
                logger.debug("[Runtime] 已重新验证产品绑定状态")
            else:
                self._apply_cache_status(generation, False, "", "Runtime 验证失败")

        def failed(error: str) -> None:
            self._cache_refresh_running = False
            if button:
                button.setEnabled(True)
            if generation == self._machine_cache_generation:
                self._update_cache_status(f"Runtime 验证失败：{error}")

        self._run_cache_operation(
            self._refresh_machine_cache_operation, finished, failed
        )

    def _refresh_machine_cache_operation(self) -> tuple[bool, str]:
        """通过稳定 Installer interface 验证完整 Runtime 绑定。"""
        inspection = self._runtime_installer.inspect()
        accelerator = (
            "NVIDIA CUDA" if inspection.accelerator == "nvidia_cuda" else "CPU"
        )
        readiness = "已就绪" if inspection.ready else f"未就绪({inspection.status})"
        summary = (
            f"{readiness} · Backend {inspection.backend_version} · "
            f"Protocol {inspection.protocol_version} · {accelerator} · "
            f"integrity={inspection.integrity}"
        )
        return True, summary

    def _run_after_supervisor_invalidated(
        self, continuation: Callable[[], None]
    ) -> None:
        """维护操作只在旧 Supervisor 确认退出后继续。"""

        if self._closing:
            return
        if self._pending_maintenance_dialog is not None:
            self._status_callback("正在停止 OCR Supervisor，请稍候...")
            return

        self._pending_maintenance_dialog = continuation
        manager = self._subprocess_manager
        manager.invalidation_finished.connect(
            self._on_supervisor_invalidated_for_maintenance
        )
        started = manager.invalidate_supervisor()
        if not started:
            self._cancel_pending_maintenance_dialog()
            backend_options = getattr(self, "_backend_options", None)
            if backend_options is not None:
                backend_options.set_change_in_progress(False)
            if manager.is_invalidating:
                self._status_callback("已有 Supervisor 维护准备正在进行，请稍候...")
                return
            QMessageBox.warning(
                None,
                "无法开始维护",
                "OCR Supervisor 无法安全停止，安装或更新未开始。",
            )
            return
        self._status_callback("正在停止 OCR Supervisor...")

    def _on_supervisor_invalidated_for_maintenance(
        self, success: bool, error: str
    ) -> None:
        continuation = self._pending_maintenance_dialog
        self._cancel_pending_maintenance_dialog()
        if self._closing or continuation is None:
            return
        if not success:
            backend_options = getattr(self, "_backend_options", None)
            if backend_options is not None:
                backend_options.set_change_in_progress(False)
            QMessageBox.warning(
                None,
                "无法开始维护",
                f"OCR Supervisor 未能安全停止，安装或更新未开始。\n{error}",
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

    def _open_reinstall_dialog(
        self, reinstall_python: bool = False, missing_only: bool = False
    ) -> None:
        self._run_after_supervisor_invalidated(
            lambda: self._show_reinstall_dialog(
                reinstall_python=reinstall_python,
                missing_only=missing_only,
            )
        )

    def _show_reinstall_dialog(
        self, reinstall_python: bool = False, missing_only: bool = False
    ) -> None:
        """以非模态方式打开重装/补装对话框（不阻塞主窗口）。

        show() 后必须持有 dialog 引用以防 GC；finished 时刷新环境状态并移除引用。
        install_succeeded 联动 MainWindow 重新检测依赖（Bug A 修复）：装完依赖后
        由 MainWindow 触发 dependency_manager.check_dependencies，使截图界面立即可用，
        无需重启程序。
        """
        dialog = BackendChoiceDialog(
            self._project_root,
            reinstall_python=reinstall_python,
            missing_only=missing_only,
        )

        def _on_finished(_result: int) -> None:
            # 成功路径由 install_succeeded 刷新一次；取消/失败才在这里刷新。
            if _result != 1:
                self._refresh_env_maintenance_state()
            # 移除引用，允许对话框被回收（用户也可再次打开新的）
            try:
                self._active_dialogs.remove(dialog)
            except ValueError:
                pass

        def _on_install_succeeded() -> None:
            # 装完依赖联动 MainWindow：刷新设置页状态 + 触发重新检测依赖
            # （检测完成回调里自动设 _ocr_ready 并启动子进程 Worker）。
            # 不直接设 _ocr_ready=True：让真实检测反映"装了但间接依赖没装完"等
            # 异常状态，避免假就绪。
            self._refresh_env_maintenance_state()
            if self._install_succeeded_callback is not None:
                self._install_succeeded_callback()

        dialog.finished.connect(_on_finished)
        dialog.install_succeeded.connect(_on_install_succeeded)
        self._active_dialogs.append(dialog)
        dialog.show()

    def _on_reinstall_python(self) -> None:
        """修复完整 Runtime profile，底层统一调用 Runtime Installer repair。"""
        reply = QMessageBox.question(
            None,
            "确认修复 Runtime",
            "将校验绑定版本的完整 Runtime profile，并重建损坏或缺失的内容。\n\n"
            "不会执行逐包 pip 变更，也不会修改用户配置、模型缓存和日志。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(reinstall_python=True)

    def _on_reinstall_deps(self) -> None:
        """让用户选择并校验或切换完整 Runtime profile。"""
        reply = QMessageBox.question(
            None,
            "选择 Runtime profile",
            "下一步可选择 CPU 或 GPU profile。只有点击“开始安装”后才会联网；"
            "安装期间会显示进度并可取消。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(reinstall_python=False)

    def _on_install_missing(self) -> None:
        """使用 Installer 的当前 accelerator 补全缺失 Runtime 内容。"""
        reply = QMessageBox.question(
            None,
            "确认补充安装缺失依赖",
            "将校验当前 Runtime profile；仅在缺失或损坏时联网补全。\n\n"
            "不会更改当前 CPU/GPU 选择。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 读当前后端作为补装后端，避免二次提示
        current_backend = self._runtime_backend_or_none()
        if current_backend is None:
            self._show_settings_toast("正在检测推理后端，请稍后再试")
            return
        self._open_install_dialog(missing_only=True, force_backend=current_backend)

    def _on_update_deps(self) -> None:
        """组件只能随产品 component-lock 整组升级；此处只刷新完整性。"""
        self._show_settings_toast("Runtime 版本由产品更新统一管理")
        self._refresh_env_maintenance_state()

    def _runtime_backend_or_none(self) -> str | None:
        """只消费后台 Installer inspect 已回填的后端，不在 GUI 线程探测。"""
        backend_options = self._backend_options
        if backend_options is not None and hasattr(backend_options, "current_backend"):
            # inspect 完成前返回 None，调用方必须等待，不能按物理 GPU 猜测。
            current = backend_options.current_backend()
            return str(current) if current in {"cpu", "gpu"} else None
        if self._runtime_has_gpu is None:
            return None
        return "gpu" if self._runtime_has_gpu else "cpu"

    def _open_install_dialog(
        self,
        missing_only: bool = False,
        force_backend: str | None = None,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
    ) -> None:
        self._run_after_supervisor_invalidated(
            lambda: self._show_install_dialog(
                missing_only=missing_only,
                force_backend=force_backend,
                single_pkg=single_pkg,
                packages=packages,
            )
        )

    def _show_install_dialog(
        self,
        missing_only: bool = False,
        force_backend: str | None = None,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
        install_component_ids: tuple[str, ...] | None = None,
        download_source_ids: tuple[str, ...] | None = None,
    ) -> None:
        """显示非模态 Runtime 安装进度；操作只通过 Installer ensure/repair。"""
        from vibeocr.classic.widgets.install_dialog import InstallDialog

        dialog = InstallDialog(
            self._project_root,
            missing_only=missing_only,
            force_backend=force_backend,
            single_pkg=single_pkg,
            packages=packages,
            maintenance_callback=self._status_callback,
            install_component_ids=install_component_ids,
            download_source_ids=download_source_ids,
        )

        def _on_finished(_result: int) -> None:
            self.refresh_runtime_state()
            try:
                self._active_dialogs.remove(dialog)
            except ValueError:
                pass

        def _on_install_succeeded() -> None:
            self._refresh_env_maintenance_state()
            if self._install_succeeded_callback is not None:
                self._install_succeeded_callback()

        dialog.finished.connect(_on_finished)
        if hasattr(dialog, "install_succeeded"):
            dialog.install_succeeded.connect(_on_install_succeeded)
        self._active_dialogs.append(dialog)
        dialog.show()
        logger.info(
            "[依赖更新] 安装对话框已 show()（missing_only=%s, backend=%s, "
            "single_pkg=%s, packages=%s）",
            missing_only,
            force_backend,
            single_pkg,
            packages,
        )

    def refresh_runtime_state(self) -> None:
        """刷新依赖区与推理后端控件的 Runtime 权威状态。"""
        self._refresh_env_maintenance_state()
        backend_options = self._backend_options
        if backend_options is not None:
            backend_options.refresh_runtime_state()
        adapter = self._connect_runtime_adapter()
        if adapter.is_started and not self._closing:
            adapter.fetch_health()
            adapter.fetch_settings()

    def _on_reinstall_single_dep(self, pkg: str) -> None:
        """单包重装入口（依赖表格"重装"按钮）。

        不二次确认——单包重装只装一个包，影响范围小，直接弹进度对话框。
        """
        self._open_install_dialog(single_pkg=pkg)

    def _on_reinstall_selected(self) -> None:
        """兼容旧按钮：修复整个不可变 Runtime profile。"""
        reply = QMessageBox.question(
            None,
            "确认修复 Runtime",
            "将校验并修复当前完整 Runtime profile；不会逐包修改。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._open_install_dialog(packages=["runtime-profile"])

    def _refresh_env_maintenance_state(self) -> None:
        """异步合并 Installer 完整性与 Supervisor HTTP 状态。"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        tree = self._ui.findChild(QTreeWidget, "treeDepsStatus")
        if label:
            label.setText("正在验证 Runtime manifest 与安装状态...")
        if tree:
            tree.clear()
        self._env_refresh_generation += 1
        generation = self._env_refresh_generation
        adapter = self._connect_runtime_adapter()
        status_client = (
            getattr(adapter, "runtime_status_client", None)
            if getattr(adapter, "is_started", False)
            else None
        )

        def operation() -> dict:
            inspection = self._runtime_installer.inspect()
            snapshot = {
                "mode": "portable",
                "inspection": inspection,
            }
            if status_client is not None and parse_runtime_status is not None:
                try:
                    payload = status_client.request_json("getRuntimeStatus")
                    snapshot["runtime_status"] = parse_runtime_status(payload)
                except Exception as exc:
                    logger.info("Supervisor HTTP 状态暂不可用，回退本地快照: %s", exc)
                    snapshot["runtime_status_error"] = str(exc)
            return snapshot

        self._run_cache_operation(
            operation,
            lambda snapshot: self._apply_env_maintenance_state(generation, snapshot),
            lambda error: self._on_env_refresh_error(generation, error),
        )

    def _on_env_refresh_error(self, generation: int, error: str) -> None:
        if generation != self._env_refresh_generation:
            return
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        if label:
            label.setText(f"运行时检测失败：{error}")
        logger.warning("环境维护状态刷新失败: %s", error)

    def _apply_env_maintenance_state(self, generation: int, snapshot: dict) -> None:
        if generation != self._env_refresh_generation:
            return
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        btn_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        btn_update = self._ui.findChild(QPushButton, "btnUpdateDeps")
        btn_reinstall_sel = self._ui.findChild(QPushButton, "btnReinstallSelected")
        tree = self._ui.findChild(QTreeWidget, "treeDepsStatus")

        mode = snapshot.get("mode", "none")
        inspection = snapshot.get("inspection")
        runtime_status = snapshot.get("runtime_status")

        if label:
            if mode == "portable" and inspection is not None:
                status = "已验证" if inspection.ready else "未就绪"
                accelerator = (
                    "NVIDIA CUDA" if inspection.accelerator == "nvidia_cuda" else "CPU"
                )
                service_labels = {
                    "ready": "已就绪",
                    "degraded": "降级",
                    "maintenance": "维护中",
                }
                service = "未连接"
                maintenance = ""
                identity = ""
                if runtime_status is not None:
                    service = service_labels.get(
                        runtime_status.service_state.value,
                        runtime_status.service_state.value,
                    )
                    if runtime_status.maintenance is not None:
                        maintenance = (
                            "\n维护："
                            f"{runtime_status.maintenance.phase.value} · "
                            f"{runtime_status.maintenance.operation_state.value}"
                        )
                    source = getattr(runtime_status, "source", None)
                    if source is not None:
                        identity = (
                            f"\nSource：{source.backend_source_sha[:12]}"
                            f"\nRuntime manifest："
                            f"{source.runtime_manifest_sha256[:12]}"
                            f"\nProtocol manifest："
                            f"{source.protocol_manifest_sha256[:12]}"
                        )
                elif getattr(inspection, "source", None) is not None:
                    source = inspection.source
                    identity = (
                        f"\nSource：{source.backend_source_sha[:12]}"
                        f"\nRuntime manifest：{source.runtime_manifest_sha256[:12]}"
                    )
                label.setText(
                    f"Runtime：{status}\n"
                    f"服务：{service}\n"
                    f"加速方案：{accelerator}\n"
                    f"Python：{inspection.python_version}\n"
                    f"Backend：{inspection.backend_version}\n"
                    f"Protocol：{inspection.protocol_version}\n"
                    f"Manifest：{inspection.manifest_sha256[:12]}"
                    f"{identity}"
                    f"{maintenance}"
                )
            else:
                label.setText("Runtime：未绑定")

        # 所有维护按钮都映射到 ensure/repair；不暴露逐包操作。
        enabled = mode == "portable"
        if btn_py:
            btn_py.setEnabled(enabled)
        if btn_deps:
            btn_deps.setEnabled(enabled)
        if btn_missing:
            btn_missing.setEnabled(enabled)
        if btn_update:
            btn_update.setEnabled(enabled)
        # "重装选中项"初始禁用，由依赖树选择变化驱动启用状态
        if btn_reinstall_sel:
            btn_reinstall_sel.setEnabled(enabled)
            btn_reinstall_sel.setText("修复 Runtime")

        # 填充依赖状态树（仅 portable 模式）
        if tree and mode == "portable":
            self._populate_deps_tree(tree, snapshot)
            if not getattr(self, "_deps_tree_signals_connected", False):
                if btn_reinstall_sel:
                    btn_reinstall_sel.clicked.connect(self._on_reinstall_selected)
                self._deps_tree_signals_connected = True
        elif tree:
            tree.clear()

    def _update_reinstall_selected_btn(
        self, tree: QTreeWidget, btn: QPushButton | None
    ) -> None:
        """根据依赖树选中状态更新"重装选中项"按钮的 enabled 和计数文本"""
        if btn is None:
            return
        count = sum(1 for it in tree.selectedItems() if it.parent() is None)
        btn.setEnabled(count > 0)
        btn.setText(f"重装选中项 ({count})" if count > 0 else "重装选中项")

    def _populate_deps_tree(
        self, tree: QTreeWidget, snapshot: dict | None = None
    ) -> None:
        """显示受产品绑定的 Runtime 组件，不向 UI 泄漏逐包安装细节。"""
        snapshot = snapshot or {}
        inspection = snapshot.get("inspection")
        runtime_status_snapshot = snapshot.get("runtime_status")
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.clear()
        if inspection is None:
            return
        runtime_status = "✓ 已验证" if inspection.ready else f"⚠ {inspection.integrity}"
        accelerator = (
            "NVIDIA CUDA" if inspection.accelerator == "nvidia_cuda" else "CPU"
        )
        tree.addTopLevelItem(
            QTreeWidgetItem(
                ["Python 运行时", runtime_status, inspection.python_version]
            )
        )
        backend_item = QTreeWidgetItem(
            ["Backend Supervisor", runtime_status, inspection.backend_version]
        )
        tree.addTopLevelItem(backend_item)
        if runtime_status_snapshot is not None:
            state_labels = {
                "not_required": "— 不需要",
                "pending": "… 等待中",
                "installing": "… 安装中",
                "verifying": "… 验证中",
                "ready": "✓ 已就绪",
                "failed": "✗ 失败",
                "cancelled": "— 已取消",
            }
            components = runtime_status_snapshot.profile.components
            for component in components:
                state_value = component.state.value
                actual = getattr(component, "actual_state", None)
                actual_value = getattr(actual, "value", actual)
                drift = getattr(component, "drift_reason", None)
                drift_value = getattr(drift, "value", drift)
                desired = getattr(component, "desired_state", None)
                desired_value = getattr(desired, "value", desired)
                status_text = state_labels.get(state_value, state_value)
                if state_value == "ready" and actual_value in {
                    "missing",
                    "drifted",
                    "unknown",
                }:
                    actual_labels = {
                        "missing": "✗ 缺失",
                        "drifted": "⚠ 已漂移",
                        "unknown": "? 未知",
                    }
                    status_text = actual_labels[actual_value]
                if drift_value not in {None, "none"}:
                    drift_labels = {
                        "missing": "缺失",
                        "version_mismatch": "版本不一致",
                        "identity_mismatch": "来源不一致",
                        "integrity_failed": "完整性失败",
                        "unexpected": "非预期组件",
                    }
                    status_text += f" · {drift_labels.get(drift_value, drift_value)}"
                if desired_value == "not_required":
                    status_text = "— 不需要"
                version = (
                    getattr(component, "actual_version", None)
                    or component.version
                    or getattr(component, "desired_version", None)
                    or "—"
                )
                backend_item.addChild(
                    QTreeWidgetItem(
                        [
                            component.display_name,
                            status_text,
                            version,
                        ]
                    )
                )
        else:
            for component in getattr(inspection, "components", ()):
                actual_labels = {
                    "ready": "✓ 已就绪",
                    "missing": "✗ 缺失",
                    "drifted": "⚠ 已漂移",
                    "unknown": "? 未知",
                }
                drift_labels = {
                    "missing": "缺失",
                    "version_mismatch": "版本不一致",
                    "identity_mismatch": "来源不一致",
                    "integrity_failed": "完整性失败",
                    "unexpected": "非预期组件",
                }
                actual_state = getattr(component, "actual_state", None)
                desired_state = getattr(component, "desired_state", None)
                drift_reason = getattr(component, "drift_reason", None)
                component_status = actual_labels.get(
                    actual_state,
                    "✓ 已验证" if inspection.ready else "⚠ 未就绪",
                )
                if drift_reason not in {None, "none"}:
                    component_status += (
                        f" · {drift_labels.get(drift_reason, drift_reason)}"
                    )
                if desired_state == "not_required":
                    component_status = "— 不需要"
                backend_item.addChild(
                    QTreeWidgetItem(
                        [
                            component.display_name,
                            component_status,
                            component.actual_version
                            or component.version
                            or component.desired_version
                            or "—",
                        ]
                    )
                )
        backend_item.setExpanded(True)
        tree.addTopLevelItem(
            QTreeWidgetItem(["Protocol", "✓ 已绑定", inspection.protocol_version])
        )
        tree.addTopLevelItem(
            QTreeWidgetItem(
                [f"{accelerator} 推理 profile", "✓ 已选择", inspection.profile]
            )
        )
        tree.setToolTip(
            "显示产品绑定的 Python、Backend、Protocol 与推理 profile。"
            "组件状态区分 desired/actual，漂移项可由 Runtime Installer 修复。"
        )

    @staticmethod
    def _format_dep_status(
        installed: bool, usable: bool, missing_module: str | None
    ) -> str:
        """把 (installed, usable, missing_module) 三元组格式化为状态列文本"""
        if usable:
            return "✓ 完整安装"
        if installed and missing_module:
            return f"⚠ 已安装，缺 {missing_module}"
        if installed:
            return "⚠ 安装残缺"
        return "✗ 未安装"

    def _on_clear_cache_clicked(self) -> None:
        """清除 Classic 自有的启动环境检测缓存。"""
        reply = QMessageBox.question(
            None,
            "确认清除",
            "确定要清除环境检测缓存吗？\n下次启动时需要重新检测应用环境与硬件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from vibeocr.classic.machine_cache import reset_cache_to_empty

            reset_cache_to_empty(self._project_root)
            self._update_cache_status("缓存已清除")
            logger.debug("[缓存] 已清除 Classic 启动环境检测状态")

    def _update_cache_status(self, status: str | None = None) -> None:
        """更新缓存状态；校验机器码的路径始终在线程池执行。"""
        from vibeocr.classic.machine_cache import get_cache_info

        label = self._ui.findChild(QLabel, "labelCacheStatus")
        if label is None:
            return
        if status:
            label.setText(status)
            return

        label.setText("正在检查缓存...")
        self._machine_cache_generation += 1
        generation = self._machine_cache_generation

        def operation() -> tuple[bool, str]:
            valid, _cached = is_cache_valid(self._project_root)
            return valid, get_cache_info(self._project_root) if valid else ""

        self._run_cache_operation(
            operation,
            lambda result: self._apply_cache_status(generation, result[0], result[1]),
            lambda error: self._apply_cache_status(
                generation, False, "", f"缓存检查失败：{error}"
            ),
        )

    def _apply_cache_status(
        self,
        generation: int,
        valid: bool,
        info: str,
        status: str | None = None,
    ) -> None:
        if generation != self._machine_cache_generation:
            return
        label = self._ui.findChild(QLabel, "labelCacheStatus")
        if label:
            label.setText(status or (f"缓存有效: {info}" if valid else "无有效缓存"))

    # --- 管道缓存生命周期管理 ---

    def _init_pipeline_cache_status_label(self) -> None:
        """在「运行时缓存」分组内追加 labelPipelineCacheStatus。

        原型由 labelCacheStatus 同时承载机器缓存与管道运行时状态，复制粘贴的
        文案让用户难以分辨"无有效缓存"指代哪一层。Task 7 拆分为两个标签：
          - ``labelCacheStatus``：仅机器缓存（依赖/模型缓存路径、机器码探测）
          - ``labelPipelineCacheStatus``：仅 supervisor 驻留状态与 TTL/pin 策略
        labelReleaseStatus 继续承载动作反馈（refresh/release 完成/失败）。
        """
        layout = self._ui.findChild(QVBoxLayout, "runtimeCacheLayout")
        if layout is None:
            return
        if self._ui.findChild(QLabel, "labelPipelineCacheStatus") is not None:
            return
        label = QLabel(self._ui)
        label.setObjectName("labelPipelineCacheStatus")
        label.setWordWrap(True)
        label.setText("运行时缓存状态：尚未读取")
        layout.addWidget(label)

    def _run_cache_operation(self, operation, on_success, on_error) -> None:
        """在线程池执行同步缓存 RPC，并隔离关闭后的迟到结果。"""
        task = FunctionTask(operation)
        self._cache_tasks.add(task)
        _BACKGROUND_TASKS.add(task)

        def finish(result) -> None:
            self._cache_tasks.discard(task)
            _BACKGROUND_TASKS.discard(task)
            if not self._closing:
                on_success(result)

        def fail(error: str) -> None:
            self._cache_tasks.discard(task)
            _BACKGROUND_TASKS.discard(task)
            if not self._closing:
                on_error(error)

        task.signals.finished.connect(finish)
        task.signals.error.connect(fail)
        QThreadPool.globalInstance().start(task)

    def _sync_configured_pipeline_ttls(self) -> None:
        """把完整 TTL snapshot 防抖下发到 supervisor。

        首次更新前必须先读取 ``ResidencyStatus.pipelines``，以保留 UI 暂未
        暴露的 pin 策略；不能用本地 TTL 字典覆盖 supervisor 的完整状态。
        """
        adapter = self._connect_runtime_adapter()
        if not adapter.is_started:
            self._update_release_status("运行时缓存状态：OCR 服务未连接")
            return

        current = self._runtime_settings_snapshot
        if current is None:
            self._pending_ttl_sync = True
            self._runtime_action = "settings-bootstrap"
            self._update_release_status("正在读取现有驻留策略...")
            adapter.refresh_residency()
            return

        from vibeocr.classic.managers.config_manager import ConfigManager
        from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

        configured_ttls = ConfigManager.instance().get_pipeline_ttls()
        pipelines = tuple(
            PipelineSpec(
                name=pipeline.value,
                ttl_seconds=(
                    ttl
                    if (ttl := int(configured_ttls.get(pipeline.value, 0))) > 0
                    else None
                ),
                pinned=int(configured_ttls.get(pipeline.value, 0)) == -1,
            )
            for pipeline in OCRPipeline
        )
        snapshot = SettingsSnapshot(
            default_ttl_seconds=current.default_ttl_seconds,
            pipelines=pipelines,
            extra=dict(current.extra),
            download_source_ids=current.download_source_ids,
        )
        self._runtime_action = "settings"
        self._update_release_status("正在更新 TTL...")
        adapter.update_settings(snapshot)

    def _on_refresh_pipeline_cache_clicked(self) -> None:
        adapter = self._connect_runtime_adapter()
        if not adapter.is_started:
            self._update_release_status("运行时缓存状态：OCR 服务未连接")
            return
        self._runtime_action = "refresh"
        self._update_release_status("正在读取 Supervisor 驻留状态...")
        adapter.refresh_residency()

    def _on_settings_updated(self, snapshot: object) -> None:
        if self._closing or not isinstance(snapshot, SettingsSnapshot):
            return
        self._runtime_settings_snapshot = snapshot
        self._runtime_action = "settings"
        self._update_release_status("TTL 已更新，正在刷新驻留状态...")
        self._connect_runtime_adapter().refresh_residency()

    def _on_settings_error(self, error: str) -> None:
        if self._closing:
            return
        self._runtime_action = ""
        self._pending_ttl_sync = False
        self._update_release_status(f"TTL 更新失败：{error}")

    # ----------------------------------------------------------------
    # OCR 引擎 / 离线能力 / 下载源（Protocol 2.7 选择面）
    # ----------------------------------------------------------------

    _FEATURE_LABELS = {
        "document_parsing": "文档智能解析（MinerU）",
        "gpu_runtime": "GPU 运行时",
    }

    _SOURCE_KIND_LABELS = {
        "package_index": "Python 包索引",
    }

    def _init_ocr_runtime_group(self) -> None:
        """初始化 OCR 引擎与 Runtime 能力分组；catalog 数据等待 health。"""

        combo = self._ui.findChild(QComboBox, "comboOcrEngine")
        if combo is not None and not self._engine_combo_initialized:
            self._engine_combo_initialized = True
            combo.currentIndexChanged.connect(self._on_engine_selected)
        button = self._ui.findChild(QPushButton, "btnInstallOfflineFeatures")
        if button is not None:
            button.clicked.connect(self._on_install_offline_features)
        save_button = self._ui.findChild(QPushButton, "btnSaveDownloadSources")
        if save_button is not None:
            save_button.clicked.connect(self._on_save_download_sources)
        try:
            self._selection_accelerator = (
                self._runtime_installer.profile_descriptor().accelerator
            )
        except Exception:  # noqa: BLE001 - 绑定缺失时由 Runtime 状态区负责提示
            self._selection_accelerator = None
        self._populate_engine_combo()
        self._refresh_selection_availability()

    def _populate_engine_combo(self) -> None:
        combo = self._ui.findChild(QComboBox, "comboOcrEngine")
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        for engine_id in ("rapidocr", "windows", "paddleocr"):
            combo.addItem(ENGINE_DISPLAY_NAMES.get(engine_id, engine_id), engine_id)
        engine_id: str | None = None
        requires_selection = False
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            selection = ConfigManager.instance().get_ocr_engine_selection()
            if isinstance(selection, tuple) and len(selection) == 2:
                engine_id, requires_selection = selection
        except RuntimeError:
            engine_id = None
        effective = engine_id or "rapidocr"
        index = combo.findData(effective)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        if requires_selection:
            self._set_engine_status("检测到未知的旧引擎配置，请重新选择识别引擎")

    def _set_engine_status(self, text: str) -> None:
        label = self._ui.findChild(QLabel, "labelOcrEngineStatus")
        if label is not None:
            label.setText(text)

    def _on_engine_selected(self, index: int) -> None:
        combo = self._ui.findChild(QComboBox, "comboOcrEngine")
        if combo is None or index < 0:
            return
        engine_id = combo.itemData(index)
        if not isinstance(engine_id, str) or engine_id not in VALID_ENGINE_IDS:
            return
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            if not ConfigManager.instance().set_ocr_engine(engine_id):
                self._set_engine_status(f"引擎 {engine_id} 不是有效选择，未保存")
                return
        except RuntimeError:
            return
        self._render_engine_availability()

    def _render_engine_availability(self) -> None:
        catalog = self._selection_catalog
        if catalog is None:
            return
        parts = []
        for entry in catalog.engines:
            label = ENGINE_AVAILABILITY_LABELS.get(
                entry.availability, entry.availability
            )
            suffix = ""
            if entry.reason_code:
                suffix = f"（{entry.reason_code}）"
            parts.append(f"{entry.display_name}：{label}{suffix}")
        if parts:
            self._set_engine_status("；".join(parts))

    def _on_health_loaded(self, health: object) -> None:
        if self._closing or not isinstance(health, dict):
            return
        descriptors = health.get("capability_descriptors")
        if not isinstance(descriptors, list):
            descriptors = []
        capabilities = health.get("capabilities")
        capabilities = set(capabilities) if isinstance(capabilities, list) else set()
        try:
            catalog = parse_capability_catalogs(descriptors)
        except RuntimeSelectionError as exc:
            self._set_engine_status(f"Backend 选择目录无效：{exc}")
            return
        self._selection_catalog = catalog
        self._render_engine_availability()
        self._render_offline_features()
        self._render_download_sources(capabilities)
        self._refresh_selection_availability()

    def _on_health_error(self, error: str) -> None:
        if self._closing:
            return
        self._set_engine_status(f"引擎状态读取失败：{error}")
        status = self._ui.findChild(QLabel, "labelDownloadSourceStatus")
        if status is not None:
            status.setText(f"下载源读取失败：{error}")

    def _render_offline_features(self) -> None:
        """按当前 accelerator 渲染可选能力勾选列表（catalog 驱动）。"""

        tree = self._ui.findChild(QTreeWidget, "treeOfflineFeatures")
        catalog = self._selection_catalog
        if tree is None or catalog is None:
            return
        accelerator = self._selection_accelerator
        if accelerator is None:
            tree.clear()
            return
        variants = catalog.variants_for_accelerator(accelerator)
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            selected = set(
                ConfigManager.instance().get_offline_component_features(accelerator)
            )
        except RuntimeError:
            selected = set()
        tree.clear()
        for variant in variants:
            label = self._FEATURE_LABELS.get(variant.feature_id, variant.feature_id)
            item = QTreeWidgetItem([label, "未安装"])
            item.setData(0, Qt.ItemDataRole.UserRole, variant.feature_id)
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if variant.feature_id in selected
                else Qt.CheckState.Unchecked,
            )
            tree.addTopLevelItem(item)

    def _selected_offline_features(self) -> tuple[str, ...]:
        tree = self._ui.findChild(QTreeWidget, "treeOfflineFeatures")
        if tree is None:
            return ()
        features: list[str] = []
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            feature_id = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feature_id, str):
                features.append(feature_id)
        return tuple(features)

    def _on_install_offline_features(self) -> None:
        catalog = self._selection_catalog
        accelerator = self._selection_accelerator
        if catalog is None or accelerator is None:
            QMessageBox.information(
                None,
                "暂不可用",
                "当前 Backend 未提供可选能力目录，无法安装可选组件。",
            )
            return
        features = self._selected_offline_features()
        if not features:
            QMessageBox.information(
                None,
                "未选择能力",
                "请先勾选需要安装的可选能力。",
            )
            return
        try:
            component_ids = catalog.component_ids_for_features(features, accelerator)
        except RuntimeSelectionError as exc:
            QMessageBox.warning(None, "无法解析所选能力", str(exc))
            return
        names = "、".join(
            self._FEATURE_LABELS.get(feature, feature) for feature in features
        )
        answer = QMessageBox.question(
            None,
            "安装可选组件",
            (
                f"即将在线下载并安装：{names}。\n"
                "下载量可能较大，安装期间会停止当前推理服务。\n是否继续？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            from vibeocr.classic.managers.config_manager import ConfigManager

            ConfigManager.instance().set_offline_component_features(
                accelerator, list(features)
            )
        except RuntimeError:
            pass
        source_ids = self._resolve_download_source_ids()
        self._run_after_supervisor_invalidated(
            lambda: self._show_install_dialog(
                install_component_ids=component_ids,
                download_source_ids=source_ids,
            )
        )

    def _clear_source_combo_rows(self) -> None:
        """移除动态下载源行；行容器必须整行销毁，不能只移除内层 combo。"""

        layout = self._ui.findChild(QVBoxLayout, "ocrRuntimeLayout")
        for row in self._source_combo_row_widgets:
            if layout is not None:
                layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._source_combo_row_widgets.clear()
        self._source_combo_rows.clear()

    def _render_download_sources(self, capabilities: set) -> None:
        """按 Backend catalog 渲染每 kind 的下载源单选。"""

        label = self._ui.findChild(QLabel, "labelDownloadSource")
        layout = self._ui.findChild(QVBoxLayout, "ocrRuntimeLayout")
        save_button = self._ui.findChild(QPushButton, "btnSaveDownloadSources")
        catalog = self._selection_catalog
        if label is None or layout is None or catalog is None:
            return
        self._clear_source_combo_rows()
        if DOWNLOAD_SOURCES_CAPABILITY not in capabilities:
            label.setText("下载源：当前 Backend 不支持下载源选择")
            self._set_source_controls_enabled(False)
            return
        grouped = catalog.editable_sources_by_kind()
        if not grouped:
            label.setText("下载源：Backend 目录未声明已知类型来源")
            self._set_source_controls_enabled(False)
            return
        insert_at = (
            layout.indexOf(save_button) if save_button is not None else layout.count()
        )
        current_snapshot = self._runtime_settings_snapshot
        selected_ids = set(
            current_snapshot.download_source_ids if current_snapshot is not None else ()
        )
        unknown_kinds = [
            source.kind for source in catalog.sources if not source.editable
        ]
        for offset, (kind, sources) in enumerate(sorted(grouped.items())):
            row = QWidget(self._ui)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            kind_label = QLabel(f"{self._SOURCE_KIND_LABELS.get(kind, kind)}：", row)
            combo = QComboBox(row)
            combo.setObjectName(f"comboDownloadSource_{kind}")
            for source in sources:
                combo.addItem(source.source_id, source.source_id)
                combo.setItemData(
                    combo.count() - 1, source.endpoint, Qt.ItemDataRole.ToolTipRole
                )
            if sources:
                selected = next(
                    (
                        source.source_id
                        for source in sources
                        if source.source_id in selected_ids
                    ),
                    sources[0].source_id,
                )
                combo.setCurrentIndex(max(0, combo.findData(selected)))
            row_layout.addWidget(kind_label)
            row_layout.addWidget(combo, 1)
            layout.insertWidget(insert_at + offset, row)
            self._source_combo_row_widgets.append(row)
            self._source_combo_rows[kind] = combo
        note = "下载源：每类至多选择一个；endpoint 由 Backend 只读声明"
        if unknown_kinds:
            note += f"；存在未支持的来源类型：{'、'.join(sorted(set(unknown_kinds)))}"
        label.setText(note)
        self._set_source_controls_enabled(True)

    def _set_source_controls_enabled(self, enabled: bool) -> None:
        save_button = self._ui.findChild(QPushButton, "btnSaveDownloadSources")
        if save_button is not None:
            save_button.setEnabled(enabled)
        for combo in self._source_combo_rows.values():
            combo.setEnabled(enabled)

    def _resolve_download_source_ids(self) -> tuple[str, ...] | None:
        """把当前 UI 选择转换为 wire download_source_ids；空选择省略。"""

        catalog = self._selection_catalog
        if catalog is None or not self._source_combo_rows:
            return None
        choices: dict[str, str] = {}
        for kind, combo in self._source_combo_rows.items():
            source_id = combo.currentData()
            if isinstance(source_id, str) and source_id:
                choices[kind] = source_id
        try:
            return catalog.normalize_source_selection(choices)
        except RuntimeSelectionError:
            return None

    def _on_save_download_sources(self) -> None:
        adapter = self._connect_runtime_adapter()
        if not adapter.is_started:
            status = self._ui.findChild(QLabel, "labelDownloadSourceStatus")
            if status is not None:
                status.setText("下载源：OCR 服务未连接，未保存")
            return
        catalog = self._selection_catalog
        if catalog is None:
            return
        # Settings 是全量 PUT：未读到现有快照前不能发送，否则会用默认
        # residency 策略覆盖 Backend 上用户配置的 TTL/pin（与 TTL 同步同规则）。
        if self._runtime_settings_snapshot is None:
            adapter.fetch_settings()
            status = self._ui.findChild(QLabel, "labelDownloadSourceStatus")
            if status is not None:
                status.setText("正在读取现有设置，稍后再保存下载源...")
            return
        source_ids = self._resolve_download_source_ids()
        current = self._runtime_settings_snapshot
        preserved_source_ids = (
            self._selection_catalog.preserve_uneditable_source_ids(
                current.download_source_ids
            )
            if self._selection_catalog is not None
            else current.download_source_ids
        )
        snapshot = SettingsSnapshot(
            default_ttl_seconds=current.default_ttl_seconds,
            pipelines=current.pipelines,
            extra=dict(current.extra),
            download_source_ids=(*preserved_source_ids, *(source_ids or ())),
        )
        adapter.update_settings(snapshot)
        status = self._ui.findChild(QLabel, "labelDownloadSourceStatus")
        if status is not None:
            status.setText(
                "正在保存下载源选择："
                + ("、".join(source_ids) if source_ids else "使用 Backend 默认源")
            )

    def _on_settings_loaded(self, snapshot: object) -> None:
        if self._closing or not isinstance(snapshot, SettingsSnapshot):
            return
        self._runtime_settings_snapshot = snapshot
        selected = set(snapshot.download_source_ids)
        for kind, combo in self._source_combo_rows.items():
            for index in range(combo.count()):
                if combo.itemData(index) in selected:
                    combo.setCurrentIndex(index)
                    break
        status = self._ui.findChild(QLabel, "labelDownloadSourceStatus")
        if status is not None:
            status.setText(
                "当前下载源："
                + ("、".join(snapshot.download_source_ids) or "Backend 默认源")
            )

    def _refresh_selection_availability(self) -> None:
        """Backend 未声明选择能力时禁用对应 UI，而不是构造请求。"""

        catalog = self._selection_catalog
        tree = self._ui.findChild(QTreeWidget, "treeOfflineFeatures")
        button = self._ui.findChild(QPushButton, "btnInstallOfflineFeatures")
        has_variants = catalog is not None and bool(catalog.variants)
        if tree is not None:
            tree.setEnabled(has_variants)
        if button is not None:
            button.setEnabled(has_variants)
        if catalog is not None and not catalog.variants:
            label = self._ui.findChild(QLabel, "labelOcrEngineStatus")
            existing = label.text() if label is not None else ""
            if not existing or existing.startswith("引擎状态"):
                self._set_engine_status(
                    "当前 Backend 未声明可选组件能力（不提供离线能力勾选）"
                )

    def _on_residency_status(self, status: object) -> None:
        if self._closing or not isinstance(status, ResidencyStatus):
            return
        current = self._runtime_settings_snapshot
        self._runtime_settings_snapshot = SettingsSnapshot(
            default_ttl_seconds=status.default_ttl_seconds,
            pipelines=status.pipelines,
            extra=dict(current.extra) if current is not None else {},
            download_source_ids=(
                current.download_source_ids if current is not None else ()
            ),
        )

        action = self._runtime_action
        prefix = {
            "settings": "TTL 已更新",
            "release": "已完成闲置模型释放",
        }.get(action, "运行时驻留状态")
        self._runtime_action = ""
        self._render_residency_status(status, prefix=prefix)
        self._set_release_controls_enabled(True)

        if self._preload_selected:
            loaded_names = {
                entry.pipeline
                for entry in status.entries
                if entry.kind is not ResidencyKind.EVICTED
            }
            selected_loaded = [
                pipeline
                for pipeline in self._preload_selected
                if pipeline in loaded_names
            ]
            self._preload_loaded_count = len(selected_loaded)
            detail = "、".join(selected_loaded) if selected_loaded else "无"
            self._update_preload_status(
                f"正在通过 Supervisor 预加载 {len(self._preload_selected)} 个管道；"
                f"已驻留 {len(selected_loaded)}/{len(self._preload_selected)}：{detail}"
            )
            self._publish_runtime_status(
                "预加载中 · "
                f"{len(selected_loaded)}/{len(self._preload_selected)} 驻留 · {detail}"
            )
        else:
            resident_names = [
                entry.pipeline
                for entry in status.entries
                if entry.kind is not ResidencyKind.EVICTED
            ]
            detail = "、".join(resident_names) if resident_names else "无"
            self._publish_runtime_status(
                f"已驻留 {len(resident_names)} 个管道 · {detail}"
                if resident_names
                else "无模型驻留 · 按需加载"
            )

        if self._pending_ttl_sync:
            self._pending_ttl_sync = False
            self._sync_configured_pipeline_ttls()

    def _render_residency_status(self, status: ResidencyStatus, *, prefix: str) -> None:
        specs = {spec.name: spec for spec in status.pipelines}
        entries: list[str] = []
        resident_entries = tuple(
            entry for entry in status.entries if entry.kind is not ResidencyKind.EVICTED
        )
        for entry in resident_entries:
            spec = specs.get(entry.pipeline)
            details = [str(entry.kind.value)]
            if spec is not None and spec.pinned:
                details.append("已固定")
            details.append(f"活动租约 {entry.active_leases}")
            if entry.remaining_ttl_seconds is not None:
                details.append(f"剩余 TTL {entry.remaining_ttl_seconds} 秒")
            if entry.estimated_vram_mb is not None:
                details.append(f"显存约 {entry.estimated_vram_mb} MB")
            if entry.eviction_reason.value != "none":
                details.append(f"最近回收原因 {entry.eviction_reason.value}")
            entries.append(f"{entry.pipeline}（{'，'.join(details)}）")

        loaded_summary = "、".join(entries) if entries else "无"
        policies = "，".join(
            (
                f"{spec.name}="
                f"{'固定' if spec.pinned else '继承默认' if spec.ttl_seconds is None else f'{spec.ttl_seconds}秒'}"
            )
            for spec in status.pipelines
        )
        vram = ""
        if status.vram_used_mb is not None or status.vram_total_mb is not None:
            used = "未知" if status.vram_used_mb is None else str(status.vram_used_mb)
            total = (
                "未知" if status.vram_total_mb is None else str(status.vram_total_mb)
            )
            vram = f"；显存 {used}/{total} MB"
        status_text = (
            f"{prefix}：驻留 {len(resident_entries)} 个（{loaded_summary}）；"
            f"默认 TTL {status.default_ttl_seconds} 秒"
            f"{f'；策略 {policies}' if policies else ''}{vram}"
        )
        label = self._ui.findChild(QLabel, "labelPipelineCacheStatus")
        if label is not None:
            label.setText(status_text)
        else:
            self._update_release_status(status_text)
        self._update_release_status("就绪")

    def _on_residency_error(self, error: str) -> None:
        if self._closing:
            return
        self._runtime_action = ""
        self._pending_ttl_sync = False
        friendly = self._friendly_cache_error(error)
        status_label = self._ui.findChild(QLabel, "labelPipelineCacheStatus")
        if status_label is not None:
            status_label.setText(friendly)
        self._update_release_status(f"运行时缓存操作失败：{error}")
        self._set_release_controls_enabled(True)

    @staticmethod
    def _friendly_cache_error(error: str) -> str:
        """把 supervisor 错误翻译成用户能理解的状态文案。"""
        msg = str(error)
        if "TimeoutError" in msg or "超时" in msg or "timed out" in msg.lower():
            return "读取驻留状态失败：Supervisor 未及时响应"
        return f"读取驻留状态失败：{msg}"

    def _on_release_heavy_clicked(self) -> None:
        """旧入口不再模拟 supervisor 未定义的 heavy-only 语义。"""
        QMessageBox.information(
            None,
            "无法按类型释放",
            "Supervisor v2 暂不支持按“重模型”筛选，请使用“释放全部闲置模型”。",
        )

    def _on_release_all_clicked(self) -> None:
        """释放全部闲置模型；active/pinned 模型由 supervisor 自动跳过。"""
        adapter = self._connect_runtime_adapter()
        if not adapter.is_started:
            QMessageBox.warning(None, "无法释放", "OCR 服务尚未就绪。")
            return

        reply = QMessageBox.question(
            None,
            "确认释放",
            "确定要释放全部闲置模型吗？活动租约和已固定模型不会被释放。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_release_controls_enabled(False)
        self._runtime_action = "release"
        self._update_release_status("正在释放全部闲置模型...")
        adapter.release_idle(None)

    def _set_release_controls_enabled(self, enabled: bool) -> None:
        for name in ("btnReleaseAll",):
            btn = self._ui.findChild(QPushButton, name)
            if btn is not None:
                btn.setEnabled(enabled)

    def _update_release_status(self, status: str) -> None:
        """更新释放状态标签。"""
        label = self._ui.findChild(QLabel, "labelReleaseStatus")
        if label:
            label.setText(status)
