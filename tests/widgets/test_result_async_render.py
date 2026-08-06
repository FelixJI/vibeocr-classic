"""ResultViewWidget 的后台 HTML 构建与迟到结果隔离测试。"""

from __future__ import annotations

import threading
import time
from importlib import import_module
from types import SimpleNamespace

import pytest

from tests.qt_responsiveness import assert_qt_event_loop_responsive


class _FakeWebView:
    def __init__(self) -> None:
        self.html_calls: list[str] = []

    def setHtml(self, html: str, _base_url=None) -> None:
        self.html_calls.append(html)


def _result(marker: str):
    return SimpleNamespace(
        raw_text=marker,
        markdown_text=marker,
        content_list=[{"type": "text", "text": marker}],
        images={},
    )


def test_slow_complex_build_keeps_qt_responsive(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module._build_result_html
    started = threading.Event()
    release = threading.Event()

    def slow_build(result, resources_dir, cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return original(result, resources_dir, cancel_event)

    monkeypatch.setattr(module, "_build_result_html", slow_build)
    complex_result = SimpleNamespace(
        raw_text="",
        markdown_text="",
        content_list=[{"type": "text", "text": str(i)} for i in range(4000)],
        images={},
    )
    widget.display_result(complex_result)
    qtbot.waitUntil(started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: bool(widget._render_jobs))
    release.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)
    assert len(web.html_calls) == 1
    assert "3999" in web.html_calls[0]


def test_rapid_display_discards_slow_old_generation(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module._build_result_html
    old_started = threading.Event()
    release_old = threading.Event()

    def ordered_build(result, resources_dir, cancel_event=None):
        if result.raw_text == "old":
            old_started.set()
            release_old.wait(timeout=2)
        return original(result, resources_dir, cancel_event)

    monkeypatch.setattr(module, "_build_result_html", ordered_build)
    widget.display_result(_result("old"))
    qtbot.waitUntil(old_started.is_set, timeout=1000)
    widget.display_result(_result("new"))
    qtbot.waitUntil(lambda: bool(web.html_calls), timeout=2000)
    release_old.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)

    assert len(web.html_calls) == 1
    assert "new" in web.html_calls[0]
    assert "old" not in web.html_calls[0]


def test_close_does_not_wait_and_drops_late_render(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    started = threading.Event()
    release = threading.Event()

    def slow_build(_result, _resources_dir, _cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return "<html>late</html>"

    monkeypatch.setattr(module, "_build_result_html", slow_build)
    widget.display_result(_result("late"))
    qtbot.waitUntil(started.is_set, timeout=1000)

    before = time.perf_counter()
    widget.close()
    elapsed_ms = (time.perf_counter() - before) * 1000
    assert elapsed_ms < 150
    assert widget._render_jobs
    release.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)
    assert web.html_calls == []


def test_image_base64_is_built_away_from_gui_thread(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    build_threads = []
    original = module._build_result_html

    def capture_thread(result, resources_dir, cancel_event=None):
        from PySide6.QtCore import QThread

        build_threads.append(QThread.currentThread())
        return original(result, resources_dir, cancel_event)

    monkeypatch.setattr(module, "_build_result_html", capture_thread)
    result = SimpleNamespace(
        raw_text="",
        markdown_text="",
        content_list=[{"type": "image", "img_path": "x.png"}],
        images={"x.png": b"png-bytes"},
    )
    widget.display_result(result)
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)
    assert build_threads[0] is not qapp.thread()
    assert "cG5nLWJ5dGVz" in web.html_calls[0]


def test_display_result_uses_submission_snapshot(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module._build_result_html
    started = threading.Event()
    release = threading.Event()

    def blocked_build(result, resources_dir, cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return original(result, resources_dir, cancel_event)

    monkeypatch.setattr(module, "_build_result_html", blocked_build)
    result = SimpleNamespace(
        raw_text="old raw",
        markdown_text="old markdown",
        content_list=[{"type": "image", "img_path": "old.png"}],
        images={"old.png": b"old-image"},
    )
    widget.display_result(result)
    qtbot.waitUntil(started.is_set, timeout=1000)
    result.raw_text = "new raw"
    result.content_list[0]["img_path"] = "new.png"
    result.images.clear()
    result.images["new.png"] = b"new-image"
    release.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)

    assert "b2xkLWltYWdl" in web.html_calls[0]
    assert "new.png" not in web.html_calls[0]


def test_display_result_detaches_only_in_worker_stable_read(qapp, qtbot, monkeypatch):
    """Large source detachment must stay off-GUI and require two equal reads."""
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from PySide6.QtCore import QThread

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module.snapshot_ocr_result
    snapshot_ran_on_gui: list[bool] = []

    def capture_snapshot_thread(*args, **kwargs):
        snapshot_ran_on_gui.append(QThread.currentThread() is qapp.thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "snapshot_ocr_result", capture_snapshot_thread)
    result = _result("submission")

    widget.display_result(result)
    qtbot.waitUntil(lambda: len(snapshot_ran_on_gui) >= 2, timeout=1000)

    assert snapshot_ran_on_gui[:2] == [False, False]
    result.content_list[0]["text"] = "mutated-after-submit"
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)
    assert "submission" in web.html_calls[0]
    assert "mutated-after-submit" not in web.html_calls[0]


def test_stable_capture_never_accepts_a_mixed_source_revision(monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

    mixed = snapshot_ocr_result(
        _result("mixed"), include_content_list=True, include_images=True
    )
    new = snapshot_ocr_result(
        _result("new-stable"), include_content_list=True, include_images=True
    )
    snapshots = iter((mixed, new, new))
    monkeypatch.setattr(module, "snapshot_ocr_result", lambda *a, **k: next(snapshots))

    captured = module._capture_stable_result_snapshot(
        object(),
        threading.Event(),
        include_content_list=True,
        include_images=True,
        include_text_blocks=False,
    )

    assert captured.raw_text == "new-stable"
    assert captured.content_list[0]["text"] == "new-stable"


def test_stable_compare_is_structural_and_does_not_pickle():
    import inspect

    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.backend.models.ocr_result import TextBlock
    from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

    source = SimpleNamespace(
        raw_text="raw",
        markdown_text="markdown",
        html_text="html",
        content_list=[
            {
                "type": "text",
                "text": "nested",
                "meta": {"rows": [1, 2, {"enabled": True}]},
            }
        ],
        images={"image.png": b"image-bytes"},
        text_blocks=[TextBlock("block", 0.9, (1, 2, 3, 4))],
    )
    left = snapshot_ocr_result(
        source,
        include_content_list=True,
        include_images=True,
        include_text_blocks=True,
    )
    right = snapshot_ocr_result(
        source,
        include_content_list=True,
        include_images=True,
        include_text_blocks=True,
    )

    assert module._stable_values_equal(left, right, threading.Event()) is True
    right.content_list[0]["meta"]["rows"][2]["enabled"] = False
    assert module._stable_values_equal(left, right, threading.Event()) is False
    assert "pickle" not in inspect.getsource(module._capture_stable_result_snapshot)


def test_stable_compare_rejects_unknown_always_equal_values():
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    class AlwaysEqual:
        def __init__(self, state):
            self.state = state

        def __eq__(self, _other):
            return True

    left = {"opaque": AlwaysEqual("old")}
    right = {"opaque": AlwaysEqual("new")}

    assert module._stable_values_equal(left, right, threading.Event()) is False


def test_stable_compare_rejects_unknown_identity_preserved_by_deepcopy():
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

    class IdentityDeepcopy:
        def __deepcopy__(self, _memo):
            return self

    opaque = IdentityDeepcopy()
    source = SimpleNamespace(
        raw_text="",
        markdown_text="",
        html_text="",
        content_list=[{"type": "text", "opaque": opaque}],
    )
    left = snapshot_ocr_result(source)
    right = snapshot_ocr_result(source)
    assert left.content_list[0]["opaque"] is right.content_list[0]["opaque"]

    assert module._stable_values_equal(left, right, threading.Event()) is False


def test_stable_compare_rejects_unknown_dict_keys_without_calling_eq():
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    class UnknownKey:
        def __hash__(self):
            return 1

        def __eq__(self, _other):
            raise AssertionError("unknown key equality must not be invoked")

    left = {UnknownKey(): "value"}
    right = {UnknownKey(): "value"}

    assert module._stable_values_equal(left, right, threading.Event()) is False


def test_copy_rebuild_cancels_during_large_field_loop():
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.backend.models.ocr_result import TextBlock
    from vibeocr.classic.utils.export_jobs import (
        ExportJobCancelled,
        snapshot_ocr_result,
    )

    source = SimpleNamespace(
        raw_text="old",
        markdown_text="old",
        html_text="old",
        content_list=[{"type": "text", "text": str(i)} for i in range(50_000)],
        text_blocks=[TextBlock(str(i), 1.0, None) for i in range(50_000)],
    )
    snapshot = snapshot_ocr_result(
        source, include_content_list=True, include_text_blocks=True
    )

    class CancelAfterChecks:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls >= 3

    started = time.perf_counter()
    with pytest.raises(ExportJobCancelled):
        module._rebuild_copy_snapshot(
            snapshot, CancelAfterChecks(), include_markdown=True
        )
    assert (time.perf_counter() - started) * 1000 < 150


def test_50k_result_submissions_stay_under_combined_gui_budget(
    qapp, qtbot, monkeypatch
):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.backend.models.ocr_result import TextBlock
    from vibeocr.classic.recognition_settings import TextBlockOptions

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    # Measure submission only.  Running the captured operations is covered by the
    # render tests; suppressing start prevents worker GIL contention from polluting
    # this GUI-thread budget assertion.
    monkeypatch.setattr(module.ExportSaveJob, "start", lambda self: None)
    content_result = SimpleNamespace(
        raw_text="",
        markdown_text="",
        content_list=[{"type": "text", "text": str(i)} for i in range(50_000)],
        images={},
    )
    layout_result = SimpleNamespace(
        raw_text="",
        text_blocks=[TextBlock(str(i), 1.0, None) for i in range(50_000)],
    )

    started = time.perf_counter()
    widget.display_result(content_result)
    widget.display_text_layout(layout_result, TextBlockOptions())
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 150
    for job in tuple(widget._render_jobs):
        widget._render_jobs.discard(job)
        job.deleteLater()


def test_old_document_edit_is_rejected_after_result_switch(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    bridge = module._Bridge(widget)
    widget._bridge = bridge
    bridge.blockEdited.connect(widget.block_edited.emit)
    edits = []
    widget.block_edited.connect(lambda index, text: edits.append((index, text)))

    widget.display_result(_result("old"))
    old_token = widget._active_document_token
    widget.display_result(_result("new"))
    new_token = widget._active_document_token

    bridge.onBlockEditedForDocument(old_token, 0, "late-old-edit")
    bridge.onBlockEditedForDocument(new_token, 0, "accepted-new-edit")

    assert old_token != new_token
    assert edits == [(0, "accepted-new-edit")]
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)


def test_stable_table_cell_bridge_rejects_stale_document(qapp):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    bridge = module._Bridge()
    edits = []
    bridge.tableCellEdited.connect(
        lambda table_id, cell_id, text: edits.append((table_id, cell_id, text))
    )
    bridge.set_active_document("current")

    bridge.onTableCellEditedForDocument(
        "stale", "table-a", "cell-a", "wrong"
    )
    bridge.onTableCellEditedForDocument(
        "current", "table-a", "cell-a", "right"
    )

    assert edits == [("table-a", "cell-a", "right")]


def test_snapshot_detaches_and_validates_canonical_table():
    from vibeocr.backend.models.ocr_result import OCRResult
    from vibeocr.classic.utils.export_jobs import snapshot_ocr_result
    from vibeocr.runtime_contracts.contracts.tables import TableCellV1, TableModelV1

    table = TableModelV1(
        table_id="snapshot",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="cell", row=0, column=0, text="before"),),
    )
    result = OCRResult(
        content_list=[{"type": "table", "table": table.to_payload()}]
    )

    snapshot = snapshot_ocr_result(result)
    result.content_list[0]["table"]["cells"][0]["text"] = "after"

    assert snapshot.content_list[0]["table"]["cells"][0]["text"] == "before"

    invalid = OCRResult(
        content_list=[
            {
                "type": "table",
                "table": {
                    **table.to_payload(),
                    "schema_version": 999,
                },
            }
        ]
    )
    with pytest.raises(ValueError, match="schema_version"):
        snapshot_ocr_result(invalid)


def test_rendered_token_waits_for_loaded_document_confirmation(
    qapp, qtbot, monkeypatch
):
    module = import_module("vibeocr.classic.widgets.result_view_widget")

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)

    widget.display_result(_result("pending-load"))
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)

    assert widget._rendered_document_token == ""
    assert widget._pending_document_token == widget._active_document_token
    widget._on_loaded_document_token(widget._pending_document_token)
    assert widget._rendered_document_token == widget._active_document_token


def _layout_result(marker: str, count: int = 1):
    from vibeocr.backend.models.ocr_result import TextBlock

    return SimpleNamespace(
        raw_text=marker,
        text_blocks=[
            TextBlock(marker if count == 1 else f"{marker}-{index}", 1.0, None)
            for index in range(count)
        ],
    )


def test_large_text_layout_build_keeps_qt_responsive(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.classic.recognition_settings import TextBlockOptions

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module._build_text_layout_html
    started = threading.Event()
    release = threading.Event()

    def slow_layout(blocks, options, cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return original(blocks, options, cancel_event)

    monkeypatch.setattr(module, "_build_text_layout_html", slow_layout)
    widget.display_text_layout(_layout_result("layout", 4000), TextBlockOptions())
    qtbot.waitUntil(started.is_set, timeout=1000)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: bool(widget._render_jobs))
    release.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)
    assert "layout-3999" in web.html_calls[0]


def test_text_layout_uses_snapshot_and_latest_generation(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.classic.recognition_settings import TextBlockOptions

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    original = module._build_text_layout_html
    old_started = threading.Event()
    release_old = threading.Event()

    def ordered_layout(blocks, options, cancel_event=None):
        if blocks[0].text == "old":
            old_started.set()
            release_old.wait(timeout=2)
        return original(blocks, options, cancel_event)

    monkeypatch.setattr(module, "_build_text_layout_html", ordered_layout)
    old_result = _layout_result("old")
    old_options = TextBlockOptions(chinese_indent=False)
    widget.display_text_layout(old_result, old_options)
    qtbot.waitUntil(old_started.is_set, timeout=1000)
    old_result.text_blocks[0].text = "mutated"
    old_options.chinese_indent = True
    widget.display_text_layout(_layout_result("new"), TextBlockOptions())
    qtbot.waitUntil(lambda: bool(web.html_calls), timeout=2000)
    release_old.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)

    assert len(web.html_calls) == 1
    assert "new" in web.html_calls[0]
    assert "old" not in web.html_calls[0]
    assert "mutated" not in web.html_calls[0]


def test_close_drops_late_text_layout(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.widgets.result_view_widget")
    from vibeocr.classic.recognition_settings import TextBlockOptions

    widget = module.ResultViewWidget()
    qtbot.addWidget(widget)
    web = _FakeWebView()
    monkeypatch.setattr(widget, "_ensure_web_view", lambda: web)
    started = threading.Event()
    release = threading.Event()

    def slow_layout(_blocks, _options, _cancel_event=None):
        started.set()
        release.wait(timeout=2)
        return "<div>late layout</div>"

    monkeypatch.setattr(module, "_build_text_layout_html", slow_layout)
    widget.display_text_layout(_layout_result("late"), TextBlockOptions())
    qtbot.waitUntil(started.is_set, timeout=1000)
    before = time.perf_counter()
    widget.close()
    assert (time.perf_counter() - before) * 1000 < 150
    release.set()
    qtbot.waitUntil(lambda: not widget._render_jobs, timeout=3000)
    assert web.html_calls == []
