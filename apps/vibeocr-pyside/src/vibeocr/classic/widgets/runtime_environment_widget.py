"""设置页「运行环境」选择组件。

推理设备（CPU/GPU）与识别依赖（基础/PaddleOCR/MinerU）整合为一组简单
选项：基础识别随主程序内置恒可用，其余由用户按需勾选；确认后交给设置
页控制器，在用户二次确认后通过可见、可取消的安装对话框执行。物理 GPU
只决定 GPU 选项是否可用；实际安装态以 Runtime Installer ``inspect``
（旧子进程形态）或引擎环境检查（融合形态）为权威。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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
from vibeocr.classic.ui import theme

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtGui import QCloseEvent

logger = logging.getLogger(__name__)

_ENGINE_LABELS = {
    "paddle": "PaddleOCR 引擎",
    "mineru": "MinerU 文档解析",
}


def _read_engine_device_preference(project_root: Path) -> str | None:
    """读取用户在未安装引擎时保存的设备偏好（"cpu"/"gpu"/None）。"""

    try:
        data = json.loads(
            (project_root / "config" / "engine-device.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    value = data.get("device") if isinstance(data, dict) else None
    return value if value in {"cpu", "gpu"} else None


def _legacy_installed_engines(components: tuple[Any, ...]) -> tuple[str, ...]:
    """从 Installer 组件闭包投影已安装引擎家族（paddle/mineru）。"""

    engines: set[str] = set()
    for component in components:
        component_id = getattr(component, "component_id", "") or ""
        if component_id.startswith("paddleocr-"):
            engines.add("paddle")
        elif component_id.startswith("mineru-"):
            engines.add("mineru")
    return tuple(sorted(engines))


class _GpuDetectWorker(QThread):
    """后台探测 GPU 与运行环境安装态。

    ``detect_gpu_info`` 会调用 ``nvidia-smi``，在有 NVIDIA GPU 的机器上可能
    耗时显著；环境检查也可能读盘。放到后台线程避免阻塞设置页控件构造
    （进而避免阻塞应用启动——该控件在 MainWindow.__init__ 的
    _connect_signals 链中被构造）。探测完成后通过信号把 info dict 回主线程。
    """

    finished_info = Signal(dict)  # detect_gpu_info() 的返回值 + 运行环境字段

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
            logger.exception("[RuntimeEnvironment] 后台 GPU 探测异常")
            info = {"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None}
        if self._cancelled.is_set():
            return
        from vibeocr.classic.backend_host import inprocess_backend_enabled

        device_preference: str | None = None
        if inprocess_backend_enabled():
            # 融合形态：基础识别随主程序内置，不存在独立 Runtime 安装态；
            # 安装态以已装引擎环境为准（Runtime Installer 不再随包分发）。
            try:
                from vibeocr.classic.engine_envs import (
                    EngineEnvManager,
                    device_framework,
                )

                states = EngineEnvManager(self._project_root / "envs").inspect()
                framework = device_framework(states)
                runtime_installed = True
                runtime_device = framework
                installed_engines = tuple(
                    sorted(
                        {
                            spec_id.split("-")[0]
                            for spec_id, state in states.items()
                            if state.installed
                        }
                    )
                )
                runtime_profile = {
                    None: "win-x64-base",
                    "cpu": "win-x64-cpu",
                    "gpu": "win-x64-cu126",
                }[framework]
            except Exception:
                # 环境检查失败（如随包 wheel/清单不可读）不得让 worker 静默
                # 退出：与 Installer 分支同样兜底，仍发送不可用状态，让硬件
                # 探测与控件能退出"检测中"态。
                logger.exception("[RuntimeEnvironment] 引擎环境状态读取异常")
                runtime_installed = False
                runtime_device = None
                installed_engines = ()
                runtime_profile = ""
            device_preference = _read_engine_device_preference(self._project_root)
        else:
            try:
                inspection = RuntimeInstallerClient(self._project_root).inspect()
                # base_ready 覆盖「仅基础 Runtime 已安装」：完整 profile 判定
                # ready 会把基础态误报成"尚未安装"。
                runtime_installed = bool(
                    getattr(inspection, "base_ready", False)
                    or getattr(inspection, "ready", False)
                )
                accelerator = (
                    getattr(inspection, "accelerator", None)
                    if runtime_installed
                    else None
                )
                runtime_device = (
                    "gpu"
                    if accelerator == "nvidia_cuda"
                    else "cpu"
                    if accelerator is not None
                    else None
                )
                runtime_profile = (
                    getattr(inspection, "profile", "") if runtime_installed else ""
                )
                installed_engines = _legacy_installed_engines(
                    tuple(getattr(inspection, "components", ()) or ())
                )
            except Exception:
                logger.exception("[RuntimeEnvironment] Runtime Installer 状态读取异常")
                runtime_installed = False
                runtime_device = None
                installed_engines = ()
                runtime_profile = ""
        if self._cancelled.is_set():
            return
        info["runtime_installed"] = runtime_installed
        info["runtime_device"] = runtime_device
        info["runtime_profile"] = runtime_profile
        info["installed_engines"] = installed_engines
        info["device_preference"] = device_preference
        self.finished_info.emit(dict(info))


_ACTIVE_GPU_DETECT_WORKERS: set[_GpuDetectWorker] = set()


def _release_gpu_detect_worker(worker: _GpuDetectWorker) -> None:
    """释放模块级保活引用；可由自然完成或非 GUI drain 重复调用。"""
    _ACTIVE_GPU_DETECT_WORKERS.discard(worker)
    worker.deleteLater()


class RuntimeEnvironmentWidget(QWidget):
    """运行环境选择组件：推理设备 + 识别依赖。"""

    runtime_change_requested = Signal(str, list)
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
        self._current_device: str | None = None
        self._installed_engines: tuple[str, ...] = ()
        self._device_preference: str | None = None
        self._runtime_installed = False
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

        self._status_label = QLabel("运行环境：检测中...")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # 硬件信息展示（GPU 型号/显存/CUDA 或未检测到）。
        # 探测完成前显示"检测中..."，由 _apply_detected_state 回填。
        self._hw_label = QLabel("硬件检测中...")
        self._hw_label.setWordWrap(True)
        self._hw_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        layout.addWidget(self._hw_label)

        # --- 推理设备（2 项单选） ---
        device_group = QGroupBox("推理设备")
        device_layout = QVBoxLayout(device_group)
        self._radio_group = QButtonGroup(self)
        radio_row = QHBoxLayout()
        self._cpu_radio = QRadioButton("CPU")
        self._cpu_radio.setToolTip("兼容大多数电脑")
        self._gpu_radio = QRadioButton("GPU 加速")
        self._gpu_radio.setToolTip(
            "需要 NVIDIA 显卡（CUDA 12.6），下载量较大，识别更快"
        )
        # 探测完成前禁用，避免基于未知硬件状态误选 GPU。
        self._cpu_radio.setEnabled(False)
        self._gpu_radio.setEnabled(False)
        self._radio_group.addButton(self._cpu_radio)
        self._radio_group.addButton(self._gpu_radio)
        radio_row.addWidget(self._cpu_radio)
        radio_row.addWidget(self._gpu_radio)
        radio_row.addStretch()
        device_layout.addLayout(radio_row)
        device_hint = QLabel(
            "GPU 需要兼容的 NVIDIA 显卡，完整运行环境（CUDA 12.6）下载量较大、"
            "识别更快；CPU 兼容大多数电脑，完整运行环境通常超过 1 GB。"
        )
        device_hint.setWordWrap(True)
        device_hint.setStyleSheet(f"color: {theme.Colors.text_muted};")
        device_layout.addWidget(device_hint)
        layout.addWidget(device_group)

        # --- 识别依赖（基础内置 + 2 项可选勾选） ---
        deps_group = QGroupBox("识别依赖")
        deps_layout = QVBoxLayout(deps_group)
        self._base_check = QCheckBox("基础识别（随主程序内置）")
        self._base_check.setToolTip("快速 OCR 等基础能力，无需额外下载")
        self._base_check.setChecked(True)
        self._base_check.setEnabled(False)
        deps_layout.addWidget(self._base_check)
        self._engine_checks: dict[str, QCheckBox] = {}
        for engine, label in _ENGINE_LABELS.items():
            check = QCheckBox(label)
            check.setEnabled(False)
            self._engine_checks[engine] = check
            deps_layout.addWidget(check)
        self._paddle_check = self._engine_checks["paddle"]
        self._paddle_check.setToolTip("高精度文字识别。CPU 版下载量约 1 GB，GPU 版更大")
        self._mineru_check = self._engine_checks["mineru"]
        self._mineru_check.setToolTip(
            "把 PDF / 图片文档解析为文字和表格。CPU 版下载量约 2 GB，GPU 版更大"
        )
        deps_hint = QLabel("按需勾选要安装的识别引擎；取消勾选会在应用后移除对应引擎。")
        deps_hint.setWordWrap(True)
        deps_hint.setStyleSheet(f"color: {theme.Colors.text_muted};")
        deps_layout.addWidget(deps_hint)
        layout.addWidget(deps_group)

        self._apply_button = QPushButton("应用更改…")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        layout.addWidget(self._apply_button)

        layout.addStretch()

        self._cpu_radio.toggled.connect(self._update_apply_state)
        self._gpu_radio.toggled.connect(self._update_apply_state)
        for check in self._engine_checks.values():
            check.toggled.connect(self._update_apply_state)

    def _show_detecting_state(self) -> None:
        self._current_device = None
        self._installed_engines = ()
        self._device_preference = None
        self._runtime_installed = False
        self._status_label.setText("运行环境：检测中...")
        self._hw_label.setText("硬件检测中...")
        self._cpu_radio.setEnabled(False)
        self._gpu_radio.setEnabled(False)
        for check in self._engine_checks.values():
            check.setEnabled(False)
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
            logger.warning("[RuntimeEnvironment] GPU detection worker did not stop")
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
        """后台探测完成后，在主线程回填硬件展示与安装态、启用控件。

        ``_current_device`` 只来自已验证的安装态（引擎环境框架或 Installer
        accelerator）；实时硬件探测仅决定 GPU 选项是否可用，不能冒充实际
        安装 profile。未安装任何引擎时回退显示用户保存过的设备偏好。
        """
        self._has_gpu = bool(info.get("has_gpu"))
        self._runtime_installed = bool(info.get("runtime_installed"))
        self._current_device = info.get("runtime_device")
        if self._current_device not in {"cpu", "gpu"}:
            self._current_device = None
        self._installed_engines = tuple(info.get("installed_engines") or ())
        self._device_preference = info.get("device_preference")

        if not self._has_gpu:
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._hw_label.setText("未检测到符合 CUDA 条件的 NVIDIA GPU")
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
                # 保守表述：驱动向下兼容，满足该版本即满足其下所有版本。
                hardware += f" · 支持 CUDA {max_cuda}"
            self._hw_label.setText(hardware)

        self._render_current_state()
        self._sync_selection_with_state()
        self._update_apply_state()
        self.gpu_capability_resolved.emit(self._current_device == "gpu")

    def _render_current_state(self) -> None:
        """按当前安装态渲染状态行与控件可用性。"""
        if not self._runtime_installed:
            self._status_label.setText("运行环境状态读取失败；仍可提交选择")
        else:
            parts = ["基础识别已内置"]
            if self._current_device is not None:
                parts.append(
                    "计算设备 GPU（NVIDIA CUDA）"
                    if self._current_device == "gpu"
                    else "计算设备 CPU"
                )
            engines = "、".join(
                _ENGINE_LABELS[engine]
                for engine in self._installed_engines
                if engine in _ENGINE_LABELS
            )
            parts.append(f"已安装引擎：{engines}" if engines else "未安装可选引擎")
            self._status_label.setText(" · ".join(parts))

        enabled = not self._change_in_progress
        self._cpu_radio.setEnabled(enabled)
        self._gpu_radio.setEnabled(
            enabled and (self._has_gpu or self._current_device == "gpu")
        )
        for check in self._engine_checks.values():
            check.setEnabled(enabled)

    def _sync_selection_with_state(self) -> None:
        """把单选/勾选回填为当前安装态（无引擎时回退设备偏好）。"""
        device = self._current_device or self._device_preference or "cpu"
        if device == "gpu" and not (self._has_gpu or self._current_device == "gpu"):
            device = "cpu"
        (self._gpu_radio if device == "gpu" else self._cpu_radio).setChecked(True)
        for engine, check in self._engine_checks.items():
            check.setChecked(engine in self._installed_engines)

    def current_backend(self) -> str | None:
        return self._current_device

    def selected_device(self) -> str:
        return "gpu" if self._gpu_radio.isChecked() else "cpu"

    def selected_engines(self) -> tuple[str, ...]:
        return tuple(
            engine for engine, check in self._engine_checks.items() if check.isChecked()
        )

    def _effective_current_device(self) -> str | None:
        """比较应用按钮状态用的“当前设备”：安装态优先，其次保存的偏好。"""
        return self._current_device or self._device_preference

    def _can_apply(self) -> bool:
        if self._change_in_progress:
            return False
        if self.selected_device() != self._effective_current_device():
            return True
        return set(self.selected_engines()) != set(self._installed_engines)

    def _update_apply_state(self) -> None:
        self._apply_button.setEnabled(self._can_apply())

    def _apply(self) -> None:
        if not self._can_apply():
            return
        device = self.selected_device()
        engines = list(self.selected_engines())
        self.set_change_in_progress(True)
        self.runtime_change_requested.emit(device, engines)

    def set_change_in_progress(self, in_progress: bool) -> None:
        self._change_in_progress = in_progress
        self._render_current_state()
        self._apply_button.setEnabled(not in_progress and self._can_apply())

    def refresh_runtime_state(self) -> None:
        """安装、取消或失败后重新读取硬件与运行环境权威状态。"""
        if self._detect_worker is not None:
            self._refresh_after_detection = True
            if hasattr(self._detect_worker, "cancel"):
                self._detect_worker.cancel()
            self.set_change_in_progress(False)
            return
        self._change_in_progress = False
        self._show_detecting_state()
        self._start_gpu_detection()
