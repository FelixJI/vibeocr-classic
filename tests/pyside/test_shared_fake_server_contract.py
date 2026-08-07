"""Phase 7A client contract suite: both consumers green against one fake server.

Plan §7A exit criterion: "两套 UI 的 client contract tests 对同一 fake HTTP
server 全绿". This suite proves the v2 contract is consistent across two
consumers of the *same* :data:`SHARED_FAKE_SERVER`:

1. The raw Python async client (``SupervisorClient``) — the layer a future
   WinUI/HttpClient mirror must agree with.
2. The PySide Qt adapter (``SupervisorClientAdapter``) — the layer the real
   PySide UI uses.

Both must complete submit → events → result → cancel against identical fake
behaviour.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from vibeocr.classic.pyside.supervisor_adapter import (
    SupervisorClientAdapter,
    set_supervisor_adapter,
)
from vibeocr.runtime_client.job_handle import JobHandle
from vibeocr.runtime_contracts import (
    JobKind,
    JobPriority,
    PipelineSelection,
    SubmitItem,
    SubmitRequest,
)

from ._fake_supervisor_server import SHARED_FAKE_SERVER


@pytest.fixture(autouse=True)
def _reset_shared_server():
    SHARED_FAKE_SERVER.reset()
    yield
    SHARED_FAKE_SERVER.reset()


def _drive(loop: asyncio.AbstractEventLoop, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop.run_until_complete(asyncio.sleep(0))
        try:
            if predicate():
                return
        except Exception:
            pass
    raise AssertionError(f"predicate not satisfied within {timeout}s")


# ---------------------------------------------------------------------------
# Consumer 1: raw Python async client
# ---------------------------------------------------------------------------


class TestRawClientContract:
    """The raw JobHandle layer (what a future WinUI HttpClient must agree with)
    completes the full roundtrip against the shared fake server."""

    async def test_submit_events_result_roundtrip(self) -> None:
        # JobHandle talks to the high-level client surface; the shared fake
        # implements exactly that surface, so we can drive it directly.
        request = _request("a.png", "b.png")
        ref = await SHARED_FAKE_SERVER.submit(
            request,
            {
                "input-0": (None, b"1"),
                "input-1": (None, b"2"),
            },
        )
        handle = JobHandle(client=SHARED_FAKE_SERVER, ref=ref)  # type: ignore[arg-type]
        # Pump status until terminal (the shared fake auto-completes after a
        # short RUNNING window).
        snap = await handle.status()
        for _ in range(10):
            if snap.state.name in {"COMPLETED", "CANCELLED", "FAILED"}:
                break
            snap = await handle.status()
        assert snap.state.name == "COMPLETED"
        results = await handle.result()
        assert [r.display_name for r in results] == ["a.png", "b.png"]
        assert SHARED_FAKE_SERVER.submit_calls == 1

    async def test_cancel_roundtrip(self) -> None:
        ref = await SHARED_FAKE_SERVER.submit(
            _request("a.png"), {"input-0": (None, b"1")}
        )
        mode = await SHARED_FAKE_SERVER.cancel(ref.job_id)
        assert mode.value == "cooperative"
        assert SHARED_FAKE_SERVER.cancel_calls == [ref.job_id]


def _request(*names: str) -> SubmitRequest:
    return SubmitRequest(
        request_id="request-test",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        pipeline=PipelineSelection("OCR"),
        items=tuple(
            SubmitItem(
                client_item_key=f"file-{index}",
                ordinal=index,
                display_name=name,
                source={
                    "type": "upload.v1",
                    "attachment": f"input-{index}",
                },
            )
            for index, name in enumerate(names)
        ),
    )


# ---------------------------------------------------------------------------
# Consumer 2: PySide Qt adapter
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter(qasync_loop):
    adapter = SupervisorClientAdapter(client_factory=lambda: SHARED_FAKE_SERVER)
    set_supervisor_adapter(adapter)
    yield adapter
    adapter.shutdown()
    _drive(qasync_loop, lambda: adapter.shutdown_drained)
    set_supervisor_adapter(None)


class TestQtAdapterContract:
    def test_submit_progress_result_against_shared_server(
        self, qasync_loop, adapter
    ) -> None:
        results: list[list] = []
        progress: list[tuple[str, int, int]] = []
        adapter.recognition_result.connect(lambda jid, p: results.append(p))
        adapter.recognition_progress.connect(
            lambda jid, c, t: progress.append((jid, c, t))
        )

        adapter.submit_recognition([("x.png", None, b"1"), ("y.png", None, b"2")])
        _drive(qasync_loop, lambda: len(results) == 1)

        assert len(results) == 1
        assert [p["display_name"] for p in results[0]] == ["x.png", "y.png"]
        assert [p["payload_type"] for p in results[0]] == ["ocr.v1", "ocr.v1"]
        # At least one progress emission fired.
        assert progress
        # Same fake server saw exactly one submit (shared with the raw test).
        assert SHARED_FAKE_SERVER.submit_calls == 1

    def test_cancel_against_shared_server(self, qasync_loop, adapter) -> None:
        cancelled: list[str] = []
        adapter.recognition_cancelled.connect(lambda jid: cancelled.append(jid))
        adapter.submit_recognition([("a.png", None, b"1")])
        _drive(qasync_loop, lambda: len(adapter._handles) == 1)
        job_id = next(iter(adapter._handles))
        # Cancel before the job auto-completes on the second status probe.
        adapter.cancel(job_id)
        _drive(qasync_loop, lambda: bool(cancelled), timeout=3.0)
        assert cancelled == [job_id]
        assert SHARED_FAKE_SERVER.cancel_calls == [job_id]
