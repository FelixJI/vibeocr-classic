"""后端切换进度对话框（重启时消费 pending_backend）"""

from __future__ import annotations

import logging
import subprocess  # noqa: TC003  类型注解用，与 install_dialog 一致
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend import env_manager
from vibeocr.classic.utils.dialog_workers import track_dialog_worker

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class SwitchWorker(QThread):
    """后端切换工作线程（协作式取消，复用 InstallWorker 范式）"""

    progress = Signal(str, str)  # (stage, message)
    completed = Signal(bool, str)  # (success, message)

    def __init__(self, project_root: Path, target: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._target = target
        # 协作式取消：替代危险的 QThread.terminate()。
        # cancel_event 被 set 后，env_manager.switch_paddle_backend 内的
        # _run_pip / Popen.wait 会检测到并中止；request_cancel 还会立即
        # kill 当前 pip 子进程，避免孤儿进程。
        self._cancel_event = threading.Event()
        # 当前正在运行的子进程句柄（由 on_proc 回调设置），
        # request_cancel 时立即 kill，避免孤儿 pip 进程。
        self._current_proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

    def _on_proc(self, proc: subprocess.Popen) -> None:
        """记录当前子进程句柄（供 request_cancel kill）"""
        with self._proc_lock:
            self._current_proc = proc

    def request_cancel(self) -> None:
        """协作式取消后端切换（线程安全）。

        1. set cancel_event → switch_paddle_backend 内的 Popen.wait/pip 检测到后中止；
        2. 立即 kill 当前 pip 子进程（若有），避免它成为孤儿继续后台运行。
        调用方（对话框 closeEvent）应在 request_cancel 后用 wait(timeout)
        等待 worker 自然结束，而非用 terminate() 强杀。
        """
        self._cancel_event.set()
        with self._proc_lock:
            proc = self._current_proc
        if proc is not None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:
        try:
            self.progress.emit("网络检测", "正在检测网络环境...")
            from vibeocr.backend.network_detector import NetworkDetector

            network_type = NetworkDetector(self._project_root).network_type

            self.progress.emit("后端切换", f"正在切换到 {self._target.upper()}...")
            success, msg = env_manager.switch_paddle_backend(
                self._project_root,
                self._target,
                network_type,
                progress_callback=lambda stage, message: self.progress.emit(
                    stage, message
                ),
                cancel_event=self._cancel_event,
                on_proc=self._on_proc,
            )
            self.completed.emit(success, msg)
        except Exception as e:
            logger.error("后端切换异常: %s", e)
            self.completed.emit(False, f"切换异常: {e}")


class SwitchDialog(QDialog):
    """后端切换进度对话框（重启时消费 pending_backend）"""

    switch_succeeded = Signal()

    def __init__(
        self, project_root: Path, target: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._target = target
        self._worker: SwitchWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        name = "GPU" if self._target == "gpu" else "CPU"
        self.setWindowTitle(f"切换到 {name} 后端")
        self.setMinimumSize(500, 400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self._title_label = QLabel(f"正在切换到 {name} 后端...")
        layout.addWidget(self._title_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self._progress_bar)

        self._stage_label = QLabel("准备中...")
        layout.addWidget(self._stage_label)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(False)
        layout.addWidget(self._close_button)

    def showEvent(self, event) -> None:
        """显示事件 - 开始切换"""
        super().showEvent(event)
        if not self._worker:
            self._start()

    def _start(self) -> None:
        self._log("开始切换后端...")
        self._worker = SwitchWorker(self._project_root, self._target)
        track_dialog_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        self._progress_bar.setVisible(False)
        if success:
            self._title_label.setText("切换成功!")
            self._stage_label.setText("后端已切换，即将启动 OCR 服务")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self.switch_succeeded.emit()
            self.done(1)
        else:
            self._title_label.setText("切换失败")
            self._stage_label.setText("切换过程中出现错误")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            self.done(0)

    def _log(self, message: str) -> None:
        self._log_text.append(message)
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        """Request cancellation and return immediately; registry owns the worker."""
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        event.accept()

    def request_shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        self.close()
