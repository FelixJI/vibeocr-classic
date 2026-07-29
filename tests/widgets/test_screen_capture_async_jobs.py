"""大画布截图合成与编码后台边界测试。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QPixmap

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.image_jobs import compose_screen_images
from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay


class _FakeScreen:
    def __init__(self, geometry: QRect, color: str) -> None:
        self._geometry = geometry
        self._color = color
        self.grabs = 0

    def geometry(self) -> QRect:
        return self._geometry

    def devicePixelRatio(self) -> float:
        return 1.0

    def grabWindow(self, _win_id: int) -> QPixmap:
        self.grabs += 1
        pixmap = QPixmap(self._geometry.size())
        pixmap.fill(QColor(self._color))
        return pixmap


def test_multiscreen_grabs_once_and_composes_off_gui(
    qtbot, qapp, monkeypatch
):
    screens = [
        _FakeScreen(QRect(0, 0, 1920, 1080), "red"),
        _FakeScreen(QRect(1920, 0, 1920, 1080), "blue"),
    ]
    entered = threading.Event()
    release = threading.Event()
    worker_threads: list[object] = []

    def slow_compose(images, size, dpr, cancel_event):
        from PySide6.QtCore import QThread

        worker_threads.append(QThread.currentThread())
        entered.set()
        release.wait(timeout=2)
        return compose_screen_images(images, size, dpr, cancel_event)

    monkeypatch.setattr(QGuiApplication, "screens", lambda: screens)
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.WindowDetector", None
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.compose_screen_images", slow_compose
    )
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    overlay.start_capture()
    assert entered.wait(timeout=1)
    assert [screen.grabs for screen in screens] == [1, 1]
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: not release.is_set())

    release.set()
    qtbot.waitUntil(lambda: overlay._screen_pixmap is not None, timeout=2000)
    assert overlay._screen_pixmap.width() == 3840
    assert all(thread is not qapp.thread() for thread in worker_threads)


def test_blocking_save_encoding_keeps_event_loop_responsive(
    qtbot, tmp_path, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()
    output = tmp_path / "capture.png"

    def slow_save(_image, path, _cancel_event):
        entered.set()
        release.wait(timeout=2)
        return path

    pixmap = QPixmap(3840, 2160)
    pixmap.fill(QColor("green"))
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.save_image_file", slow_save
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "PNG"),
    )
    saved: list[str] = []
    overlay.saved.connect(saved.append)

    overlay._on_save()
    assert entered.wait(timeout=1)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: not release.is_set())
    release.set()
    qtbot.waitUntil(lambda: bool(saved), timeout=1000)
    assert saved == [str(output)]


def test_close_does_not_wait_and_cancelled_clip_temp_is_removed(
    qtbot, tmp_path, monkeypatch
):
    entered = threading.Event()
    temp_path = tmp_path / "pending-clip.png"

    def cancellable_write(image, existing, max_files, cancel_event):
        temp_path.write_bytes(b"pending")
        entered.set()
        cancel_event.wait(timeout=2)
        temp_path.unlink(missing_ok=True)

    class _Clipboard:
        def __init__(self) -> None:
            self.mime_calls = 0

        def setImage(self, _image) -> None:
            pass

        def setMimeData(self, _mime) -> None:
            self.mime_calls += 1

    clipboard = _Clipboard()
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("yellow"))
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.sys.platform", "win32"
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.QApplication.clipboard",
        lambda: clipboard,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.write_clipboard_png",
        cancellable_write,
    )

    overlay._on_copy()
    assert entered.wait(timeout=1)
    started = time.perf_counter()
    overlay.closeEvent(QCloseEvent())
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 150
    qtbot.waitUntil(lambda: not temp_path.exists(), timeout=1000)
    assert clipboard.mime_calls == 0


def test_confirmed_saves_survive_new_capture_and_second_save(
    qtbot, tmp_path, monkeypatch
):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    entered = {str(first): threading.Event(), str(second): threading.Event()}
    release = threading.Event()

    def slow_save(_image, path, cancel_event):
        entered[path].set()
        release.wait(timeout=2)
        if cancel_event.is_set():
            raise RuntimeError(f"cancelled: {path}")
        Path(path).write_bytes(b"saved")
        return path

    screen = _FakeScreen(QRect(0, 0, 64, 64), "black")
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("green"))
    paths = iter((str(first), str(second)))
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [screen])
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.WindowDetector", None
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.save_image_file", slow_save
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (next(paths), "PNG"),
    )
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    saved: list[str] = []
    overlay.saved.connect(saved.append)

    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    overlay._on_save()
    assert entered[str(first)].wait(timeout=1)

    overlay.start_capture()
    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    overlay._on_save()
    assert entered[str(second)].wait(timeout=1)
    release.set()

    qtbot.waitUntil(lambda: len(saved) == 2, timeout=2000)
    assert set(saved) == {str(first), str(second)}
    assert first.read_bytes() == b"saved"
    assert second.read_bytes() == b"saved"


def test_confirmed_save_failure_is_reported(qtbot, tmp_path, monkeypatch):
    output = tmp_path / "failed.png"

    def fail_save(_image, _path, _cancel_event):
        raise OSError("disk full")

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("red"))
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.save_image_file", fail_save
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "PNG"),
    )
    failures: list[str] = []
    overlay.save_failed.connect(failures.append)

    overlay._on_save()
    qtbot.waitUntil(lambda: bool(failures), timeout=1000)
    assert "disk full" in failures[0]


def test_save_shutdown_drains_confirmed_save_without_blocking_close(
    qtbot, tmp_path, monkeypatch
):
    output = tmp_path / "during-close.png"
    entered = threading.Event()
    release = threading.Event()

    def slow_save(_image, path, _cancel_event):
        entered.set()
        release.wait(timeout=2)
        Path(path).write_bytes(b"complete")
        return path

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("cyan"))
    overlay = ScreenCaptureOverlay()
    qtbot.addWidget(overlay)
    overlay._canvas = type(
        "Canvas",
        (),
        {"export_image": lambda self: pixmap, "deleteLater": lambda self: None},
    )()
    monkeypatch.setattr(
        "vibeocr.classic.widgets.screen_capture_overlay.save_image_file", slow_save
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "PNG"),
    )
    saved: list[str] = []
    overlay.saved.connect(saved.append)

    overlay._on_save()
    assert entered.wait(timeout=1)
    started = time.perf_counter()
    overlay.closeEvent(QCloseEvent())
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 150

    drained: list[bool] = []
    drain_thread = threading.Thread(
        target=lambda: drained.append(overlay.drain_saves(1000))
    )
    drain_thread.start()
    assert drain_thread.is_alive()
    release.set()
    qtbot.waitUntil(lambda: bool(drained), timeout=1000)
    drain_thread.join(timeout=1)

    assert drained == [True]
    assert saved == [str(output)]
    assert output.read_bytes() == b"complete"
