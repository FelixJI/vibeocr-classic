"""Supervisor-only lifecycle regression tests for SubprocessManager."""

from types import SimpleNamespace
from unittest.mock import Mock

from vibeocr.classic.managers.subprocess_manager import SubprocessManager


def test_started_transfers_process_owner_before_task_is_released(
    qapp, tmp_path, monkeypatch
) -> None:
    manager = SubprocessManager(tmp_path)
    process = Mock()
    task = SimpleNamespace(
        supervisor_proc=process,
        required_capabilities=("ocr.recognition.v2",),
    )
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


def test_external_shutdown_callable_closes_adapter_and_owned_process(
    qapp, tmp_path, monkeypatch
) -> None:
    manager = SubprocessManager(tmp_path)
    process = Mock()
    adapter = Mock()
    manager._supervisor_process = process
    manager._is_ready = True
    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: adapter,
    )

    shutdown = manager.take_shutdown_callable()

    assert callable(shutdown)
    assert manager._supervisor_process is None
    assert manager.is_ready is False
    shutdown()
    adapter.shutdown.assert_called_once_with()
    process.shutdown.assert_called_once_with()


def test_synchronous_shutdown_closes_transferred_process(qapp, tmp_path) -> None:
    manager = SubprocessManager(tmp_path)
    process = Mock()
    manager._supervisor_process = process
    manager._is_ready = True

    assert manager.shutdown(timeout_ms=100) is True

    process.shutdown.assert_called_once_with()
    assert manager._supervisor_process is None
    assert manager.is_ready is False
