"""结果组件导出与 QR 保存的异步 UI 契约。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QThread

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.export_jobs import snapshot_ocr_result


class _Messages:
    def __init__(self) -> None:
        self.information_calls: list[tuple] = []
        self.warning_calls: list[tuple] = []

    def information(self, *args):
        self.information_calls.append(args)

    def warning(self, *args):
        self.warning_calls.append(args)


class _ExportClient:
    def __init__(self, export) -> None:
        self._export = export

    def export_ocr_sync(self, payload, *, output_path, **kwargs):
        return self._export(payload, output_path, kwargs["format"])


def _patch_save_dialog(monkeypatch, module: str, path) -> None:
    if module.endswith("qrcode_tab"):
        from PySide6.QtWidgets import QFileDialog

        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(path), ""),
        )
    else:
        monkeypatch.setattr(
            f"{module}.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(path), ""),
        )


def test_result_export_success_is_async_busy_and_reports_on_gui(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

    started = threading.Event()
    release = threading.Event()
    messages = _Messages()
    output = tmp_path / "result.docx"
    backend_threads: list[QThread] = []

    def slow_export(_payload, path, _fmt):
        backend_threads.append(QThread.currentThread())
        started.set()
        release.wait(timeout=2)
        path = Path(path)
        path.write_bytes(b"PK")
        return {"output_path": str(path)}

    widget = ResultViewWidget(utility_client=_ExportClient(slow_export))
    qtbot.addWidget(widget)
    widget._current_result = {"raw_text": "hello"}
    widget._current_snapshot = snapshot_ocr_result(widget._current_result)
    _patch_save_dialog(monkeypatch, "vibeocr.classic.widgets.result_view_widget", output)
    monkeypatch.setattr("vibeocr.classic.widgets.result_view_widget.QMessageBox", messages)
    widget._on_export_file("docx")
    qtbot.waitUntil(started.is_set, timeout=1000)

    assert not widget._export_docx_btn.isEnabled()
    assert not widget._export_xlsx_btn.isEnabled()
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: widget._export_job is not None
    )
    release.set()
    qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)

    assert output.read_bytes() == b"PK"
    assert len(backend_threads) == 1
    assert backend_threads[0] is not qapp.thread()
    assert len(messages.information_calls) == 1
    assert messages.warning_calls == []


def test_result_export_failure_uses_existing_warning(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

    widget = ResultViewWidget(
        utility_client=_ExportClient(lambda *_args: {})
    )
    qtbot.addWidget(widget)
    widget._current_result = {"raw_text": "hello"}
    widget._current_snapshot = snapshot_ocr_result(widget._current_result)
    messages = _Messages()
    output = tmp_path / "failed.xlsx"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.widgets.result_view_widget", output)
    monkeypatch.setattr("vibeocr.classic.widgets.result_view_widget.QMessageBox", messages)

    widget._on_export_file("xlsx")
    qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)

    assert messages.information_calls == []
    assert len(messages.warning_calls) == 1
    assert messages.warning_calls[0][1:] == (
        "导出失败",
        "导出失败，请重试或查看日志。",
    )


def test_result_export_keeps_submission_snapshot_across_switch_and_clear(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

    live_result = {
        "raw_text": "submitted",
        "markdown_text": "submitted markdown",
        "content_list": [{"type": "text", "text": "submitted block"}],
    }
    started = threading.Event()
    release = threading.Event()
    seen_results: list[object] = []
    messages = _Messages()
    output = tmp_path / "snapshot.docx"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.widgets.result_view_widget", output)
    def slow_export(payload, path, _fmt):
        started.set()
        release.wait(timeout=2)
        seen_results.append(payload)
        path = Path(path)
        path.write_bytes(b"snapshot")
        return {"output_path": str(path)}

    widget = ResultViewWidget(utility_client=_ExportClient(slow_export))
    qtbot.addWidget(widget)
    widget._current_result = live_result
    widget._current_snapshot = snapshot_ocr_result(live_result)
    monkeypatch.setattr("vibeocr.classic.widgets.result_view_widget.QMessageBox", messages)
    widget._on_export_file("docx")
    qtbot.waitUntil(started.is_set, timeout=1000)

    live_result["raw_text"] = "mutated"
    live_result["content_list"][0]["text"] = "mutated block"
    widget.display_result(
        {
            "raw_text": "replacement",
            "content_list": [{"type": "text", "text": "replacement"}],
        }
    )
    widget.clear()
    assert widget._export_job is not None
    release.set()
    qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)

    snapshot = seen_results[0]
    assert snapshot["raw_text"] == "submitted"
    assert snapshot["content_list"][0]["text"] == "submitted block"
    assert len(messages.information_calls) == 1
    assert messages.warning_calls == []


def test_result_drain_from_non_gui_thread_waits_native_but_requires_gui_cleanup(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

    started = threading.Event()
    release = threading.Event()
    output = tmp_path / "wait.docx"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.widgets.result_view_widget", output)
    def slow_export(_payload, path, _fmt):
        started.set()
        release.wait(timeout=2)
        path = Path(path)
        path.write_bytes(b"wait")
        return {"output_path": str(path)}

    widget = ResultViewWidget(utility_client=_ExportClient(slow_export))
    qtbot.addWidget(widget)
    widget._current_result = {"raw_text": "wait"}
    widget._current_snapshot = snapshot_ocr_result(widget._current_result)
    monkeypatch.setattr(
        "vibeocr.classic.widgets.result_view_widget.QMessageBox", _Messages()
    )
    widget._on_export_file("docx")
    qtbot.waitUntil(started.is_set, timeout=1000)
    ui_calls: list[int] = []
    original_set_enabled = widget._export_docx_btn.setEnabled

    def tracked_set_enabled(enabled):
        ui_calls.append(threading.get_ident())
        original_set_enabled(enabled)

    monkeypatch.setattr(widget._export_docx_btn, "setEnabled", tracked_set_enabled)
    drained: list[bool] = []
    waiter = threading.Thread(target=lambda: drained.append(widget.drain(2000)))
    waiter.start()
    release.set()
    waiter.join(3)

    # Native work is done, but a non-GUI waiter cannot run the queued stopped
    # slot that releases the QWidget-owned reference.  The GUI shutdown poll is
    # therefore the authoritative drained transition.
    assert drained == [False]
    assert ui_calls == []
    assert widget._export_job is not None
    qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)
    assert widget.drain(0)


def test_result_close_returns_immediately_and_drops_late_export_ui(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.utils.export_jobs import _ACTIVE_JOBS
    from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

    started = threading.Event()
    release = threading.Event()
    messages = _Messages()
    output = tmp_path / "late.docx"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.widgets.result_view_widget", output)
    def slow_export(_payload, path, _fmt):
        started.set()
        release.wait(timeout=2)
        path = Path(path)
        path.write_bytes(b"late")
        return {"output_path": str(path)}

    widget = ResultViewWidget(utility_client=_ExportClient(slow_export))
    qtbot.addWidget(widget)
    widget._current_result = {"raw_text": "late"}
    widget._current_snapshot = snapshot_ocr_result(widget._current_result)
    monkeypatch.setattr("vibeocr.classic.widgets.result_view_widget.QMessageBox", messages)
    widget._on_export_file("docx")
    qtbot.waitUntil(started.is_set, timeout=1000)
    job = widget._export_job
    assert job is not None

    before = time.perf_counter()
    widget.close()
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 150
    assert job.isRunning()
    assert job in _ACTIVE_JOBS
    release.set()
    assert job.drain(2000)
    qtbot.waitUntil(lambda: job not in _ACTIVE_JOBS, timeout=1000)
    assert messages.information_calls == []
    assert messages.warning_calls == []


class _QrBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_qrcode_svg_sync(self, text, *, options=None):
        self.started.set()
        self.release.wait(timeout=2)
        return f"<svg>{text}</svg>"


def test_qr_svg_save_is_async_and_close_drops_late_write(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.utils.export_jobs import _ACTIVE_JOBS
    from vibeocr.classic.views.tabs.qrcode_tab import QrcodeTab

    backend = _QrBackend()
    tab = QrcodeTab(backend=backend)
    qtbot.addWidget(tab)
    tab._current_image = Image.new("RGB", (8, 8), "black")
    tab._text_input.setPlainText("late")
    tab._debounce_timer.stop()
    output = tmp_path / "late.svg"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.views.tabs.qrcode_tab", output)

    tab._on_save()
    qtbot.waitUntil(backend.started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: tab._save_job is not None)
    tab.show()
    before = time.perf_counter()
    tab.close()
    elapsed_ms = (time.perf_counter() - before) * 1000
    assert elapsed_ms < 150
    assert tab._save_job is not None and tab._save_job.isRunning()
    assert tab._save_job in _ACTIVE_JOBS
    backend.release.set()
    qtbot.waitUntil(lambda: tab._save_job is None, timeout=2000)

    assert not output.exists()
    assert not tab._btn_save.isEnabled()


def test_qr_png_and_jpeg_save_in_worker(qapp, qtbot, monkeypatch, tmp_path):
    from vibeocr.classic.views.tabs.qrcode_tab import QrcodeTab

    tab = QrcodeTab(backend=_QrBackend())
    qtbot.addWidget(tab)
    tab._current_image = Image.new("RGB", (8, 8), "red")

    for suffix, expected_format in (("png", "PNG"), ("jpeg", "JPEG")):
        output = tmp_path / f"qr.{suffix}"
        _patch_save_dialog(monkeypatch, "vibeocr.classic.views.tabs.qrcode_tab", output)
        tab._on_save()
        qtbot.waitUntil(lambda: tab._save_job is None, timeout=2000)
        with Image.open(output) as saved:
            assert saved.format == expected_format


def test_qr_svg_success_writes_expected_content(qapp, qtbot, monkeypatch, tmp_path):
    from vibeocr.classic.views.tabs.qrcode_tab import QrcodeTab

    backend = _QrBackend()
    backend.release.set()
    tab = QrcodeTab(backend=backend)
    qtbot.addWidget(tab)
    tab._current_image = Image.new("RGB", (8, 8), "black")
    tab._text_input.setPlainText("payload")
    tab._debounce_timer.stop()
    output = tmp_path / "qr.svg"
    _patch_save_dialog(monkeypatch, "vibeocr.classic.views.tabs.qrcode_tab", output)

    tab._on_save()
    qtbot.waitUntil(lambda: tab._save_job is None, timeout=2000)
    assert output.read_text(encoding="utf-8") == "<svg>payload</svg>"


def test_qr_save_failure_uses_existing_warning(qapp, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from vibeocr.classic.views.tabs.qrcode_tab import QrcodeTab

    class BrokenBackend(_QrBackend):
        def generate_qrcode_svg_sync(self, text, *, options=None):
            raise OSError("read only")

    tab = QrcodeTab(backend=BrokenBackend())
    qtbot.addWidget(tab)
    tab._current_image = Image.new("RGB", (8, 8), "black")
    output = tmp_path / "failed.svg"
    warnings: list[tuple] = []
    _patch_save_dialog(monkeypatch, "vibeocr.classic.views.tabs.qrcode_tab", output)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    tab._on_save()
    qtbot.waitUntil(lambda: tab._save_job is None, timeout=2000)
    assert not output.exists()
    assert len(warnings) == 1
    assert warnings[0][1:] == ("保存失败", "read only")
