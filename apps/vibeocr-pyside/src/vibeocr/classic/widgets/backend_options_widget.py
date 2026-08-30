"""设置页「推理后端」组件。

物理 GPU 只决定选项是否可用；实际运行后端以 Runtime Installer ``inspect``
返回的 accelerator + 组件 desired_state 为权威，并区分三态：GPU profile /
CPU profile / 仅基础 Runtime（未选择加速框架）。切换请求交给设置页控制器，
在用户二次确认后通过可见、可取消的安装对话框执行。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.hardware_probe import detect_gpu_info
from vibeocr.classic.runtime_installation import RuntimeInstallerClient
from vibeocr.classic.runtime_status_messages import (
    accelerator_framework,
    cuda_requirement_label,
    cuda_requirement_version,
)
from vibeocr.classic.ui import theme

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtGui import QCloseEvent

logger = logging.getLogger(__name__)


def _cuda_at_least(available: str, required: str) -> bool:
    """比较两个 "major.minor" CUDA 版本；无法解析时视为不满足。"""

    def _key(value: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in value.split("."))
        except ValueError:
            return ()

    key_a, key_b = _key(available), _key(required)
    return bool(key_a) and bool(key_b) and key_a >= key_b


class _GpuDetectWorker(QThread):
    """后台 GPU 探测 worker。

    ``detect_gpu_info`` 会调用 ``nvidia-smi``，在有 NVIDIA GPU 的机器上可能
    耗时显著。放到后台线程避免阻塞
    设置页控件构造（进而避免阻塞应用启动——该控件在 MainWindow.__init__ 的
    _connect_signals 链中被构造）。探测完成后通过信号把 info dict 回主线程。
    """

    finished_info = Signal(dict)  # detect_gpu_info() 的返回值

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        try:
            info = detect_gpu_info(cancel_event=self._cancelled)
        except Exception:
            # detect_gpu_info 自身有兜底，理论上不抛；防御性捕获避免线程静默挂起。
            logger.exception("[BackendOptions] 后台 GPU 探测异常")
            info = {"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None}
        if self._cancelled.is_set():
            return
        try:
            inspection = RuntimeInstallerClient(self._project_root).inspect()
            # base_ready 覆盖「仅基础 Runtime 已安装」：完整 profile 判定 ready
            # 会把基础态误报成"尚未安装"。
            runtime_installed = bool(
                getattr(inspection, "base_ready", False)
                or getattr(inspection, "ready", False)
            )
            runtime_accelerator = (
                getattr(inspection, "accelerator", None) if runtime_installed else None
            )
            runtime_components = tuple(getattr(inspection, "components", ()) or ())
            runtime_profile = (
                getattr(inspection, "profile", "") if runtime_installed else ""
            )
        except Exception:
            logger.exception("[BackendOptions] Runtime Installer 状态读取异常")
            runtime_installed = False
            runtime_accelerator = None
            runtime_components = ()
            runtime_profile = ""
        if self._cancelled.is_set():
            return
        info["runtime_ready"] = runtime_installed
        info["runtime_accelerator"] = runtime_accelerator
        info["runtime_profile"] = runtime_profile
        info["runtime_components"] = runtime_components
        # 与旧字段语义一致：是否实际安装了 GPU 框架（基础 Runtime 不算）。
        info["runtime_has_gpu"] = (
            accelerator_framework(runtime_accelerator, runtime_components) == "gpu"
        )
        self.finished_info.emit(dict(info))


_ACTIVE_GPU_DETECT_WORKERS: set[_GpuDetectWorker] = set()


def _release_gpu_detect_worker(worker: _GpuDetectWorker) -> None:
    """释放模块级保活引用；可由自然完成或非 GUI drain 重复调用。"""
    _ACTIVE_GPU_DETECT_WORKERS.discard(worker)
    worker.deleteLater()


class BackendOptionsWidget(QWidget):
    """推理后端设置组件"""

    backend_change_requested = Signal(str)
    gpu_capability_resolved = Signal(bool)

    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        *,
        gpu_capability_callback: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._has_gpu = False
        self._current: str | None = None
        self._runtime_installed = False
        self._runtime_profile = ""
        self._change_in_progress = False
        self._detect_worker: _GpuDetectWorker | None = None
        self._detect_generation = 0
        self._refresh_after_detection = False
        self._closing = False
        self._setup_ui()
        if gpu_capability_callback is not None:
            self.gpu_capability_resolved.connect(gpu_capability_callback)
        self._show_detecting_state()
        self._start_gpu_detection()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        group = QGroupBox("推理后端")
        group_layout = QVBoxLayout(group)

        self._current_label = QLabel("当前后端：检测中...")
        group_layout.addWidget(self._current_label)

        # 硬件信息展示（GPU 型号/显存/CUDA 或未检测到）
        # 探测完成前显示"检测中..."，由 _apply_detected_state 回填。
        self._hw_label = QLabel("硬件检测中...")
        self._hw_label.setWordWrap(True)
        self._hw_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        group_layout.addWidget(self._hw_label)

        # 单选（放进 QButtonGroup 确保互斥）
        self._radio_group = QButtonGroup(self)
        radio_layout = QHBoxLayout()
        self._gpu_radio = QRadioButton("GPU 加速（推荐）")
        self._gpu_radio.setToolTip(
            "通常需要下载数 GB 依赖，识别更快，需兼容的 NVIDIA GPU"
        )
        self._cpu_radio = QRadioButton("CPU 模式")
        self._cpu_radio.setToolTip("完整文档解析 profile 通常超过 1 GB，兼容性较广")
        # 探测完成前禁用，避免基于未知硬件状态误操作后端切换。
        self._gpu_radio.setEnabled(False)
        self._cpu_radio.setEnabled(False)
        self._radio_group.addButton(self._gpu_radio)
        self._radio_group.addButton(self._cpu_radio)
        radio_layout.addWidget(self._gpu_radio)
        radio_layout.addWidget(self._cpu_radio)
        radio_layout.addStretch()
        group_layout.addLayout(radio_layout)

        # 提示文字
        self._hint_label = QLabel(
            "GPU：需要 NVIDIA GPU，完整 profile（CUDA 12.6）通常需下载数 GB，识别更快\n"
            "CPU：兼容性较广，完整 profile 通常超过 1 GB；两者均在基础 Runtime 之上安装"
        )
        self._hint_label.setWordWrap(True)
        group_layout.addWidget(self._hint_label)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        group_layout.addWidget(self._status_label)

        self._apply_button = QPushButton("切换并安装…")
        self._apply_button.clicked.connect(self._apply)
        group_layout.addWidget(self._apply_button)

        layout.addWidget(group)
        layout.addStretch()

        # 单选变化时更新应用按钮状态
        self._gpu_radio.toggled.connect(self._update_apply_state)

    def _show_detecting_state(self) -> None:
        self._current = None
        self._runtime_installed = False
        self._runtime_profile = ""
        self._current_label.setText("当前后端：检测中...")
        self._hw_label.setText("硬件检测中...")
        self._status_label.setText("")
        self._gpu_radio.setEnabled(False)
        self._cpu_radio.setEnabled(False)
        self._apply_button.setEnabled(False)

    def _start_gpu_detection(self) -> None:
        """启动后台线程探测 GPU，完成后回填 UI。"""
        self._detect_generation += 1
        generation = self._detect_generation
        worker = _GpuDetectWorker(self._project_root)
        self._detect_worker = worker
        _ACTIVE_GPU_DETECT_WORKERS.add(worker)
        worker.finished_info.connect(
            lambda info, worker=worker, generation=generation: (
                self._apply_detected_state_if_current(worker, generation, info)
            )
        )
        worker.finished.connect(
            lambda worker=worker, generation=generation: (
                self._on_gpu_detection_finished(worker, generation)
            )
        )
        worker.finished.connect(
            lambda worker=worker: _release_gpu_detect_worker(worker)
        )
        worker.start()

    def _apply_detected_state_if_current(
        self, worker: _GpuDetectWorker, generation: int, info: dict[str, Any]
    ) -> None:
        if (
            self._closing
            or generation != self._detect_generation
            or worker is not self._detect_worker
        ):
            return
        self._apply_detected_state(info)

    def _on_gpu_detection_finished(
        self, worker: _GpuDetectWorker, generation: int
    ) -> None:
        del generation
        if worker is self._detect_worker:
            self._detect_worker = None
            if self._refresh_after_detection and not self._closing:
                self._refresh_after_detection = False
                self._change_in_progress = False
                self._show_detecting_state()
                self._start_gpu_detection()

    def request_gpu_detection_shutdown(self) -> None:
        """Request hardware-probe cancellation without blocking the GUI thread."""
        self._closing = True
        self._refresh_after_detection = False
        self._detect_generation += 1
        worker = self._detect_worker
        if worker is None:
            return
        if worker.isRunning():
            if hasattr(worker, "cancel"):
                worker.cancel()
            worker.quit()

    def drain_gpu_detection(self, timeout_ms: int) -> bool:
        """Wait for hardware detection within the caller's shared shutdown budget."""
        worker = self._detect_worker
        if worker is None:
            return True
        stopped = not worker.isRunning() or worker.wait(max(0, timeout_ms))
        if not stopped:
            logger.warning("[BackendOptions] GPU detection worker did not stop")
            return False
        self._detect_worker = None
        _release_gpu_detect_worker(worker)
        return True

    def is_gpu_detection_drained(self) -> bool:
        """Non-blocking GUI-thread probe used by application shutdown."""
        # Requiring the GUI finished callback to clear the reference also proves
        # that no queued lambda still captures this QWidget owner.
        return self._detect_worker is None

    def shutdown_gpu_detection(self, timeout_ms: int = 3000) -> bool:
        """Compatibility entry point for standalone widget shutdown."""
        self.request_gpu_detection_shutdown()
        return self.drain_gpu_detection(timeout_ms)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.request_gpu_detection_shutdown()
        super().closeEvent(event)

    def _apply_detected_state(self, info: dict[str, Any]) -> None:
        """后台 GPU 探测完成后，在主线程回填 _has_gpu 与硬件展示、启用控件。

        ``_current`` 只来自 Runtime Installer 的已验证 accelerator，并区分三态：
        GPU profile / CPU profile / 仅基础 Runtime（未选择加速框架）。实时硬件
        探测仅决定 GPU 选项是否可用和展示硬件信息，不能冒充实际安装 profile。

        Args:
            info: ``detect_gpu_info()`` 返回的 dict 加上 worker 附加的
                runtime_ready / runtime_accelerator / runtime_profile /
                runtime_components 字段
        """
        self._has_gpu = bool(info.get("has_gpu"))
        runtime_installed = bool(info.get("runtime_ready"))
        runtime_accelerator = info.get("runtime_accelerator")
        runtime_components = info.get("runtime_components") or ()
        self._runtime_installed = runtime_installed and runtime_accelerator in {
            "cpu",
            "nvidia_cuda",
        }
        self._runtime_profile = str(info.get("runtime_profile") or "")
        self._current = (
            accelerator_framework(runtime_accelerator, runtime_components)
            if self._runtime_installed
            else None
        )

        if not self._has_gpu:
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._hw_label.setText(
                "未检测到符合 CUDA 条件的 NVIDIA GPU（文档解析 MinerU 与 VL 模型不可用）"
            )
        else:
            gpu_name = info.get("name") or "NVIDIA GPU"
            vram = info.get("vram_mb") or 0
            vram_str = f"{vram // 1024}GB" if vram >= 1024 else f"{vram}MB"
            driver = info.get("driver_version") or ""
            max_cuda = info.get("cuda")
            hardware = f"GPU：{gpu_name}（{vram_str}）"
            if driver:
                hardware += f" · 驱动 {driver}"
            if max_cuda:
                hardware += f" · 最高支持 CUDA {max_cuda}"
            requirement = cuda_requirement_label(self._runtime_profile)
            if requirement:
                if max_cuda:
                    verdict = (
                        "满足"
                        if _cuda_at_least(
                            max_cuda, cuda_requirement_version(self._runtime_profile)
                        )
                        else "低于要求"
                    )
                    hardware += f"；GPU profile 需要 {requirement}：{verdict}"
                else:
                    hardware += (
                        f"；GPU profile 需要 {requirement}（本机驱动支持版本未知）"
                    )
            self._hw_label.setText(hardware)

        self._cpu_radio.setEnabled(not self._change_in_progress)
        self._gpu_radio.setEnabled(
            not self._change_in_progress and (self._has_gpu or self._current == "gpu")
        )

        self._render_current_state()

        target = self._current or ("gpu" if self._has_gpu else "cpu")
        if target == "gpu" and self._gpu_radio.isEnabled():
            self._gpu_radio.setChecked(True)
        else:
            self._cpu_radio.setChecked(True)

        self._update_apply_state()
        self.gpu_capability_resolved.emit(self._current == "gpu")

    def _render_current_state(self) -> None:
        """按三态（未安装/基础 Runtime/已选择框架）渲染当前后端与提示。"""

        if not self._runtime_installed:
            self._current_label.setText("当前后端：尚未安装")
            self._status_label.setText(
                "请选择推理后端；确认后才会联网下载并安装完整 Runtime profile。"
            )
            return
        if self._current is None:
            self._current_label.setText("当前后端：基础 Runtime（未选择加速框架）")
            self._status_label.setText(
                "基础 Runtime 已就绪（快速 OCR 可用）；安装完整 profile 后可启用"
                "文档解析、表格、公式等高级能力。"
            )
            return
        cuda = cuda_requirement_label(self._runtime_profile)
        name = (
            f"GPU（{cuda}）"
            if self._current == "gpu" and cuda
            else self._current.upper()
        )
        self._current_label.setText(f"当前后端：{name}")
        self._status_label.setText("")

    def current_backend(self) -> str | None:
        return self._current

    def _can_apply(self) -> bool:
        """当前单选目标是否需要安装或切换。"""
        if self._change_in_progress:
            return False
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        return target != self._current

    def _update_apply_state(self) -> None:
        self._apply_button.setEnabled(self._can_apply())

    def _apply(self) -> None:
        if not self._can_apply():
            return
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        self.set_change_in_progress(True)
        self.backend_change_requested.emit(target)

    def set_change_in_progress(self, in_progress: bool) -> None:
        self._change_in_progress = in_progress
        self._gpu_radio.setEnabled(
            not in_progress and (self._has_gpu or self._current == "gpu")
        )
        self._cpu_radio.setEnabled(not in_progress)
        if in_progress:
            target = "GPU" if self._gpu_radio.isChecked() else "CPU"
            self._status_label.setText(f"等待确认切换到 {target}…")
        else:
            self._render_current_state()
        self._update_apply_state()

    def refresh_runtime_state(self) -> None:
        """安装、取消或失败后重新读取硬件与 Runtime 权威状态。"""
        if self._detect_worker is not None:
            self._refresh_after_detection = True
            if hasattr(self._detect_worker, "cancel"):
                self._detect_worker.cancel()
            self.set_change_in_progress(False)
            return
        self._change_in_progress = False
        self._show_detecting_state()
        self._start_gpu_detection()
