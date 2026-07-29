"""Reusable assertions for proving that a Qt action did not block the GUI loop."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

if TYPE_CHECKING:
    from collections.abc import Callable


def assert_qt_event_loop_responsive(
    qtbot,
    *,
    in_flight: Callable[[], bool],
    max_latency_ms: int = 150,
) -> float:
    """Assert that a zero-delay Qt timer fires while background work is in flight.

    Call this immediately after the UI action under test returns.  A synchronous
    implementation cannot reach this helper until its slow work completes, so the
    ``in_flight`` assertion also protects against false green timer results.

    Returns:
        Observed timer latency in milliseconds for optional diagnostic assertions.
    """

    fired_at: list[float] = []
    started = time.perf_counter()
    QTimer.singleShot(0, lambda: fired_at.append(time.perf_counter()))
    qtbot.waitUntil(lambda: bool(fired_at), timeout=max_latency_ms)

    elapsed_ms = (fired_at[0] - started) * 1000
    assert in_flight(), "后台慢操作应仍在进行，Qt timer 已先得到处理"
    assert elapsed_ms <= max_latency_ms, (
        f"Qt 事件延迟 {elapsed_ms:.1f}ms 超过 {max_latency_ms}ms"
    )
    return elapsed_ms


__all__ = ["assert_qt_event_loop_responsive"]
