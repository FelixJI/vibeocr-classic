"""批量标签页导出作业的 UI 生命周期测试。"""

from __future__ import annotations

import threading

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.export_jobs import ExportItem, snapshot_ocr_result


class _SlowExportBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.payloads: list[dict] = []

    def export_ocr_sync(self, payload, **kwargs):
        self.started.set()
        self.release.wait(timeout=2)
        self.payloads.append(payload)
        return {"path": kwargs["output_path"]}


def test_batch_export_cancel_close_is_bounded_and_drops_late_ui(qapp, qtbot, tmp_path):
    from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab

    backend = _SlowExportBackend()
    tab = BatchRecognitionTab(backend=backend)
    qtbot.addWidget(tab)
    item = ExportItem("input.png", {"raw_text": "x"}, tmp_path, "txt")

    tab._start_export_job((item,), mode="all")
    qtbot.waitUntil(backend.started.is_set, timeout=1000)
    assert not tab._export_widget.isEnabled()
    assert tab._cancel_btn.isEnabled()
    assert_qt_event_loop_responsive(
        qtbot, in_flight=lambda: tab._export_job is not None
    )

    assert tab.shutdown(timeout_ms=1) is False
    job = tab._export_job
    assert job is not None
    label = tab._progress_label.text()
    backend.release.set()
    assert tab.drain(2000)
    qtbot.wait(20)

    assert tab._export_job is None
    assert tab._progress_label.text() == label
    assert not tab._export_widget.isEnabled()
    assert not tab._start_btn.isEnabled()


def test_batch_export_uses_submission_snapshot(
    qapp, qtbot, monkeypatch, tmp_path
):
    from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab

    backend = _SlowExportBackend()
    tab = BatchRecognitionTab(backend=backend)
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "vibeocr.classic.views.batch_recognition_tab.QMessageBox.information",
        lambda *args: None,
    )
    result = {
        "raw_text": "submitted",
        "content_list": [{"type": "text", "text": "submitted block"}],
    }
    item = ExportItem(
        "input.png",
        snapshot_ocr_result(result, include_content_list=True),
        tmp_path,
        "txt",
    )

    tab._start_export_job((item,), mode="all")
    qtbot.waitUntil(backend.started.is_set, timeout=1000)
    result["raw_text"] = "mutated"
    result["content_list"][0]["text"] = "mutated block"
    backend.release.set()
    qtbot.waitUntil(lambda: tab._export_job is None, timeout=2000)

    assert backend.payloads[0]["raw_text"] == "submitted"
    assert backend.payloads[0]["content_list"][0]["text"] == "submitted block"
