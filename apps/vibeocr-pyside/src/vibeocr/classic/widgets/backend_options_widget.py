"""设置页"推理后端"组件

显示当前 OCR 后端（GPU/CPU），允许用户标记待切换（下次重启自动下载安装）。
不立即执行切换——纯写 pending_backend 标记。
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

from vibeocr.backend import env_manager
from vibeocr.classic.machine_cache import CACHE_VERSION, load_cache, update_cache_field
from vibeocr.classic.ui import theme

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtGui import QCloseEvent

logger = logging.getLogger(__name__)


class _GpuDetectWorker(QThread):
    """后台 GPU 探测 worker。

    ``env_manager.detect_gpu_info`` 内部会同步 ``subprocess.run(["nvidia-smi"],
    timeout=5)``，在有 NVIDIA GPU 的机器上耗时显著。放到后台线程避免阻塞
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
            info = env_manager.detect_gpu_info(cancel_event=self._cancelled)
        except Exception:
            # detect_gpu_info 自身有兜底，理论上不抛；防御性捕获避免线程静默挂起。
            logger.exception("[BackendOptions] 后台 GPU 探测异常")
            info = {"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None}
        if self._cancelled.is_set():
            return
        try:
            runtime_has_gpu = env_manager.get_runtime_gpu_capability(
                self._project_root,
                detected_has_gpu=bool(info.get("has_gpu")),
            )
        except Exception:
            logger.exception("[BackendOptions] 后台运行时 GPU 能力解析异常")
            runtime_has_gpu = bool(info.get("has_gpu"))
        if self._cancelled.is_set():
            return
        info["runtime_has_gpu"] = runtime_has_gpu
        self.finished_info.emit(dict(info))


_ACTIVE_GPU_DETECT_WORKERS: set[_GpuDetectWorker] = set()


def _release_gpu_detect_worker(worker: _GpuDetectWorker) -> None:
    """释放模块级保活引用；可由自然完成或非 GUI drain 重复调用。"""
    _ACTIVE_GPU_DETECT_WORKERS.discard(worker)
    worker.deleteLater()


class BackendOptionsWidget(QWidget):
    """推理后端设置组件"""

    backend_changed = Signal()  # pending_backend 写入后发射
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
        self._current = "cpu"
        self._pending: str | None = None
        self._detect_worker: _GpuDetectWorker | None = None
        self._detect_generation = 0
        self._closing = False
        self._setup_ui()
        if gpu_capability_callback is not None:
            self.gpu_capability_resolved.connect(gpu_capability_callback)
        # 缓存读取（纯文件 IO，无 subprocess）可在构造期同步完成；
        # detect_gpu_info 的 nvidia-smi 探测改为后台线程，避免阻塞启动。
        self._load_cached_state()
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
        self._gpu_radio.setToolTip("约 1.5GB，识别更快，需 NVIDIA GPU")
        self._cpu_radio = QRadioButton("CPU 模式")
        self._cpu_radio.setToolTip("约 150MB，兼容性广")
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
            "GPU：约 1.5GB，识别更快，需 NVIDIA GPU\nCPU：约 150MB，兼容性广"
        )
        self._hint_label.setWordWrap(True)
        group_layout.addWidget(self._hint_label)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        group_layout.addWidget(self._status_label)

        self._apply_button = QPushButton("应用（下次重启生效）")
        self._apply_button.clicked.connect(self._apply)
        group_layout.addWidget(self._apply_button)

        layout.addWidget(group)
        layout.addStretch()

        # 单选变化时更新应用按钮状态
        self._gpu_radio.toggled.connect(self._update_apply_state)

    def _load_cached_state(self) -> None:
        """从缓存加载当前/待切换状态（纯文件 IO，无 subprocess，可在构造期同步执行）。

        注意：``_current`` 来自缓存 hardware_info.has_gpu（上次检测写入），
        ``_has_gpu``（能否选 GPU）要等实时探测 ``_apply_detected_state`` 回填。
        在探测完成前，radio/apply 均禁用，仅展示"检测中..."。
        """
        # This display-only snapshot must never validate machine identity here:
        # validation can launch WMIC.  The background detector resolves the
        # authoritative runtime capability before controls become interactive.
        cached = load_cache(self._project_root)
        is_valid = bool(cached and cached.get("version") == CACHE_VERSION)
        hw = (cached or {}).get("hardware_info", {}) if is_valid else {}
        self._current = "gpu" if hw.get("has_gpu") else "cpu"
        self._pending = (cached or {}).get("pending_backend") if is_valid else None

        # 待切换状态可立即展示（无需 GPU 探测结果）。
        self._refresh_status(self._pending)

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

    def request_gpu_detection_shutdown(self) -> None:
        """Request hardware-probe cancellation without blocking the GUI thread."""
        self._closing = True
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

        ``_current``（"当前后端"展示值）必须与实际推理设备一致——实际推理走
        ``env_manager.resolve_use_gpu(project_root)``（main_window 启动 worker 时
        同样调用它）。早期版本用实时 ``detect_gpu_info()`` 的 has_gpu 直接覆盖
        ``_current``，当 nvidia-smi 后台探测超时/失败（返回 has_gpu=False）但缓存
        ``hardware_info.has_gpu=True`` 时，UI 误显示 CPU 而推理实为 GPU。
        现在 ``detect_gpu_info()`` 仅用于：①决定 GPU 单选是否可用；②展示硬件信息。

        Args:
            info: ``detect_gpu_info()`` 返回的 dict
                (has_gpu/name/vram_mb/cuda)
        """
        self._has_gpu = bool(info.get("has_gpu"))
        # 实际运行后端：与 main_window 启动 worker 用同一判断，保证展示与推理一致。
        # 运行时后端已在 _GpuDetectWorker 中解析；这里绝不再
        # 调 resolve_use_gpu，否则缓存缺失时会在 GUI 线程再跑
        # nvidia-smi，拖动浮动工具栏时表现为卡死。
        runtime_has_gpu = bool(info.get("runtime_has_gpu", self._current == "gpu"))
        self._current = "gpu" if runtime_has_gpu else "cpu"

        if not self._has_gpu:
            self._gpu_radio.setEnabled(False)
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._hw_label.setText(
                "未检测到符合 CUDA 条件的 NVIDIA GPU（文档解析 MinerU 与 VL 模型不可用）"
            )
        else:
            # CPU 单选始终可选；GPU 单选仅在检测到 GPU 时启用。
            self._cpu_radio.setEnabled(True)
            self._gpu_radio.setEnabled(True)
            gpu_name = info.get("name") or "NVIDIA GPU"
            vram = info.get("vram_mb") or 0
            vram_str = f"{vram // 1024}GB" if vram >= 1024 else f"{vram}MB"
            cuda = info.get("cuda")
            cuda_str = f"CUDA {cuda}" if cuda else "CUDA 版本未知"
            self._hw_label.setText(f"GPU：{gpu_name}（{vram_str}），{cuda_str}")

        name = "GPU" if self._current == "gpu" else "CPU"
        self._current_label.setText(f"当前后端：{name}")

        # 单选反映"待切换目标"（若有）否则"当前"
        target = self._pending or self._current
        if target == "gpu" and self._has_gpu:
            self._gpu_radio.setChecked(True)
        else:
            self._cpu_radio.setChecked(True)

        self._update_apply_state()
        self.gpu_capability_resolved.emit(runtime_has_gpu)

    def current_backend(self) -> str:
        return self._current

    def _refresh_status(self, pending: str | None) -> None:
        if pending:
            name = "GPU" if pending == "gpu" else "CPU"
            self._status_label.setText(f"⏳ 待切换到 {name}，下次重启自动下载并生效")
        else:
            self._status_label.setText("")

    def _can_apply(self) -> bool:
        """当前单选目标是否与待切换/当前不同（即有变化可应用）"""
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        return target != (self._pending or self._current)

    def _update_apply_state(self) -> None:
        self._apply_button.setEnabled(self._can_apply())

    def _apply(self) -> None:
        if not self._can_apply():
            return
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        ok = update_cache_field(self._project_root, "pending_backend", target)
        if ok:
            self._pending = target
            self._refresh_status(target)
            self._update_apply_state()
            self.backend_changed.emit()
        else:
            self._status_label.setText("⚠ 写入缓存失败，请重试")
