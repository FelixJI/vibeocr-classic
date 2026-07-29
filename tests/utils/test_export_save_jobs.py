"""共享 Export/Save QThread 作业的生命周期与线程边界测试。"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, QThread, Slot

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.export_jobs import (
    BatchExportReport,
    ExportedFile,
    ExportItem,
    ExportJobCancelled,
    ExportSaveJob,
    OCRResultSnapshot,
    TextBlockSnapshot,
    _detached_value,
    _reserved_unique_path,
    _result_value,
    export_batch_operation,
    export_single_operation,
    save_bitmap_operation,
    save_svg_operation,
    snapshot_ocr_result,
)


def test_slow_job_keeps_qt_event_loop_responsive_and_callbacks_on_gui_thread(
    qapp, qtbot
):
    started = threading.Event()
    release = threading.Event()
    operation_threads: list[QThread] = []
    callback_threads: list[QThread] = []

    def slow_operation(_cancel, _progress):
        operation_threads.append(QThread.currentThread())
        started.set()
        release.wait(timeout=2)
        return "ok"

    class Receiver(QObject):
        @Slot(object)
        def completed(self, _result):
            callback_threads.append(QThread.currentThread())

    receiver = Receiver()
    job = ExportSaveJob(slow_operation)
    job.completed.connect(receiver.completed)
    job.start()
    qtbot.waitUntil(started.is_set, timeout=1000)

    assert_qt_event_loop_responsive(qtbot, in_flight=job.isRunning)
    release.set()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=2000)
    qtbot.waitUntil(lambda: bool(callback_threads), timeout=1000)

    assert operation_threads[0] is not qapp.thread()
    assert callback_threads == [qapp.thread()]
    assert job.drain(0)


def test_batch_n_items_reserves_duplicate_names_and_reports_failures(
    qapp, qtbot, tmp_path
):
    calls: list[Path] = []

    class ExportClient:
        def export_ocr_sync(self, _payload, *, output_path, **_kwargs):
            path = Path(output_path)
            calls.append(path)
            if path.stem.endswith("_1"):
                return {}
            path.write_text("ok", encoding="utf-8")
            return {"output_path": str(path)}

    items = (
        ExportItem("same.png", {}, tmp_path, "txt"),
        ExportItem("same.jpg", {}, tmp_path, "txt"),
        ExportItem("other.png", {}, tmp_path, "txt"),
    )
    progress: list[tuple[int, int, str]] = []
    results: list[BatchExportReport] = []
    job = ExportSaveJob(export_batch_operation(ExportClient(), items))
    job.progress.connect(lambda *args: progress.append(args))
    job.completed.connect(results.append)
    job.start()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=2000)
    qtbot.waitUntil(lambda: bool(results), timeout=1000)

    assert [path.name for path in calls] == ["same.txt", "same_1.txt", "other.txt"]
    assert progress == [
        (1, 3, "same.png"),
        (2, 3, "same.jpg"),
        (3, 3, "other.png"),
    ]
    assert results[0].success_count == 2
    assert results[0].fail_count == 1
    assert [item.actual_path.name for item in results[0].renamed] == ["same_1.txt"]


def test_cancel_and_bounded_drain_ignore_slow_result(qapp, qtbot):
    started = threading.Event()
    release = threading.Event()
    completed: list[object] = []
    cancelled: list[bool] = []

    def slow_operation(_cancel, _progress):
        started.set()
        release.wait(timeout=2)
        return "late"

    job = ExportSaveJob(slow_operation)
    job.completed.connect(completed.append)
    job.cancelled.connect(lambda: cancelled.append(True))
    job.start()
    qtbot.waitUntil(started.is_set, timeout=1000)
    job.cancel()
    assert not job.drain(1)
    release.set()
    assert job.drain(2000)
    qtbot.waitUntil(lambda: bool(cancelled), timeout=1000)
    assert completed == []
    assert job.status == ExportSaveJob.STATUS_CANCELLED


def test_uncaught_failure_has_error_and_terminal_callback(qapp, qtbot):
    failures: list[str] = []

    def broken(_cancel, _progress):
        raise OSError("disk full")

    job = ExportSaveJob(broken)
    job.failed.connect(failures.append)
    job.start()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=1000)
    qtbot.waitUntil(lambda: bool(failures), timeout=1000)
    assert failures == ["disk full"]
    assert job.status == ExportSaveJob.STATUS_FAILED
    assert job.error_message == "disk full"


# =============================================================================
# Task 4 覆盖率补充：export_jobs 纯逻辑（无 GUI 线程依赖，直接函数调用）
# =============================================================================


def test_result_value_dict_and_object_and_default():
    """_result_value: dict.get / getattr / 默认值三种路径。"""
    obj = SimpleNamespace(raw_text="hi", score=0.5)
    assert _result_value(obj, "raw_text", "") == "hi"
    assert _result_value(obj, "missing", "fallback") == "fallback"

    as_dict = {"raw_text": "dh", "score": 0.9}
    assert _result_value(as_dict, "raw_text", "") == "dh"
    assert _result_value(as_dict, "missing", 7) == 7


def test_detached_value_handles_scalars_containers_and_bytes():
    """_detached_value: 标量透传、dict/list/tuple 深拷贝、bytes 转换。"""
    # 标量与 None 原样返回
    assert _detached_value(None) is None
    assert _detached_value("str") == "str"
    assert _detached_value(42) == 42
    assert _detached_value(3.14) == 3.14
    assert _detached_value(True) is True

    # bytearray / memoryview → bytes
    assert _detached_value(bytearray(b"abc")) == b"abc"
    assert _detached_value(memoryview(b"xyz")) == b"xyz"

    # dict 深拷贝（嵌套）
    nested = {"a": [1, 2], "b": {"c": 3}}
    detached = _detached_value(nested)
    assert detached == nested
    detached["a"].append(99)
    assert nested["a"] == [1, 2]  # 原始未被改动

    # list 深拷贝
    original_list = [{"k": 1}, {"k": 2}]
    detached_list = _detached_value(original_list)
    assert detached_list == original_list
    detached_list[0]["k"] = 999
    assert original_list[0]["k"] == 1

    # tuple 深拷贝（仍是 tuple）
    assert _detached_value((1, 2, 3)) == (1, 2, 3)
    assert isinstance(_detached_value((1, 2, 3)), tuple)

    # 未知类型走 deepcopy
    class Custom:
        def __deepcopy__(self, memo):
            return self

    custom = Custom()
    assert _detached_value(custom) is custom


def test_snapshot_ocr_result_text_blocks_only():
    """snapshot_ocr_result: 仅 text_blocks 分支，构造 TextBlockSnapshot。"""
    block = SimpleNamespace(
        text="hello",
        score=0.8,
        bbox=(1.0, 2.0, 3.0, 4.0),
        polygon=(1.0, 2.0, 3.0, 4.0),
        page_idx=0,
        is_manually_edited=True,
        content_index=2,
        content_id="blk-2",
        label="text",
        order=5,
    )
    result = SimpleNamespace(
        raw_text="raw", markdown_text="md", html_text="<p>html</p>",
        text_blocks=[block],
    )

    snap = snapshot_ocr_result(
        result, include_content_list=False, include_images=False, include_text_blocks=True
    )

    assert isinstance(snap, OCRResultSnapshot)
    assert snap.raw_text == "raw"
    assert snap.markdown_text == "md"
    assert snap.html_text == "<p>html</p>"
    assert snap.content_list == ()
    assert snap.images is None
    assert len(snap.text_blocks) == 1
    tb = snap.text_blocks[0]
    assert isinstance(tb, TextBlockSnapshot)
    assert tb.text == "hello"
    assert tb.score == 0.8
    assert tb.bbox == (1.0, 2.0, 3.0, 4.0)
    assert tb.polygon == (1.0, 2.0, 3.0, 4.0)
    assert tb.page_idx == 0
    assert tb.is_manually_edited is True
    assert tb.content_index == 2
    assert tb.content_id == "blk-2"
    assert tb.label == "text"
    assert tb.order == 5


def test_snapshot_ocr_result_handles_missing_and_falsy_attributes():
    """缺字段、空值、None 均安全降级（getattr 默认 + or 短路）。"""
    block = SimpleNamespace(text=None, score=None, bbox=None, polygon=None)
    # 仅含 text 属性，其它全走默认
    result = SimpleNamespace(text_blocks=[block], raw_text=None)

    snap = snapshot_ocr_result(
        result, include_content_list=False, include_text_blocks=True
    )

    tb = snap.text_blocks[0]
    assert tb.text == ""
    assert tb.score == 0.0
    assert tb.bbox is None
    assert tb.polygon is None
    assert tb.page_idx is None
    assert tb.label == "text"
    assert tb.order == -1


def test_snapshot_ocr_result_content_list_validates(monkeypatch):
    """include_content_list=True 时调用 validate_table_blocks。"""
    calls: list = []

    def fake_validate(content):
        calls.append(content)

    monkeypatch.setattr(
        "vibeocr.backend.tables.blocks.validate_table_blocks", fake_validate
    )
    result = SimpleNamespace(
        content_list=[{"type": "text", "text": "a"}],
        text_blocks=(),
    )

    snap = snapshot_ocr_result(result, include_content_list=True)

    assert snap.content_list == ({"type": "text", "text": "a"},)
    assert calls == [snap.content_list]


def test_snapshot_ocr_result_images_included():
    """include_images=True 时深拷贝 images。"""
    images = {"img1": {"url": "data:..."}}
    result = SimpleNamespace(images=images, text_blocks=())

    snap = snapshot_ocr_result(
        result, include_content_list=False, include_images=True
    )

    assert snap.images == images
    # 深拷贝：改 snap.images 不影响原始
    snap.images["img1"]["url"] = "changed"
    assert images["img1"]["url"] == "data:..."


def test_snapshot_ocr_result_empty_text_blocks():
    """text_blocks 为空元组时 blocks 为 ()。"""
    result = SimpleNamespace(text_blocks=())
    snap = snapshot_ocr_result(result, include_text_blocks=True)
    assert snap.text_blocks == ()


def test_batch_export_report_properties():
    """BatchExportReport: success_count / fail_count / renamed。"""
    files = (
        ExportedFile(
            requested_path=Path("/a.pdf"), actual_path=Path("/a.pdf"), success=True
        ),
        ExportedFile(
            requested_path=Path("/b.pdf"), actual_path=Path("/b_1.pdf"), success=False
        ),
        ExportedFile(
            requested_path=Path("/c.pdf"),
            actual_path=Path("/c_1.pdf"),
            success=True,
        ),
    )
    report = BatchExportReport(files)

    assert report.success_count == 2
    assert report.fail_count == 1
    assert [f.actual_path.name for f in report.renamed] == ["b_1.pdf", "c_1.pdf"]


def test_reserved_unique_path_avoids_existing_and_reserved(tmp_path):
    """_reserved_unique_path: 磁盘已存在 + 预留集已占用 双重避让。"""
    existing = tmp_path / "out.txt"
    existing.write_text("x", encoding="utf-8")

    reserved: set[Path] = set()
    first = _reserved_unique_path(tmp_path / "out.txt", reserved)
    # out.txt 已存在 → out_1.txt
    assert first.name == "out_1.txt"
    assert first in reserved

    # 第二次：out.txt 存在 + out_1.txt 在 reserved → out_2.txt
    second = _reserved_unique_path(tmp_path / "out.txt", reserved)
    assert second.name == "out_2.txt"
    assert second in reserved


def test_export_single_operation_success_returns_path(tmp_path):
    """export_single_operation: client 返回 truthy body → 返回 output_path。"""
    client = MagicMock()
    client.export_ocr_sync.return_value = {"output_path": str(tmp_path / "r.txt")}
    output = tmp_path / "r.txt"

    cancel_event = threading.Event()
    op = export_single_operation(client, {"raw_text": "x"}, output, "txt")
    result = op(cancel_event, lambda *args: None)

    assert result == output
    client.export_ocr_sync.assert_called_once()


def test_export_single_operation_false_raises_runtime_error(tmp_path):
    """export_single_operation: _export_via_supervisor 返回 False → RuntimeError。"""
    client = MagicMock()
    client.export_ocr_sync.return_value = {}  # 无 output_path → False
    output = tmp_path / "r.txt"

    cancel_event = threading.Event()
    op = export_single_operation(client, {"raw_text": "x"}, output, "txt")
    with pytest.raises(RuntimeError, match="导出失败"):
        op(cancel_event, lambda *args: None)


def test_export_single_operation_cancel_event_raises(tmp_path):
    """cancel_event 已 set → ExportJobCancelled（被 ExportSaveJob 转成 cancelled）。"""
    client = MagicMock()
    output = tmp_path / "r.txt"
    cancel_event = threading.Event()
    cancel_event.set()

    op = export_single_operation(client, {"raw_text": "x"}, output, "txt")
    with pytest.raises(ExportJobCancelled):
        op(cancel_event, lambda *args: None)
    client.export_ocr_sync.assert_not_called()


def test_export_via_supervisor_adapter_fallback_returns_false(tmp_path, monkeypatch):
    """无 injected export_ocr_sync 时走 adapter fallback，client 为 None → False。"""
    # get_supervisor_adapter().inference_sync_client is None → RuntimeError → False
    class FakeAdapter:
        inference_sync_client = None

    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: FakeAdapter(),
    )
    output = tmp_path / "r.txt"
    cancel_event = threading.Event()
    op = export_single_operation(None, {"raw_text": "x"}, output, "txt")
    with pytest.raises(RuntimeError):
        op(cancel_event, lambda *args: None)


def test_save_bitmap_operation_writes_png(tmp_path):
    """save_bitmap_operation: PIL Image → 写盘 → 返回 path。"""
    from PIL import Image

    img = Image.new("RGB", (4, 4), color="red")
    output = tmp_path / "out.png"

    cancel_event = threading.Event()
    op = save_bitmap_operation(img, output, "PNG")
    result = op(cancel_event, lambda *args: None)

    assert result == output
    assert output.exists()
    # 验证写出的内容确实是有效 PNG
    reloaded = Image.open(output)
    assert reloaded.size == (4, 4)


def test_save_bitmap_operation_cancel_raises(tmp_path):
    """cancel_event 已 set → ExportJobCancelled。"""
    from PIL import Image

    img = Image.new("RGB", (2, 2))
    output = tmp_path / "out.png"
    cancel_event = threading.Event()
    cancel_event.set()

    op = save_bitmap_operation(img, output, "PNG")
    with pytest.raises(ExportJobCancelled):
        op(cancel_event, lambda *args: None)


def test_save_svg_operation_injected_backend(tmp_path):
    """save_svg_operation: 注入 backend.generate_qrcode_svg_sync → 写 SVG。"""
    backend = MagicMock()
    backend.generate_qrcode_svg_sync.return_value = "<svg></svg>"
    output = tmp_path / "qr.svg"

    cancel_event = threading.Event()
    op = save_svg_operation(backend, "hello", {"size": 100}, output)
    result = op(cancel_event, lambda *args: None)

    assert result == output
    assert output.read_text(encoding="utf-8") == "<svg></svg>"
    backend.generate_qrcode_svg_sync.assert_called_once()


def test_save_svg_operation_adapter_fallback(tmp_path, monkeypatch):
    """无注入 generate_qrcode_svg_sync 时走 adapter，base64 decode 写盘。"""
    import base64

    svg_bytes = b"<svg>fallback</svg>"
    b64 = base64.b64encode(svg_bytes).decode("ascii")

    class FakeClient:
        def generate_qrcode(self, *args, **kwargs):
            return b64

    class FakeAdapter:
        inference_sync_client = FakeClient()

    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: FakeAdapter(),
    )
    output = tmp_path / "qr.svg"
    cancel_event = threading.Event()
    op = save_svg_operation(None, "text", {}, output)
    result = op(cancel_event, lambda *args: None)

    assert result == output
    assert output.read_bytes() == svg_bytes


def test_save_svg_operation_adapter_unavailable_raises(tmp_path, monkeypatch):
    """adapter.inference_sync_client 为 None → RuntimeError。"""
    class FakeAdapter:
        inference_sync_client = None

    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: FakeAdapter(),
    )
    output = tmp_path / "qr.svg"
    cancel_event = threading.Event()
    op = save_svg_operation(None, "text", {}, output)
    with pytest.raises(RuntimeError, match="supervisor utility client"):
        op(cancel_event, lambda *args: None)


def test_export_via_supervisor_injected_path_field(tmp_path):
    """injected_export 返回 dict 含 'path'（非 'output_path'）→ 仍判 truthy。"""
    client = MagicMock()
    client.export_ocr_sync.return_value = {"path": str(tmp_path / "x.txt")}
    output = tmp_path / "x.txt"
    cancel_event = threading.Event()
    op = export_single_operation(client, {"raw_text": "x"}, output, "txt")
    result = op(cancel_event, lambda *args: None)
    assert result == output


# =============================================================================
# ExportSaveJob.run 直接调用 + properties + cancel/drain 边界
# （不走 QThread.start，直接调 run() 覆盖各分支）
# =============================================================================


def test_export_save_job_run_completed_path_emits_signals(qapp):
    """run() 成功路径：completed + terminal 信号 + status/result 属性。"""
    job = ExportSaveJob(lambda cancel, progress: ("ok", 42))
    completed: list = []
    terminal: list = []
    job.completed.connect(completed.append)
    job.terminal.connect(lambda status, result: terminal.append((status, result)))

    job.run()  # 直接调用（同线程）

    assert job.status == ExportSaveJob.STATUS_COMPLETED
    assert job.result == ("ok", 42)
    assert job.error_message == ""
    assert completed == [("ok", 42)]
    assert terminal[-1] == (ExportSaveJob.STATUS_COMPLETED, ("ok", 42))


def test_export_save_job_run_cancel_event_set_before_operation(qapp):
    """cancel_event 在 run() 入口已 set → ExportJobCancelled 分支。"""
    job = ExportSaveJob(lambda cancel, progress: "should not run")
    job.cancel()  # 设 cancel_event
    cancelled: list = []
    job.cancelled.connect(lambda: cancelled.append(True))

    job.run()

    assert job.status == ExportSaveJob.STATUS_CANCELLED
    assert cancelled == [True]


def test_export_save_job_run_cancel_event_set_after_operation(qapp):
    """operation 返回后 cancel_event 才 set → 仍判为 cancelled（line 240-241）。"""
    def operation(cancel, progress):
        # operation 成功返回，但之后 cancel
        cancel.set()
        return "late-result"

    job = ExportSaveJob(operation)
    cancelled: list = []
    completed: list = []
    job.cancelled.connect(lambda: cancelled.append(True))
    job.completed.connect(completed.append)

    job.run()

    assert job.status == ExportSaveJob.STATUS_CANCELLED
    assert cancelled == [True]
    assert completed == []
    assert job.result is None


def test_export_save_job_run_failure_emits_failed(qapp):
    def broken(cancel, progress):
        raise OSError("disk full")

    job = ExportSaveJob(broken)
    failed: list = []
    job.failed.connect(failed.append)

    job.run()

    assert job.status == ExportSaveJob.STATUS_FAILED
    assert job.error_message == "disk full"
    assert failed == ["disk full"]


def test_export_save_job_cancel_sets_event(qapp):
    job = ExportSaveJob(lambda c, p: None)
    assert not job._cancel_event.is_set()
    job.cancel()
    assert job._cancel_event.is_set()


def test_export_save_job_drain_returns_true_when_not_running(qapp):
    job = ExportSaveJob(lambda c, p: None)
    # 未 start，drain 直接返回 True
    assert job.drain(0) is True


def test_export_save_job_drain_from_self_returns_false(qapp):
    """drain 在 worker 自身线程内调用 → 返回 False（防自等待死锁）。"""
    job = ExportSaveJob(lambda c, p: None)
    # monkeypatch QThread.currentThread to mimic self
    from PySide6.QtCore import QThread

    original = QThread.currentThread
    QThread.currentThread = lambda: job  # type: ignore[method-assign]
    try:
        assert job.drain(100) is False
    finally:
        QThread.currentThread = original  # type: ignore[method-assign]


# =============================================================================
# export_batch_operation 取消路径（reserved_unique_path + cancel 分支）
# =============================================================================


def test_export_batch_operation_cancel_at_start_raises(tmp_path):
    """cancel_event 在规划前已 set → ExportJobCancelled。"""
    client = MagicMock()
    items = (ExportItem("a.png", {}, tmp_path, "txt"),)
    cancel_event = threading.Event()
    cancel_event.set()

    op = export_batch_operation(client, items)
    with pytest.raises(ExportJobCancelled):
        op(cancel_event, lambda *args: None)
    client.export_ocr_sync.assert_not_called()


def test_export_batch_operation_cancel_midway_stops(tmp_path):
    """第二批前 cancel → 抛 ExportJobCancelled，已导出的不计入 report。"""
    calls: list = []

    class ExportClient:
        def export_ocr_sync(self, _payload, *, output_path, **_kwargs):
            calls.append(Path(output_path))
            Path(output_path).write_text("ok", encoding="utf-8")
            return {"output_path": str(output_path)}

    client = ExportClient()
    items = (
        ExportItem("a.png", {}, tmp_path, "txt"),
        ExportItem("b.png", {}, tmp_path, "txt"),
    )
    cancel_event = threading.Event()

    def set_cancel_after_first(cancel, progress):
        cancel_event.set()

    # 第一个 item 后设 cancel：通过在 operation 内部无法直接做，
    # 改用包裹 client：第一次调用后 set cancel
    original = client.export_ocr_sync

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel_event.set()
        return result

    client.export_ocr_sync = wrapped  # type: ignore

    op = export_batch_operation(client, items)
    with pytest.raises(ExportJobCancelled):
        op(cancel_event, lambda *args: None)

    # 只导出了第一个
    assert len(calls) == 1


def test_export_batch_operation_adapter_fallback(tmp_path, monkeypatch):
    """无 injected export_ocr_sync → 走 adapter fallback。"""
    class FakeClient:
        def export_ocr(self, **kwargs):
            path = kwargs["output_path"]
            Path(path).write_text("ok", encoding="utf-8")
            return {"output_path": path}

    class FakeAdapter:
        inference_sync_client = FakeClient()

    monkeypatch.setattr(
        "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
        lambda: FakeAdapter(),
    )
    items = (ExportItem("a.png", {"raw_text": "x"}, tmp_path, "txt"),)
    cancel_event = threading.Event()
    op = export_batch_operation(None, items)
    report = op(cancel_event, lambda *args: None)

    assert report.success_count == 1
    assert report.fail_count == 0
