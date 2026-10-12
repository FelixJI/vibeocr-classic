"""设置页「运行环境」选择组件测试（推理设备 + 识别依赖）"""

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
                "runtime_installed": True,
                "runtime_device": "cpu",
                "runtime_profile": "win-x64-cpu",
                "installed_engines": (),
                "device_preference": None,
            }
        )


def _make_widget(
    tmp_path,
    has_gpu=True,
    runtime_device: str | None = "cpu",
    installed_engines: tuple[str, ...] = (),
    device_preference: str | None = None,
    worker_cls: type[_StubGpuDetectWorker]
    | type[_RunningStubGpuDetectWorker] = _StubGpuDetectWorker,
):
    """用可控 worker 构造组件并回填物理 GPU + 运行环境安装态。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    orig_worker = rew._GpuDetectWorker
    detect_info = {
        "has_gpu": has_gpu,
        "name": "NVIDIA GeForce RTX 4090" if has_gpu else "",
        "vram_mb": 24564 if has_gpu else 0,
        "driver_version": "566.36" if has_gpu else "",
        "cuda": "12.6" if has_gpu else None,
        "runtime_installed": runtime_device is not None,
        "runtime_device": runtime_device,
        "runtime_profile": {
            "cpu": "win-x64-cpu",
            "gpu": "win-x64-cu126",
            None: "win-x64-base",
        }[runtime_device],
        "installed_engines": installed_engines,
        "device_preference": device_preference,
    }

    rew._GpuDetectWorker = worker_cls

    try:
        widget = rew.RuntimeEnvironmentWidget(tmp_path)
        assert widget._detect_worker is not None
        widget._detect_worker.finished_info.emit(detect_info)
        if not widget._detect_worker.isRunning():
            widget._detect_worker.finished.emit()
    finally:
        rew._GpuDetectWorker = orig_worker
    return widget


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def test_shows_current_device_gpu(_cleanup, qtbot, tmp_path):
    widget = _make_widget(
        tmp_path, has_gpu=True, runtime_device="gpu", installed_engines=("paddle",)
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert widget._gpu_radio.isChecked()
    assert widget._paddle_check.isChecked()
    assert not widget._mineru_check.isChecked()
    assert "基础识别已内置" in widget._status_label.text()
    assert "计算设备 GPU" in widget._status_label.text()


def test_shows_current_device_cpu_when_no_gpu(_cleanup, qtbot, tmp_path):
    widget = _make_widget(
        tmp_path, has_gpu=False, runtime_device="cpu", installed_engines=("mineru",)
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "cpu"
    assert widget._cpu_radio.isChecked()
    assert not widget._gpu_radio.isEnabled()
    assert widget._mineru_check.isChecked()
    assert not widget._paddle_check.isChecked()


def test_base_check_always_checked_and_disabled(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    assert widget._base_check.isChecked()
    assert not widget._base_check.isEnabled()


def test_gpu_radio_disabled_without_nvidia_gpu(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=False, runtime_device="cpu")
    qtbot.addWidget(widget)
    assert not widget._gpu_radio.isEnabled()
    assert "未检测到" in widget._hw_label.text()


def test_device_preference_used_when_no_engines(_cleanup, qtbot, tmp_path):
    """未装引擎时设备单选回退显示已保存的设备偏好。"""
    widget = _make_widget(
        tmp_path, has_gpu=True, runtime_device=None, device_preference="gpu"
    )
    qtbot.addWidget(widget)
    assert widget._gpu_radio.isChecked()
    assert widget._can_apply() is False


def test_apply_emits_device_and_engines(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_device="cpu")
    qtbot.addWidget(widget)
    requested: list[tuple[str, list[str]]] = []
    widget.runtime_change_requested.connect(
        lambda device, engines: requested.append((device, list(engines)))
    )

    widget._gpu_radio.setChecked(True)
    widget._paddle_check.setChecked(True)
    widget._mineru_check.setChecked(True)
    assert widget._apply_button.isEnabled()
    widget._apply_button.click()

    assert requested == [("gpu", ["paddle", "mineru"])]
    assert widget._change_in_progress is True
    assert not widget._apply_button.isEnabled()


def test_apply_disabled_when_selection_matches_state(_cleanup, qtbot, tmp_path):
    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_device="gpu",
        installed_engines=("paddle", "mineru"),
    )
    qtbot.addWidget(widget)
    # 状态回填后单选/勾选与安装态一致，无变更可应用。
    assert not widget._apply_button.isEnabled()


def test_engine_uncheck_counts_as_change(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, runtime_device="cpu", installed_engines=("paddle",))
    qtbot.addWidget(widget)
    assert not widget._apply_button.isEnabled()
    widget._paddle_check.setChecked(False)
    assert widget._apply_button.isEnabled()


def test_cancelled_change_reenables_controls(_cleanup, qtbot, tmp_path):
    widget = _make_widget(tmp_path, has_gpu=True, runtime_device="cpu")
    qtbot.addWidget(widget)
    widget._gpu_radio.setChecked(True)
    widget._apply_button.click()
    assert widget._change_in_progress is True

    widget.set_change_in_progress(False)

    assert widget._change_in_progress is False
    assert widget._cpu_radio.isEnabled()
    assert widget._gpu_radio.isEnabled()


def test_current_backend_matches_installed_state_not_live_detect(
    _cleanup, qtbot, tmp_path
):
    """实时硬件探测失败（无 NVIDIA GPU）时仍展示已安装的 GPU 引擎态。"""
    widget = _make_widget(
        tmp_path,
        has_gpu=False,
        runtime_device="gpu",
        installed_engines=("paddle",),
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert widget._gpu_radio.isEnabled()


def test_gpu_worker_uses_runtime_installer_state(
    _cleanup, qtbot, tmp_path, monkeypatch
):
    """子进程形态：设备与已装引擎来自 Installer inspection 投影。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    monkeypatch.setenv("VIBEOCR_SUPERVISOR_SUBPROCESS", "1")
    client = MagicMock()
    client.inspect.return_value = SimpleNamespace(
        base_ready=True,
        ready=True,
        accelerator="nvidia_cuda",
        profile="win-x64-cu126",
        components=(
            SimpleNamespace(component_id="paddleocr-cuda"),
            SimpleNamespace(component_id="rapidocr-base"),
        ),
    )
    monkeypatch.setattr(
        rew, "RuntimeInstallerClient", lambda _root: client, raising=False
    )
    monkeypatch.setattr(
        rew,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": True,
            "name": "NVIDIA GeForce RTX 4090",
            "vram_mb": 24564,
            "cuda": "cu126",
        },
    )
    worker = rew._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    info = received[0]
    assert info["runtime_device"] == "gpu"
    assert info["installed_engines"] == ("paddle",)
    client.inspect.assert_called_once_with()


def test_gpu_worker_fused_uses_engine_envs(_cleanup, qtbot, tmp_path, monkeypatch):
    """融合形态：探测不触碰 Runtime Installer，设备与引擎来自引擎环境。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)

    def _boom(*_args, **_kwargs):
        raise AssertionError("融合形态不应构造 RuntimeInstallerClient")

    monkeypatch.setattr(rew, "RuntimeInstallerClient", _boom, raising=False)
    monkeypatch.setattr(
        rew,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        },
    )

    from vibeocr.classic.engine_envs import EngineEnvState

    def _fake_init(self, root):
        self._root = root

    def _fake_inspect(self):
        root = self._root
        return {
            f"{family}-{device}": EngineEnvState(
                spec_id=f"{family}-{device}",
                installed=family == "paddle" and device == "gpu",
                env_root=root / f"{family}-{device}",
                python=root / f"{family}-{device}" / "Scripts" / "python.exe",
            )
            for family in ("paddle", "mineru")
            for device in ("cpu", "gpu")
        }

    monkeypatch.setattr(
        "vibeocr.classic.engine_envs.EngineEnvManager.__init__", _fake_init
    )
    monkeypatch.setattr(
        "vibeocr.classic.engine_envs.EngineEnvManager.inspect", _fake_inspect
    )

    worker = rew._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    info = received[0]
    # 基础识别内置即就绪；paddle-gpu 在位 → GPU 设备与 paddle 引擎。
    assert info["runtime_installed"] is True
    assert info["runtime_device"] == "gpu"
    assert info["installed_engines"] == ("paddle",)


def test_gpu_worker_fused_base_only_reports_no_device(
    _cleanup, qtbot, tmp_path, monkeypatch
):
    """融合形态未装任何引擎：就绪但计算设备为“未选择”。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)

    def _boom(*_args, **_kwargs):
        raise AssertionError("融合形态不应构造 RuntimeInstallerClient")

    monkeypatch.setattr(rew, "RuntimeInstallerClient", _boom, raising=False)
    monkeypatch.setattr(
        rew,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": True,
            "name": "NVIDIA GeForce RTX 4090",
            "vram_mb": 24564,
            "cuda": "12.6",
        },
    )

    from vibeocr.classic.engine_envs import EngineEnvManager

    def _fake_inspect(self):
        return {
            spec_id: type("State", (), {"installed": False})()
            for spec_id in (
                "paddle-cpu",
                "paddle-gpu",
                "mineru-cpu",
                "mineru-gpu",
            )
        }

    monkeypatch.setattr(EngineEnvManager, "inspect", _fake_inspect)

    worker = rew._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    info = received[0]
    assert info["runtime_installed"] is True
    assert info["runtime_device"] is None
    assert info["installed_engines"] == ()


def test_gpu_worker_fused_inspect_failure_still_emits(
    _cleanup, qtbot, tmp_path, monkeypatch
):
    """融合形态引擎环境检查抛错时仍发 finished_info。

    检查异常（如随包 wheel/清单不可读）不得让 worker 静默退出：
    否则控件永久停在"检测中"，选项与应用按钮保持禁用。
    """

    from vibeocr.classic.widgets import runtime_environment_widget as rew

    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)
    monkeypatch.setattr(
        rew,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        },
    )

    def _raise(self, _root):
        raise RuntimeError("resources unreadable")

    monkeypatch.setattr("vibeocr.classic.engine_envs.EngineEnvManager.__init__", _raise)

    worker = rew._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.run()

    info = received[0]
    assert info["runtime_installed"] is False
    assert info["runtime_device"] is None


def test_refresh_waits_for_running_detection_then_reads_runtime_again(
    _cleanup, qtbot, tmp_path
):
    """探测进行中收到刷新请求：先取消，worker 结束后再启动新一轮。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    orig_worker = rew._GpuDetectWorker
    rew._GpuDetectWorker = _RunningStubGpuDetectWorker
    try:
        widget = rew.RuntimeEnvironmentWidget(tmp_path)
        qtbot.addWidget(widget)
        worker = widget._detect_worker
        assert worker is not None and worker.isRunning()

        widget.refresh_runtime_state()

        assert worker.cancel_called
        assert widget._detect_worker is worker, "旧 worker 结束前不得启动新一轮"

        worker.finished.emit()
    finally:
        rew._GpuDetectWorker = orig_worker

    qtbot.waitUntil(
        lambda: (
            widget._detect_worker is not None and widget._detect_worker is not worker
        )
    )


def test_close_stops_running_gpu_detection_worker(_cleanup, qtbot, tmp_path):
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    orig_worker = rew._GpuDetectWorker
    rew._GpuDetectWorker = _RunningStubGpuDetectWorker
    try:
        widget = rew.RuntimeEnvironmentWidget(tmp_path)
        qtbot.addWidget(widget)
        worker = widget._detect_worker
        assert worker is not None

        widget.request_gpu_detection_shutdown()

        assert worker.cancel_called
        assert worker.quit_called
        assert widget.is_gpu_detection_drained() is False
    finally:
        rew._GpuDetectWorker = orig_worker


def test_close_drops_late_gpu_detection_result(_cleanup, qtbot, tmp_path):
    """关闭后迟到的探测结果不得回填已释放的控件状态。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    orig_worker = rew._GpuDetectWorker
    rew._GpuDetectWorker = _RunningStubGpuDetectWorker
    try:
        widget = rew.RuntimeEnvironmentWidget(tmp_path)
        qtbot.addWidget(widget)
        worker = widget._detect_worker
        assert worker is not None

        widget.request_gpu_detection_shutdown()
        worker.finished_info.emit(
            {
                "has_gpu": True,
                "runtime_installed": True,
                "runtime_device": "gpu",
                "runtime_profile": "win-x64-cu126",
                "installed_engines": ("paddle",),
                "device_preference": None,
            }
        )

        assert widget.current_backend() is None, "迟到结果不得回填"
    finally:
        rew._GpuDetectWorker = orig_worker


def test_cancelled_gpu_worker_does_not_emit_result(
    _cleanup, qtbot, tmp_path, monkeypatch
):
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    monkeypatch.setattr(
        rew,
        "detect_gpu_info",
        lambda **_kwargs: {
            "has_gpu": True,
            "name": "NVIDIA GeForce RTX 4090",
            "vram_mb": 24564,
            "cuda": "cu126",
        },
    )
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)

    def _raise(self, _root):
        raise RuntimeError("no envs")

    monkeypatch.setattr("vibeocr.classic.engine_envs.EngineEnvManager.__init__", _raise)

    worker = rew._GpuDetectWorker(tmp_path)
    received: list[dict] = []
    worker.finished_info.connect(received.append)

    worker.cancel()
    worker.run()

    assert received == []


def test_gpu_worker_is_kept_until_finished_then_released(_cleanup, qtbot, tmp_path):
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_device="gpu",
        worker_cls=_RunningStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    worker = widget._detect_worker
    assert worker is not None
    assert worker in rew._ACTIVE_GPU_DETECT_WORKERS

    widget.close()
    assert worker in rew._ACTIVE_GPU_DETECT_WORKERS
    worker._running = False
    worker.finished.emit()

    qtbot.waitUntil(lambda: worker not in rew._ACTIVE_GPU_DETECT_WORKERS, timeout=1000)
    assert widget._detect_worker is None


def test_running_qthread_outlives_widget_and_releases_after_finished(
    _cleanup, qtbot, tmp_path
):
    """控件销毁后运行中的 QThread wrapper 仍被模块级引用保活。"""
    from vibeocr.classic.widgets import runtime_environment_widget as rew

    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        runtime_device="gpu",
        worker_cls=_SlowQThreadGpuDetectWorker,
    )
    worker = widget._detect_worker
    assert isinstance(worker, _SlowQThreadGpuDetectWorker)
    assert worker.entered.wait(timeout=1)
    assert worker.parent() is None
    assert worker in rew._ACTIVE_GPU_DETECT_WORKERS

    started = time.perf_counter()
    widget.close()
    elapsed_ms = (time.perf_counter() - started) * 1000
    widget.deleteLater()
    qtbot.wait(10)
    assert elapsed_ms < 150
    assert worker.isRunning()

    worker.release.set()
    qtbot.waitUntil(lambda: worker not in rew._ACTIVE_GPU_DETECT_WORKERS, timeout=1000)
