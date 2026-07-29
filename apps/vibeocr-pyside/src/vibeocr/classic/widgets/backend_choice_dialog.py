"""首启 GPU/CPU 后端选择 + 安装进度合并对话框"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend import env_manager
from vibeocr.classic.utils.dialog_workers import track_dialog_worker
from vibeocr.classic.widgets.install_dialog import InstallWorker

if TYPE_CHECKING:
    from pathlib import Path


class _GpuDetectSignals(QObject):
    finished = Signal(dict)


class _GpuDetectTask(QRunnable):
    def __init__(self, detector) -> None:
        super().__init__()
        self._detector = detector
        self.signals = _GpuDetectSignals()

    def run(self) -> None:
        try:
            info = self._detector()
        except Exception:
            info = {"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None}
        try:
            self.signals.finished.emit(info)
        except RuntimeError:
            # 对话框可能已在硬件探测结束前关闭。
            pass


class BackendChoiceDialog(QDialog):
    """首启后端选择对话框（合并安装进度）

    顶部 GPU/CPU 单选 + 体积/速度提示，点"开始安装"后显示进度区跑安装。
    无 NVIDIA GPU 时 GPU 选项禁用。
    """

    install_succeeded = Signal()

    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._worker: InstallWorker | None = None
        self._has_gpu = False
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
        self._setup_ui()
        self._detect_and_set_default()

    def _setup_ui(self) -> None:
        self.setWindowTitle("选择 OCR 推理后端")
        self.setMinimumSize(520, 480)
        # 非模态：设置页重装时不阻塞主窗口（首启路径由 main_window.exec() 调起，
        # exec() 自身是模态事件循环，与 setModal 无关，首启仍阻塞，符合预期）。
        self.setModal(False)

        layout = QVBoxLayout(self)

        # 硬件信息展示区（在选项上方，让用户知情后选择）
        self._hw_label = QLabel("正在检测硬件…")
        self._hw_label.setWordWrap(True)
        self._hw_label.setStyleSheet(
            "QLabel { background: #f5f5f5; padding: 8px; border-radius: 4px; }"
        )
        layout.addWidget(self._hw_label)

        # 后端选择区
        choice_group = QGroupBox("选择推理后端")
        choice_layout = QVBoxLayout(choice_group)

        self._radio_group = QButtonGroup(self)
        self._gpu_radio = QRadioButton("GPU 加速（推荐）")
        self._cpu_radio = QRadioButton("CPU 模式")
        self._radio_group.addButton(self._gpu_radio)
        self._radio_group.addButton(self._cpu_radio)
        choice_layout.addWidget(self._gpu_radio)
        choice_layout.addWidget(self._cpu_radio)

        self._hint_label = QLabel(
            "GPU：约 1.5GB，识别更快，需 NVIDIA GPU\n"
            "CPU：约 150MB，兼容性广，无需独立显卡"
        )
        self._hint_label.setWordWrap(True)
        choice_layout.addWidget(self._hint_label)
        layout.addWidget(choice_group)

        # 进度区（初始隐藏）
        self._progress_label = QLabel("")
        self._progress_label.setVisible(False)
        layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setVisible(False)
        layout.addWidget(self._log_text)

        # 按钮
        btn_layout = QHBoxLayout()
        self._install_button = QPushButton("开始安装")
        self._install_button.clicked.connect(self._on_install_clicked)
        # 取消按钮（安装进行中显示，触发协作式取消）
        self._cancel_button = QPushButton("取消安装")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.setVisible(False)
        btn_layout.addStretch()
        btn_layout.addWidget(self._install_button)
        btn_layout.addWidget(self._cancel_button)
        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.reject)
        self._close_button.setVisible(False)
        btn_layout.addWidget(self._close_button)
        layout.addLayout(btn_layout)

    def _detect_and_set_default(self) -> None:
        self._install_button.setEnabled(False)
        self._gpu_detect_task = _GpuDetectTask(env_manager.detect_gpu_info)
        self._gpu_detect_task.signals.finished.connect(self._apply_gpu_info)
        QThreadPool.globalInstance().start(self._gpu_detect_task)

    @Slot(dict)
    def _apply_gpu_info(self, info: dict) -> None:
        self._has_gpu = bool(info["has_gpu"])
        cuda = info["cuda"]

        # 展示检测到的硬件信息（GPU 型号/显存/CUDA 或未检测到）
        if self._has_gpu:
            name = info.get("name") or "NVIDIA GPU"
            vram = int(info.get("vram_mb") or 0)  # type: ignore[arg-type]
            vram_str = f"{vram // 1024}GB" if vram >= 1024 else f"{vram}MB"
            cuda_str = f"CUDA {cuda}" if cuda else "CUDA 版本未知"
            self._hw_label.setText(
                f"✅ 检测到 GPU：{name}（{vram_str}），{cuda_str}\n"
                f"建议选择 GPU 加速以获得更快的识别速度。"
            )
        else:
            self._hw_label.setText(
                "⚠️ 未检测到符合 CUDA 条件的 NVIDIA GPU。\n"
                "将使用 CPU 模式（文档解析 MinerU 与 VL 模型将不可用）。"
            )

        if self._has_gpu:
            self._gpu_radio.setChecked(True)
        else:
            self._gpu_radio.setEnabled(False)
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._cpu_radio.setChecked(True)
        self._install_button.setEnabled(True)
        self._gpu_detect_task = None

    def selected_backend(self) -> str:
        return "gpu" if self._gpu_radio.isChecked() else "cpu"

    def _on_install_clicked(self) -> None:
        # 锁定选择区，显示进度
        self._gpu_radio.setEnabled(False)
        self._cpu_radio.setEnabled(False)
        self._install_button.setEnabled(False)
        self._install_button.setVisible(False)
        self._cancel_button.setVisible(True)
        self._progress_label.setVisible(True)
        self._progress_bar.setVisible(True)
        self._log_text.setVisible(True)

        backend = self.selected_backend()
        self._log(f"选择后端：{backend.upper()}，开始安装...")

        self._worker = InstallWorker(
            self._project_root,
            force_backend=backend,
            reinstall_python=self._reinstall_python,
            missing_only=self._missing_only,
        )
        track_dialog_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()

    def _on_cancel_clicked(self) -> None:
        """取消按钮：确认后协作式取消安装。"""
        reply = QMessageBox.question(
            self,
            "取消安装",
            "确定要取消安装吗？\n已下载的内容会保留，下次可继续补装缺失依赖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._worker and self._worker.isRunning():
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("正在取消...")
            self._log("用户取消安装，正在停止当前任务（可能需要数秒）...")
            self._worker.request_cancel()
        else:
            self._cancel_button.setVisible(False)

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        self._progress_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)
        if success:
            self._progress_label.setText("安装成功！")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("完成")
            self.install_succeeded.emit()
            self.done(1)
        else:
            self._progress_label.setText("安装失败")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            # 失败弹窗：展示详情 + 提示增量重试
            QMessageBox.warning(
                self,
                "依赖安装失败",
                f"{message}\n\n"
                "可点击「补充安装缺失依赖」按钮重试（已安装的依赖会自动跳过）。",
            )
            self.done(0)

    def _log(self, msg: str) -> None:
        self._log_text.append(msg)
        sb = self._log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event) -> None:
        """关闭事件：协作式取消安装，绝不强杀线程（避免孤儿 pip 进程）。"""
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        event.accept()

    def request_shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        self.close()
