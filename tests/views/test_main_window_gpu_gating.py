"""启动阶段 GPU 门控的 UI 响应性回归测试。"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QPoint, Qt, QTimer
from PySide6.QtTest import QTest

from vibeocr.classic.views.main_window import MainWindow
from vibeocr.classic.widgets.backend_options_widget import BackendOptionsWidget
from vibeocr.classic.widgets.toolbar import EdgeToolbar


class _GpuGatingHarness(QObject):
    """只提供 MainWindow GPU 门控所需的最小界面。"""

    def __init__(self) -> None:
        super().__init__()
        self._closing = False
        self._project_root = Path.cwd()

    def findChildren(self, _widget_type):
        return []


def test_slow_gpu_probe_does_not_block_edge_toolbar_drag(qapp, monkeypatch):
    """依赖/GPU 检测慢时，工具栏拖动仍应及时处理。"""
    release_probe = threading.Event()
    probe_returned = threading.Event()

    class SlowRuntimeInstallerClient:
        def __init__(self, _project_root):
            pass

        def inspect(self):
            try:
                release_probe.wait(timeout=1.0)
                return SimpleNamespace(ready=True, accelerator="nvidia_cuda")
            finally:
                probe_returned.set()

    monkeypatch.setattr(
        "vibeocr.classic.widgets.backend_options_widget.RuntimeInstallerClient",
        SlowRuntimeInstallerClient,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.backend_options_widget.detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": True,
            "name": "NVIDIA GeForce RTX 4090",
            "vram_mb": 24564,
            "cuda": "cu126",
        },
    )

    harness = _GpuGatingHarness()
    toolbar = EdgeToolbar()
    toolbar.show()
    qapp.processEvents()

    drag_times: list[float] = []
    started_at = time.monotonic()

    def drag_toolbar() -> None:
        QTest.mousePress(
            toolbar,
            Qt.MouseButton.LeftButton,
            pos=QPoint(8, toolbar.height() // 2),
        )
        QTest.mouseMove(toolbar, QPoint(48, toolbar.height() // 2))
        QTest.mouseRelease(
            toolbar,
            Qt.MouseButton.LeftButton,
            pos=QPoint(48, toolbar.height() // 2),
        )
        drag_times.append(time.monotonic())

    # 用独立 Python timer 释放慢探测：即使 GUI 线程被堵住，
    # 测试也能在确定时间内结束，且延迟可稳定测量。
    release_timer = threading.Timer(0.25, release_probe.set)
    release_timer.daemon = True
    release_timer.start()

    backend_options = BackendOptionsWidget(
        Path.cwd(),
        gpu_capability_callback=lambda has_gpu: MainWindow._apply_gpu_gating_to_all(
            harness, has_gpu
        ),
    )
    QTimer.singleShot(25, drag_toolbar)

    deadline = started_at + 1.0
    while (
        not drag_times or not probe_returned.is_set()
    ) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    release_timer.join(timeout=0.1)
    assert backend_options.shutdown_gpu_detection(timeout_ms=500)
    backend_options.close()
    toolbar.close()

    assert drag_times, "工具栏拖动事件未被 Qt 主事件循环处理"
    drag_latency = drag_times[0] - started_at
    assert drag_latency < 0.15, (
        f"GPU 检测阻塞 GUI 线程，工具栏拖动延迟 {drag_latency:.3f}s"
    )
