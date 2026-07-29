"""Preview 与 QR 文件图片后台解码的 generation/closing 回归。"""

from __future__ import annotations

import threading
import time
from importlib import import_module

from PySide6.QtGui import QColor, QImage

from tests.qt_responsiveness import assert_qt_event_loop_responsive


def _image(color: str) -> QImage:
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_preview_slow_decode_keeps_event_loop_responsive(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.preview_widget")
    widget = module.PreviewWidget()
    qtbot.addWidget(widget)
    started = threading.Event()
    release = threading.Event()

    def slow_decode(_path, _cancel_event):
        started.set()
        release.wait(timeout=2)
        return _image("red")

    monkeypatch.setattr(module, "decode_image_file", slow_decode)
    widget.load_file("slow.png")
    qtbot.waitUntil(started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: widget._image_load_jobs.is_running
    )
    release.set()
    qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
    assert widget._original_pixmap is not None


def test_preview_fast_second_file_wins_and_pdf_cancels_image(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.preview_widget")
    widget = module.PreviewWidget()
    qtbot.addWidget(widget)
    old_started = threading.Event()
    release_old = threading.Event()

    def decode(path, _cancel_event):
        if path == "old.png":
            old_started.set()
            release_old.wait(timeout=2)
            return _image("red")
        return _image("blue")

    monkeypatch.setattr(module, "decode_image_file", decode)
    widget.load_file("old.png")
    qtbot.waitUntil(old_started.is_set, timeout=1000)
    widget.load_file("new.png")
    qtbot.waitUntil(
        lambda: (
            widget._original_pixmap is not None
            and widget._original_pixmap.toImage().pixelColor(0, 0) == QColor("blue")
        ),
        timeout=2000,
    )
    release_old.set()
    qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
    assert widget._original_pixmap.toImage().pixelColor(0, 0) == QColor("blue")

    # 新图片尚未完成时切到 PDF，迟到图片不得覆盖 PDF 路径。
    old_started.clear()
    release_old.clear()
    widget.load_file("old.png")
    qtbot.waitUntil(old_started.is_set, timeout=1000)
    pdf_loaded: list[str] = []
    monkeypatch.setattr(widget, "_load_pdf", pdf_loaded.append)
    widget.load_file("document.pdf")
    release_old.set()
    qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
    assert pdf_loaded == ["document.pdf"]
    assert widget._current_file == "document.pdf"


def test_preview_clear_and_close_drop_late_decode(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.preview_widget")
    widget = module.PreviewWidget()
    qtbot.addWidget(widget)
    started = threading.Event()
    release = threading.Event()

    def slow_decode(_path, _cancel_event):
        started.set()
        release.wait(timeout=2)
        return _image("red")

    monkeypatch.setattr(module, "decode_image_file", slow_decode)
    widget.load_file("late.png")
    qtbot.waitUntil(started.is_set, timeout=1000)
    widget.clear()
    release.set()
    qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
    assert widget._original_pixmap is None

    started.clear()
    release.clear()
    widget.load_file("closing.png")
    qtbot.waitUntil(started.is_set, timeout=1000)
    before = time.perf_counter()
    widget.close()
    assert (time.perf_counter() - before) * 1000 < 150
    release.set()
    qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
    assert widget._original_pixmap is None


class _QrBackend:
    pass


def test_qr_slow_file_decode_is_responsive_and_second_file_wins(
    qapp, qtbot, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.qrcode_tab")
    tab = module.QrcodeTab(backend=_QrBackend())
    qtbot.addWidget(tab)
    tab._sub_tabs.setCurrentIndex(1)
    old_started = threading.Event()
    release_old = threading.Event()

    def decode(path, _cancel_event):
        if path == "old.png":
            old_started.set()
            release_old.wait(timeout=2)
            return _image("red")
        return _image("blue")

    monkeypatch.setattr(module, "decode_image_file", decode)
    tab._request_decode_image_file("old.png")
    qtbot.waitUntil(old_started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: tab._file_load_jobs.is_running
    )
    tab._request_decode_image_file("new.png")
    qtbot.waitUntil(
        lambda: (
            tab._decode_pending_pixmap is not None
            and tab._decode_pending_pixmap.toImage().pixelColor(0, 0) == QColor("blue")
        ),
        timeout=2000,
    )
    release_old.set()
    qtbot.waitUntil(lambda: not tab._file_load_jobs.is_running, timeout=2000)
    assert tab._decode_pending_pixmap.toImage().pixelColor(0, 0) == QColor("blue")


def test_qr_clear_and_close_drop_late_file_decode(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.views.tabs.qrcode_tab")
    tab = module.QrcodeTab(backend=_QrBackend())
    qtbot.addWidget(tab)
    tab._sub_tabs.setCurrentIndex(1)
    started = threading.Event()
    release = threading.Event()

    def slow_decode(_path, _cancel_event):
        started.set()
        release.wait(timeout=2)
        return _image("red")

    monkeypatch.setattr(module, "decode_image_file", slow_decode)
    tab._request_decode_image_file("late.png")
    qtbot.waitUntil(started.is_set, timeout=1000)
    tab._on_clear_decode()
    release.set()
    qtbot.waitUntil(lambda: not tab._file_load_jobs.is_running, timeout=2000)
    assert tab._decode_pending_pixmap is None

    started.clear()
    release.clear()
    tab._request_decode_image_file("closing.png")
    qtbot.waitUntil(started.is_set, timeout=1000)
    before = time.perf_counter()
    tab.close()
    assert (time.perf_counter() - before) * 1000 < 150
    release.set()
    qtbot.waitUntil(lambda: not tab._file_load_jobs.is_running, timeout=2000)
    assert tab._decode_pending_pixmap is None
