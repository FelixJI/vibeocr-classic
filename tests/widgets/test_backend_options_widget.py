"""设置页"推理后端"组件测试"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, QThread, Signal


class _StubGpuDetectWorker(QObject):
    """替代 _GpuDetectWorker 的桩：不启动真线程，start() 为空操作。

    信号 finished_info 与真 worker 同名，测试可显式 emit 触发回填，
    使断言不依赖 QThread 异步时序。
    """

    finished_info = Signal(dict)
    finished = Signal()

    def __init__(self, _project_root=None, parent=None):
        super().__init__(parent)

    def start(self):  # 真线程启动的空操作
        pass

    def isRunning(self):
        return False

    def quit(self):
        pass

    def wait(self, _timeout_ms):
        return True


class _RunningStubGpuDetectWorker(QObject):
    finished_info = Signal(dict)
    finished = Signal()

    def __init__(self, _project_root=None, parent=None):
        super().__init__(parent)
        self.quit_called = False
        self.wait_calls: list[int] = []
        self._running = False
        self.cancel_called = False

    def start(self):
        self._running = True

    def isRunning(self):
        return self._running

    def quit(self):
        self.quit_called = True

    def cancel(self):
        self.cancel_called = True

    def wait(self, timeout_ms):
        self.wait_calls.append(timeout_ms)
        self._running = False
        return True


class _SlowWaitStubGpuDetectWorker(_RunningStubGpuDetectWorker):
    def wait(self, timeout_ms):
        self.wait_calls.append(timeout_ms)
        time.sleep(0.4)
        self._running = False
        return True


class _SlowQThreadGpuDetectWorker(QThread):
    finished_info = Signal(dict)

    def __init__(self, _project_root=None, parent=None):
        super().__init__(parent)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_called = False

    def cancel(self):
        self.cancel_called = True

    def run(self):
        self.entered.set()
        self.release.wait(timeout=2)
        # 故意模拟不协作的底层调用：即使取消后仍发迟到结果。
        self.finished_info.emit(
            {
                "has_gpu": False,
                "name": "late",
                "vram_mb": 0,
                "cuda": None,
                "runtime_has_gpu": False,
            }
        )


def _make_widget(
    tmp_path,
    has_gpu=True,
    runtime_backend: str | None = "cpu",
    worker_cls: type[_StubGpuDetectWorker]
    | type[_RunningStubGpuDetectWorker] = _StubGpuDetectWorker,
):
    """用可控 worker 构造组件并回填物理 GPU + Installer 状态。"""
    from vibeocr.classic.widgets import backend_options_widget as bow

    orig_worker = bow._GpuDetectWorker
    runtime_accelerator = {
        "cpu": "cpu",
        "gpu": "nvidia_cuda",
        None: None,
    }[runtime_backend]
    detect_info = {
        "has_gpu": has_gpu,
        "name": "NVIDIA GeForce RTX 4090" if has_gpu else "",
        "vram_mb": 24564 if has_gpu else 0,
        "cuda": "cu126" if has_gpu else None,
        "runtime_ready": runtime_backend is not None,
        "runtime_accelerator": runtime_accelerator,
        "runtime_has_gpu": runtime_backend == "gpu",
    }

    bow._GpuDetectWorker = worker_cls

    try:
        widget = bow.BackendOptionsWidget(tmp_path)
        assert widget._detect_worker is not None
        widget._detect_worker.finished_info.emit(detect_info)
        if not widget._detect_worker.isRunning():
            widget._detect_worker.finished.emit()
    finally:
        bow._GpuDetectWorker = orig_worker
    return widget


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def test_shows_current_backend_gpu(_cleanup, qtbot, tmp_path):
    """有 GPU 时应显示当前后端为 GPU，GPU 单选默认选中"""
    widget = _make_widget(tmp_path, has_gpu=True, runtime_backend="gpu")
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert widget._gpu_radio.isChecked()
    assert not widget._cpu_radio.isChecked()


def test_shows_current_backend_cpu_when_no_gpu(_cleanup, qtbot, tmp_path):
    """无 GPU 时 CPU 单选默认选中，GPU 禁用"""
    widget = _make_widget(tmp_path, has_gpu=False, runtime_backend="cpu")
    qtbot.addWidget(widget)
    assert widget.current_backend() == "cpu"
    assert widget._cpu_radio.isChecked()
    assert not widget._gpu_radio.isEnabled()


def test_uninstalled_runtime_requires_explicit_choice(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_backend=None)
    qtbot.addWidget(widget)
    assert widget.current_backend() is None
    assert "确认后" in widget._status_label.text()
    assert widget._gpu_radio.isChecked()
    assert widget._apply_button.isEnabled()


def test_apply_requests_visible_backend_change(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_backend="gpu")
    qtbot.addWidget(widget)
    received: list[str] = []
    widget.backend_change_requested.connect(received.append)
    widget._cpu_radio.setChecked(True)
    widget._apply()
    assert received == ["cpu"]
    assert "等待确认" in widget._status_label.text()


def test_apply_disabled_when_selection_matches_runtime(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_backend="gpu")
    qtbot.addWidget(widget)
    # 当前 gpu，pending 也是 gpu → 单选选中 gpu → 无变化
    widget._gpu_radio.setChecked(True)
    assert not widget._can_apply()


def test_cancelled_change_reenables_controls(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_backend="gpu")
    qtbot.addWidget(widget)
    widget._cpu_radio.setChecked(True)
    widget._apply()
    assert not widget._apply_button.isEnabled()
    widget.set_change_in_progress(False)
    assert widget._apply_button.isEnabled()


def test_refresh_waits_for_running_detection_then_reads_runtime_again(
    _cleanup, qtbot, tmp_path
):
    from vibeocr.classic.widgets import backend_options_widget as bow

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_backend="cpu",
        worker_cls=_RunningStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    first_worker = widget._detect_worker
    assert isinstance(first_worker, _RunningStubGpuDetectWorker)

    with patch.object(bow, "_GpuDetectWorker", _StubGpuDetectWorker):
        widget.refresh_runtime_state()
        assert first_worker.cancel_called

        first_worker._running = False
        first_worker.finished.emit()

    assert isinstance(widget._detect_worker, _StubGpuDetectWorker)
    assert widget._detect_worker is not first_worker
    assert widget.current_backend() is None


def test_current_backend_matches_installer_not_live_detect(_cleanup, qtbot, tmp_path):
    """实时硬件探测失败时仍展示 Installer 已验证的 GPU profile。"""
    widget = _make_widget(
        tmp_path,
        has_gpu=False,
        runtime_backend="gpu",
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert "GPU" in widget._current_label.text()


def test_gpu_worker_uses_runtime_installer_accelerator(
    _cleanup, qtbot, tmp_path, monkeypatch
):
    """物理 GPU 与实际安装 profile 不同时，以 Installer 状态为准。"""
    from vibeocr.classic.widgets import backend_options_widget as bow

    client = MagicMock()
    client.inspect.return_value = SimpleNamespace(accelerator="cpu")
    monkeypatch.setattr(
        bow,
        "RuntimeInstallerClient",
        lambda _root: client,
        raising=False,
    )
    monkeypatch.setattr(
        bow.env_manager,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": True,
            "name": "NVIDIA GeForce RTX 4090",
            "vram_mb": 24564,
            "cuda": "cu126",
        },
    )
    monkeypatch.setattr(
        bow.env_manager,
        "get_runtime_gpu_capability",
        lambda *_args, **_kwargs: True,
    )
    worker = bow._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    assert received[0]["runtime_has_gpu"] is False
    client.inspect.assert_called_once_with()


def test_close_stops_running_gpu_detection_worker(_cleanup, qtbot, tmp_path):
    """Closing requests cancellation without waiting on the GUI thread."""
    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_backend="gpu",
        worker_cls=_SlowWaitStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    worker = widget._detect_worker
    assert worker is not None
    assert worker.isRunning()

    started = time.perf_counter()
    widget.close()
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert isinstance(worker, _RunningStubGpuDetectWorker)
    assert elapsed_ms < 150
    assert worker.cancel_called
    assert worker.quit_called
    assert worker.wait_calls == []


def test_close_drops_late_gpu_detection_result(_cleanup, qtbot, tmp_path):
    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_backend="gpu",
        worker_cls=_RunningStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    worker = widget._detect_worker
    assert worker is not None
    original_status = widget._status_label.text()
    original_backend = widget.current_backend()

    widget.close()
    worker.finished_info.emit(
        {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
            "runtime_has_gpu": False,
        }
    )

    assert widget._status_label.text() == original_status
    assert widget.current_backend() == original_backend


def test_cancelled_gpu_worker_does_not_emit_result(_cleanup, tmp_path, monkeypatch):
    from vibeocr.classic.widgets import backend_options_widget as bow

    def detect(cancel_event):
        cancel_event.set()
        return {"has_gpu": True, "name": "late", "vram_mb": 1, "cuda": "x"}

    monkeypatch.setattr(bow.env_manager, "detect_gpu_info", detect)
    worker = bow._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    assert received == []


def test_gpu_worker_is_kept_until_finished_then_released(_cleanup, qtbot, tmp_path):
    from vibeocr.classic.widgets import backend_options_widget as bow

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_backend="gpu",
        worker_cls=_RunningStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    worker = widget._detect_worker
    assert worker is not None
    assert worker in bow._ACTIVE_GPU_DETECT_WORKERS

    widget.close()
    assert worker in bow._ACTIVE_GPU_DETECT_WORKERS
    worker._running = False
    worker.finished.emit()

    qtbot.waitUntil(lambda: worker not in bow._ACTIVE_GPU_DETECT_WORKERS, timeout=1000)
    assert widget._detect_worker is None


def test_running_qthread_outlives_widget_and_releases_after_finished(
    _cleanup, qtbot, tmp_path
):
    from vibeocr.classic.widgets import backend_options_widget as bow

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_backend="gpu",
        worker_cls=_SlowQThreadGpuDetectWorker,
    )
    worker = widget._detect_worker
    assert isinstance(worker, _SlowQThreadGpuDetectWorker)
    assert worker.entered.wait(timeout=1)
    assert worker.parent() is None
    assert worker in bow._ACTIVE_GPU_DETECT_WORKERS

    started = time.perf_counter()
    widget.close()
    elapsed_ms = (time.perf_counter() - started) * 1000
    widget.deleteLater()
    qtbot.wait(10)
    assert elapsed_ms < 150
    assert worker.isRunning()

    worker.release.set()
    qtbot.waitUntil(lambda: worker not in bow._ACTIVE_GPU_DETECT_WORKERS, timeout=1000)
