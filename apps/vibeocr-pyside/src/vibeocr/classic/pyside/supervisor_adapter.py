"""Qt-safe application adapter for the unified v2 inference supervisor.

This is the Phase 7A seam that bridges the async HTTP v2 client
(:class:`vibeocr.runtime_client.client.SupervisorClient`) into the PySide Qt
main thread. It owns a long-lived supervisor client context and exposes a
typed Qt-signal surface so UI tabs can submit jobs and receive results,
progress, cancellation and errors **without ever touching the GUI thread
with HTTP I/O**.

Design (see ``specs/2026-07-24-inference-supervisor-rewrite-plan.md`` §7A):

* Submit/long-poll/cancel/result coroutines are dispatched on the existing
  qasync event loop via ``get_async_runner().run(...)`` — we do NOT spawn a
  second asyncio loop. qasync already marshals callbacks onto the GUI thread,
  so the typed signals below fire there.
* A monotonic ``generation`` counter discards stale results: every submit
  bumps it; only the most-recent generation's signals are re-emitted. This
  mirrors the SingleRecognitionTab / image_jobs pattern already in the repo.
* ``shutdown()`` cancels in-flight jobs and closes the client context; it is
  safe to call from ``app.aboutToQuit`` (preferred over ``atexit``).

Production wiring constructs the client from the supervisor process's
ready envelope (``SupervisorProcess``). Tests inject a fake client via the
``client_factory`` parameter so no real subprocess or socket is needed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from vibeocr.classic.utils.qt_async import get_async_runner
from vibeocr.runtime_client.errors import InferenceClientError
from vibeocr.runtime_client.job_handle import JobHandle
from vibeocr.runtime_contracts import (
    TERMINAL_JOB_STATES,
    JobKind,
    JobPriority,
    JobState,
    PipelineSelection,
    SettingsSnapshot,
    StageEvent,
    SubmitItem,
    SubmitRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

    from vibeocr.runtime_client.client import SupervisorClient

logger = logging.getLogger(__name__)


class SupervisorClientAdapter(QObject):
    """Qt-facing façade over :class:`SupervisorClient`.

    All public methods are non-blocking and return immediately; results are
    delivered via the signals below on the GUI thread.
    """

    # --- Recognition signals (Single + Batch share these) ---
    recognition_submitted = Signal(str)  # job_id
    recognition_progress = Signal(str, int, int)  # job_id, current, total
    recognition_stage = Signal(str, str)  # job_id, stage
    recognition_result = Signal(
        str, list
    )  # job_id, ResultEntry payloads (stable order)
    recognition_error = Signal(str, str)  # job_id, error message
    recognition_cancelled = Signal(str)  # job_id

    # --- Runtime/settings signals ---
    residency_status = Signal(object)  # ResidencyStatus
    residency_error = Signal(str)
    settings_updated = Signal(object)  # SettingsSnapshot
    settings_error = Signal(str)
    preload_completed = Signal(object)  # ResidencyStatus
    preload_error = Signal(str)

    @classmethod
    def from_runtime_endpoint(
        cls,
        *,
        base_url: str,
        session_token: str,
        instance_id: str,
        parent: QObject | None = None,
    ) -> SupervisorClientAdapter:
        """Own all Protocol clients for one authenticated Runtime endpoint."""
        from vibeocr.classic.pdf_client import SyncPdfSupervisorClient
        from vibeocr.runtime_client.client import RuntimeHttpClient, SupervisorClient
        from vibeocr.runtime_client.sync_client import SyncSupervisorClient

        client = SupervisorClient(
            base_url=base_url,
            session_token=session_token,
            instance_id=instance_id,
        )
        return cls(
            client_factory=lambda: client,
            pdf_sync_client_factory=lambda: SyncPdfSupervisorClient(
                base_url=base_url,
                session_token=session_token,
                instance_id=instance_id,
            ),
            inference_sync_client_factory=lambda: SyncSupervisorClient(
                base_url=base_url,
                session_token=session_token,
                instance_id=instance_id,
            ),
            runtime_status_client_factory=lambda: RuntimeHttpClient(
                base_url=base_url,
                session_token=session_token,
                timeout=10.0,
            ),
            parent=parent,
        )

    def __init__(
        self,
        *,
        client_factory: Callable[[], SupervisorClient | _AwaitableClient],
        pdf_sync_client_factory: Callable[[], Any] | None = None,
        inference_sync_client_factory: Callable[[], Any] | None = None,
        runtime_status_client_factory: Callable[[], Any] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client_factory = client_factory
        # Optional factory for a SyncPdfSupervisorClient bound to the same
        # supervisor (same base_url + token). When None, PDF session ops are
        # unavailable and PdfSessionManager will surface that as an error.
        self._pdf_sync_client_factory = pdf_sync_client_factory
        self._pdf_sync_client: Any = None
        self._inference_sync_client_factory = inference_sync_client_factory
        self._inference_sync_client: Any = None
        self._runtime_status_client_factory = runtime_status_client_factory
        self._runtime_status_client: Any = None
        self._client: SupervisorClient | None = None
        self._handles: dict[str, JobHandle] = {}
        self._generation = 0
        self._closing = False
        self._runtime_request_timeout_seconds = 10.0
        self._preload_timeout_seconds = 600.0
        self._started = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_task: asyncio.Task[Any] | None = None

    @property
    def pdf_sync_client(self) -> Any:
        """Lazily-built :class:`SyncPdfSupervisorClient` (or None if unwired).

        PdfSessionManager reads this once at construction and treats it as the
        PDF backend transport. The factory is set by the production startup
        (SupervisorStartTask) which knows the supervisor base_url + token.
        """
        if self._pdf_sync_client is None and self._pdf_sync_client_factory is not None:
            self._pdf_sync_client = self._pdf_sync_client_factory()
        return self._pdf_sync_client

    @property
    def inference_sync_client(self) -> Any:
        """Generic blocking job client for non-async worker threads."""
        if (
            self._inference_sync_client is None
            and self._inference_sync_client_factory is not None
        ):
            self._inference_sync_client = self._inference_sync_client_factory()
        return self._inference_sync_client

    @property
    def runtime_status_client(self) -> Any:
        """认证的同步 HTTP 客户端，供后台线程读取统一运行状态。"""
        if (
            self._runtime_status_client is None
            and self._runtime_status_client_factory is not None
        ):
            self._runtime_status_client = self._runtime_status_client_factory()
        return self._runtime_status_client

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        """Whether the supervisor has been started (production client attached).

        False until the Phase 8 atomic switch calls ``start()``; until then
        the PySide tabs use their legacy backend path so the app keeps working.
        """
        return self._started and not self._closing

    def start(self) -> None:
        """Mark the supervisor as started (production client attached).

        Called by the Phase 8 atomic switch once the supervisor process is up
        and the client is ready. After this, PySide tabs route to v2 by default.
        """
        self._started = True

    def stop(self) -> None:
        """Mark the supervisor as stopped (client detached)."""
        self._started = False

    def _ensure_client(self) -> SupervisorClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def _acquire_client(self) -> SupervisorClient:
        """Return a usable supervisor client.

        If the client exposes an async context manager entry (production
        ``SupervisorClient``), enter it lazily so the underlying httpx
        transport is initialised once.
        """
        client = self._ensure_client()
        # Production SupervisorClient is an async context manager. Tests pass a
        # bare object whose methods are awaitable; it does not need entering.
        if hasattr(client, "__aenter__") and getattr(client, "_client", None) is None:
            await client.__aenter__()
        return client

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
        """Schedule and retain adapter-owned work until it reaches terminal state."""
        if self._closing:
            coro.close()
            return None
        task = get_async_runner().run(coro)
        self._loop = task.get_loop()
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def shutdown(self) -> None:
        """Cancel in-flight jobs and close the client. Idempotent."""
        if self._closing:
            return
        self._closing = True
        owned_tasks = tuple(self._tasks)

        # Do not let ``get_async_runner`` manufacture a standard event loop
        # during late teardown.  Such a loop has no owner to advance it and
        # would strand both the wrapper and ``_drain`` coroutines.  Production
        # calls arrive on a running qasync loop; tests may install a qasync
        # loop and advance it explicitly.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop
        can_schedule = (
            loop is not None
            and not loop.is_closed()
            and (
                loop.is_running()
                or loop.__class__.__module__.split(".", maxsplit=1)[0] == "qasync"
            )
        )
        if not can_schedule:
            for task in owned_tasks:
                if not task.done():
                    task.cancel()
            self._tasks.clear()
            self._handles.clear()
            self._client = None
            for sync_client in (
                self._pdf_sync_client,
                self._inference_sync_client,
                self._runtime_status_client,
            ):
                try:
                    if sync_client is not None:
                        sync_client.close()
                except Exception:  # pragma: no cover - best effort
                    pass
            self._pdf_sync_client = None
            self._inference_sync_client = None
            self._runtime_status_client = None
            self._loop = None
            return

        runner = get_async_runner()

        async def _drain() -> None:
            try:
                for _job_id, handle in list(self._handles.items()):
                    try:
                        await handle.cancel()
                    except Exception:  # pragma: no cover - best effort
                        pass
                for task in owned_tasks:
                    if not task.done():
                        task.cancel()
                if owned_tasks:
                    await asyncio.gather(*owned_tasks, return_exceptions=True)
                if self._client is not None and hasattr(self._client, "__aexit__"):
                    try:
                        await self._client.__aexit__(None, None, None)
                    except Exception:  # pragma: no cover - best effort
                        pass
                for sync_client in (
                    self._pdf_sync_client,
                    self._inference_sync_client,
                    self._runtime_status_client,
                ):
                    if sync_client is not None:
                        try:
                            sync_client.close()
                        except Exception:  # pragma: no cover - best effort
                            pass
            finally:
                self._tasks.clear()
                self._handles.clear()
                self._client = None
                self._pdf_sync_client = None
                self._inference_sync_client = None
                self._runtime_status_client = None
                self._loop = None

        try:
            self._shutdown_task = runner.run(_drain())
        except RuntimeError:
            # No usable loop (e.g. late interpreter teardown).
            for task in owned_tasks:
                if not task.done():
                    task.cancel()
            self._tasks.clear()
            self._handles.clear()
            self._client = None
            for sync_client in (
                self._pdf_sync_client,
                self._inference_sync_client,
                self._runtime_status_client,
            ):
                try:
                    if sync_client is not None:
                        sync_client.close()
                except Exception:  # pragma: no cover - best effort
                    pass
            self._pdf_sync_client = None
            self._inference_sync_client = None
            self._runtime_status_client = None
            self._loop = None

    @property
    def shutdown_drained(self) -> bool:
        """Whether asynchronous shutdown has reached its terminal state."""
        task = self._shutdown_task
        return (
            self._closing
            and (task is None or task.done())
            and not any(not owned.done() for owned in self._tasks)
        )

    # ------------------------------------------------------------------
    # Recognition (single = one-element batch; batch = one logical job)
    # ------------------------------------------------------------------

    def submit_recognition(
        self,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority = JobPriority.INTERACTIVE,
        pipeline: PipelineSelection | None = None,
    ) -> int:
        """Submit a recognition job (one or many inputs).

        Returns the ``generation`` so callers can scope stale-result guards.
        Emits ``recognition_submitted`` then ``recognition_progress``/
        ``recognition_stage``/``recognition_result`` / ``recognition_error``.
        """
        self._generation += 1
        generation = self._generation

        async def _run() -> None:
            try:
                client = await self._acquire_client()
                ref = await self._submit_job(
                    client,
                    uploads,
                    priority=priority,
                    pipeline=pipeline,
                )
                handle = JobHandle(client=client, ref=ref)
                self._handles[ref.job_id] = handle
                if generation == self._generation:
                    self.recognition_submitted.emit(ref.job_id)
                await self._pump_until_terminal(handle, generation)
            except InferenceClientError as exc:
                if generation == self._generation:
                    self.recognition_error.emit("", exc.message)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("recognition submit failed")
                if generation == self._generation:
                    self.recognition_error.emit("", str(exc))

        self._schedule(_run())
        return generation

    async def recognize(
        self,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority = JobPriority.INTERACTIVE,
        pipeline: PipelineSelection | None = None,
    ) -> list:
        """Submit and await a logical job without exposing HTTP to UI code."""
        client = await self._acquire_client()
        ref = await self._submit_job(
            client, uploads, priority=priority, pipeline=pipeline
        )
        handle = JobHandle(client=client, ref=ref)
        self._handles[ref.job_id] = handle
        await handle.wait_for_terminal()
        return await handle.result()

    @staticmethod
    async def _submit_job(
        client,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority,
        pipeline: PipelineSelection | None,
    ):
        selection = pipeline or PipelineSelection("OCR")
        request_id = str(uuid4())
        items = tuple(
            SubmitItem(
                client_item_key=f"{request_id}:{index}",
                ordinal=index,
                display_name=name,
                source={
                    "type": "upload.v1",
                    "attachment": f"input-{index}",
                },
            )
            for index, (name, _content_type, _data) in enumerate(uploads)
        )
        request = SubmitRequest(
            request_id=request_id,
            kind=(
                JobKind.MINERU_PARSE
                if selection.pipeline_id == "MinerU"
                else JobKind.RECOGNITION
            ),
            priority=priority,
            pipeline=selection,
            items=items,
        )
        attachments = {
            f"input-{index}": (content_type, data)
            for index, (_name, content_type, data) in enumerate(uploads)
        }
        return await client.submit(request, attachments)

    async def _pump_until_terminal(self, handle: JobHandle, generation: int) -> None:
        """Long-poll events until terminal, emitting progress/stage/result."""
        job_id = handle.job_id
        last_seq = 0
        while True:
            try:
                update = await handle.observe(after_sequence=last_seq)
                snap = update.snapshot
            except InferenceClientError as exc:
                if generation == self._generation:
                    self.recognition_error.emit(job_id, exc.message)
                return
            if generation == self._generation:
                self.recognition_progress.emit(
                    job_id, snap.progress_current, snap.progress_total
                )
                if snap.stage:
                    self.recognition_stage.emit(job_id, snap.stage)
            events: Iterable[StageEvent] = update.events
            for event in events:
                if generation == self._generation and event.stage:
                    self.recognition_stage.emit(job_id, event.stage)
            last_seq = update.through_sequence
            if snap.state in TERMINAL_JOB_STATES:
                break
            # Yield to the event loop between long-poll cycles so other
            # coroutines (e.g. cancel, shutdown) can run. Without this a
            # tight loop of instantly-completing awaits starves them.
            await asyncio.sleep(0.02)
        # Terminal: emit result or terminal signal.
        if generation != self._generation:
            return
        try:
            final_snap = await handle.status()
        except InferenceClientError:
            final_snap = None
        if final_snap is not None and final_snap.state is JobState.CANCELLED:
            self.recognition_cancelled.emit(job_id)
            return
        try:
            results = await handle.result()
        except InferenceClientError as exc:
            self.recognition_error.emit(job_id, exc.message)
            return
        self.recognition_result.emit(job_id, [r.to_payload() for r in results])

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel(self, job_id: str) -> None:
        async def _cancel() -> None:
            handle = self._handles.get(job_id)
            if handle is None:
                return
            try:
                await handle.cancel()
            except InferenceClientError:
                pass

        self._schedule(_cancel())

    # ------------------------------------------------------------------
    # Runtime / settings
    # ------------------------------------------------------------------

    def refresh_residency(self) -> None:
        async def _refresh() -> None:
            try:

                async def _request() -> Any:
                    client = await self._acquire_client()
                    return await client.residency()

                status = await asyncio.wait_for(
                    _request(), timeout=self._runtime_request_timeout_seconds
                )
                self.residency_status.emit(status)
            except TimeoutError:
                self.residency_error.emit(
                    f"读取驻留状态超时（{self._runtime_request_timeout_seconds:g} 秒）"
                )
            except InferenceClientError as exc:
                self.residency_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover - defensive
                self.residency_error.emit(str(exc))

        self._schedule(_refresh())

    def release_idle(self, pipeline: str | None = None) -> None:
        async def _release() -> None:
            try:

                async def _request() -> Any:
                    client = await self._acquire_client()
                    return await client.release_idle(pipeline)

                status = await asyncio.wait_for(
                    _request(), timeout=self._runtime_request_timeout_seconds
                )
                self.residency_status.emit(status)
            except TimeoutError:
                self.residency_error.emit("释放闲置模型超时")
            except InferenceClientError as exc:
                self.residency_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover - defensive
                self.residency_error.emit(str(exc))

        self._schedule(_release())

    def preload(self, pipelines: tuple[str, ...]) -> None:
        async def _preload() -> None:
            try:

                async def _request() -> Any:
                    client = await self._acquire_client()
                    return await client.preload(pipelines)

                status = await asyncio.wait_for(
                    _request(), timeout=self._preload_timeout_seconds
                )
                self.preload_completed.emit(status)
            except TimeoutError:
                self.preload_error.emit("模型预加载超时")
            except InferenceClientError as exc:
                reason = exc.detail.get("reason")
                self.preload_error.emit(
                    reason if isinstance(reason, str) and reason else exc.message
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.preload_error.emit(str(exc))

        self._schedule(_preload())

    def update_settings(self, snapshot: SettingsSnapshot) -> None:
        async def _update() -> None:
            try:

                async def _request() -> Any:
                    client = await self._acquire_client()
                    return await client.put_settings(snapshot)

                updated = await asyncio.wait_for(
                    _request(), timeout=self._runtime_request_timeout_seconds
                )
                self.settings_updated.emit(updated)
            except TimeoutError:
                self.settings_error.emit("更新驻留策略超时")
            except InferenceClientError as exc:
                self.settings_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover - defensive
                self.settings_error.emit(str(exc))

        self._schedule(_update())

    # ------------------------------------------------------------------
    # Export / QR / PDF session (v2 surface extensions for PySide tabs)
    # ------------------------------------------------------------------

    # Export signals
    export_done = Signal(str)  # output_path
    export_error = Signal(str)

    # QR signals
    qr_decoded = Signal(list)  # list of decoded items
    qr_generated = Signal(str)  # base64 PNG
    qr_error = Signal(str)

    def export_ocr(
        self,
        raw_text: str,
        markdown_text: str,
        html_text: str,
        output_path: str,
        fmt: str,
        overwrite: bool = False,
    ) -> None:
        """Export OCR result to file via the supervisor /v2/export endpoint."""

        async def _export() -> None:
            try:
                client = await self._acquire_client()
                body = await client.export_ocr(
                    raw_text=raw_text,
                    markdown_text=markdown_text,
                    html_text=html_text,
                    output_path=output_path,
                    fmt=fmt,
                    overwrite=overwrite,
                )
                self.export_done.emit(body.get("output_path", output_path))
            except InferenceClientError as exc:
                self.export_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover
                self.export_error.emit(str(exc))

        self._schedule(_export())

    def decode_qrcode(self, image_bytes: bytes) -> None:
        """Decode QR/barcode via the supervisor /v2/qrcode/decode endpoint."""

        async def _decode() -> None:
            try:
                client = await self._acquire_client()
                self.qr_decoded.emit(await client.decode_qrcode(image_bytes))
            except InferenceClientError as exc:
                self.qr_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover
                self.qr_error.emit(str(exc))

        self._schedule(_decode())

    def generate_qrcode(self, data: str, fmt: str = "qrcode") -> None:
        """Generate QR/barcode via the supervisor /v2/qrcode/generate endpoint."""

        async def _generate() -> None:
            try:
                client = await self._acquire_client()
                self.qr_generated.emit(await client.generate_qrcode(data, fmt=fmt))
            except InferenceClientError as exc:
                self.qr_error.emit(exc.message)
            except Exception as exc:  # pragma: no cover
                self.qr_error.emit(str(exc))

        self._schedule(_generate())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_adapter: SupervisorClientAdapter | None = None


def get_supervisor_adapter() -> SupervisorClientAdapter:
    """Return the process-wide supervisor adapter.

    The production client factory is installed lazily; tests install their
    own adapter (with a fake client) via :func:`set_supervisor_adapter`.
    """
    global _global_adapter
    if _global_adapter is None:
        _global_adapter = SupervisorClientAdapter(
            client_factory=_default_client_factory
        )
    return _global_adapter


def set_supervisor_adapter(adapter: SupervisorClientAdapter | None) -> None:
    """Install/replace the global adapter (tests use this)."""
    global _global_adapter
    _global_adapter = adapter


def _default_client_factory() -> SupervisorClient:  # pragma: no cover - prod wiring
    """Construct the production supervisor client.

    Phase 8 will start the supervisor subprocess via ``SupervisorProcess``
    and read its ready envelope. Until then this raises so misuse fails
    loudly rather than silently degrading.
    """
    raise RuntimeError(
        "production supervisor client factory not installed; "
        "call set_supervisor_adapter() with a real or fake client"
    )


class _AwaitableClient:
    """Type hint marker for test fakes that implement the client surface."""


__all__ = [
    "SupervisorClientAdapter",
    "get_supervisor_adapter",
    "set_supervisor_adapter",
]
