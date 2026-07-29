"""可取消、可排空的导出与保存后台作业。

这个模块只承载非 GUI 工作：结果序列化/RPC、图片编码以及文件写入。
调用方必须在启动作业前从 QWidget/QPixmap 提取好不可变输入。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from pathlib import Path

    from PIL import Image

logger = logging.getLogger(__name__)


class ExportJobCancelled(Exception):
    """作业在安全边界收到协作式取消请求。"""


@dataclass(frozen=True)
class ExportItem:
    """一项已经从 UI 脱离的 OCR 导出输入。"""

    source_name: str
    result: Any
    output_dir: Path
    export_format: str


@dataclass(frozen=True)
class TextBlockSnapshot:
    """Worker-safe, detached copy of the text-block fields used by layout."""

    text: str
    score: float
    bbox: tuple | None
    polygon: tuple | None = None
    page_idx: int | None = None
    is_manually_edited: bool = False
    content_index: int | None = None
    content_id: str | None = None
    label: str = "text"
    order: int = -1


@dataclass(frozen=True)
class OCRResultSnapshot:
    """Minimal OCR payload detached from GUI-editable model containers."""

    raw_text: str = ""
    markdown_text: str = ""
    html_text: str = ""
    content_list: tuple[Any, ...] = ()
    images: dict[str, Any] | None = None
    text_blocks: tuple[TextBlockSnapshot, ...] = ()


def _result_value(result: Any, name: str, default: Any) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _detached_value(value: Any) -> Any:
    """Copy mutable JSON-like containers while reusing immutable byte payloads."""
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, dict):
        return {
            _detached_value(key): _detached_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_detached_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_detached_value(item) for item in value)
    return deepcopy(value)


def snapshot_ocr_result(
    result: Any,
    *,
    include_content_list: bool = True,
    include_images: bool = False,
    include_text_blocks: bool = False,
) -> OCRResultSnapshot:
    """Capture one consistent submission-time view for a background job."""
    blocks: tuple[TextBlockSnapshot, ...] = ()
    if include_text_blocks:
        blocks = tuple(
            TextBlockSnapshot(
                text=str(getattr(block, "text", "") or ""),
                score=float(getattr(block, "score", 0.0) or 0.0),
                bbox=_detached_value(getattr(block, "bbox", None)),
                polygon=_detached_value(getattr(block, "polygon", None)),
                page_idx=getattr(block, "page_idx", None),
                is_manually_edited=bool(
                    getattr(block, "is_manually_edited", False)
                ),
                content_index=getattr(block, "content_index", None),
                content_id=getattr(block, "content_id", None),
                label=str(getattr(block, "label", "text") or "text"),
                order=int(getattr(block, "order", -1)),
            )
            for block in (_result_value(result, "text_blocks", ()) or ())
        )
    content = (
        tuple(
            _detached_value(item)
            for item in (_result_value(result, "content_list", ()) or ())
        )
        if include_content_list
        else ()
    )
    if include_content_list:
        from vibeocr.backend.tables.blocks import validate_table_blocks

        validate_table_blocks(content)
    images = (
        _detached_value(_result_value(result, "images", {}) or {})
        if include_images
        else None
    )
    return OCRResultSnapshot(
        raw_text=str(_result_value(result, "raw_text", "") or ""),
        markdown_text=str(_result_value(result, "markdown_text", "") or ""),
        html_text=str(_result_value(result, "html_text", "") or ""),
        content_list=content,
        images=images,
        text_blocks=blocks,
    )


@dataclass(frozen=True)
class ExportedFile:
    requested_path: Path
    actual_path: Path
    success: bool


@dataclass(frozen=True)
class BatchExportReport:
    files: tuple[ExportedFile, ...]

    @property
    def success_count(self) -> int:
        return sum(item.success for item in self.files)

    @property
    def fail_count(self) -> int:
        return len(self.files) - self.success_count

    @property
    def renamed(self) -> tuple[ExportedFile, ...]:
        return tuple(
            item for item in self.files if item.requested_path != item.actual_path
        )


ProgressCallback = Callable[[int, int, str], None]
JobOperation = Callable[[threading.Event, ProgressCallback], Any]


# QWidget 关闭且其 Python 包装器被回收后，线程也必须继续存活到自然退出。
_ACTIVE_JOBS: set[ExportSaveJob] = set()


class ExportSaveJob(QThread):
    """在专用 QThread 中执行一个导出/保存操作。

    ``cancel`` 是协作式取消；同步 RPC 或一次编码正在执行时不会强杀线程，
    而会在下一个安全边界终止。``drain`` 提供可验证的有界等待。
    """

    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    terminal = Signal(str, object)
    stopped = Signal(object)

    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    def __init__(self, operation: JobOperation) -> None:
        # 不设置 QWidget parent；全局保活集合保证关闭时不会析构运行中的 QThread。
        super().__init__()
        self._operation = operation
        self._cancel_event = threading.Event()
        self._status: str | None = None
        self._result: Any = None
        self._error = ""
        self.finished.connect(self._notify_stopped)
        self.finished.connect(self._release_global_reference)

    @property
    def status(self) -> str | None:
        return self._status

    @property
    def result(self) -> Any:
        return self._result

    @property
    def error_message(self) -> str:
        return self._error

    def start(self, priority=QThread.Priority.InheritPriority) -> None:  # type: ignore[override]
        _ACTIVE_JOBS.add(self)
        super().start(priority)

    def cancel(self) -> None:
        self._cancel_event.set()

    def drain(self, timeout_ms: int = 0) -> bool:
        if QThread.currentThread() is self:
            return False
        if self.isRunning() and timeout_ms > 0:
            self.wait(max(0, int(timeout_ms)))
        return not self.isRunning()

    def run(self) -> None:
        try:
            if self._cancel_event.is_set():
                raise ExportJobCancelled
            result = self._operation(self._cancel_event, self.progress.emit)
            if self._cancel_event.is_set():
                raise ExportJobCancelled
        except ExportJobCancelled:
            self._status = self.STATUS_CANCELLED
            self.cancelled.emit()
            self.terminal.emit(self._status, None)
        except Exception as exc:
            logger.exception("导出/保存后台作业失败")
            self._status = self.STATUS_FAILED
            self._error = str(exc)
            self.failed.emit(self._error)
            self.terminal.emit(self._status, self._error)
        else:
            self._status = self.STATUS_COMPLETED
            self._result = result
            self.completed.emit(result)
            self.terminal.emit(self._status, result)

    def _release_global_reference(self) -> None:
        _ACTIVE_JOBS.discard(self)

    def _notify_stopped(self) -> None:
        """携带稳定 job 身份转发原生 finished，避免 Python sender() 丢失。"""
        self.stopped.emit(self)


def _reserved_unique_path(path: Path, reserved: set[Path]) -> Path:
    candidate = path
    counter = 1
    while candidate.exists() or candidate in reserved:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    reserved.add(candidate)
    return candidate


def export_batch_operation(client: Any, items: tuple[ExportItem, ...]) -> JobOperation:
    """构造顺序导出作业；同批次输出名也会预留，避免互相覆盖。

    v2-only: always uses the supervisor's /v2/export endpoint.
    """

    def operation(cancel_event: threading.Event, progress: ProgressCallback):
        from vibeocr.classic.client import get_output_filename

        if cancel_event.is_set():
            raise ExportJobCancelled
        reserved: set[Path] = set()
        planned: list[tuple[ExportItem, Path, Path]] = []
        for item in items:
            requested = item.output_dir / get_output_filename(
                item.source_name, item.export_format
            )
            planned.append(
                (item, requested, _reserved_unique_path(requested, reserved))
            )

        exported: list[ExportedFile] = []
        total = len(planned)
        for index, (item, requested, actual) in enumerate(planned, start=1):
            if cancel_event.is_set():
                raise ExportJobCancelled
            ok = _export_via_supervisor(
                client, item.result, actual, item.export_format
            )
            if cancel_event.is_set():
                raise ExportJobCancelled
            exported.append(ExportedFile(requested, actual, ok))
            progress(index, total, item.source_name)
        return BatchExportReport(tuple(exported))

    return operation


def export_single_operation(
    client: Any, result: Any, output_path: Path, export_format: str
) -> JobOperation:
    """构造单文件 OCR 导出作业；False 统一转换为失败回调。

    v2-only: always uses the supervisor's /v2/export endpoint.
    """

    def operation(cancel_event: threading.Event, progress: ProgressCallback):
        if cancel_event.is_set():
            raise ExportJobCancelled
        if not _export_via_supervisor(client, result, output_path, export_format):
            raise RuntimeError("导出失败，请重试或查看日志。")
        return output_path




    return operation


def _export_via_supervisor(
    injected_client: Any,
    result: Any,
    output_path: Path,
    export_format: str,
) -> bool:
    """Export OCR result via the v2 supervisor /v2/export endpoint.

    This is a synchronous blocking call that runs inside a QThread (ExportSaveJob).
    The blocking QThread uses the adapter's public synchronous utility client;
    transport credentials never leave the client package.
    """
    raw_text = str(_result_value(result, "raw_text", "") or _result_value(result, "text", "") or "")
    markdown_text = str(_result_value(result, "markdown_text", "") or raw_text)
    html_text = str(_result_value(result, "html_text", "") or raw_text)
    raw_blocks = list(_result_value(result, "content_list", ()) or ())
    fmt = export_format

    # Explicitly injected backends are a test/embedding seam. Production callers
    # pass None and resolve the process-wide typed Supervisor adapter below.
    injected_export = getattr(injected_client, "export_ocr_sync", None)
    if callable(injected_export):
        body = injected_export(
            {
                "raw_text": raw_text,
                "markdown_text": markdown_text,
                "html_text": html_text,
                "content_list": raw_blocks,
                "raw_blocks": raw_blocks,
            },
            output_path=str(output_path),
            format=fmt,
            overwrite=output_path.exists(),
        )
        return bool(body and (body.get("output_path") or body.get("path")))

    try:
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        adapter = get_supervisor_adapter()
        client = adapter.inference_sync_client
        if client is None:
            raise RuntimeError("supervisor utility client is unavailable")
        result_body = client.export_ocr(
            raw_text=raw_text,
            markdown_text=markdown_text,
            html_text=html_text,
            raw_blocks=raw_blocks,
            output_path=str(output_path),
            fmt=fmt,
            overwrite=output_path.exists(),
        )

        return bool(result_body.get("output_path"))
    except Exception:
        logger.exception("v2 supervisor export failed")
        return False


def save_bitmap_operation(image: Image.Image, output_path: Path, image_format: str):
    """构造 PIL 位图编码/写盘作业。image 应由 GUI 线程预先 copy。"""

    def operation(cancel_event: threading.Event, progress: ProgressCallback):
        if cancel_event.is_set():
            raise ExportJobCancelled
        image.save(output_path, image_format)
        return output_path

    return operation


def save_svg_operation(
    backend: Any, text: str, options: dict[str, Any], output_path: Path
):
    """构造 QR SVG 生成与写盘作业。"""

    def operation(cancel_event: threading.Event, progress: ProgressCallback):
        if cancel_event.is_set():
            raise ExportJobCancelled

        injected_generate = getattr(backend, "generate_qrcode_svg_sync", None)
        if callable(injected_generate):
            svg = injected_generate(text, options=options)
            if cancel_event.is_set():
                raise ExportJobCancelled
            output_path.write_text(str(svg), encoding="utf-8")
            return output_path

        import base64

        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        adapter = get_supervisor_adapter()
        client = adapter.inference_sync_client
        if client is None:
            raise RuntimeError("supervisor utility client is unavailable")
        b64 = client.generate_qrcode(text, fmt="svg", options=options)
        if cancel_event.is_set():
            raise ExportJobCancelled
        output_path.write_bytes(base64.b64decode(b64))
        return output_path

    return operation


__all__ = [
    "BatchExportReport",
    "ExportItem",
    "ExportJobCancelled",
    "ExportSaveJob",
    "ExportedFile",
    "OCRResultSnapshot",
    "TextBlockSnapshot",
    "export_batch_operation",
    "export_single_operation",
    "save_bitmap_operation",
    "save_svg_operation",
    "snapshot_ocr_result",
]
