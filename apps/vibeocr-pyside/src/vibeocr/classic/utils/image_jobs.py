"""GUI 共用的可取消图像解码、合成和编码后台任务。"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QImage, QPainter

if TYPE_CHECKING:
    from collections.abc import Callable


class _JobSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


_ACTIVE_JOBS: set[object] = set()


class _DiscardJob(QRunnable):
    """Dispose a stale worker result without doing file I/O on the GUI thread."""

    def __init__(self, result: object) -> None:
        super().__init__()
        self._result = result

    def run(self) -> None:
        try:
            discard = getattr(self._result, "discard", None)
            if discard is not None:
                discard()
        finally:
            _ACTIVE_JOBS.discard(self)


def _discard_result_async(result: object) -> None:
    if not hasattr(result, "discard"):
        return
    job = _DiscardJob(result)
    _ACTIVE_JOBS.add(job)
    QThreadPool.globalInstance().start(job)


class _Job(QRunnable):
    def __init__(
        self,
        generation: int,
        operation: Callable[[threading.Event], Any],
        cancel_event: threading.Event,
        done_event: threading.Event,
    ) -> None:
        super().__init__()
        self.generation = generation
        self._operation = operation
        self._cancel_event = cancel_event
        self._done_event = done_event
        self.signals = _JobSignals()

    def run(self) -> None:
        try:
            result = self._operation(self._cancel_event)
        except Exception as exc:
            self.signals.failed.emit(self.generation, str(exc))
        else:
            if self._cancel_event.is_set() and hasattr(result, "discard"):
                result.discard()
            self.signals.finished.emit(self.generation, result)
        finally:
            self._done_event.set()
            # run() 栈在此仍持有 self；发射完成后即可释放进程级保活引用。
            _ACTIVE_JOBS.discard(self)


class GenerationImageJobs(QObject):
    """按 generation 丢弃旧结果，关闭只取消、不等待。"""

    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._closing = False
        self._jobs: dict[
            int, tuple[_Job, threading.Event, threading.Event]
        ] = {}
        self._jobs_lock = threading.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def is_running(self) -> bool:
        with self._jobs_lock:
            return bool(self._jobs)

    def submit(self, operation: Callable[[threading.Event], Any]) -> int:
        if self._closing:
            return self._generation
        with self._jobs_lock:
            for _job, cancel_event, _done_event in self._jobs.values():
                cancel_event.set()
        self._generation += 1
        generation = self._generation
        cancel_event = threading.Event()
        done_event = threading.Event()
        job = _Job(generation, operation, cancel_event, done_event)
        with self._jobs_lock:
            self._jobs[generation] = (job, cancel_event, done_event)
        _ACTIVE_JOBS.add(job)
        job.signals.finished.connect(self._on_finished)
        job.signals.failed.connect(self._on_failed)

        QThreadPool.globalInstance().start(job)
        return generation

    def close(self) -> None:
        self._closing = True
        self._generation += 1
        with self._jobs_lock:
            for _job, cancel_event, _done_event in self._jobs.values():
                cancel_event.set()

    def cancel_current(self) -> None:
        """使当前 generation 失效，但保持控制器可再次提交。"""
        with self._jobs_lock:
            for _job, cancel_event, _done_event in self._jobs.values():
                cancel_event.set()
        self._generation += 1

    def drain(self, timeout_ms: int = 0) -> bool:
        """只等待 worker 结束，可安全地从非 GUI 协调线程调用。"""
        with self._jobs_lock:
            done_events = tuple(entry[2] for entry in self._jobs.values())
        if not done_events:
            return True
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for done_event in done_events:
            if done_event.is_set():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not done_event.wait(remaining):
                return False
        return True

    @Slot(int, object)
    def _on_finished(self, generation: int, result: object) -> None:
        with self._jobs_lock:
            entry = self._jobs.pop(generation, None)
        if entry is not None:
            _ACTIVE_JOBS.discard(entry[0])
        if self._closing or generation != self._generation:
            _discard_result_async(result)
            return
        self.completed.emit(generation, result)

    @Slot(int, str)
    def _on_failed(self, generation: int, error: str) -> None:
        with self._jobs_lock:
            entry = self._jobs.pop(generation, None)
        if entry is not None:
            _ACTIVE_JOBS.discard(entry[0])
        if self._closing or generation != self._generation:
            return
        self.failed.emit(generation, error)


def decode_image_file(path: str, cancel_event: threading.Event) -> QImage:
    """在线程中解码并返回脱离文件/解码器生命周期的 QImage。"""
    if cancel_event.is_set():
        return QImage()
    image = QImage(path)
    if image.isNull():
        raise ValueError(f"无法读取图片：{path}")
    detached = image.copy()
    if cancel_event.is_set():
        return QImage()
    return detached


def decode_image_bytes(data: bytes, cancel_event: threading.Event) -> QImage:
    """在线程中解码内存图片，并返回脱离输入缓冲区的 QImage。"""
    if cancel_event.is_set():
        return QImage()
    image = QImage.fromData(data)
    if image.isNull():
        raise ValueError("无法解码预处理图片")
    detached = image.copy()
    if cancel_event.is_set():
        return QImage()
    return detached


def compose_screen_images(
    images: list[tuple[QPoint, QImage]],
    physical_size: QSize,
    dpr: float,
    cancel_event: threading.Event,
) -> QImage:
    """在线程中把各屏幕 QImage 合成为虚拟桌面图像。"""
    if cancel_event.is_set() or physical_size.isEmpty():
        return QImage()
    target = QImage(physical_size, QImage.Format.Format_ARGB32)
    target.fill(QColor("black"))
    target.setDevicePixelRatio(dpr)
    painter = QPainter(target)
    try:
        for offset, image in images:
            if cancel_event.is_set():
                return QImage()
            painter.drawImage(offset, image)
    finally:
        painter.end()
    return target


@dataclass(slots=True)
class ClipboardPngResult:
    path: Path
    kept_paths: list[Path]
    image: QImage

    def discard(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)


def write_clipboard_png(
    image: QImage,
    existing_paths: list[Path],
    max_files: int,
    cancel_event: threading.Event,
) -> ClipboardPngResult | None:
    """编码 PNG、写临时文件并在 worker 中滚动清理。"""
    if cancel_event.is_set():
        return None
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    ok = image.save(buffer, "PNG")  # type: ignore[call-overload,arg-type]
    buffer.close()
    if not ok or cancel_event.is_set():
        return None

    fd, name = tempfile.mkstemp(
        prefix="vibeocr_clip_", suffix=".png", dir=tempfile.gettempdir()
    )
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(bytes(data))
        if cancel_event.is_set():
            path.unlink(missing_ok=True)
            return None
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise

    kept = [candidate for candidate in existing_paths if candidate.exists()]
    kept.append(path)
    while len(kept) > max_files:
        oldest = kept.pop(0)
        with contextlib.suppress(OSError):
            oldest.unlink(missing_ok=True)
    return ClipboardPngResult(path=path, kept_paths=kept, image=image)


def save_image_file(
    image: QImage, path: str, cancel_event: threading.Event
) -> str:
    """在线程中编码并写入用户选择的图片文件。"""
    if cancel_event.is_set():
        return ""
    if not image.save(path):
        raise OSError(f"保存图片失败：{path}")
    return path


def delete_files(paths: list[Path], cancel_event: threading.Event) -> bool:
    """尽力删除临时文件；清理任务不因单个文件失败中止。"""
    for path in paths:
        if cancel_event.is_set():
            return False
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
    return True
