"""Delayed asyncio and native executor shutdown ownership regressions."""

from __future__ import annotations

import asyncio
import threading

from vibeocr.classic.utils.qt_async import (
    DelayedAsyncTask,
    are_tracked_native_jobs_drained,
    tracked_to_thread,
)


def test_delayed_task_shutdown_prevents_close_boundary_start():
    loop = asyncio.new_event_loop()
    started: list[bool] = []

    async def operation():
        started.append(True)

    lifecycle = DelayedAsyncTask(loop, 0.01, operation)
    lifecycle.request_shutdown()
    loop.run_until_complete(asyncio.sleep(0.03))
    loop.close()

    assert started == []
    assert lifecycle.is_drained() is True


def test_cancelled_async_task_keeps_native_probe_until_callable_returns():
    entered = threading.Event()
    release = threading.Event()

    def native_operation():
        entered.set()
        release.wait(timeout=2)

    async def scenario():
        task = asyncio.create_task(tracked_to_thread(native_operation))
        while not entered.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert are_tracked_native_jobs_drained() is False
        release.set()
        for _ in range(200):
            if are_tracked_native_jobs_drained():
                break
            await asyncio.sleep(0.005)

    asyncio.run(scenario())

    assert are_tracked_native_jobs_drained() is True
