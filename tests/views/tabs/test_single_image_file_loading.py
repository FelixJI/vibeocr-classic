"""SingleRecognitionTab 独立图片加载的异步与生命周期回归。"""

from __future__ import annotations

import threading
from importlib import import_module

from PySide6.QtGui import QColor, QGuiApplication, QImage

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.recognition_result import OCRResult


def _image(color: str) -> QImage:
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_standalone_file_button_loads_image_without_main_window(
    qapp, qtbot, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: ("standalone.png", ""),
    )
    monkeypatch.setattr(module, "decode_image_file", lambda *_args: _image("blue"))
    recognize_calls = []
    monkeypatch.setattr(tab, "run_ocr", recognize_calls.append)

    tab._on_file_btn_clicked()
    qtbot.waitUntil(lambda: not tab._image_load_jobs.is_running, timeout=2000)

    assert tab._pending_pixmap is not None
    assert tab._preview_widget.original_pixmap() is not None
    assert tab._start_btn.isEnabled()
    assert recognize_calls == []


def test_process_file_decodes_off_gui_and_starts_recognition(
    qapp, qtbot, tmp_path, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    path = tmp_path / "slow.png"
    path.write_bytes(b"placeholder")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    entered = threading.Event()
    release = threading.Event()

    def slow_decode(_path, _cancel_event):
        entered.set()
        release.wait(timeout=2)
        return _image("green")

    monkeypatch.setattr(module, "decode_image_file", slow_decode)
    recognize_calls = []
    monkeypatch.setattr(tab, "run_ocr", recognize_calls.append)

    tab.process_file(str(path))
    assert entered.wait(timeout=1)
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: tab._image_load_jobs.is_running
    )
    release.set()
    qtbot.waitUntil(lambda: bool(recognize_calls), timeout=2000)

    assert recognize_calls[0].toImage().pixelColor(0, 0) == QColor("green")


def test_latest_image_wins_and_closing_drops_late_result(
    qapp, qtbot, tmp_path, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    old_path = tmp_path / "old.png"
    new_path = tmp_path / "new.png"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    old_entered = threading.Event()
    release_old = threading.Event()

    def decode(path, _cancel_event):
        if path == str(old_path):
            old_entered.set()
            release_old.wait(timeout=2)
            return _image("red")
        return _image("blue")

    monkeypatch.setattr(module, "decode_image_file", decode)
    recognize_colors = []
    monkeypatch.setattr(
        tab,
        "run_ocr",
        lambda pixmap: recognize_colors.append(
            pixmap.toImage().pixelColor(0, 0).name()
        ),
    )

    tab.process_file(str(old_path))
    assert old_entered.wait(timeout=1)
    tab.process_file(str(new_path))
    qtbot.waitUntil(lambda: recognize_colors == ["#0000ff"], timeout=2000)
    release_old.set()
    qtbot.waitUntil(lambda: not tab._image_load_jobs.is_running, timeout=2000)
    assert recognize_colors == ["#0000ff"]

    old_entered.clear()
    release_old.clear()
    tab.process_file(str(old_path))
    assert old_entered.wait(timeout=1)
    tab.set_closing(True)
    release_old.set()
    qtbot.waitUntil(lambda: not tab._image_load_jobs.is_running, timeout=2000)
    assert recognize_colors == ["#0000ff"]


def test_paste_invalidates_slow_file_decode(
    qapp, qtbot, tmp_path, monkeypatch, sample_pixmap
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    path = tmp_path / "old.png"
    path.write_bytes(b"old")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    entered = threading.Event()
    release = threading.Event()

    def slow_decode(_path, _cancel_event):
        entered.set()
        release.wait(timeout=2)
        return _image("red")

    class FakeClipboard:
        def pixmap(self):
            return sample_pixmap

    monkeypatch.setattr(module, "decode_image_file", slow_decode)
    monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
    recognize_calls = []
    monkeypatch.setattr(tab, "run_ocr", recognize_calls.append)

    tab.process_file(str(path))
    assert entered.wait(timeout=1)
    tab._on_paste()
    release.set()
    qtbot.waitUntil(lambda: not tab._image_load_jobs.is_running, timeout=2000)

    assert recognize_calls == []
    assert tab._pending_pixmap is not None
    assert tab._pending_pixmap.toImage().pixelColor(
        0, 0
    ) == sample_pixmap.toImage().pixelColor(0, 0)


def test_busy_state_rejects_all_new_input_entrypoints(
    qapp, qtbot, tmp_path, monkeypatch, sample_pixmap
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    path = tmp_path / "busy.png"
    path.write_bytes(b"placeholder")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    original = QImage(2, 2, QImage.Format.Format_RGB32)
    original.fill(QColor("red"))
    tab.set_image_for_recognition(sample_pixmap)

    dialogs = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: dialogs.append(True) or (str(path), ""),
    )
    decode_calls = []
    monkeypatch.setattr(
        module,
        "decode_image_file",
        lambda *_args: decode_calls.append(True) or original,
    )

    class FakeClipboard:
        def pixmap(self):
            return sample_pixmap

    monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
    screenshots = []
    tab.screenshot_requested.connect(lambda: screenshots.append(True))

    tab._set_processing(True)
    assert not tab._file_btn.isEnabled()
    assert not tab._paste_btn.isEnabled()
    assert not tab._screenshot_btn.isEnabled()

    tab._on_file_btn_clicked()
    tab._on_paste()
    tab._preview_widget.screenshot_requested.emit()
    tab.process_file(str(path))
    qtbot.wait(30)

    assert dialogs == []
    assert decode_calls == []
    assert screenshots == []


def test_document_gpu_capability_unknown_does_not_probe_on_gui_thread(
    qapp, qtbot, tmp_path, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    path = tmp_path / "cold.pdf"
    path.write_bytes(b"%PDF")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    messages = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        lambda _parent, title, text: messages.append((title, text)),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, title, text: messages.append((title, text)),
    )
    recognize_calls = []
    monkeypatch.setattr(tab, "_run_ocr_with_file", recognize_calls.append)

    assert tab._preprocess_options.gpu_capability is None
    tab.process_file(str(path))

    assert recognize_calls == []
    assert messages
    assert "检测" in "".join(messages[0])


def _result_with_preprocessed_image(payload: bytes) -> OCRResult:
    return OCRResult(
        raw_text="result",
        content_list=[{"type": "text", "text": "result"}],
        preprocessed_image=payload,
    )


def _quiet_result_rendering(tab, monkeypatch) -> None:
    monkeypatch.setattr(tab, "_display_result", lambda _result: None)
    monkeypatch.setattr(tab._preview_widget, "set_text_blocks", lambda _blocks: None)
    monkeypatch.setattr(tab._preprocess_options, "set_collapsed", lambda _value: None)
    monkeypatch.setattr(tab._text_options_widget, "set_collapsed", lambda _value: None)


def test_preprocessed_image_decode_is_responsive_and_latest_wins(
    qapp, qtbot, monkeypatch
):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    _quiet_result_rendering(tab, monkeypatch)
    old_entered = threading.Event()
    release_old = threading.Event()

    def decode(payload, _cancel_event):
        if payload == b"old":
            old_entered.set()
            release_old.wait(timeout=2)
            return _image("red")
        return _image("blue")

    monkeypatch.setattr(module, "decode_image_bytes", decode)
    colors = []
    monkeypatch.setattr(
        tab._preview_widget,
        "set_pixmap",
        lambda pixmap: colors.append(pixmap.toImage().pixelColor(0, 0).name()),
    )

    tab._on_ocr_finished(_result_with_preprocessed_image(b"old"))
    assert old_entered.wait(timeout=1)
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: tab._preprocessed_image_jobs.is_running
    )
    tab._on_ocr_finished(_result_with_preprocessed_image(b"new"))
    qtbot.waitUntil(lambda: colors == ["#0000ff"], timeout=2000)
    release_old.set()
    qtbot.waitUntil(lambda: not tab._preprocessed_image_jobs.is_running, timeout=2000)
    assert colors == ["#0000ff"]


def test_preprocessed_image_decode_drops_result_after_closing(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    _quiet_result_rendering(tab, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def slow_decode(_payload, _cancel_event):
        entered.set()
        release.wait(timeout=2)
        return _image("red")

    monkeypatch.setattr(module, "decode_image_bytes", slow_decode)
    applied = []
    monkeypatch.setattr(
        tab._preview_widget, "set_pixmap", lambda pixmap: applied.append(pixmap)
    )

    tab._on_ocr_finished(_result_with_preprocessed_image(b"slow"))
    assert entered.wait(timeout=1)
    tab.set_closing(True)
    release.set()
    qtbot.waitUntil(lambda: not tab._preprocessed_image_jobs.is_running, timeout=2000)
    assert applied == []


def test_closing_propagates_and_drain_is_widget_free_poll(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.views.tabs.single_recognition_tab")
    tab = module.SingleRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    child_closing = []
    monkeypatch.setattr(
        tab._result_widget,
        "set_closing",
        lambda closing: child_closing.append(closing),
    )
    monkeypatch.setattr(
        tab._result_widget,
        "drain",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("不得从协调线程调用 QWidget.drain")
        ),
    )

    class DrainJob:
        def __init__(self):
            self.thread_ids = []

        def drain(self, _timeout_ms):
            self.thread_ids.append(threading.get_ident())
            return True

    export_job = DrainJob()
    render_job = DrainJob()
    tab._result_widget._export_job = export_job
    tab._result_widget._render_jobs = {render_job}
    worker_entered = threading.Event()
    worker_release = threading.Event()
    tab._image_load_jobs.submit(
        lambda _cancel_event: (
            worker_entered.set(),
            worker_release.wait(timeout=2),
            QImage(),
        )[-1]
    )
    assert worker_entered.wait(timeout=1)

    tab.set_closing(True)
    assert tab.drain(0) is False
    worker_release.set()
    qtbot.waitUntil(lambda: not tab._image_load_jobs.is_running, timeout=2000)
    assert tab.drain(0) is True

    assert child_closing == [True]
    assert export_job.thread_ids == [threading.get_ident()]
    assert render_job.thread_ids == [threading.get_ident()]
    tab._result_widget._export_job = None
    tab._result_widget._render_jobs = set()
