"""Process-wide keepalive and shutdown boundary for parentless dialog workers."""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_ACTIVE_DIALOG_WORKERS: set[Any] = set()


def track_dialog_worker(worker: Any) -> None:
    """Keep a QThread alive until its native ``finished`` signal is delivered."""
    with _LOCK:
        _ACTIVE_DIALOG_WORKERS.add(worker)
    worker.finished.connect(
        lambda *_args, current=worker: release_dialog_worker(current)
    )


def release_dialog_worker(worker: Any) -> None:
    with _LOCK:
        _ACTIVE_DIALOG_WORKERS.discard(worker)
    worker.deleteLater()


def request_dialog_workers_shutdown() -> None:
    """Request cooperative cancellation without waiting on the GUI thread."""
    with _LOCK:
        workers = tuple(_ACTIVE_DIALOG_WORKERS)
    for worker in workers:
        request_cancel = getattr(worker, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()


def are_dialog_workers_drained() -> bool:
    """Require registry release so queued completion callbacks are also drained."""
    with _LOCK:
        return not _ACTIVE_DIALOG_WORKERS


def active_dialog_workers() -> tuple[Any, ...]:
    """Expose a read-only snapshot for diagnostics and regression tests."""
    with _LOCK:
        return tuple(_ACTIVE_DIALOG_WORKERS)


__all__ = [
    "active_dialog_workers",
    "are_dialog_workers_drained",
    "request_dialog_workers_shutdown",
    "track_dialog_worker",
]
