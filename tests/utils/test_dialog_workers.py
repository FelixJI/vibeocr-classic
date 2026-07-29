"""父对象已关闭后，对话框 QThread 的原生完成边界回归。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from vibeocr.classic.utils.dialog_workers import (
    active_dialog_workers,
    track_dialog_worker,
)


class _WorkerWithBusinessTerminal(QThread):
    completed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.business_emitted = threading.Event()
        self.release_tail = threading.Event()

    def run(self) -> None:
        self.completed.emit()
        self.business_emitted.set()
        self.release_tail.wait(timeout=2)


def test_registry_waits_for_native_finished_after_business_completed(qtbot):
    worker = _WorkerWithBusinessTerminal()
    track_dialog_worker(worker)
    worker.start()
    qtbot.waitUntil(worker.business_emitted.is_set, timeout=1000)

    assert worker in active_dialog_workers()
    assert worker.isRunning()

    worker.release_tail.set()
    qtbot.waitUntil(lambda: worker not in active_dialog_workers(), timeout=2000)
