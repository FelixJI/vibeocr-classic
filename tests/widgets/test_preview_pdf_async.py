"""PreviewWidget PDF 后台渲染的响应性与生命周期回归。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage

import vibeocr.classic.widgets.preview_widget as preview_module
from vibeocr.classic.widgets.preview_widget import PreviewWidget


def _image(color: str) -> QImage:
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def _prime_pdf(widget: PreviewWidget, page: int = 0) -> None:
    widget._current_file = "slow.pdf"
    widget._is_pdf = True
    widget._current_page = page
    widget._total_pages = 3


def test_slow_pdf_render_keeps_gui_heartbeat(qtbot, monkeypatch):
    widget = PreviewWidget()
    qtbot.addWidget(widget)
    entered = threading.Event()
    release = threading.Event()

    def slow_render(path, page, cancel_event):
        entered.set()
        release.wait(timeout=2)
        return path, page, 3, _image("red")

    monkeypatch.setattr(preview_module, "_render_pdf_page", slow_render)
    _prime_pdf(widget)
    widget._render_current_page()
    qtbot.waitUntil(entered.is_set, timeout=1000)

    heartbeat: list[bool] = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    qtbot.waitUntil(lambda: heartbeat == [True], timeout=1000)
    assert widget._pdf_jobs.is_running

    release.set()
    qtbot.waitUntil(lambda: not widget._pdf_jobs.is_running, timeout=2000)
    assert widget._original_pixmap is not None


def test_rapid_pdf_page_changes_are_latest_wins(qtbot, monkeypatch):
    widget = PreviewWidget()
    qtbot.addWidget(widget)
    first_entered = threading.Event()
    release_first = threading.Event()

    def render(path, page, cancel_event):
        if page == 0:
            first_entered.set()
            release_first.wait(timeout=2)
        color = {0: "red", 1: "green", 2: "blue"}[page]
        return path, page, 3, _image(color)

    monkeypatch.setattr(preview_module, "_render_pdf_page", render)
    _prime_pdf(widget)
    widget._render_current_page()
    qtbot.waitUntil(first_entered.is_set, timeout=1000)

    widget._current_page = 1
    widget._render_current_page()
    widget._current_page = 2
    widget._render_current_page()
    qtbot.waitUntil(lambda: widget._original_pixmap is not None, timeout=2000)

    pixel = widget._original_pixmap.toImage().pixelColor(0, 0)
    assert pixel.blue() > pixel.red() and pixel.blue() > pixel.green()

    release_first.set()
    qtbot.waitUntil(lambda: not widget._pdf_jobs.is_running, timeout=2000)
    pixel = widget._original_pixmap.toImage().pixelColor(0, 0)
    assert pixel.blue() > pixel.red() and pixel.blue() > pixel.green()


def test_close_requests_pdf_cancel_and_drain_tracks_native_finish(
    qtbot, monkeypatch
):
    widget = PreviewWidget()
    qtbot.addWidget(widget)
    entered = threading.Event()
    release = threading.Event()

    def slow_render(path, page, cancel_event):
        entered.set()
        release.wait(timeout=2)
        return path, page, 3, _image("red")

    monkeypatch.setattr(preview_module, "_render_pdf_page", slow_render)
    _prime_pdf(widget)
    widget._render_current_page()
    qtbot.waitUntil(entered.is_set, timeout=1000)

    widget.close()
    assert widget._closing is True
    assert widget.is_drained() is False

    release.set()
    qtbot.waitUntil(widget.is_drained, timeout=2000)
    assert widget._original_pixmap is None
