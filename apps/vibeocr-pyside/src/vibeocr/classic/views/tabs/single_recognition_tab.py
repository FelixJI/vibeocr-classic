"""单次识别标签页"""

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.text_layout import TextBlockProcessor
from vibeocr.classic.ui import theme
from vibeocr.classic.utils.image_jobs import (
    GenerationImageJobs,
    decode_image_bytes,
    decode_image_file,
)
from vibeocr.classic.views.tabs.base_tab import BaseOcrTab
from vibeocr.classic.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.classic.widgets.preview_widget import PreviewWidget
from vibeocr.classic.widgets.result_view_widget import ResultViewWidget
from vibeocr.classic.widgets.text_block_options_widget import TextBlockOptionsWidget

logger = logging.getLogger(__name__)


class SingleRecognitionTab(BaseOcrTab):
    """单次识别标签页

    左侧：统一预览（图片/PDF/截图）
    右侧：管道选项 + 结果展示
    """

    SPLITTER_ID = "ocr_tab"

    screenshot_requested = Signal()
    file_open_requested = Signal()
    image_file_requested = Signal(str)
    # 截图来源的识别完成时发出，由 MainWindow 重新把主窗口提到前台。
    # 根因：主窗口激活此前只在 OCR 开始前发生一次；异步识别期间用户/系统切走
    # 窗口后，识别完成时窗口就静悄悄留在后台（表现为「识别后主界面不弹出」）。
    # 仅截图来源识别需要抢焦点（用户离开过应用）；文件/粘贴来源用户本就在应用内，
    # 不发信号以免无谓抢焦点。
    bring_to_front_requested = Signal()
    # status_changed 保留给既有调用方；typed 信号供全局仪表条避免任务与
    # 结果互相覆盖。不复用普通日志，避免后台模型消息把识别结论说错。
    status_changed = Signal(str)
    task_status_changed = Signal(str)
    result_status_changed = Signal(str)
    _native_call_finished = Signal()
    _LOW_CONFIDENCE_THRESHOLD = 0.80

    def __init__(self, parent=None, *, backend=None):
        super().__init__(parent)
        self._closing = False
        self._pending_pixmap: QPixmap | None = None
        self._pending_file_path: str | None = None
        # 本次识别是否来自截图（由 run_ocr 的 from_screenshot 参数设置）。
        # _on_ocr_finished 据此决定是否发 bring_to_front_requested。
        self._ocr_from_screenshot: bool = False
        # RPC 后端（SyncBackendClient）；测试可注入 fake。为 None 时延迟创建。
        self._backend = backend
        self._uses_shared_backend = backend is None
        # 异步识别协程的 Task 引用，用于忙时串行与关闭时取消。None 表示当前
        # 没有识别在进行。忙时状态同时由基类 _is_processing 反映（驱动按钮禁用）。
        self._recognize_task: asyncio.Task | None = None
        self._recognition_started_at: float | None = None
        self._active_ocr_options: OCROptions | None = None
        self._native_call_events: set[threading.Event] = set()
        self._native_call_events_lock = threading.Lock()
        self._image_load_jobs = GenerationImageJobs(self)
        self._image_load_jobs.completed.connect(self._on_image_file_loaded)
        self._image_load_jobs.failed.connect(self._on_image_file_load_failed)
        self._preprocessed_image_jobs = GenerationImageJobs(self)
        self._preprocessed_image_jobs.completed.connect(
            self._on_preprocessed_image_loaded
        )
        self._preprocessed_image_jobs.failed.connect(
            self._on_preprocessed_image_load_failed
        )
        # set_closing(True) 在 GUI 线程快照；drain() 仅等待这些线程对象。
        self._result_drain_jobs: tuple[Any, ...] = ()
        self._pending_text_layout: tuple[object, object] | None = None
        self._setup_ui()
        self._connect_signals()
        self._native_call_finished.connect(self._on_native_call_finished)
        self._init_options_from_preferences(batch=False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        self._splitter = QSplitter()

        # 左侧：按钮栏 + 统一预览（包裹在容器中）
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(4)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 操作按钮栏
        action_bar = QWidget()
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(4, 2, 4, 2)
        action_layout.setSpacing(4)

        self._screenshot_btn = QPushButton("截图")
        self._screenshot_btn.setFixedHeight(28)
        self._file_btn = QPushButton("选择文件")
        self._file_btn.setFixedHeight(28)
        self._paste_btn = QPushButton("粘贴")
        self._paste_btn.setFixedHeight(28)
        self._copy_image_btn = QPushButton("复制图片")
        self._copy_image_btn.setFixedHeight(28)
        self._copy_image_btn.setEnabled(False)  # 默认禁用，有图后启用

        # 复制图片成功浮层提示（锚点为复制图片按钮）
        self._copy_toast = QLabel("原图已复制到剪贴板", self._copy_image_btn)
        self._copy_toast.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.text};"
            f" color: {theme.Colors.surface}; padding: 6px 12px;"
            f" border-radius: {theme.Radius.sm}px;"
            f" font-size: {theme.Typography.small}px; }}"
        )
        self._copy_toast.hide()
        self._start_btn = QPushButton("开始识别")
        self._start_btn.setFixedHeight(28)
        self._start_btn.setEnabled(False)

        action_layout.addWidget(self._screenshot_btn)
        action_layout.addWidget(self._file_btn)
        action_layout.addWidget(self._paste_btn)
        action_layout.addWidget(self._copy_image_btn)
        action_layout.addStretch()
        action_layout.addWidget(self._start_btn)

        left_layout.addWidget(action_bar)

        self._preview_widget = PreviewWidget(
            empty_text="左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式"
        )
        left_layout.addWidget(self._preview_widget, stretch=1)

        self._splitter.addWidget(left_panel)

        # 右侧：管道选项 + 结果展示
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(6)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._preprocess_options = PreprocessOptionsWidget()
        right_layout.addWidget(self._preprocess_options)

        self._text_options_widget = TextBlockOptionsWidget()
        right_layout.addWidget(self._text_options_widget)

        self._result_widget = ResultViewWidget(utility_client=self._backend)
        right_layout.addWidget(self._result_widget, stretch=1)

        right_panel.setMinimumWidth(300)
        self._splitter.addWidget(right_panel)

        self._splitter.setSizes([400, 500])
        layout.addWidget(self._splitter, stretch=1)
        self.setLayout(layout)

    def _connect_signals(self):
        self._setup_hover_sync()
        self._preview_widget.block_text_edited.connect(self._on_block_text_edited)
        self._preview_widget.block_clicked.connect(self._result_widget.highlight_block)
        self._result_widget.block_edited.connect(self._on_result_block_edited)
        self._result_widget.table_cell_edited.connect(self._on_table_cell_edited)
        # 文本块处理选项变化 → 实时重排当前结果（仅纯文本结果生效）。
        self._text_options_widget.options_changed.connect(self._on_text_options_changed)

        # 转发预览组件的截图/文件请求信号
        self._preview_widget.screenshot_requested.connect(self._request_screenshot)
        self._preview_widget.file_open_requested.connect(self._on_file_btn_clicked)

        # 操作按钮
        self._screenshot_btn.clicked.connect(self._request_screenshot)
        self._file_btn.clicked.connect(self._on_file_btn_clicked)
        self._paste_btn.clicked.connect(self._on_paste)
        self._copy_image_btn.clicked.connect(self._on_copy_image)
        self._start_btn.clicked.connect(self._start_recognition)

    def _on_file_btn_clicked(self) -> None:
        """选择文件；图片后台解码，文档保持等待用户点击开始识别。"""
        if not self._accepting_new_input():
            logger.debug("识别进行中，忽略选择文件请求")
            return
        from PySide6.QtWidgets import QFileDialog

        from vibeocr.classic.utils.mime_types import FILE_FILTER_ALL, is_document_file

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            f"{FILE_FILTER_ALL};;所有文件 (*)",
        )
        if not file_path:
            return

        self._invalidate_image_decodes()
        self._file_btn.setEnabled(True)
        self._pending_file_path = file_path
        self._pending_pixmap = None

        if is_document_file(file_path):
            self._file_btn.setEnabled(True)
            self._preprocess_options.lock_to_document_parsing("当前文件仅支持文档解析")
            self._preview_widget.load_file(file_path)
            self._copy_image_btn.setEnabled(False)
            self._refresh_start_btn_enabled()
        else:
            self._preprocess_options.unlock_pipeline()
            self._request_image_file_load(file_path, auto_recognize=False)
        self._start_btn.setText("开始识别")

    def _request_image_file_load(self, file_path: str, *, auto_recognize: bool) -> None:
        if not self._accepting_new_input():
            return
        self._file_btn.setEnabled(False)
        self._image_load_jobs.submit(
            lambda cancel_event: (
                file_path,
                auto_recognize,
                decode_image_file(file_path, cancel_event),
            )
        )

    @Slot(int, object)
    def _on_image_file_loaded(self, _generation: int, result: object) -> None:
        if self._closing or not isinstance(result, tuple) or len(result) != 3:
            return
        _file_path, auto_recognize, image = result
        if image.isNull():
            self._on_image_file_load_failed(_generation, "无法显示所选图片")
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_image_file_load_failed(_generation, "无法显示所选图片")
            return
        self._file_btn.setEnabled(True)
        self._preprocess_options.unlock_pipeline()
        self.set_pixmap(pixmap)
        self.set_image_for_recognition(pixmap)
        self._start_btn.setText("开始识别")
        if auto_recognize:
            self.run_ocr(pixmap)

    @Slot(int, str)
    def _on_image_file_load_failed(self, _generation: int, error: str) -> None:
        if self._closing:
            return
        self._file_btn.setEnabled(True)
        self._pending_pixmap = None
        self._pending_file_path = None
        self._refresh_start_btn_enabled()
        logger.warning("加载图片失败: %s", error)
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "加载图片失败", error)

    def _on_paste(self) -> None:
        if not self._accepting_new_input():
            logger.debug("识别进行中，忽略粘贴请求")
            return
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        pixmap = clipboard.pixmap()
        if pixmap.isNull():
            self._show_copy_toast("剪贴板中没有图片")
            return

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._invalidate_image_decodes()
        self._file_btn.setEnabled(True)
        self._preprocess_options.unlock_pipeline()
        self._preview_widget.set_pixmap(pixmap)
        self.set_image_for_recognition(pixmap)
        self._start_btn.setText("开始识别")

    def _request_screenshot(self) -> None:
        if not self._accepting_new_input():
            logger.debug("识别进行中，忽略截图请求")
            return
        self._invalidate_image_decodes()
        self._file_btn.setEnabled(True)
        self.screenshot_requested.emit()

    def _accepting_new_input(self) -> bool:
        return not self._closing and not self._is_processing

    def _invalidate_image_decodes(self) -> None:
        """使文件与预处理图片的迟到解码结果都失效。"""
        self._image_load_jobs.cancel_current()
        self._preprocessed_image_jobs.cancel_current()

    def _on_copy_image(self) -> None:
        """复制原始图片到剪贴板（取 original_pixmap，非预处理后图像）。"""
        pixmap = self._preview_widget.original_pixmap()
        if pixmap is None or pixmap.isNull():
            self._show_copy_toast("暂无图片可复制")
            return
        QGuiApplication.clipboard().setPixmap(pixmap)
        self._show_copy_toast()

    def _show_copy_toast(self, message: str = "原图已复制到剪贴板") -> None:
        """显示浮层提示（按钮上方居中，1.5s 自动隐藏）。

        默认文案为复制图片成功提示；复制图片/粘贴等失败场景传入对应 message。
        """
        toast = self._copy_toast
        toast.setText(message)
        toast.adjustSize()
        x = (self._copy_image_btn.width() - toast.width()) // 2
        y = -toast.height() - 8
        toast.move(x, y)
        toast.show()
        QTimer.singleShot(1500, toast.hide)

    def _on_start(self):
        self._start_recognition()

    def _start_recognition(self) -> None:
        """开始识别：保存当前管道选项后执行 OCR"""
        if self._is_processing:
            # 识别进行中本按钮呈现为「取消」；就地取消而不是静默忽略。
            self._request_cancel_recognition()
            return
        self._save_current_pipeline_options()
        if self._pending_pixmap:
            self.run_ocr(self._pending_pixmap)
        elif self._pending_file_path:
            self.process_file(self._pending_file_path)

    def _request_cancel_recognition(self) -> None:
        """请求取消进行中的识别。

        取消 asyncio task 会经 SupervisorClientAdapter.recognize 转发为
        Backend CANCEL 命令；注入式同步 backend 的原生线程调用不可抢占，
        线程返回前保持「正在取消…」禁用态。
        """
        task = self._recognize_task
        if task is None or task.done():
            return
        self._start_btn.setEnabled(False)
        self.status_changed.emit("正在取消…")
        self.task_status_changed.emit("单次识别 · 正在取消")
        task.cancel()

    def _finish_cancelled_recognition(self) -> None:
        """取消落地后的界面复位（与失败路径同构）。"""
        self._start_btn.setText("开始识别")
        if self._closing:
            return
        self._ocr_from_screenshot = False
        self._result_widget._ensure_web_view().setHtml(
            "<p style='color:#888;'>识别已取消</p>"
        )
        self.status_changed.emit("已取消")
        self.task_status_changed.emit("单次识别 · 已取消")

    def _save_current_pipeline_options(self) -> None:
        """保存当前管道选项到持久化"""
        if not self._preprocess_options:
            return
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            pipeline = self._preprocess_options.get_current_pipeline()
            options = self._preprocess_options.get_options()
            prefs.set_pipeline_options("main", pipeline, options)
        except RuntimeError:
            pass

    def set_closing(self, closing: bool) -> None:
        """标记 tab 正在关闭。

        除设置标志外，在进入关闭态时主动取消正在跑的识别协程，避免其回调
        在 _result_widget 已被 cleanup 后写入已销毁的 web view。closeEvent
        应在 widget 清理之前调用本方法。
        """
        self._closing = closing
        self._result_widget.set_closing(closing)
        if closing:
            self.request_base_shutdown()
            self._pending_text_layout = None
            self._preview_widget.request_shutdown()
            self._image_load_jobs.close()
            self._preprocessed_image_jobs.close()
            candidates = (
                self._result_widget._export_job,
                *tuple(self._result_widget._render_jobs),
            )
            self._result_drain_jobs = tuple(
                dict.fromkeys(job for job in candidates if job is not None)
            )
            self._file_btn.setEnabled(False)
            self._paste_btn.setEnabled(False)
            self._screenshot_btn.setEnabled(False)
            self._start_btn.setEnabled(False)
        else:
            self._result_drain_jobs = ()
        if closing and self._recognize_task is not None:
            task = self._recognize_task
            if not task.done():
                task.cancel()

    def drain(self, timeout_ms: int = 0) -> bool:
        """有界等待后台图片/结果作业；不读取或更新任何 QWidget 状态。"""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        waitables = (
            self._preview_widget,
            self._image_load_jobs,
            self._preprocessed_image_jobs,
            self._content_jobs,
            self._result_rebuild_jobs,
            *self._result_drain_jobs,
        )
        for waitable in waitables:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not waitable.drain(remaining_ms):
                return False
        with self._native_call_events_lock:
            native_events = tuple(self._native_call_events)
        for done_event in native_events:
            if done_event.is_set():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not done_event.wait(remaining):
                return False
        return True

    def is_drained(self) -> bool:
        """供 GUI 关闭轮询使用；不阻塞事件循环。"""
        with self._native_call_events_lock:
            self._native_call_events = {
                event for event in self._native_call_events if not event.is_set()
            }
        task = self._recognize_task
        return (task is None or task.done()) and self.drain(0)

    def request_shutdown(self) -> None:
        self.set_closing(True)

    def closeEvent(self, event) -> None:
        self.request_shutdown()
        super().closeEvent(event)

    # ── 公共接口（由 MainWindow 调用）──

    def set_pixmap(self, pixmap) -> None:
        if not self._accepting_new_input():
            return
        self._preview_widget.set_pixmap(pixmap)
        self._update_copy_image_enabled()

    def _update_copy_image_enabled(self) -> None:
        """根据是否有原始图片启用/禁用「复制图片」按钮。"""
        pix = self._preview_widget.original_pixmap()
        self._copy_image_btn.setEnabled(pix is not None and not pix.isNull())

    def set_image_for_recognition(self, pixmap: QPixmap) -> None:
        """记录待识别图（用于粘贴 / 截图后启用「重新识别」）。

        - 存入 _pending_pixmap，清空 _pending_file_path
        - 启用 _start_btn（OCR 进行中除外，保持禁用以防重入）

        截图入口与粘贴入口都应经过此方法，确保识别完成后按钮可用、
        能用界面面板选项（main 源）反复重识别。
        注意：截图首次识别的 options 仍由调用方按截图源传入，本方法
        只负责让「重新识别」可用，不改变首次识别的选项来源。
        """
        if not self._accepting_new_input():
            return
        self._invalidate_image_decodes()
        self._pending_pixmap = pixmap
        self._pending_file_path = None
        self._refresh_start_btn_enabled()
        self._update_copy_image_enabled()

    def pixmap(self):
        return self._preview_widget.pixmap()

    def _build_options_from_ui(self):
        return self._preprocess_options.get_options()

    def _check_ocr_ready(self) -> bool:
        if self._ocr_service is None and self._paddlex_service is None:
            logger.debug("OCR 服务未就绪")
            return False
        return True

    # ── OCR 执行 ──

    def run_ocr(
        self, pixmap: QPixmap, options=None, *, from_screenshot: bool = False
    ) -> None:
        """执行 OCR 识别（入口方法，由 MainWindow 调用）

        通过 RPC 后端（SyncBackendClient）调用独占 WorkerHost 的 ocr.recognize，
        不再直接 import 后端 OCR 服务（ADR §5.1）。

        异步化：本方法仅做轻量前置工作（DPR 归一、清空结果区、PNG 编码），随后把
        后端调用派发到 qasync 事件循环上（经 asyncio.to_thread 跑在线程池），
        GUI 事件循环在 OCR 期间保持响应。识别完成/失败由
        _on_ocr_async_finished / _on_ocr_async_error 回调处理，它们在 qasync loop
        上执行，可直接操作 Qt widget。

        重入守卫：识别进行中（_is_processing）再次调用会被静默忽略，避免旧结果
        覆盖新图。所有触发入口（开始/重新识别、截图确认、拖文件、粘贴）最终都汇到
        这里，一处拦截覆盖全部。

        Args:
            pixmap: 待识别图片。
            options: OCR 选项；为 None 时从界面面板读取。
            from_screenshot: 本次识别是否来自截图确认路径。为 True 时，识别完成
                (_on_ocr_finished) 会发出 bring_to_front_requested，让 MainWindow
                重新把主窗口提到前台。文件/粘贴来源传 False，避免无谓抢焦点。
        """
        # 重入守卫：异步化后事件循环在 OCR 期间照常转动，用户可能再次触发识别。
        if self._closing:
            return
        if self._is_processing:
            logger.debug("识别进行中，忽略新的 run_ocr 请求")
            return

        self._preprocessed_image_jobs.cancel_current()

        # 记录识别来源，_on_ocr_finished / _on_ocr_error 据此决定是否发前置信号。
        self._ocr_from_screenshot = from_screenshot

        self._preprocess_options.unlock_pipeline()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._result_widget.clear()

        if options is None:
            options = self._build_options_from_ui()

        pipeline_val = options.pipeline.value
        self._active_ocr_options = options

        # QPixmap 只在 GUI 线程访问；先生成脱离的 QImage 快照，
        # PNG 编码和后端 RPC 都在异步任务内执行。
        image = pixmap.toImage().copy()
        self._dispatch_image_recognize(image, pipeline_val)

    def _dispatch_recognize(self, payload: bytes, pipeline_val: str) -> None:
        """把识别请求派发到 qasync loop，并设置忙时状态。

        run_ocr（图片）与 _run_ocr_with_data（文档字节）都经此入口，统一管理
        忙时状态与 task 引用。用全局 AsyncTaskRunner 派发（而非 run_coroutine），
        因后者强制要求 running loop，测试环境 loop 仅 set 不 running；AsyncTaskRunner
        用 _get_running_or_set_loop 兜底，两者在生产环境（qasync loop running）行为
        一致，且都纳入 closeEvent 的 cancel_all 取消范围。
        """
        self._dispatch_ocr_coroutine(
            self._run_ocr_async(payload, pipeline_val), pipeline_val
        )

    def _dispatch_image_recognize(self, image: QImage, pipeline_val: str) -> None:
        self._dispatch_ocr_coroutine(
            self._prepare_image_and_run_async(image, pipeline_val), pipeline_val
        )

    def _dispatch_file_recognize(self, path: Path, pipeline_val: str) -> None:
        self._dispatch_ocr_coroutine(
            self._read_file_and_run_async(path, pipeline_val), pipeline_val
        )

    def _dispatch_ocr_coroutine(self, coro, pipeline_val: str) -> None:
        """Dispatch preparation plus recognition as one cancellable UI task."""
        from vibeocr.classic.utils.qt_async import get_async_runner

        self._recognition_started_at = time.monotonic()
        self.status_changed.emit("正在识别…")
        self.task_status_changed.emit("单次识别 · 处理中")
        self._set_processing(True)
        # 识别期间按钮转为「取消」并保持可点；各终态路径恢复文案。
        self._start_btn.setText("取消")
        self._start_btn.setEnabled(True)

        runner = get_async_runner()
        self._recognize_task = runner.run(
            coro,
            on_complete=self._on_ocr_async_finished,
            on_error=lambda exc: self._on_ocr_async_error(exc, pipeline_val),
        )
        # 兜底清理：正常完成/失败由 on_complete/on_error 清 _recognize_task，
        # 但 cancel 路径（set_closing）不触发这两个回调，故用 done_callback 确保
        # task 引用最终被释放（避免持有已完成 task 阻止下一次识别）。
        # 同时 retrieve 异常：AsyncTaskRunner.wrapped 即使调了 on_error 仍会 raise，
        # 导致 task 带异常结束；on_error 已完整处理（显示给用户），这里消费掉异常
        # 避免 "Task exception was never retrieved" 警告。
        task = self._recognize_task
        if task is not None:

            def _clear_ref(completed: asyncio.Task) -> None:
                # 消费异常（cancel 路径除外，cancelled() 取异常会抛 CancelledError）
                if not completed.cancelled():
                    try:
                        completed.exception()
                    except Exception:
                        pass
                if self._recognize_task is completed:
                    self._recognize_task = None
                # cancel 路径下 on_complete/on_error 不会被调，需手动复位忙时。
                # 正常路径下 _on_ocr_async_* 已清过，这里幂等。注入式同步
                # backend 的原生线程未返回时不复位，等 _on_native_call_finished。
                if (
                    self._is_processing
                    and completed.cancelled()
                    and not self._has_native_calls()
                ):
                    self._set_processing(False)
                    self._refresh_start_btn_enabled()
                    self._finish_cancelled_recognition()

            task.add_done_callback(_clear_ref)

    @staticmethod
    def _qimage_to_png_bytes(image: QImage) -> bytes:
        buffer = QBuffer()
        if not buffer.open(QBuffer.OpenModeFlag.ReadWrite):
            raise RuntimeError("cannot open image buffer")
        try:
            if not image.save(buffer, "PNG"):  # pyright: ignore[reportCallIssue, reportArgumentType]
                raise RuntimeError("PNG encoding failed")
            return bytes(buffer.data().data())
        finally:
            buffer.close()

    async def _prepare_image_and_run_async(self, image: QImage, pipeline_val: str):
        payload = await self._run_tracked_native_async(self._qimage_to_png_bytes, image)
        return await self._recognize_payload_async(payload, pipeline_val)

    def _prepare_image_and_run_sync(self, image: QImage, pipeline_val: str):
        payload = self._qimage_to_png_bytes(image)
        return self._call_backend_recognize(payload, pipeline_val)

    async def _read_file_and_run_async(self, path: Path, pipeline_val: str):
        payload = await self._run_tracked_native_async(path.read_bytes)
        return await self._recognize_payload_async(payload, pipeline_val)

    def _read_file_and_run_sync(self, path: Path, pipeline_val: str):
        return self._call_backend_recognize(path.read_bytes(), pipeline_val)

    async def _run_ocr_async(self, payload: bytes, pipeline_val: str):
        """异步执行后端识别调用。

        _call_backend_recognize 是同步阻塞调用（经 SyncBackendClient.recognize_sync
        → fut.result(timeout=300)），用 asyncio.to_thread 把它整个跑在线程池里，
        不阻塞 qasync loop。失败重试逻辑（restart backend）封装在同步方法内部，
        无需在此重复。
        """
        return await self._recognize_payload_async(payload, pipeline_val)

    async def _recognize_payload_async(self, payload: bytes, pipeline_val: str):
        # Explicitly injected sync backends and instance-level test seams stay
        # off the GUI thread. Production uses only the public supervisor adapter.
        if self._backend is not None or "_call_backend_recognize" in self.__dict__:
            return await self._run_tracked_native_async(
                self._call_backend_recognize, payload, pipeline_val
            )

        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter
        from vibeocr.classic.recognition_result import ocr_result_from_payload

        recognition_options = (
            self._active_ocr_options.copy(pipeline=pipeline_val)
            if self._active_ocr_options is not None
            else OCROptions.from_dict({"pipeline": pipeline_val})
        )
        entries = await get_supervisor_adapter().recognize(
            [("input", None, payload)],
            pipeline=recognition_options.to_pipeline_selection(),
        )
        if not entries or entries[0].error_code:
            raise RuntimeError(entries[0].error_code if entries else "识别结果缺失")
        return ocr_result_from_payload(entries[0].payload_type, entries[0].payload)

    async def _run_tracked_native_async(self, operation, *args):
        """追踪无法随 asyncio Task 取消的 ``to_thread`` 原生调用。"""
        done_event = threading.Event()
        with self._native_call_events_lock:
            self._native_call_events.add(done_event)

        def invoke():
            try:
                return operation(*args)
            finally:
                self._native_call_finished.emit()
                done_event.set()

        # ``run_ocr`` can be scheduled on a qasync loop that is installed but
        # advanced in short ``run_until_complete`` steps (not continuously
        # running).  The shared helper binds the concurrent future to that
        # installed loop and also keeps the native call visible to shutdown.
        from vibeocr.classic.utils.qt_async import tracked_to_thread

        return await tracked_to_thread(invoke)

    @Slot()
    def _on_native_call_finished(self) -> None:
        with self._native_call_events_lock:
            self._native_call_events = {
                event for event in self._native_call_events if not event.is_set()
            }
            has_native_calls = bool(self._native_call_events)
        task = self._recognize_task
        if (
            not has_native_calls
            and self._is_processing
            and (task is None or task.done())
        ):
            self._set_processing(False)
            self._refresh_start_btn_enabled()
            # 取消请求发出后原生线程才返回：终态复位补走取消界面路径。
            if task is not None and task.cancelled():
                self._finish_cancelled_recognition()

    def _on_ocr_async_finished(self, result) -> None:
        """异步识别完成回调（在 qasync loop 上执行）。"""
        try:
            if self._closing:
                return
            self._on_ocr_finished(result)
        finally:
            self._recognize_task = None
            self._set_processing(False)
            self._refresh_start_btn_enabled()

    def _on_ocr_async_error(self, exc: Exception, pipeline_val: str) -> None:
        """异步识别失败回调（在 qasync loop 上执行）。

        run_coroutine 的 on_error 收到的是底层异常；CancelledError（关闭取消）
        已被 AsyncTaskRunner 单独处理，不会走到这里。
        """
        try:
            if self._closing:
                return
            logger.error(f"OCR 识别失败: {exc}", exc_info=exc)
            self._on_ocr_error(
                str(exc) + self._first_use_suffix(pipeline_val, str(exc))
            )
        finally:
            self._recognize_task = None
            self._set_processing(False)
            self._refresh_start_btn_enabled()

    def _refresh_start_btn_enabled(self) -> None:
        """根据忙时状态与是否有待识别图，统一管理 _start_btn 启用状态。

        OCR 进行中不在此处改动按钮：派发时置为可用「取消」，取消请求后
        禁用，终态路径恢复「开始识别/重新识别」。识别被 Backend 卡死或
        耗时过长时用户必须能就地取消，不再无条件禁用。
        """
        if self._closing:
            self._start_btn.setEnabled(False)
            return
        if self._is_processing:
            return
        has_pending = (
            self._pending_pixmap is not None and not self._pending_pixmap.isNull()
        ) or self._pending_file_path is not None
        self._start_btn.setEnabled(has_pending)

    def _set_processing(self, processing: bool) -> None:
        super()._set_processing(processing)
        accepting = not processing and not self._closing
        self._file_btn.setEnabled(accepting)
        self._paste_btn.setEnabled(accepting)
        self._screenshot_btn.setEnabled(accepting)

    def _has_native_calls(self) -> bool:
        with self._native_call_events_lock:
            return any(not event.is_set() for event in self._native_call_events)

    def process_file(self, file_path: str) -> None:
        """处理文件（由 MainWindow 调用，支持 PDF/Office/图片）。

        Attached-aware routing: when the supervisor adapter is started, route
        image inputs through the v2 supervisor; otherwise use the legacy
        backend. PDF/Office documents always use the legacy path (MinerU
        document parsing is not yet on the v2 supervisor). Same safe
        default-switch pattern as the WinUI ViewModels.
        """
        if not self._accepting_new_input():
            logger.debug("识别进行中，忽略 process_file 请求")
            return
        from vibeocr.classic.utils.mime_types import is_document_file

        path = Path(file_path)
        if not path.exists():
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "无法识别", f"文件不存在：\n{file_path}")
            return

        if is_document_file(file_path):
            self._invalidate_image_decodes()
            self._file_btn.setEnabled(self._accepting_new_input())
            # 文档文件(PDF/Office)强制走 MinerU 文档解析，CPU 后端下不可用。
            # 在此拦截，避免进入 _run_ocr_with_data 后因管道被 GPU 门控禁用而崩溃。
            from PySide6.QtWidgets import QMessageBox

            gpu_capability = self._preprocess_options.gpu_capability
            if gpu_capability is None:
                QMessageBox.information(
                    self,
                    "GPU 能力检测中",
                    "正在检测运行时 GPU 能力，请稍候再试。",
                )
                return
            if not gpu_capability:
                QMessageBox.warning(
                    self,
                    "文档解析不可用",
                    "当前为 CPU 后端，文档解析(MinerU)需要 GPU 支持。\n"
                    "请将文件转为图片后识别，或在设置页切换到 GPU 后端后重启。",
                )
                return
            self._run_ocr_with_file(path)
        else:
            # Decode off-thread; run_ocr then submits one generic supervisor job.
            self._request_image_file_load(file_path, auto_recognize=True)

    def _run_ocr_with_data(self, data: bytes, mime_type: str, filename: str) -> None:
        """使用原始文件数据进行 OCR（文档解析管道）。

        通过 RPC 后端调用 ocr.recognize，pipeline=DOCUMENT_PARSING。

        异步化：与 run_ocr 一致，经 _dispatch_recognize 派发到 qasync loop。
        """
        # 重入守卫：与 run_ocr 共用同一忙时状态。
        if self._is_processing:
            logger.debug("识别进行中，忽略新的 _run_ocr_with_data 请求")
            return

        self._result_widget.clear()
        self._active_ocr_options = self._preprocess_options.get_options()
        self._dispatch_recognize(data, "DOCUMENT_PARSING")

    def _run_ocr_with_file(self, path: Path) -> None:
        """Read a potentially large document inside the asynchronous task."""
        if self._is_processing:
            logger.debug("识别进行中，忽略新的文档识别请求")
            return
        self._result_widget.clear()
        self._active_ocr_options = self._preprocess_options.get_options()
        self._dispatch_file_recognize(path, "DOCUMENT_PARSING")

    # -- backend bridge (v2 supervisor only) --------

    def _call_backend_recognize(self, image_data: bytes, pipeline: str):
        """Test-only injected sync backend seam; production is async v2."""
        if self._backend is None:
            raise RuntimeError("no synchronous backend is attached")
        return self._backend.recognize_sync(image_data, pipeline=pipeline)

    def recognize_via_supervisor(
        self, image_data: bytes, display_name: str = "image.png"
    ) -> int:
        """Submit a one-element recognition job through the v2 supervisor.

        Phase 7A path (coexists with the legacy sync path until the Phase 8
        atomic switch). The image is submitted as a single-item recognition
        job; results/cancel/progress arrive via the adapter's Qt signals on
        the GUI thread. Returns the adapter generation for stale-result
        scoping.

        Callers connect to the adapter's ``recognition_result`` /
        ``recognition_error`` / ``recognition_cancelled`` signals (filtered
        by ``job_id``) instead of blocking on a synchronous return value.
        """
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        adapter = get_supervisor_adapter()
        return adapter.submit_recognition([(display_name, None, image_data)])

    def _on_result_block_edited(self, index: int, new_text: str) -> None:
        """右侧结果块被编辑后同步更新数据模型。

        表格块的 new_text 是新的 ``<table>`` HTML（见 JS _finishTableEdit），
        其数据源是 ``content_list`` 的 ``table_body``，处理逻辑与左侧网格编辑
        一致，故直接委托给 ``_on_table_block_edited``，复用其 table_body 更新、
        set_content_list（保持块类型模式）、update_block_text(HTML) 等正确流程。
        """
        if not self._current_ocr_result or index < 0:
            return
        result = self._current_ocr_result

        if not result.content_list or index >= len(result.content_list):
            return

        cl_block = result.content_list[index]
        # 表格块委托给表格专用同步逻辑（举一反三：与左侧网格编辑同一数据源）
        if cl_block.get("type", "") == "table":
            self._on_table_block_edited(index, new_text)
            return

        old_text = cl_block.get("text", "")
        if old_text == new_text:
            return

        # 更新 content_list
        cl_block["text"] = new_text
        block_type = cl_block.get("type", "text")
        if block_type == "list" and "list_items" in cl_block:
            cl_block["list_items"] = new_text.split("\n")
        elif block_type == "code":
            cl_block["code_body"] = new_text

        # 查找并更新对应的 text_block
        for tb in result.text_blocks:
            if getattr(tb, "content_index", None) == index:
                tb.text = new_text
                tb.is_manually_edited = True
                if tb.content_index is not None and tb.content_index < len(
                    result.text_with_scores
                ):
                    score = result.text_with_scores[tb.content_index][1]
                    result.text_with_scores[tb.content_index] = (new_text, score)
                break

        # 全量重建 raw_text，避免 str.replace 子串误匹配。
        # 结构化结果（has_content_list）保持原 "\n".join 行为；纯文本结果走后处理器，
        # 保证手动改某块后重建的 raw_text 与识别时排版规则一致。
        if self._requires_async_result_rebuild(result):
            if self._preview_widget:
                if result.has_content_list:
                    self._preview_widget.set_content_list(result.content_list)
                else:
                    self._preview_widget.set_text_blocks(result.text_blocks)
            self._result_widget.invalidate_snapshot()
            self._schedule_text_result_rebuild(result, old_text, new_text)
            return
        if result.has_content_list:
            result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)
        else:
            text_opts = self._text_options_widget.get_text_options()
            result.raw_text = TextBlockProcessor.process(
                result.text_blocks, text_opts, result.image_height
            )

        # 同步更新 markdown_text / html_text
        if old_text:
            if result.markdown_text and old_text in result.markdown_text:
                result.markdown_text = result.markdown_text.replace(
                    old_text, new_text, 1
                )
            if result.html_text and old_text in result.html_text:
                result.html_text = result.html_text.replace(old_text, new_text, 1)

        # 刷新左侧 overlay（显示手动修改标记）。
        # 结构化结果（表格/公式/MinerU）左侧在块类型模式渲染，必须用
        # set_content_list 保持该模式；否则切到置信度模式会让块类型着色与
        # 编辑状态错位（右侧变黄但左侧无变化）。
        if self._preview_widget:
            if result.has_content_list:
                self._preview_widget.set_content_list(result.content_list)
            else:
                self._preview_widget.set_text_blocks(result.text_blocks)

    def _on_text_options_changed(self, _options) -> None:
        """「文本块处理」选项变化 → 实时重排当前结果。

        仅对识别时即为纯文本的结果生效（_plain_text_at_recognition=True）：
        结构化结果（表格/公式/MinerU）走块类型渲染，不读 raw_text，重排无意义
        且会破坏复制/导出链路（误改其 raw_text）。

        重排后重算 raw_text / markdown_text，并刷新结果区（块间排版会随
        换行模式/空格/缩进/去空白块选项变化）。结果区用 display_text_layout
        整体渲染（而非逐块），使排版变化在屏幕上可见。
        """
        result = self._current_ocr_result
        if result is None or not getattr(self, "_plain_text_at_recognition", False):
            return

        text_opts = self._text_options_widget.get_text_options()
        result.raw_text = TextBlockProcessor.process(
            result.text_blocks, text_opts, result.image_height
        )
        # markdown_text 对纯文本结果即 raw_text（见各 pipeline 的 `or raw_text` 兜底），
        # 同步以保持复制 MD / 导出的一致性。
        result.markdown_text = result.raw_text
        # 用按选项排版的整体渲染刷新右侧（而非逐块的 _display_result），
        # 使换行模式/空格/缩进可见。左侧预览保持置信度模式（块级编辑入口仍在）。
        if self._result_widget is not None:
            self._result_widget.display_text_layout(result, text_opts)

    def _on_ocr_finished(self, result) -> None:
        """OCR 完成回调"""
        # 异步化后，识别完成时窗口可能已在关闭流程中（_result_widget 已 cleanup），
        # 此时写入已销毁的 web view 会崩溃。set_closing(True) 会先取消 task，
        # 这里作为双重保险。
        if self._closing:
            return
        self._current_ocr_result = result
        self._start_btn.setText("重新识别")

        # 文本块后处理：仅对纯文本结果应用（结构化结果走块类型渲染，不读 raw_text）。
        # 改写 raw_text 后，下游的 _display_result / 复制 / 手动编辑重建均读 raw_text，自动一致。
        # 同时记录识别时的纯文本标志，供 _on_text_options_changed 实时重排判断：
        # 注意 has_content_list 必须在 _display_result 之前读，否则通用 OCR 会被
        # _build_content_list 回填成 content_list 而误判为结构化结果。
        self._plain_text_at_recognition = not result.has_content_list
        if self._plain_text_at_recognition:
            text_opts = self._text_options_widget.get_text_options()
            result.raw_text = TextBlockProcessor.process(
                result.text_blocks, text_opts, result.image_height
            )

        char_count = len(result.raw_text) if result.raw_text else 0
        block_count = len(result.text_with_scores)
        logger.info(f"OCR 完成: {block_count} 个文本块, {char_count} 个字符")
        status = self._build_recognition_status(
            result, self._take_recognition_elapsed_seconds()
        )
        self.status_changed.emit(status)
        self.result_status_changed.emit(status)

        # 预处理改变了图像时，用预处理后的图像更新预览
        self._preprocessed_image_jobs.cancel_current()
        if result.preprocessed_image:
            payload = bytes(result.preprocessed_image)
            self._preprocessed_image_jobs.submit(
                lambda cancel_event: decode_image_bytes(payload, cancel_event)
            )

        # 设置文本块到预览（置信度模式）
        self._preview_widget.set_text_blocks(result.text_blocks)

        # 显示结果。纯文本结果用按选项排版的整体渲染（display_text_layout），
        # 使换行模式/空格/缩进在识别完成即可见；结构化结果走 _display_result
        # 的块类型渲染。二者都会为左侧预览回填 content_list（块级编辑入口）。
        if self._plain_text_at_recognition and self._result_widget is not None:
            text_opts = self._text_options_widget.get_text_options()
            # 纯文本路径只渲染一次（display_text_layout），不再先 display_result。
            # 此前先 display_result（文档 token A）再 display_text_layout（token B）
            # 的双重渲染会作废 A：两次渲染之间点「复制文本」，异步 JS 回调返回
            # 旧 token A，命中 _on_web_copy_payload 的「结果已刷新」toast。
            # 这里仅做 _display_result 的准备（重置 rebuild/取消后台 job/清状态）
            # 并回填 content_list / 同步预览（_apply_content_index，不渲染 WebEngine），
            # 随后用 display_text_layout 渲染一次。
            self._pending_text_layout = None
            self._prepare_result_display_state(result)
            self._apply_content_index(result, self._build_content_list(result))
            self._result_widget.display_text_layout(result, text_opts)
        else:
            self._pending_text_layout = None
            self._display_result(result)

        # 识别成功后折叠选项面板，让结果区获得最大空间（失败路径不折叠，
        # 保留选项可见方便调整重试；用户可随时点标题重新展开）。
        if self._preprocess_options is not None:
            self._preprocess_options.set_collapsed(True)
        if self._text_options_widget is not None:
            self._text_options_widget.set_collapsed(True)

        # 截图来源识别完成 → 通知 MainWindow 重新把主窗口提到前台。
        # 异步识别可能耗时数秒（首次还需下载模型），期间用户/系统切走窗口后，
        # OCR 开始前那次激活已失效，故在此再次前置。发出后复位标记，避免
        # 后续手动「重新识别」（文件来源语义）误触发抢焦点。
        if self._ocr_from_screenshot:
            self._ocr_from_screenshot = False
            self.bring_to_front_requested.emit()

    def _on_content_list_ready(self, result) -> None:
        pending = self._pending_text_layout
        if pending is None or pending[0] is not result or self._closing:
            return
        self._pending_text_layout = None
        self._result_widget.display_text_layout(result, pending[1])

    @Slot(int, object)
    def _on_preprocessed_image_loaded(self, _generation: int, result: object) -> None:
        if self._closing or not isinstance(result, QImage) or result.isNull():
            return
        pixmap = QPixmap.fromImage(result)
        if not pixmap.isNull():
            self._preview_widget.set_pixmap(pixmap)

    @Slot(int, str)
    def _on_preprocessed_image_load_failed(self, _generation: int, error: str) -> None:
        if not self._closing:
            logger.warning("预处理图片解码失败: %s", error)

    def _first_use_suffix(self, pipeline_val: str, error_text: str = "") -> str:
        """首次使用失败时返回追加提示。

        依赖类错误（dependency/缺少依赖/DependencyError）优先给依赖修复提示，
        而非误导性的"下载模型"——模型下载解决不了依赖缺失，反而让用户白等。
        """
        # 依赖缺失特征词（覆盖 PaddleX DependencyError / 本项目 TableDependencyError）
        lowered = error_text.lower()
        if any(
            k in lowered
            for k in (
                "dependency",
                "依赖",
                "缺少依赖",
                "paddlex[ocr]",
                "additional dependencies",
                "tabledependencyerror",
            )
        ):
            return "\n\n提示：检测到依赖缺失，请在「设置 → 重装 OCR 依赖」修复后重试。"

        if pipeline_val in (
            "OCR",
            "PP-StructureV3",
            "TABLE_RECOGNITION",
            "FORMULA_RECOGNITION",
        ):
            return "\n\n提示：首次使用可能需要下载模型，请保持网络畅通后重试。"
        return ""

    def _on_ocr_error(self, error_msg: str) -> None:
        """OCR 失败回调"""
        # 同 _on_ocr_finished，关闭流程中不再触碰可能已销毁的 widget。
        if self._closing:
            return
        # 失败不复位标记会让下次识别误判来源，显式复位。
        self._ocr_from_screenshot = False
        self._current_ocr_result = None
        self._start_btn.setText("开始识别")
        self._result_widget.clear()
        self._result_widget._ensure_web_view().setHtml(
            f"<p style='color:#f44336;'>识别失败：{error_msg}</p>"
        )
        elapsed = self._take_recognition_elapsed_seconds()
        status = "识别失败"
        if elapsed is not None:
            status += f" · 耗时 {self._format_elapsed(elapsed)}"
        self.status_changed.emit(status)
        self.result_status_changed.emit(status)

    def _take_recognition_elapsed_seconds(self) -> float | None:
        """读取并清除本轮端到端识别计时，避免复用上一轮耗时。"""
        started_at = self._recognition_started_at
        self._recognition_started_at = None
        if started_at is None:
            return None
        return max(0.0, time.monotonic() - started_at)

    @classmethod
    def _build_recognition_status(
        cls, result: object, elapsed_seconds: float | None
    ) -> str:
        """生成紧凑、可核对且不夸大成功的识别摘要。

        文本框与低置信数量都从同一组实际展示块计算，避免
        ``low_confidence_items`` 与 ``text_blocks`` 来源不同导致分母对不上。
        空结果明确提示“未识别到文本”；只有文本但后端未给框时也不伪造 0 框。
        """
        raw_blocks = getattr(result, "text_blocks", None) or []
        visible_blocks = [
            block
            for block in raw_blocks
            if str(getattr(block, "text", "") or "").strip()
        ]

        # 老结果或个别管道可能只返回 text_with_scores，用它作为可信的兼容回退。
        scored_items = getattr(result, "text_with_scores", None) or []
        if visible_blocks:
            block_count = len(visible_blocks)
            low_count = sum(
                1
                for block in visible_blocks
                if cls._is_low_confidence(getattr(block, "score", None))
            )
        elif scored_items:
            non_empty_items = [
                (text, score) for text, score in scored_items if str(text or "").strip()
            ]
            block_count = len(non_empty_items)
            low_count = sum(
                1 for _, score in non_empty_items if cls._is_low_confidence(score)
            )
        else:
            block_count = 0
            low_count = 0

        raw_text = str(getattr(result, "raw_text", "") or "").strip()
        if block_count:
            parts = [
                f"识别到 {block_count} 个文本框",
                f"低置信（<80%）{low_count} 个",
            ]
        elif raw_text:
            parts = ["识别完成（未返回文本框统计）"]
        else:
            parts = ["未识别到文本"]

        if elapsed_seconds is not None:
            parts.append(f"耗时 {cls._format_elapsed(elapsed_seconds)}")
        return " · ".join(parts)

    @classmethod
    def _is_low_confidence(cls, score: object) -> bool:
        if not isinstance(score, (int, float, str)):
            return False
        try:
            return float(score) < cls._LOW_CONFIDENCE_THRESHOLD
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _format_elapsed(elapsed_seconds: float) -> str:
        if elapsed_seconds < 1:
            return f"{max(1, round(elapsed_seconds * 1000))} 毫秒"
        if elapsed_seconds < 10:
            return f"{elapsed_seconds:.2f} 秒"
        return f"{elapsed_seconds:.1f} 秒"

    def show_waiting_message(self, message: str) -> None:
        """在结果面板显示等待提示（预加载排队时调用）"""
        self._result_widget._ensure_web_view().setHtml(
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'height:100%;color:#666;font-size:14px;">'
            f"<p>{message}</p></div>"
        )
