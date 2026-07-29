"""批量识别标签页

提供批量文件识别功能，三栏布局：文件列表 | 文件预览 | 识别结果。
"""

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QThread, QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend.models.ocr_options import OCROptions
from vibeocr.classic.pyside.batch_budget import (
    BatchBudget,
    BatchEntry,
    image_pixel_count,
    partition_batches,
)
from vibeocr.classic.ui import theme
from vibeocr.classic.utils.export_jobs import (
    BatchExportReport,
    ExportItem,
    ExportSaveJob,
    export_batch_operation,
    snapshot_ocr_result,
)
from vibeocr.classic.views.background_tasks import FunctionTask
from vibeocr.classic.views.tabs.base_tab import BaseOcrTab
from vibeocr.classic.widgets.batch_file_list_widget import BatchFileListWidget
from vibeocr.classic.widgets.export_settings_widget import ExportSettingsWidget
from vibeocr.classic.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.classic.widgets.preview_widget import PreviewWidget
from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

# 向后兼容别名
PreprocessOptions = OCROptions

logger = logging.getLogger(__name__)


# BatchRecognitionTab 可能在统一关闭预算耗尽后先被销毁。worker 没有 QWidget
# parent，必须独立保活到原生 QThread.finished，避免析构仍运行的 QThread。
_ACTIVE_BATCH_WORKERS: set = set()


class BatchRecognitionWorker(QThread):
    """批量识别工作线程"""

    progress = Signal(int, int, str)  # completed, total, current_file
    file_completed = Signal(str, str, object)  # file_path, status, result
    file_snapshot_ready = Signal(str, object)  # file_path, immutable export DTO
    # 业务终态与 QThread.finished 分离。后者只表示线程已经真正退出，UI 只能在
    # 收到 QThread.finished 后释放 worker 引用。
    terminal = Signal(str, dict)  # status, results
    error = Signal(str)
    native_stopped = Signal(object)

    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_PARTIAL_FAILED = "partial_failed"

    def __init__(
        self,
        service,
        files: list[dict],
        preprocess_options: PreprocessOptions,
        parent=None,
        *,
        batch_budget: BatchBudget | None = None,
    ):
        super().__init__(parent)
        self._service = service
        self._files = files
        self._preprocess_options = preprocess_options
        self._cancelled = False
        self._batch_budget = batch_budget or BatchBudget.ocr_default()
        self._terminal_status: str | None = None
        self._results: dict = {}
        self.finished.connect(self._on_native_finished)

    def start(self, priority=QThread.Priority.InheritPriority) -> None:  # type: ignore[override]
        _ACTIVE_BATCH_WORKERS.add(self)
        try:
            super().start(priority)
        except Exception:
            _ACTIVE_BATCH_WORKERS.discard(self)
            raise

    def _on_native_finished(self) -> None:
        """仅在原生线程结束后释放全局保活并通知仍存活的 GUI。"""
        _ACTIVE_BATCH_WORKERS.discard(self)
        self.native_stopped.emit(self)
        self.deleteLater()

    @property
    def terminal_status(self) -> str | None:
        """返回本次运行的业务终态。"""
        return self._terminal_status

    @property
    def results(self) -> dict:
        """返回已产生的结果快照。"""
        return dict(self._results)

    def run(self):
        """执行批量识别，并保证每次运行都产生一个明确业务终态。

        This worker is retained only for the legacy backend during migration.
        The supervisor path is submitted once by the tab and never enters the
        UI-side partition loop below.
        """
        try:
            if self._cancelled:
                raise InterruptedError("批量识别已取消")

            if self._cancelled:
                raise InterruptedError("批量识别已取消")
            results, completed, total, failed = self._run_batches()
        except InterruptedError:
            results = dict(self._results)
            completed = len(results)
            total = len(self._files)
            failed = sum("error" in item for item in results.values())
        except Exception as exc:  # 防止意外异常绕过终态和 UI 清理
            # 冷启动期间可能与关闭/取消并发；此时取消终态优先，不弹伪错误。
            if not self._cancelled:
                logger.exception("批量识别线程异常终止")
                self.error.emit(str(exc))
            results = dict(self._results)
            completed = len(results)
            total = len(self._files)
            failed = (
                sum("error" in item for item in results.values())
                if self._cancelled
                else max(1, sum("error" in item for item in results.values()))
            )

        if self._cancelled:
            status = self.STATUS_CANCELLED
            self.progress.emit(completed, total, "已取消")
        elif failed:
            status = self.STATUS_PARTIAL_FAILED
            self.progress.emit(completed, total, f"完成（{failed} 个失败）")
        else:
            status = self.STATUS_COMPLETED
            self.progress.emit(total, total, "完成")

        self._results = results
        self._terminal_status = status
        self.terminal.emit(status, dict(results))

    def _run_batches(self) -> tuple[dict, int, int, int]:
        """执行分批识别，返回 results/completed/total/failed。

        旧实现用逐文件 batch_add（每文件一次 SHM 往返）+ batch_commit 流式回调，
        N 个文件 = 2N+1 次消息交换。改为 recognize_batch（RCBG 单次往返）后，
        N 个文件只需按预算后的批次数次往返，IPC 开销降一个数量级。

        recognize_batch 阻塞返回 list（无流式回调），故按 16 个/批切片，
        现同时按文件数、编码字节数、解码像素数切片；每批完成即逐文件发
        file_completed + progress，保持 UI 流式反馈。
        取消在批边界检查 _cancelled（协作式，单批 predict 进行中不可中断）。
        """
        results: dict = {}
        total = len(self._files)
        completed = 0
        failed = 0
        entries: list[BatchEntry[dict]] = []
        for file_info in self._files:
            path = Path(file_info["path"])
            try:
                encoded_bytes = path.stat().st_size
            except OSError:
                encoded_bytes = 0
            entries.append(
                BatchEntry(
                    value=file_info,
                    encoded_bytes=encoded_bytes,
                    pixels=image_pixel_count(path),
                )
            )

        batches = partition_batches(entries, self._batch_budget)
        for batch_index, chunk in enumerate(batches):
            if self._cancelled:
                break

            batch_files = chunk.values
            logger.info(
                "提交图片 OCR 批次",
                extra={
                    "batch": {
                        "index": batch_index,
                        "items": len(batch_files),
                        "encoded_bytes": chunk.encoded_bytes,
                        "pixels": chunk.pixels,
                        "oversized_single": chunk.oversized_single,
                    }
                },
            )

            # 读文件 bytes（读取失败的单文件标记 failed，不影响整批）
            images: list[bytes | None] = []
            read_errors: dict[int, str] = {}  # batch 内索引 -> 错误
            for bi, file_info in enumerate(batch_files):
                try:
                    with open(file_info["path"], "rb") as f:
                        images.append(f.read())
                except Exception as e:
                    logger.error(f"读取文件失败 {file_info['path']}: {e}")
                    images.append(None)
                    read_errors[bi] = str(e)

            # 识别有效图像
            valid_indices = [bi for bi, img in enumerate(images) if img is not None]
            batch_results: list = [None] * len(valid_indices)
            if self._cancelled:
                break
            if valid_indices:
                valid_images = [images[bi] for bi in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._service.recognize_batch(
                        valid_images, self._preprocess_options
                    )
                except Exception as e:
                    if self._cancelled:
                        break
                    logger.error("批量识别失败(batch=%d): %s", batch_index, e)
                    self.error.emit(str(e))
                    # 识别整批失败：有效文件使用 RPC 错误；本批读取失败文件仍
                    # 保留各自 I/O 错误，确保终态计数覆盖整批。
                    for bi, file_info in enumerate(batch_files):
                        file_path = file_info["path"]
                        error = read_errors.get(bi, str(e))
                        self.file_completed.emit(file_path, "failed", {"error": error})
                        results[file_path] = {
                            "file_path": file_path,
                            "error": error,
                        }
                        failed += 1
                        completed += 1
                        self.progress.emit(
                            completed,
                            total,
                            f"失败: {Path(file_path).name}",
                        )
                    # 继续下一批（单批失败不中断整体）
                    continue

            # cancel() 可能在 recognize_batch 阻塞期间到达。此时不得把返回结果
            # 再包装成“全部完成”，也不得继续下一批。
            if self._cancelled:
                break

            # 逐文件上报结果（保持 UI 流式反馈）
            result_iter = iter(batch_results)
            for bi, file_info in enumerate(batch_files):
                if self._cancelled:
                    break
                file_path = file_info["path"]
                if file_path in results:
                    # 已在 read_errors 或整批失败中报告
                    completed += 1
                    self.progress.emit(completed, total, Path(file_path).name)
                    continue
                if bi in read_errors:
                    self.file_completed.emit(
                        file_path, "failed", {"error": read_errors[bi]}
                    )
                    results[file_path] = {
                        "file_path": file_path,
                        "error": read_errors[bi],
                    }
                    failed += 1
                else:
                    # 取对应识别结果（valid_indices 顺序与 batch_results 一致）
                    try:
                        res = next(result_iter)
                    except StopIteration:
                        res = None
                    if res is None:
                        self.file_completed.emit(
                            file_path, "failed", {"error": "识别失败"}
                        )
                        results[file_path] = {
                            "file_path": file_path,
                            "error": "识别失败",
                        }
                        failed += 1
                    else:
                        # Capture the immutable export payload while the result is
                        # still exclusively owned by this worker.  The GUI receives
                        # this signal before file_completed, so exporting never has
                        # to race a mutable OCRResult or deep-copy it on the UI thread.
                        result_snapshot = snapshot_ocr_result(
                            res,
                            include_content_list=True,
                            include_images=False,
                            include_text_blocks=False,
                        )
                        self.file_snapshot_ready.emit(file_path, result_snapshot)
                        self.file_completed.emit(file_path, "completed", res)
                        results[file_path] = {
                            "file_path": file_path,
                            "result": res,
                        }
                completed += 1
                self.progress.emit(completed, total, Path(file_path).name)

        self._results = results
        return results, completed, total, failed

    def cancel(self):
        """取消处理（协作式）。

        设置 _cancelled 标志，run() 的批循环在下一个批边界检查并停止。
        batch_cancel 仍调用以兼容 service 层（若底层有 batch_commit 路径仍可中断）；
        对 recognize_batch 路径，当前批 predict 不可抢占，完成后即停止。
        """
        self._cancelled = True
        with contextlib.suppress(Exception):
            self._service.batch_cancel()


class BatchRecognitionTab(BaseOcrTab):
    """批量识别标签页

    三栏布局：文件列表 | 文件预览 | 识别结果
    """

    SPLITTER_ID = "batch_tab_v2"

    STATE_IDLE = "idle"
    STATE_RUNNING = "running"
    STATE_CANCELLING = "cancelling"
    STATE_SHUTDOWN = "shutdown"

    def __init__(self, ocr_service=None, parent=None, *, backend=None):
        super().__init__(parent)
        self._ocr_service = ocr_service  # MinerUBatchService
        self._paddlex_service = None  # OCRServiceSubprocess
        self._backend = backend
        self._batch_backend = None
        self._worker: BatchRecognitionWorker | None = None
        self._submission_task: FunctionTask | None = None
        self._supervisor_adapter = None
        self._supervisor_job_id: str | None = None
        self._supervisor_generation = 0
        self._supervisor_files: list[dict] = []
        self._supervisor_results: dict = {}
        self._export_job: ExportSaveJob | None = None
        self._export_generation = 0
        self._export_mode = ""
        self._has_document_files = False
        self._run_state = self.STATE_IDLE
        self._run_total = 0
        self._last_terminal_status: str | None = None
        self._shutting_down = False
        self._layout_manager = None
        self._current_file_path: str = ""
        self._result_snapshots: dict[str, object] = {}

        self._setup_ui()
        self._connect_signals()
        self._init_options_from_preferences(batch=True)

    def _setup_ui(self):
        """设置三栏 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        # 主分割器（三栏）
        self._splitter = QSplitter()

        # ── 左侧面板：文件列表 + 识别选项+操作 + 导出设置 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(0)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._file_list_widget = BatchFileListWidget()
        left_layout.addWidget(self._file_list_widget, stretch=3)

        self._preprocess_options = PreprocessOptionsWidget()
        left_layout.addWidget(self._preprocess_options)

        # 操作区：开始/取消按钮 + 进度
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(8, 4, 8, 4)

        self._start_btn = QPushButton("开始识别")
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)

        self._progress_label = QLabel("0/0")
        self._progress_label.setStyleSheet(
            f"color: {theme.Colors.accent}; font-weight: bold;"
        )

        action_layout.addWidget(self._start_btn)
        action_layout.addWidget(self._cancel_btn)
        action_layout.addStretch()
        action_layout.addWidget(self._progress_label)

        left_layout.addLayout(action_layout)

        self._export_widget = ExportSettingsWidget()
        left_layout.addWidget(self._export_widget)

        self._splitter.addWidget(left_panel)

        # ── 中间面板：文件预览 ──
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setSpacing(4)
        center_layout.setContentsMargins(0, 0, 0, 0)

        preview_label = QLabel("文件预览")
        preview_label.setStyleSheet(
            f"font-weight: bold; color: {theme.Colors.text_muted};"
        )
        center_layout.addWidget(preview_label)

        self._preview_widget = PreviewWidget(empty_text="选择文件以预览")
        center_layout.addWidget(self._preview_widget, stretch=1)

        self._splitter.addWidget(center_panel)

        # ── 右侧面板：识别结果（独占） ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(4)
        right_layout.setContentsMargins(0, 0, 0, 0)

        result_label = QLabel("识别结果")
        result_label.setStyleSheet(
            f"font-weight: bold; color: {theme.Colors.text_muted};"
        )
        right_layout.addWidget(result_label)

        self._result_widget = ResultViewWidget(utility_client=self._backend)
        right_layout.addWidget(self._result_widget, stretch=1)

        self._splitter.addWidget(right_panel)

        # 设置分割比例 [280, 45%, 45%]
        self._splitter.setSizes([280, 450, 450])

        layout.addWidget(self._splitter, stretch=1)

        self.setLayout(layout)

    def _connect_signals(self):
        """连接信号"""
        self._setup_hover_sync()

        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._file_list_widget.selection_changed.connect(self._on_file_selected)
        self._file_list_widget.files_changed.connect(self._on_files_changed)
        self._export_widget.export_requested.connect(self._on_export_current)
        self._export_widget.export_all_requested.connect(self._on_export_all)
        self._result_widget.snapshot_ready.connect(self._on_render_snapshot_ready)

    def _on_files_changed(self, files: list[dict]) -> None:
        """文件列表变化时，根据是否包含文档文件锁定管道"""
        from vibeocr.backend.utils.mime_types import is_document_file

        live_paths = {item["path"] for item in files}
        self._result_snapshots = {
            path: snapshot
            for path, snapshot in self._result_snapshots.items()
            if path in live_paths
        }
        has_document = any(is_document_file(f["path"]) for f in files)
        self._has_document_files = has_document
        if has_document:
            # 这里只消费 MainWindow 后台探测后写入的三态缓存，绝不能在文件
            # 变化这一 GUI 槽内 shell-out。None 表示探测尚未完成。
            gpu_capability = self._preprocess_options.gpu_capability
            if gpu_capability is False:
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "文档解析不可用",
                    "当前为 CPU 后端，文档解析(MinerU)需要 GPU 支持。\n"
                    "请移除文档文件，或在设置页切换到 GPU 后端后重启。",
                )
            reason = (
                "正在检测 GPU 能力，文档解析暂未就绪"
                if gpu_capability is None
                else "队列含文档文件，仅支持文档解析"
            )
            self._preprocess_options.lock_to_document_parsing(
                reason
            )
            if gpu_capability is None:
                self._progress_label.setText("正在检测 GPU 能力，文档解析暂未就绪")
        else:
            self._preprocess_options.unlock_pipeline()

    def refresh_gpu_capability(self) -> None:
        """Refresh document-queue status after the shared async probe resolves."""
        if not self._has_document_files or self._shutting_down:
            return
        capability = self._preprocess_options.gpu_capability
        if capability is None:
            reason = "正在检测 GPU 能力，文档解析暂未就绪"
            status = reason
        elif capability:
            reason = "队列含文档文件，仅支持文档解析"
            status = f"0/{self._file_list_widget.get_pending_count()}"
        else:
            reason = "CPU 后端不支持文档解析"
            status = reason
        self._preprocess_options.lock_to_document_parsing(reason)
        if self._worker is None and self._export_job is None:
            self._progress_label.setText(status)

    def _on_start(self):
        """开始识别"""
        # QThread 对象必须保留到原生 finished 信号到达。在 cancelling 或线程
        # 刚退出但 finished 尚未派发的窗口内，一律禁止重入。
        if (
            self._shutting_down
            or self._worker is not None
            or self._submission_task is not None
            or self._supervisor_job_id is not None
            or self._export_job is not None
        ):
            return

        files = self._file_list_widget.get_selected_files()
        if not files:
            self._result_widget.clear()
            return

        if self._has_document_files:
            gpu_capability = self._preprocess_options.gpu_capability
            if gpu_capability is not True:
                if gpu_capability is None:
                    title = "文档解析检测中"
                    message = "正在检测 GPU 能力，请稍候再开始文档解析。"
                else:
                    title = "文档解析不可用"
                    message = (
                        "当前为 CPU 后端，文档解析(MinerU)需要 GPU 支持。\n"
                        "请移除文档文件，或在设置页切换到 GPU 后端后重启。"
                    )
                QMessageBox.information(self, title, message)
                return
            # 不预探测/预下载 MinerU 模型：mineru-api 不依赖模型即可启动，模型在
            # 首次解析时由 mineru 自己按需下载。我们只保证识别超时够长
            # （MinerU HTTP 总超时 30 分钟），失败由 _recognize_one_mineru 兜底。

        preprocess_options = self._preprocess_options.get_options()
        # An injected adapter is consumed directly.  Cold shared-client startup is
        # deferred to BatchRecognitionWorker.run so clicking Start cannot block Qt.
        service = self._batch_backend

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._run_state = self.STATE_RUNNING
        self._run_total = len(files)
        self._last_terminal_status = None
        for file_info in files:
            self._result_snapshots.pop(file_info["path"], None)

        self._progress_label.setText(f"0/{len(files)}")

        self._result_widget.clear()

        try:
            from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

            supervisor = get_supervisor_adapter()
        except Exception:
            supervisor = None
        if supervisor is not None and supervisor.is_started:
            self._start_supervisor_batch(
                supervisor, files, preprocess_options
            )
            return

        worker = BatchRecognitionWorker(service, files, preprocess_options)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.file_snapshot_ready.connect(self._on_file_snapshot_ready)
        worker.file_completed.connect(self._on_file_completed)
        worker.terminal.connect(self._on_terminal)
        worker.error.connect(self._on_error)
        worker.native_stopped.connect(self._on_worker_stopped)
        worker.start()

    def _on_file_snapshot_ready(self, file_path: str, snapshot: object) -> None:
        """Store a worker-produced immutable export payload on the GUI thread."""
        if self._is_current_worker_signal():
            self._result_snapshots[file_path] = snapshot

    def submit_batch_via_supervisor(self, entries: list[tuple[str, bytes]]) -> int:
        """Submit all batch inputs as ONE logical recognition job via v2.

        Phase 7A path. The plan requires "Batch tab 一次提交逻辑 job, 不在 UI
        切 GPU 微批": instead of the UI slicing into transport/compute
        microbatches, the whole input list is handed to the supervisor as a
        single recognition job; the supervisor owns budgeting and the UI only
        observes progress/cancel via the adapter's Qt signals.

        ``entries`` is a list of ``(display_name, image_bytes)``. Returns the
        adapter generation for stale-result scoping.
        """
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter
        from vibeocr.runtime_contracts import JobPriority

        adapter = get_supervisor_adapter()
        uploads = [(name, None, data) for name, data in entries]
        return adapter.submit_recognition(uploads, priority=JobPriority.BACKGROUND)

    def _start_supervisor_batch(
        self, adapter, files: list[dict], options: OCROptions
    ) -> None:
        """Read inputs off-thread, then submit one logical supervisor job."""
        self._bind_supervisor_adapter(adapter)
        self._supervisor_generation += 1
        generation = self._supervisor_generation
        self._supervisor_files = []
        self._supervisor_results = {}

        def load_inputs() -> tuple[list[tuple[dict, bytes]], list[tuple[dict, str]]]:
            loaded: list[tuple[dict, bytes]] = []
            failed: list[tuple[dict, str]] = []
            for file_info in files:
                try:
                    loaded.append(
                        (file_info, Path(file_info["path"]).read_bytes())
                    )
                except OSError as exc:
                    failed.append((file_info, str(exc)))
            return loaded, failed

        task = FunctionTask(load_inputs)
        self._submission_task = task
        task.signals.finished.connect(
            lambda result: self._submit_loaded_supervisor_inputs(
                generation, adapter, options, result
            )
        )
        task.signals.error.connect(
            lambda error: self._fail_supervisor_submission(generation, error)
        )
        QThreadPool.globalInstance().start(task)

    def _bind_supervisor_adapter(self, adapter) -> None:
        if self._supervisor_adapter is adapter:
            return
        previous = self._supervisor_adapter
        if previous is not None:
            for signal, slot in (
                (previous.recognition_submitted, self._on_supervisor_submitted),
                (previous.recognition_progress, self._on_supervisor_progress),
                (previous.recognition_result, self._on_supervisor_result),
                (previous.recognition_error, self._on_supervisor_error),
                (previous.recognition_cancelled, self._on_supervisor_cancelled),
            ):
                with contextlib.suppress(RuntimeError):
                    signal.disconnect(slot)
        self._supervisor_adapter = adapter
        adapter.recognition_submitted.connect(self._on_supervisor_submitted)
        adapter.recognition_progress.connect(self._on_supervisor_progress)
        adapter.recognition_result.connect(self._on_supervisor_result)
        adapter.recognition_error.connect(self._on_supervisor_error)
        adapter.recognition_cancelled.connect(self._on_supervisor_cancelled)

    def _submit_loaded_supervisor_inputs(
        self,
        generation: int,
        adapter,
        options: OCROptions,
        result: object,
    ) -> None:
        self._submission_task = None
        if generation != self._supervisor_generation or self._shutting_down:
            return
        loaded, read_failures = result  # type: ignore[misc]
        for file_info, error in read_failures:
            path = file_info["path"]
            self._file_list_widget.update_file_status(
                path, "failed", {"error": error}
            )
            self._supervisor_results[path] = {
                "file_path": path,
                "error": error,
            }
        if not loaded:
            self._finish_supervisor_batch()
            return

        from vibeocr.runtime_contracts import JobPriority, PipelineSelection
        from vibeocr.runtime_contracts.contracts.pipelines import (
            get_pipeline_supported_options,
        )

        option_payload = options.to_dict()
        pipeline = options.pipeline
        pipeline_id = (
            pipeline.value if hasattr(pipeline, "value") else str(pipeline)
        )
        allowed = set(get_pipeline_supported_options(pipeline))
        semantic_options = {
            name: value
            for name, value in option_payload.items()
            if name in allowed and value is not None
        }
        self._supervisor_files = [file_info for file_info, _data in loaded]
        uploads = [
            (Path(file_info["path"]).name, None, data)
            for file_info, data in loaded
        ]
        # submit_recognition 同步抛异常会逃出 Qt slot，导致 _run_state 卡在
        # STATE_RUNNING、Start 按钮永久禁用。捕获后走统一失败路径，复位状态。
        try:
            adapter.submit_recognition(
                uploads,
                priority=JobPriority.BACKGROUND,
                pipeline=PipelineSelection(
                    pipeline_id=pipeline_id,
                    options=semantic_options,
                ),
            )
        except Exception as exc:
            self._fail_supervisor_submission(generation, str(exc))

    def _fail_supervisor_submission(
        self, generation: int, error: str
    ) -> None:
        self._submission_task = None
        if generation != self._supervisor_generation or self._shutting_down:
            return
        self._on_supervisor_error("", error)

    def _on_supervisor_submitted(self, job_id: str) -> None:
        if self._run_state not in (
            self.STATE_RUNNING,
            self.STATE_CANCELLING,
        ):
            return
        self._supervisor_job_id = job_id
        if self._run_state == self.STATE_CANCELLING:
            self._supervisor_adapter.cancel(job_id)

    def _on_supervisor_progress(
        self, job_id: str, current: int, total: int
    ) -> None:
        if job_id != self._supervisor_job_id:
            return
        self._progress_label.setText(f"{current}/{total}")

    def _on_supervisor_result(self, job_id: str, entries: list) -> None:
        if job_id != self._supervisor_job_id or self._shutting_down:
            return
        from vibeocr.backend.models import ocr_result_from_payload

        for index, file_info in enumerate(self._supervisor_files):
            path = file_info["path"]
            entry = entries[index] if index < len(entries) else {}
            error_code = entry.get("error_code")
            payload = entry.get("payload") or {}
            if error_code or not payload:
                error = error_code or "识别结果缺失"
                self._file_list_widget.update_file_status(
                    path, "failed", {"error": error}
                )
                self._supervisor_results[path] = {
                    "file_path": path,
                    "error": error,
                }
                continue
            result = ocr_result_from_payload(payload)
            self._result_snapshots[path] = snapshot_ocr_result(
                result,
                include_content_list=True,
                include_images=False,
                include_text_blocks=False,
            )
            self._file_list_widget.update_file_status(
                path, "completed", result
            )
            self._supervisor_results[path] = {
                "file_path": path,
                "result": result,
            }
            if path == self._current_file_path:
                self._display_result(result)
                self._export_widget.set_current_result(result)
        self._finish_supervisor_batch()

    def _on_supervisor_error(self, job_id: str, message: str) -> None:
        if job_id and job_id != self._supervisor_job_id:
            return
        if self._run_state not in (
            self.STATE_RUNNING,
            self.STATE_CANCELLING,
        ):
            return
        for file_info in self._supervisor_files:
            path = file_info["path"]
            if path in self._supervisor_results:
                continue
            self._file_list_widget.update_file_status(
                path, "failed", {"error": message}
            )
            self._supervisor_results[path] = {
                "file_path": path,
                "error": message,
            }
        self._finish_supervisor_batch()

    def _on_supervisor_cancelled(self, job_id: str) -> None:
        if job_id != self._supervisor_job_id:
            return
        self._apply_terminal(
            BatchRecognitionWorker.STATUS_CANCELLED,
            self._supervisor_results,
        )
        self._reset_supervisor_run()

    def _finish_supervisor_batch(self) -> None:
        failed = any(
            "error" in result for result in self._supervisor_results.values()
        )
        status = (
            BatchRecognitionWorker.STATUS_PARTIAL_FAILED
            if failed
            else BatchRecognitionWorker.STATUS_COMPLETED
        )
        self._apply_terminal(status, self._supervisor_results)
        self._reset_supervisor_run()

    def _reset_supervisor_run(self) -> None:
        self._supervisor_job_id = None
        self._supervisor_files = []
        self._run_state = (
            self.STATE_SHUTDOWN if self._shutting_down else self.STATE_IDLE
        )
        self._start_btn.setEnabled(not self._shutting_down)
        self._cancel_btn.setEnabled(False)

    def _on_cancel(self):
        """请求取消；线程真正结束前不释放引用、也不允许重新开始。"""
        export_job = self._export_job
        if export_job is not None:
            self._export_generation += 1
            self._cancel_btn.setEnabled(False)
            self._progress_label.setText("正在取消导出…")
            export_job.cancel()
            return

        if (
            self._submission_task is not None
            or self._supervisor_job_id is not None
        ):
            self._run_state = self.STATE_CANCELLING
            self._start_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            self._progress_label.setText("正在取消…")
            if self._supervisor_job_id is not None:
                self._supervisor_adapter.cancel(self._supervisor_job_id)
            return

        worker = self._worker
        if worker is None or self._run_state != self.STATE_RUNNING:
            return

        self._run_state = self.STATE_CANCELLING
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._progress_label.setText("正在取消…")
        worker.cancel()

    def _is_current_worker_signal(self) -> bool:
        """过滤已经释放/替换的 worker 迟到信号。"""
        if self._shutting_down:
            return False
        sender = self.sender()
        return sender is None or sender is self._worker

    def _on_progress(self, completed: int, total: int, current_file: str):
        """进度更新"""
        if not self._is_current_worker_signal():
            return
        if self._run_state == self.STATE_CANCELLING and current_file != "已取消":
            return
        self._progress_label.setText(
            f"{completed}/{total} {current_file}"
            if current_file
            else f"{completed}/{total}"
        )

    def _on_file_completed(self, file_path: str, status: str, result):
        """单个文件完成"""
        if not self._is_current_worker_signal():
            return
        self._file_list_widget.update_file_status(file_path, status, result)

        # 如果是当前选中的文件，刷新显示
        if file_path == self._current_file_path and status == "completed" and result:
            self._display_result(result)
            self._export_widget.set_current_result(result)

    def _on_render_snapshot_ready(self, result: object, snapshot: object) -> None:
        """Refresh the cache after an edit has been re-rendered in the result view."""
        if self._shutting_down or snapshot is None or not self._current_file_path:
            return
        for item in self._file_list_widget._files:
            if item["path"] == self._current_file_path and item.get("result") is result:
                self._result_snapshots[self._current_file_path] = snapshot
                return

    def _on_terminal(self, status: str, results: dict):
        """记录业务终态；引用释放仍等待 QThread.finished。"""
        if not self._is_current_worker_signal():
            return
        self._apply_terminal(status, results)

    def _apply_terminal(self, status: str, results: dict) -> None:
        """将明确的 completed/cancelled/partial_failed 终态呈现到 UI。"""
        self._last_terminal_status = status
        completed = len([r for r in results.values() if "result" in r])
        failed = len([r for r in results.values() if "error" in r])

        if status == BatchRecognitionWorker.STATUS_CANCELLED:
            processed = completed + failed
            self._progress_label.setText(f"{processed}/{self._run_total} 已取消")
            logger.info("批量处理已取消: %d 成功, %d 失败", completed, failed)
        elif status == BatchRecognitionWorker.STATUS_PARTIAL_FAILED:
            self._progress_label.setText(
                f"{completed + failed}/{self._run_total} 完成，{failed} 个失败"
            )
            logger.warning("批量处理部分失败: %d 成功, %d 失败", completed, failed)
        else:
            self._progress_label.setText(f"{self._run_total}/{self._run_total} 完成")
            logger.info("批量处理完成: %d 成功", completed)

        self._cancel_btn.setEnabled(False)

    def _on_worker_stopped(self, worker: BatchRecognitionWorker) -> None:
        """QThread 已真实退出后，才释放引用并恢复可启动状态。"""
        if worker is not self._worker:
            return

        if self._last_terminal_status is None and not self._shutting_down:
            status = (
                worker.terminal_status or BatchRecognitionWorker.STATUS_PARTIAL_FAILED
            )
            self._apply_terminal(status, worker.results)

        self._release_worker(worker)

    def _release_worker(self, worker: BatchRecognitionWorker) -> None:
        if worker is not self._worker or worker.isRunning():
            return
        self._worker = None
        self._run_state = (
            self.STATE_SHUTDOWN if self._shutting_down else self.STATE_IDLE
        )
        self._start_btn.setEnabled(not self._shutting_down)
        self._cancel_btn.setEnabled(False)

    def _on_error(self, error_msg: str):
        """记录单批错误；worker 会继续处理，故不得重置 UI。"""
        logger.error("Batch recognition error: %s", error_msg)

    def _on_file_selected(self, file_path: str):
        """文件选择变更：加载预览和结果"""
        if self._shutting_down:
            return
        self._current_file_path = file_path

        # 加载文件预览
        self._preview_widget.load_file(file_path)

        # 查找并显示结果
        files = self._file_list_widget._files
        for f in files:
            if f["path"] == file_path:
                result = f.get("result")
                # 只有真正的 OCRResult（带 markdown_text 属性）才可显示。
                # 失败项的 result 是 {"error": ...} dict（update_file_status 在
                # status="failed" 时存入），没有 markdown_text；若直接交给
                # _display_result，_reset_text_rebuild_state 访问 result.markdown_text
                # 会抛 AttributeError。非 OCRResult 与 pending 一样走清空。
                if getattr(result, "markdown_text", None) is not None:
                    self._display_result(result)
                    self._export_widget.set_current_result(result)
                else:
                    self._result_widget.clear()
                break

    # ── 导出功能 ──

    def _on_export_current(self, fmt: str, result) -> None:
        """导出当前文件"""
        if not result or self._export_job is not None or self._worker is not None:
            return

        snapshot = self._result_widget.current_snapshot()
        if snapshot is None or self._result_widget.get_result() is not result:
            # A large result may still be producing its immutable render/export
            # payload.  Do not fall back to a live model reference.
            self._progress_label.setText("正在准备导出数据…")
            return
        export_dir = self._export_widget.get_export_dir(self._current_file_path)
        item = ExportItem(
            source_name=Path(self._current_file_path).name,
            result=snapshot,
            output_dir=Path(export_dir),
            export_format=fmt,
        )
        self._start_export_job((item,), mode="current")

    def _on_export_all(self, fmt: str) -> None:
        """导出全部已完成的文件"""
        if self._export_job is not None or self._worker is not None:
            return

        files = self._file_list_widget._files
        completed_files = [
            f for f in files if f["status"] == "completed" and f.get("result")
        ]

        if not completed_files:
            QMessageBox.information(self, "提示", "没有可导出的结果")
            return

        items_list: list[ExportItem] = []
        for file_info in completed_files:
            snapshot = self._result_snapshots.get(file_info["path"])
            if snapshot is None:
                self._progress_label.setText("正在准备导出数据…")
                return
            items_list.append(
                ExportItem(
                    source_name=file_info["name"],
                    result=snapshot,
                    output_dir=Path(
                        self._export_widget.get_export_dir(file_info["path"])
                    ),
                    export_format=fmt,
                )
            )
        items = tuple(items_list)
        self._start_export_job(items, mode="all")

    def _start_export_job(self, items: tuple[ExportItem, ...], *, mode: str) -> None:
        """Consume pre-detached immutable inputs and execute RPC/write in worker."""
        if self._shutting_down or self._export_job is not None:
            return
        source_items = tuple(items)
        backend = self._backend

        def export(cancel_event, progress):
            return export_batch_operation(backend, source_items)(cancel_event, progress)

        self._export_generation += 1
        job = ExportSaveJob(export)
        job.setProperty("generation", self._export_generation)
        self._export_job = job
        self._export_mode = mode
        self._export_widget.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress_label.setText(f"0/{len(source_items)} 正在导出")
        job.progress.connect(self._on_export_progress)
        job.completed.connect(self._on_export_completed)
        job.failed.connect(self._on_export_failed)
        job.cancelled.connect(self._on_export_cancelled)
        job.stopped.connect(self._on_export_job_finished)
        job.start()

    def _is_current_export_signal(self) -> bool:
        job = self.sender()
        return bool(
            not self._shutting_down
            and job is self._export_job
            and job.property("generation") == self._export_generation
        )

    def _on_export_progress(self, completed: int, total: int, name: str) -> None:
        if self._is_current_export_signal():
            self._progress_label.setText(f"{completed}/{total} {name}")

    def _on_export_completed(self, report: BatchExportReport) -> None:
        if not self._is_current_export_signal():
            return
        if self._export_mode == "current":
            exported = report.files[0]
            if exported.success:
                QMessageBox.information(
                    self, "导出成功", f"已导出到:\n{exported.actual_path}"
                )
            else:
                QMessageBox.warning(
                    self, "导出失败", f"导出失败:\n{exported.actual_path}"
                )
            return

        msg = f"导出完成: {report.success_count} 成功"
        if report.fail_count:
            msg += f", {report.fail_count} 失败"
        if report.renamed:
            renamed = [
                f"{item.requested_path.name} → {item.actual_path.name}"
                for item in report.renamed
            ]
            msg += "\n\n以下文件因同名已自动重命名:\n" + "\n".join(renamed)
        QMessageBox.information(self, "导出结果", msg)

    def _on_export_failed(self, error: str) -> None:
        if self._is_current_export_signal():
            QMessageBox.warning(self, "导出失败", f"导出失败:\n{error}")

    def _on_export_cancelled(self) -> None:
        if self._is_current_export_signal():
            self._progress_label.setText("导出已取消")

    def _on_export_job_finished(self, job: ExportSaveJob) -> None:
        if job is not self._export_job:
            return
        # drain() 期间没有事件派发时，也能从线程快照恢复最终进度。
        if not self._shutting_down and job.status == ExportSaveJob.STATUS_CANCELLED:
            self._progress_label.setText("导出已取消")
        self._export_job = None
        self._export_mode = ""
        self._export_widget.setEnabled(not self._shutting_down)
        self._start_btn.setEnabled(not self._shutting_down)
        self._cancel_btn.setEnabled(False)
        job.deleteLater()

    def _reset_ui(self):
        """兼容性 UI 复位；运行中的 worker 永远不能由此释放。"""
        if self._worker is not None:
            return
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._run_state = self.STATE_IDLE

    def drain(self, timeout_ms: int = 0) -> bool:
        """只做有界等待并返回状态；可安全地由非 GUI 关闭线程调用。

        所有 QWidget 更新、worker 引用释放和 deleteLater 均由原生 finished
        派发到 GUI 线程的槽完成。timeout_ms=0 仅探测，不进入事件循环。
        """
        import time

        deadline = time.monotonic() + max(0, timeout_ms) / 1000

        def remaining_ms() -> int:
            return max(0, int((deadline - time.monotonic()) * 1000))

        all_stopped = True
        all_stopped = self.drain_base_jobs(remaining_ms()) and all_stopped
        all_stopped = self._preview_widget.drain(remaining_ms()) and all_stopped
        export_job = self._export_job
        if export_job is not None:
            all_stopped = export_job.drain(remaining_ms()) and all_stopped
        submission = self._submission_task
        if submission is not None:
            all_stopped = submission.is_drained() and all_stopped
        if self._supervisor_job_id is not None:
            all_stopped = False

        worker = self._worker
        if worker is not None:
            if QThread.currentThread() is worker:
                all_stopped = False
            else:
                if worker.isRunning() and remaining_ms() > 0:
                    worker.wait(remaining_ms())
                all_stopped = not worker.isRunning() and all_stopped

        # ResultViewWidget 的 drain 同样只等待其确认导出，不改按钮/引用。
        return self._result_widget.drain(remaining_ms()) and all_stopped

    def request_shutdown(self) -> None:
        """GUI 阶段：冻结界面并只发协作取消，不等待任何线程。"""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.request_base_shutdown()
        self._export_generation += 1
        self._result_widget.set_closing(True)
        self._preview_widget.request_shutdown()
        self._export_widget.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)

        export_job = self._export_job
        if export_job is not None:
            export_job.cancel()
        worker = self._worker
        if worker is not None:
            self._run_state = self.STATE_CANCELLING
            worker.cancel()
        elif self._supervisor_job_id is not None:
            self._run_state = self.STATE_CANCELLING
            self._supervisor_adapter.cancel(self._supervisor_job_id)
        elif self._submission_task is not None:
            self._run_state = self.STATE_CANCELLING
        else:
            self._run_state = self.STATE_SHUTDOWN

    def is_drained(self) -> bool:
        """Non-blocking probe for the application shutdown state machine."""
        return self.drain(0)

    def closeEvent(self, event) -> None:
        """Standalone tabs use the same cooperative shutdown path as MainWindow."""
        self.request_shutdown()
        super().closeEvent(event)

    def shutdown(self, timeout_ms: int = 1000) -> bool:
        """兼容入口：GUI 请求阶段后，在同一预算内做纯等待。"""
        self.request_shutdown()
        return self.drain(timeout_ms)

    def set_layout_manager(self, layout_manager) -> None:
        """设置布局管理器并恢复分割器状态"""
        self._layout_manager = layout_manager
        if self._layout_manager and hasattr(self, "_splitter"):
            state = self._layout_manager.get_splitter_state(self.SPLITTER_ID)
            if state:
                self._splitter.restoreState(state)

    def save_layout(self) -> None:
        """保存分割器状态"""
        if self._layout_manager and hasattr(self, "_splitter"):
            self._layout_manager.set_splitter_state(
                self.SPLITTER_ID, self._splitter.saveState()
            )
