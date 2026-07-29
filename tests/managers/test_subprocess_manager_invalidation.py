"""Maintenance invalidation must not block the GUI or lose process ownership."""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

from vibeocr.classic.managers.subprocess_manager import SubprocessManager


def test_invalidate_supervisor_returns_before_slow_shutdown(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    manager = SubprocessManager(tmp_path)
    process = Mock()
    adapter = Mock()
    manager._is_ready = True
    manager._supervisor_process = process
    entered = threading.Event()
    release = threading.Event()

    def slow_wait(_timeout_ms: int) -> bool:
        entered.set()
        return release.wait(2)

    manager._thread_pool.waitForDone = slow_wait
    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: adapter,
    )

    started = time.monotonic()
    assert manager.invalidate_supervisor() is True
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    qtbot.waitUntil(entered.is_set, timeout=1000)
    assert process.shutdown.call_count == 0

    release.set()
    qtbot.waitUntil(lambda: process.shutdown.call_count == 1, timeout=2000)
    assert manager.is_ready is False


def test_failed_invalidation_keeps_process_owned_for_retry(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    manager = SubprocessManager(tmp_path)
    process = Mock()
    process.shutdown.side_effect = RuntimeError("still running")
    adapter = Mock()
    manager._is_ready = True
    manager._supervisor_process = process
    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: adapter,
    )
    outcomes: list[tuple[bool, str]] = []
    manager.invalidation_finished.connect(
        lambda success, error: outcomes.append((success, error))
    )

    assert manager.invalidate_supervisor() is True
    qtbot.waitUntil(lambda: bool(outcomes), timeout=2000)

    assert outcomes[0][0] is False
    assert "still running" in outcomes[0][1]
    assert manager._supervisor_process is process

    process.shutdown.side_effect = None
    assert manager.invalidate_supervisor() is True
    qtbot.waitUntil(lambda: len(outcomes) == 2, timeout=2000)
    assert outcomes[1] == (True, "")
    assert manager._supervisor_process is None
