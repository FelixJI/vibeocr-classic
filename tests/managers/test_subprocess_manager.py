"""Supervisor-only SubprocessManager tests."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vibeocr.classic.managers.subprocess_manager import (
    SubprocessManager,
    SubprocessStartSignals,
    SupervisorStartTask,
)


def test_start_signals_expose_ready_and_progress(qapp) -> None:
    signals = SubprocessStartSignals()
    assert hasattr(signals, "started")
    assert hasattr(signals, "progress")


def test_start_task_cancel_uses_thread_safe_event() -> None:
    task = SupervisorStartTask("python")
    task.cancel()
    assert task._cancelled.is_set()


def test_start_task_reports_process_and_handshake_stage(monkeypatch) -> None:
    task = SupervisorStartTask("python")
    progress: list[str] = []
    started: list[bool] = []
    task.signals.progress.connect(progress.append)
    task.signals.started.connect(started.append)
    monkeypatch.setattr(
        "vibeocr.runtime_client.process.SupervisorProcess.launch",
        lambda **_kwargs: Mock(),
    )

    task.run()

    assert progress == ["正在创建子进程并等待就绪握手"]
    assert started == [True]


@pytest.fixture()
def manager(qapp, tmp_path):
    (tmp_path / "component-lock.json").write_text(
        '{"required_capabilities":["ocr.recognition.v2"]}',
        encoding="utf-8",
    )
    instance = SubprocessManager(tmp_path)
    yield instance
    instance.shutdown(timeout_ms=100)


def test_manager_has_one_readiness_source(manager: SubprocessManager) -> None:
    assert manager.is_ready is False
    assert hasattr(manager, "service_ready")
    assert hasattr(manager, "progress_update")
    assert not hasattr(manager, "service")
    assert not hasattr(manager, "preload_finished")
    assert not hasattr(manager, "preload_pipelines")


def test_start_creates_one_background_task(manager: SubprocessManager) -> None:
    manager._thread_pool.start = Mock()
    manager.start_supervisor()

    assert isinstance(manager._start_task, SupervisorStartTask)
    manager._thread_pool.start.assert_called_once_with(manager._start_task)


def test_t6_smoke_uses_explicit_test_python(
    manager: SubprocessManager, monkeypatch, tmp_path
) -> None:
    test_python = tmp_path / "release-python.exe"
    monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")
    monkeypatch.setenv("VIBEOCR_SELF_TEST_PYTHON", str(test_python))
    manager._thread_pool.start = Mock()

    manager.start_supervisor()

    assert manager._start_task._python_exe == str(test_python)


def test_production_start_ignores_test_python_override(
    manager: SubprocessManager, monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)
    monkeypatch.setenv(
        "VIBEOCR_SELF_TEST_PYTHON", str(tmp_path / "untrusted-python.exe")
    )
    manager._thread_pool.start = Mock()

    manager.start_supervisor()

    assert manager._start_task._python_exe is None
    assert manager._start_task._installer_client is manager._installer_client


def test_start_is_idempotent_when_ready_or_starting(
    manager: SubprocessManager,
) -> None:
    manager._thread_pool.start = Mock()
    manager._is_ready = True
    manager.start_supervisor()
    manager._thread_pool.start.assert_not_called()

    manager._is_ready = False
    manager._start_task = SupervisorStartTask("python")
    manager.start_supervisor()
    manager._thread_pool.start.assert_not_called()


def test_worker_start_keeps_qt_event_loop_responsive(
    manager: SubprocessManager, qapp, qtbot, monkeypatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    expected_python = manager._project_root / "runtime" / "python.exe"
    launch_kwargs: dict[str, object] = {}
    fake_process = Mock(
        base_url="http://127.0.0.1:54321",
        session_token="test-token",
        ready=SimpleNamespace(
            instance_id="sup-test",
            capabilities=("ocr.recognition.v2", "future.feature.v3"),
        ),
    )
    manager._installer_client.ensure = Mock(
        return_value=SimpleNamespace(
            python_executable=str(expected_python),
            supervisor_module="vibeocr.backend.supervisor.main",
            working_directory=str(manager._project_root),
            environment={"VIBEOCR_RUNTIME_ROOT": str(expected_python.parent)},
        )
    )

    def slow_launch(**kwargs):
        launch_kwargs.update(kwargs)
        entered.set()
        assert release.wait(5)
        return fake_process

    monkeypatch.setattr(
        "vibeocr.runtime_client.process.SupervisorProcess.launch",
        slow_launch,
    )
    ready: list[bool] = []
    manager.service_ready.connect(ready.append)

    manager.start_supervisor()
    qtbot.waitUntil(entered.is_set, timeout=2000)

    from PySide6.QtCore import QTimer

    gui_tick: list[bool] = []
    QTimer.singleShot(0, lambda: gui_tick.append(True))
    qtbot.waitUntil(lambda: bool(gui_tick), timeout=1000)

    release.set()
    qtbot.waitUntil(lambda: ready == [True], timeout=3000)
    assert manager.is_ready is True
    assert manager._supervisor_process is fake_process
    assert launch_kwargs["python_exe"] == str(expected_python)
    assert launch_kwargs["module"] == "vibeocr.backend.supervisor.main"
    assert launch_kwargs["working_directory"] == str(manager._project_root)
    assert launch_kwargs["extra_env"]["VIBEOCR_RUNTIME_ROOT"] == str(
        expected_python.parent
    )
    assert manager._installer_client.ensure.call_args.kwargs[
        "required_capabilities"
    ] == ("ocr.recognition.v2",)
    assert (
        manager._installer_client.ensure.call_args.kwargs["install_component_ids"] == ()
    )
    from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

    assert get_supervisor_adapter().thread() is qapp.thread()


def test_on_started_transfers_process_owner(
    manager: SubprocessManager, monkeypatch
) -> None:
    process = Mock()
    task = SupervisorStartTask(
        "python",
        required_capabilities=("ocr.recognition.v2",),
    )
    task.supervisor_proc = process
    manager._start_task = task
    install_adapter = Mock()
    monkeypatch.setattr(manager, "_install_runtime_adapter", install_adapter)

    manager._on_started(True)

    install_adapter.assert_called_once_with(
        process,
        required_capabilities=("ocr.recognition.v2",),
    )
    assert manager.is_ready is True
    assert manager._supervisor_process is process
    assert task.supervisor_proc is None
    assert manager._start_task is None


def test_runtime_adapter_receives_only_ready_endpoint_identity(monkeypatch) -> None:
    from vibeocr.classic.pyside.supervisor_adapter import SupervisorClientAdapter

    process = SimpleNamespace(
        base_url="http://127.0.0.1:43210",
        session_token="token",
        ready=SimpleNamespace(
            instance_id="runtime-1",
            capabilities=("ocr.recognition.v2", "future.feature.v3"),
        ),
    )
    adapter = Mock()
    factory = Mock(return_value=adapter)
    monkeypatch.setattr(
        SupervisorClientAdapter,
        "from_runtime_endpoint",
        factory,
    )

    SubprocessManager._install_runtime_adapter(
        process,
        required_capabilities=("ocr.recognition.v2",),
    )

    factory.assert_called_once_with(
        base_url=process.base_url,
        session_token=process.session_token,
        instance_id=process.ready.instance_id,
    )
    adapter.start.assert_called_once_with()


def test_runtime_adapter_rejects_missing_product_capability(monkeypatch) -> None:
    from vibeocr.classic.pyside.supervisor_adapter import SupervisorClientAdapter

    process = SimpleNamespace(
        base_url="http://127.0.0.1:43210",
        session_token="token",
        ready=SimpleNamespace(
            instance_id="runtime-1",
            capabilities=("ocr.recognition.v2",),
        ),
    )
    factory = Mock()
    monkeypatch.setattr(
        SupervisorClientAdapter,
        "from_runtime_endpoint",
        factory,
    )

    with pytest.raises(RuntimeError, match="pdf.edit.v2"):
        SubprocessManager._install_runtime_adapter(
            process,
            required_capabilities=("ocr.recognition.v2", "pdf.edit.v2"),
        )

    factory.assert_not_called()


def test_on_started_missing_capability_shuts_down_process(
    manager: SubprocessManager,
) -> None:
    process = SimpleNamespace(
        base_url="http://127.0.0.1:43210",
        session_token="token",
        ready=SimpleNamespace(instance_id="runtime-1", capabilities=()),
        shutdown=Mock(),
    )
    task = SupervisorStartTask(
        "python",
        required_capabilities=("ocr.recognition.v2",),
    )
    task.supervisor_proc = process
    manager._start_task = task

    manager._on_started(True)

    process.shutdown.assert_called_once_with()
    assert manager.is_ready is False
    assert manager._supervisor_process is None
    assert manager._start_task is None


def test_on_started_failure_clears_task(manager: SubprocessManager) -> None:
    manager._start_task = SupervisorStartTask("python")
    manager._on_started(False)
    assert manager.is_ready is False
    assert manager._start_task is None


def test_late_started_signal_is_ignored_after_shutdown(
    manager: SubprocessManager,
) -> None:
    task = SupervisorStartTask("python")
    task.supervisor_proc = Mock()
    manager._start_task = task
    manager.request_shutdown()

    manager._on_started(True)

    assert manager.is_ready is False
    assert manager._start_task is task


def test_take_shutdown_callable_closes_adapter_then_process(
    manager: SubprocessManager, monkeypatch
) -> None:
    calls: list[str] = []
    adapter = Mock()
    adapter.shutdown.side_effect = lambda: calls.append("adapter")
    process = Mock()
    process.shutdown.side_effect = lambda: calls.append("process")
    manager._is_ready = True
    manager._supervisor_process = process
    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: adapter,
    )

    shutdown = manager.take_shutdown_callable()

    assert shutdown is not None
    shutdown()
    assert calls == ["adapter", "process"]
    assert manager.is_ready is False
    assert manager._supervisor_process is None


def test_take_shutdown_callable_recovers_process_from_cancelled_task(
    manager: SubprocessManager, monkeypatch
) -> None:
    adapter = Mock()
    process = Mock()
    task = SupervisorStartTask("python")
    task.supervisor_proc = process
    manager._start_task = task
    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: adapter,
    )

    shutdown = manager.take_shutdown_callable()

    assert shutdown is not None
    shutdown()
    adapter.shutdown.assert_called_once_with()
    process.shutdown.assert_called_once_with()
    assert task.supervisor_proc is None


def test_shutdown_cancels_start_task_and_resets_state(
    manager: SubprocessManager,
) -> None:
    task = SupervisorStartTask("python")
    task.cancel = Mock()
    manager._start_task = task
    manager._is_ready = True

    assert manager.shutdown(timeout_ms=100) is True

    task.cancel.assert_called_once_with()
    assert manager.is_ready is False
    assert manager._start_task is None
