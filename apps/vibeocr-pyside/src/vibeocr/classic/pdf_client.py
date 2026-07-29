"""Async + sync HTTP v2 client for PDF session operations.

The supervisor owns the PDF child process (plan §6 / ADR §"Transport"); the
GUI never instantiates ``PdfBackendClient`` directly. This module exposes the
client surface the PySide PDF session manager / IPC workers use instead.

* :class:`PdfSupervisorClient` — async domain adapter over the publishable
  Protocol SDK transport, mirrors
  the full ``PdfBackendClient`` business API (open/close/load_stream/render/
  mutate/text-layer/save/cancel). Method names and DTOs (``vibeocr.backend.ipc.schemas``)
  are identical to the legacy client so the PySide transport swap is a drop-in.
* :class:`SyncPdfSupervisorClient` — sync wrapper driving the async client on a
  dedicated background event loop. PySide PDF workers are plain ``QThread`` and
  cannot await; this wrapper lets them call the same surface synchronously,
  including streaming operations (``load_stream`` / ``delete_text_layers_stream``)
  which yield ``ProgressEvent`` objects from the NDJSON response.

Loopback + Bearer token are pinned exactly like :class:`SupervisorClient`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

from vibeocr.backend.ipc.schemas import (
    AddTextLayerRequest,
    BatchAddTextLayerPage,
    BatchAddTextLayerRequest,
    DeletePagesRequest,
    DetectTextLayersRequest,
    DetectTextLayersResponse,
    InsertBlankRequest,
    InsertFromRequest,
    MovePageRequest,
    MutateResponse,
    OpenRequest,
    OpenResponse,
    PageListRequest,
    PdfDocumentMirror,
    ProgressEvent,
    RenderPreviewRequest,
    RenderThumbnailRequest,
    ReorderRequest,
    RewriteTextLayerRequest,
    RotateRequest,
    SaveRequest,
    SaveResponse,
    UpdateBlockTextRequest,
)
from vibeocr.runtime_client.background_loop import (
    get_background_loop,
    shutdown_background_loop,
)
from vibeocr.runtime_client.client import (
    AsyncRuntimeTransport,
    RuntimeClientError,
    bind_operation_path,
)
from vibeocr.runtime_client.errors import InferenceClientError
from vibeocr.runtime_contracts import ErrorCode
from vibeocr.runtime_contracts.generated import RuntimeHealthEnvelope
from vibeocr.runtime_contracts.generated.operations import operation_path
from vibeocr.runtime_contracts.utils.http_log import (
    guess_request_size,
    guess_response_size,
    log_http_response,
)


class PdfBackendError(InferenceClientError):
    """Backwards-compatible error for PDF backend transport failures.

    The legacy ``PdfBackendError`` was a ``RuntimeError`` subclass raised with
    a single message string (e.g. ``PdfBackendError("load 失败 (500)")``).
    PySide code both raises it that way and catches it by name. We keep the
    single-string call site working by mapping the message to
    ``ErrorCode.INTERNAL_ERROR`` while still being a typed
    :class:`InferenceClientError` subclass (so new code can read ``.code``).

    Note: the *legacy* ``vibeocr.backend.services.pdf_backend_client.PdfBackendError``
    (still used inside the supervisor to talk to the PDF child) is a separate
    ``RuntimeError`` subclass. The two are NOT the same class — code that
    needs to catch both should catch ``Exception`` or import the specific one.
    The session manager catches this (new) class for the supervisor transport
    path; supervisor-internal code catches its own.
    """

    def __init__(
        self, message_or_code: Any, message: str | None = None, **kwargs: Any
    ) -> None:
        if message is None:
            # Legacy single-string form: PdfBackendError("boom").
            super().__init__(ErrorCode.INTERNAL_ERROR, str(message_or_code), **kwargs)
        else:
            # Typed form: PdfBackendError(ErrorCode.X, "boom", ...).
            super().__init__(message_or_code, message, **kwargs)


# HTTP timeouts mirror the legacy PdfBackendClient: quick ops 60 s, long ops
# (render at 300 DPI / batch write / streaming load) 600 s, both with a 5 s
# connect bound so a wedged supervisor fails fast.
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_HTTP_LONG_TIMEOUT = httpx.Timeout(600.0, connect=5.0)

logger = logging.getLogger(__name__)


class PdfSupervisorClient:
    """Async HTTP v2 client for PDF session ops. Use as an async context manager.

    The lifecycle mirrors :class:`SupervisorClient`: the Protocol SDK pins
    loopback, attaches the session Bearer token and owns ``httpx``. Method names
    and return DTOs are identical to the legacy ``PdfBackendClient`` so PySide
    workers can swap transports with no signature change.
    """

    def __init__(
        self, *, base_url: str, session_token: str, instance_id: str | None = None
    ) -> None:
        try:
            self._transport = AsyncRuntimeTransport(
                base_url=base_url,
                session_token=session_token,
                timeout=_HTTP_TIMEOUT,
                response_hook=self._log_http_response,
            )
        except RuntimeClientError as exc:
            raise PdfBackendError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                detail=exc.detail,
            ) from exc
        self.instance_id = instance_id

    @property
    def _client(self) -> httpx.AsyncClient | None:
        """Compatibility seam for existing in-process ASGI tests."""
        return self._transport.client

    @_client.setter
    def _client(self, value: httpx.AsyncClient | None) -> None:
        self._transport.client = value

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    async def __aenter__(self) -> PdfSupervisorClient:
        await self._transport.open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self._transport.close()

    def _require_client(self) -> AsyncRuntimeTransport:
        if self._client is None:
            raise RuntimeError(
                "PdfSupervisorClient must be used as an async context manager"
            )
        return self._transport

    async def _log_http_response(self, resp: httpx.Response) -> None:
        request = resp.request
        elapsed = None
        try:
            raw = getattr(resp, "elapsed", None)
            if raw is not None:
                elapsed = raw.total_seconds() * 1000.0
        except Exception:
            elapsed = None

        try:
            request_content = request.content
        except httpx.StreamError:
            request_content = None
        try:
            response_content = resp.content
        except httpx.StreamError:
            response_content = None
        request_bytes = guess_request_size(request_content)
        response_bytes = guess_response_size(dict(resp.headers), response_content)

        log_http_response(
            logger=logger,
            method=request.method,
            url=str(request.url),
            status_code=resp.status_code,
            reason=getattr(resp, "reason_phrase", None),
            elapsed_ms=elapsed,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            stream=not resp.is_stream_consumed,
        )

    def _error_from_response(self, resp: httpx.Response) -> PdfBackendError:
        try:
            body = resp.json()
            from vibeocr.runtime_contracts import parse_error_payload

            payload = parse_error_payload(body)
            return PdfBackendError.from_payload(payload)  # type: ignore[attr-defined]
        except Exception:
            return PdfBackendError(
                ErrorCode.INTERNAL_ERROR,
                f"unexpected pdf response status={resp.status_code}",
                retryable=False,
                detail={"status_code": resp.status_code},
            )

    def _raise_on_error(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise self._error_from_response(resp)

    # ---- session lifecycle --------------------------------------------

    async def start(self) -> None:
        """No-op kept for API parity with the legacy PdfBackendClient.

        The supervisor process owns the PDF child; the supervisor spawns it on
        first ``open_session``. Calling this is harmless.
        """

    async def health(self) -> dict[str, Any]:
        client = self._require_client()
        resp = await client.get(operation_path("getRuntimeHealth"))
        self._raise_on_error(resp)
        try:
            body = RuntimeHealthEnvelope.from_payload(resp.json())
        except (TypeError, ValueError) as exc:
            raise PdfBackendError(
                ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                "runtime health response violates Protocol v2",
                retryable=False,
            ) from exc
        return body.to_payload()

    async def open_session(self, path: str) -> OpenResponse:
        client = self._require_client()
        resp = await client.post(
            operation_path("openPdfSession"),
            json=OpenRequest(path=path).model_dump(),
        )
        self._raise_on_error(resp)
        return OpenResponse.model_validate(resp.json())

    async def close_session(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(bind_operation_path("closePdfSession", session_id=sid))
        self._raise_on_error(resp)

    async def get_model(self, sid: str) -> PdfDocumentMirror:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("getPdfSessionModel", session_id=sid)
        )
        self._raise_on_error(resp)
        return PdfDocumentMirror.model_validate(resp.json())

    async def load_stream(self, sid: str) -> AsyncIterator[ProgressEvent]:
        """Stream per-page text-layer detection. Yields one ProgressEvent per page."""
        client = self._require_client()
        try:
            async with client.stream(
                "POST",
                bind_operation_path("loadPdfSession", session_id=sid),
                timeout=_HTTP_LONG_TIMEOUT,
            ) as resp:
                self._raise_on_error(resp)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(
                ErrorCode.INTERNAL_ERROR, f"load 流式调用失败: {e}"
            ) from e

    # ---- render -------------------------------------------------------

    async def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("renderPdfThumbnail", session_id=sid),
            json=RenderThumbnailRequest(page=page, size=size).model_dump(),
            timeout=_HTTP_TIMEOUT,
        )
        self._raise_on_error(resp)
        return resp.content

    async def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("renderPdfPreview", session_id=sid),
            json=RenderPreviewRequest(page=page, dpi=dpi).model_dump(),
            timeout=_HTTP_LONG_TIMEOUT,
        )
        self._raise_on_error(resp)
        return resp.content

    async def detect_text_layers(self, sid: str, page: int) -> DetectTextLayersResponse:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("detectPdfTextLayers", session_id=sid),
            json=DetectTextLayersRequest(page=page).model_dump(),
        )
        self._raise_on_error(resp)
        return DetectTextLayersResponse.model_validate(resp.json())

    # ---- page mutations ----------------------------------------------

    async def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return await self._mutate(
            sid,
            "rotatePdfPages",
            RotateRequest(pages=pages, angle=angle).model_dump(),
        )

    async def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return await self._mutate(
            sid, "deletePdfPages", DeletePagesRequest(pages=pages).model_dump()
        )

    async def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "insertBlankPdfPage",
            InsertBlankRequest(
                after_index=after_index, width=width, height=height
            ).model_dump(),
        )

    async def insert_from(
        self, sid: str, source_path: str, after_index: int
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "insertPdfPagesFromFile",
            InsertFromRequest(
                source_path=source_path, after_index=after_index
            ).model_dump(),
        )

    async def move_page(
        self, sid: str, from_index: int, to_index: int
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "movePdfPage",
            MovePageRequest(from_index=from_index, to_index=to_index).model_dump(),
        )

    async def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return await self._mutate(
            sid, "reorderPdfPages", ReorderRequest(new_order=new_order).model_dump()
        )

    async def _mutate(
        self,
        sid: str,
        operation_id: str,
        body: dict[str, Any],
    ) -> MutateResponse:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path(operation_id, session_id=sid),
            json=body,
        )
        self._raise_on_error(resp)
        return MutateResponse.model_validate(resp.json())

    # ---- text layer ---------------------------------------------------

    async def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "addPdfTextLayer",
            AddTextLayerRequest(
                page=page,
                ocr_result=ocr_result,
                pdf_settings=pdf_settings,
                overwrite=overwrite,
            ).model_dump(),
        )

    async def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        client = self._require_client()
        body = BatchAddTextLayerRequest(
            pages=[
                BatchAddTextLayerPage(
                    page=p["page"],
                    ocr_result=p["ocr_result"],
                )
                for p in pages_data
            ],
            pdf_settings=pdf_settings,
            overwrite=overwrite,
            save=save,
        ).model_dump()
        resp = await client.post(
            bind_operation_path("addPdfTextLayerBatch", session_id=sid),
            json=body,
            timeout=_HTTP_LONG_TIMEOUT,
        )
        self._raise_on_error(resp)
        return MutateResponse.model_validate(resp.json())

    async def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "rewritePdfTextLayer",
            RewriteTextLayerRequest(
                page=page,
                text_blocks=text_blocks,
                preproc_angle=preproc_angle,
                pdf_settings=pdf_settings,
            ).model_dump(),
        )

    async def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ) -> MutateResponse:
        return await self._mutate(
            sid,
            "updatePdfBlockText",
            UpdateBlockTextRequest(
                page=page, block_index=block_index, new_text=new_text
            ).model_dump(),
        )

    async def delete_text_layers_stream(
        self, sid: str, pages: list[int]
    ) -> AsyncIterator[ProgressEvent]:
        """Stream per-page text-layer deletion."""
        client = self._require_client()
        try:
            async with client.stream(
                "POST",
                bind_operation_path("deletePdfTextLayers", session_id=sid),
                json=PageListRequest(pages=pages).model_dump(),
                timeout=_HTTP_LONG_TIMEOUT,
            ) as resp:
                self._raise_on_error(resp)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(
                ErrorCode.INTERNAL_ERROR, f"delete_text_layers 流式调用失败: {e}"
            ) from e

    # ---- save ---------------------------------------------------------

    async def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
        *,
        rewrite_text_layers: bool = True,
    ) -> SaveResponse:
        client = self._require_client()
        body = SaveRequest(
            path=path,
            pdf_settings=pdf_settings,
            rewrite_text_layers=rewrite_text_layers,
        ).model_dump()
        resp = await client.post(
            bind_operation_path("savePdfSession", session_id=sid),
            json=body,
            timeout=_HTTP_LONG_TIMEOUT,
        )
        self._raise_on_error(resp)
        return SaveResponse.model_validate(resp.json())

    # ---- cancel -------------------------------------------------------

    async def cancel(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("cancelPdfSession", session_id=sid)
        )
        self._raise_on_error(resp)

    async def reset_cancel(self, sid: str) -> None:
        client = self._require_client()
        resp = await client.post(
            bind_operation_path("resetPdfSessionCancellation", session_id=sid)
        )
        self._raise_on_error(resp)


_get_bg_loop = get_background_loop
_shutdown_bg_loop = shutdown_background_loop


class SyncPdfSupervisorClient:
    """Sync wrapper over :class:`PdfSupervisorClient` for QThread callers.

    Each method runs the underlying coroutine on a shared background asyncio
    loop and blocks the worker thread until it returns. Streaming operations
    (``load_stream`` / ``delete_text_layers_stream``) yield sync iterators.

    Constructed once per PySide PDF session manager and held for the app
    lifetime; the underlying ``httpx.AsyncClient`` is created lazily on first
    use and closed via :meth:`close`.
    """

    def __init__(
        self, *, base_url: str, session_token: str, instance_id: str | None = None
    ) -> None:
        self._async = PdfSupervisorClient(
            base_url=base_url, session_token=session_token, instance_id=instance_id
        )
        self._entered = False

    def _ensure_entered(self) -> PdfSupervisorClient:
        if not self._entered:
            # Enter the async context manager once on the background loop so the
            # httpx transport lives there. Subsequent calls reuse it.
            _get_bg_loop().run(self._async.__aenter__())
            self._entered = True
        return self._async

    @property
    def base_url(self) -> str:
        return self._async.base_url

    def close(self) -> None:
        if self._entered:
            try:
                _get_bg_loop().run(self._async.__aexit__(None, None, None))
            finally:
                self._entered = False

    # The wrapper methods delegate by building the coro and driving it on the
    # background loop. Each keeps the same signature/return type as the legacy
    # PdfBackendClient so PySide workers are unchanged.

    def start(self) -> None:
        self._ensure_entered()

    def health(self) -> dict[str, Any]:
        return _get_bg_loop().run(self._ensure_entered().health())

    def open_session(self, path: str) -> OpenResponse:
        return _get_bg_loop().run(self._ensure_entered().open_session(path))

    def close_session(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().close_session(sid))

    def get_model(self, sid: str) -> PdfDocumentMirror:
        return _get_bg_loop().run(self._ensure_entered().get_model(sid))

    def load_stream(self, sid: str) -> Iterator[ProgressEvent]:
        client = self._ensure_entered()

        return _get_bg_loop().iterate_stream(lambda: client.load_stream(sid))

    def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        return _get_bg_loop().run(
            self._ensure_entered().render_thumbnail(sid, page, size=size)
        )

    def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        return _get_bg_loop().run(
            self._ensure_entered().render_preview(sid, page, dpi=dpi)
        )

    def detect_text_layers(self, sid: str, page: int) -> DetectTextLayersResponse:
        return _get_bg_loop().run(self._ensure_entered().detect_text_layers(sid, page))

    def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return _get_bg_loop().run(self._ensure_entered().rotate(sid, pages, angle))

    def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return _get_bg_loop().run(self._ensure_entered().delete_pages(sid, pages))

    def insert_blank(
        self,
        sid: str,
        after_index: int,
        width: float = 612.0,
        height: float = 792.0,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().insert_blank(sid, after_index, width, height)
        )

    def insert_from(
        self, sid: str, source_path: str, after_index: int
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().insert_from(sid, source_path, after_index)
        )

    def move_page(self, sid: str, from_index: int, to_index: int) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().move_page(sid, from_index, to_index)
        )

    def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return _get_bg_loop().run(self._ensure_entered().reorder(sid, new_order))

    def add_text_layer(
        self,
        sid: str,
        page: int,
        ocr_result: dict[str, Any],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().add_text_layer(
                sid, page, ocr_result, pdf_settings, overwrite
            )
        )

    def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict[str, Any]],
        pdf_settings: dict[str, Any] | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().add_text_layer_batch(
                sid, pages_data, pdf_settings, overwrite, save
            )
        )

    def rewrite_text_layer(
        self,
        sid: str,
        page: int,
        text_blocks: list[Any],
        preproc_angle: int = 0,
        pdf_settings: dict[str, Any] | None = None,
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().rewrite_text_layer(
                sid, page, text_blocks, preproc_angle, pdf_settings
            )
        )

    def update_block_text(
        self, sid: str, page: int, block_index: int, new_text: str
    ) -> MutateResponse:
        return _get_bg_loop().run(
            self._ensure_entered().update_block_text(sid, page, block_index, new_text)
        )

    def delete_text_layers_stream(
        self, sid: str, pages: list[int]
    ) -> Iterator[ProgressEvent]:
        client = self._ensure_entered()

        return _get_bg_loop().iterate_stream(
            lambda: client.delete_text_layers_stream(sid, pages)
        )

    def save(
        self,
        sid: str,
        path: str | None = None,
        pdf_settings: dict[str, Any] | None = None,
        *,
        rewrite_text_layers: bool = True,
    ) -> SaveResponse:
        return _get_bg_loop().run(
            self._ensure_entered().save(
                sid, path, pdf_settings, rewrite_text_layers=rewrite_text_layers
            )
        )

    def cancel(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().cancel(sid))

    def reset_cancel(self, sid: str) -> None:
        _get_bg_loop().run(self._ensure_entered().reset_cancel(sid))


__all__ = [
    "PdfBackendError",
    "PdfSupervisorClient",
    "SyncPdfSupervisorClient",
]
