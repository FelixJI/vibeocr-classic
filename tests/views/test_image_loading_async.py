"""图片文件后台解码与 generation 生命周期测试。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QStatusBar

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.image_jobs import GenerationImageJobs
from vibeocr.classic.views.main_window import MainWindow


class _ImageLoadHarness(QObject):
    _request_image_load = MainWindow._request_image_load
    _on_image_file_loaded = MainWindow._on_image_file_loaded
    _on_image_file_load_failed = MainWindow._on_image_file_load_failed

    def __init__(self) -> None:
        super().__init__()
        self._closing = False
        self._statusbar = QStatusBar()
        self._single_tab = _FakeSingleTab()
        self._image_load_jobs = GenerationImageJobs(self)
        self._image_load_jobs.completed.connect(self._on_image_file_loaded)
        self._image_load_jobs.failed.connect(self._on_image_file_load_failed)


class _FakeSingleTab:
    def __init__(self) -> None:
        self.is_processing = False
        self.applied: list[tuple[str, object]] = []

    def set_image_for_recognition(self, pixmap) -> None:
        self.applied.append(("recognition", QThread.currentThread()))

    def set_pixmap(self, pixmap) -> None:
        self.applied.append(("preview", pixmap.toImage().pixelColor(0, 0)))

    def run_ocr(self, pixmap) -> None:
        self.applied.append(("ocr", pixmap.toImage().pixelColor(0, 0)))


def _solid_image(color: str) -> QImage:
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def test_slow_decode_is_responsive_and_latest_request_wins(qtbot, qapp, monkeypatch):
    first_entered = threading.Event()
    first_release = threading.Event()
    worker_threads: list[object] = []

    def decode(path: str, _cancel_event) -> QImage:
        worker_threads.append(QThread.currentThread())
        if path == "first.png":
            first_entered.set()
            first_release.wait(timeout=2)
            return _solid_image("red")
        return _solid_image("blue")

    monkeypatch.setattr("vibeocr.classic.views.main_window.decode_image_file", decode)
    harness = _ImageLoadHarness()
    harness._request_image_load("first.png")
    assert first_entered.wait(timeout=1)
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: not first_release.is_set()
    )

    harness._request_image_load("second.png")
    qtbot.waitUntil(
        lambda: any(kind == "ocr" for kind, _value in harness._single_tab.applied),
        timeout=1000,
    )
    first_release.set()
    qtbot.wait(50)

    applied_colors = [
        value.name()
        for kind, value in harness._single_tab.applied
        if kind in {"preview", "ocr"}
    ]
    assert applied_colors == ["#0000ff", "#0000ff"]
    assert all(thread is not qapp.thread() for thread in worker_threads)
    gui_threads = [
        value for kind, value in harness._single_tab.applied if kind == "recognition"
    ]
    assert gui_threads == [qapp.thread()]


def test_close_drops_late_image_decode_result(qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def decode(_path: str, _cancel_event) -> QImage:
        entered.set()
        release.wait(timeout=2)
        return _solid_image("green")

    monkeypatch.setattr("vibeocr.classic.views.main_window.decode_image_file", decode)
    harness = _ImageLoadHarness()
    harness._request_image_load("slow.png")
    assert entered.wait(timeout=1)
    harness._closing = True
    harness._image_load_jobs.close()
    release.set()
    qtbot.wait(50)
    assert harness._single_tab.applied == []


def test_busy_state_drops_loaded_image_and_decode_errors_reach_statusbar(
    qtbot, monkeypatch
):
    harness = _ImageLoadHarness()
    harness._single_tab.is_processing = True
    harness._on_image_file_loaded(1, ("busy.png", _solid_image("yellow")))
    assert harness._single_tab.applied == []
    assert "已忽略" in harness._statusbar.currentMessage()

    harness._single_tab.is_processing = False

    def fail_decode(_path: str, _cancel_event) -> QImage:
        raise ValueError("broken image")

    monkeypatch.setattr("vibeocr.classic.views.main_window.decode_image_file", fail_decode)
    harness._request_image_load("broken.png")
    qtbot.waitUntil(
        lambda: "broken image" in harness._statusbar.currentMessage(), timeout=1000
    )
    assert harness._single_tab.applied == []
