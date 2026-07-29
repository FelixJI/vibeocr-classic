"""设置页"推理后端"组件测试"""

import threading
import time
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
    cached_hardware_gpu=False,
    pending=None,
    worker_cls: type[_StubGpuDetectWorker] | type[_RunningStubGpuDetectWorker] = _StubGpuDetectWorker,
):
    """构造 BackendOptionsWidget（在 patch 作用域外也能用，patch 注入到模块）。

    返回 widget。由于 widget 的 _apply 在构造后才调用，patch 必须覆盖调用时刻。
    本函数把 update_cache_field 的 mock 存到 widget._mock_update 以便断言。

    GPU 探测由后台 _GpuDetectWorker 完成；测试用 _StubGpuDetectWorker 替换，
    构造后显式 emit finished_info 同步触发回填，避免依赖真线程时序。
    """
    from vibeocr.classic.widgets import backend_options_widget as bow

    # 直接替换模块级引用（widget 内 from ... import 拿到的就是模块属性）
    orig_em = bow.env_manager
    orig_load_cache = bow.load_cache
    orig_worker = bow._GpuDetectWorker

    mock_em = patch.object(bow, "env_manager").start()
    mock_load_cache = patch.object(bow, "load_cache").start()
    mock_update = patch.object(bow, "update_cache_field").start()

    mock_em.detect_gpu.return_value = (has_gpu, "cu126") if has_gpu else (False, None)
    # 运行时 GPU 能力由后台 worker 计算；用缓存/待切换状态
    # 推导期望值，并随 finished_info 一起回传主线程。
    if pending == "gpu":
        resolved_gpu = True
    elif pending == "cpu":
        resolved_gpu = False
    else:
        resolved_gpu = cached_hardware_gpu
    detect_info = {
        "has_gpu": has_gpu,
        "name": "NVIDIA GeForce RTX 4090" if has_gpu else "",
        "vram_mb": 24564 if has_gpu else 0,
        "cuda": "cu126" if has_gpu else None,
        "runtime_has_gpu": resolved_gpu,
    }
    mock_em.detect_gpu_info.return_value = detect_info
    mock_em.resolve_use_gpu.return_value = resolved_gpu
    mock_em.get_runtime_gpu_capability.return_value = resolved_gpu
    mock_load_cache.return_value = {
        "version": bow.CACHE_VERSION,
        "hardware_info": {"has_gpu": cached_hardware_gpu},
        "pending_backend": pending,
    }
    mock_update.return_value = True

    # 用桩替换真 worker 类，构造时不会启动真线程
    bow._GpuDetectWorker = worker_cls

    try:
        widget = bow.BackendOptionsWidget(tmp_path)
        # 显式触发回填（模拟后台探测完成回调在主线程执行）
        assert widget._detect_worker is not None
        widget._detect_worker.finished_info.emit(detect_info)
        if not widget._detect_worker.isRunning():
            widget._detect_worker.finished.emit()
    finally:
        # 恢复模块引用（构造已完成，状态已读入 widget 实例）
        patch.object(bow, "env_manager", orig_em).start()
        patch.object(bow, "load_cache", orig_load_cache).start()
        bow._GpuDetectWorker = orig_worker
        # 注意：update_cache_field 保持 mock，因为 _apply 才调用
        bow.update_cache_field = mock_update
    widget._mock_update = mock_update
    return widget


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def test_shows_current_backend_gpu(_cleanup, qtbot, tmp_path):
    """有 GPU 时应显示当前后端为 GPU，GPU 单选默认选中"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert widget._gpu_radio.isChecked()
    assert not widget._cpu_radio.isChecked()


def test_shows_current_backend_cpu_when_no_gpu(_cleanup, qtbot, tmp_path):
    """无 GPU 时 CPU 单选默认选中，GPU 禁用"""
    widget = _make_widget(tmp_path, has_gpu=False, cached_hardware_gpu=False)
    qtbot.addWidget(widget)
    assert widget.current_backend() == "cpu"
    assert widget._cpu_radio.isChecked()
    assert not widget._gpu_radio.isEnabled()


def test_shows_pending_status(_cleanup, qtbot, tmp_path):
    """pending_backend 存在时应显示待切换状态"""
    widget = _make_widget(
        tmp_path, has_gpu=True, cached_hardware_gpu=False, pending="gpu"
    )
    qtbot.addWidget(widget)
    assert (
        "待切换" in widget._status_label.text() or "重启" in widget._status_label.text()
    )


def test_apply_writes_pending_backend(_cleanup, qtbot, tmp_path):
    """点应用应写 pending_backend 到缓存"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    # 当前是 gpu，切到 cpu
    widget._cpu_radio.setChecked(True)
    widget._apply()
    widget._mock_update.assert_called_once()
    args = widget._mock_update.call_args[0]
    assert args[1] == "pending_backend"
    assert args[2] == "cpu"


def test_apply_disabled_when_already_pending_same(_cleanup, qtbot, tmp_path):
    """当前已是待切换目标时，应用按钮应禁用"""
    widget = _make_widget(
        tmp_path, has_gpu=True, cached_hardware_gpu=True, pending="gpu"
    )
    qtbot.addWidget(widget)
    # 当前 gpu，pending 也是 gpu → 单选选中 gpu → 无变化
    widget._gpu_radio.setChecked(True)
    assert not widget._can_apply()


def test_apply_emits_backend_changed(_cleanup, qtbot, tmp_path):
    """应用成功后应发射 backend_changed 信号"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    received = []
    widget.backend_changed.connect(lambda: received.append(True))
    widget._cpu_radio.setChecked(True)
    widget._apply()
    assert received == [True]


def test_current_backend_matches_resolve_use_gpu_not_live_detect(
    _cleanup, qtbot, tmp_path
):
    """问题5：实时 nvidia-smi 探测失败（has_gpu=False）但 resolve_use_gpu=True
    （缓存 has_gpu=True）时，"当前后端"应显示 GPU（与实际推理一致），而非 CPU。

    早期版本用 detect_gpu_info 的 has_gpu 直接覆盖 _current，导致 UI 显示 CPU
    而推理实为 GPU。修复后 _current 由 resolve_use_gpu 决定。
    """
    widget = _make_widget(
        tmp_path,
        has_gpu=False,  # 实时探测失败（nvidia-smi 超时/不可用）
        cached_hardware_gpu=True,  # 缓存记录有 GPU → resolve_use_gpu 返回 True
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert "GPU" in widget._current_label.text()


def test_close_stops_running_gpu_detection_worker(_cleanup, qtbot, tmp_path):
    """Closing requests cancellation without waiting on the GUI thread."""
    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        cached_hardware_gpu=True,
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
        cached_hardware_gpu=True,
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


def test_cancelled_gpu_worker_does_not_emit_result(
    _cleanup, tmp_path, monkeypatch
):
    from vibeocr.classic.widgets import backend_options_widget as bow

    def detect(cancel_event):
        cancel_event.set()
        return {"has_gpu": True, "name": "late", "vram_mb": 1, "cuda": "x"}

    monkeypatch.setattr(bow.env_manager, "detect_gpu_info", detect)
    monkeypatch.setattr(
        bow.env_manager, "get_runtime_gpu_capability", lambda *_args, **_kwargs: True
    )
    worker = bow._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    assert received == []


def test_gpu_worker_is_kept_until_finished_then_released(
    _cleanup, qtbot, tmp_path
):
    from vibeocr.classic.widgets import backend_options_widget as bow

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        cached_hardware_gpu=True,
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

    qtbot.waitUntil(
        lambda: worker not in bow._ACTIVE_GPU_DETECT_WORKERS, timeout=1000
    )
    assert widget._detect_worker is None


def test_running_qthread_outlives_widget_and_releases_after_finished(
    _cleanup, qtbot, tmp_path
):
    from vibeocr.classic.widgets import backend_options_widget as bow

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        cached_hardware_gpu=True,
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
    qtbot.waitUntil(
        lambda: worker not in bow._ACTIVE_GPU_DETECT_WORKERS, timeout=1000
    )
