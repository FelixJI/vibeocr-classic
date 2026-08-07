"""BatchFileListWidget 大批量路径与分帧表格更新测试。"""

from __future__ import annotations

from pathlib import Path

from tests.qt_responsiveness import assert_qt_event_loop_responsive


def test_thousands_of_files_are_deduplicated_and_rows_are_chunked(qapp, qtbot):
    from vibeocr.classic.widgets.batch_file_list_widget import BatchFileListWidget

    widget = BatchFileListWidget()
    qtbot.addWidget(widget)
    paths = [f"C:/images/f_{index}.png" for index in range(2500)]
    changed: list[list[dict]] = []
    widget.files_changed.connect(lambda files: changed.append(list(files)))

    widget.add_files([*paths, paths[0], "C:/images/./f_1.png"])
    assert widget.get_file_count() == 2500
    assert len(changed) == 1
    assert widget._pending_rows
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: bool(widget._pending_rows))
    qtbot.waitUntil(lambda: not widget._pending_rows, timeout=5000)

    assert widget._table.rowCount() == 2500
    assert len(changed) == 1
    assert changed[0][0]["path"] == paths[0]
    assert changed[0][-1]["path"] == paths[-1]


def test_document_pipeline_signal_precedes_chunked_rows(qapp, qtbot, monkeypatch):
    from vibeocr.classic.views.batch_recognition_tab import (
        BatchRecognitionTab,
        BatchRecognitionWorker,
    )

    tab = BatchRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "vibeocr.classic.utils.mime_types.is_document_file",
        lambda path: path.endswith(".pdf"),
    )
    monkeypatch.setattr(BatchRecognitionWorker, "start", lambda _self, *_args: None)
    tab._batch_backend = object()
    # GPU capability is now a tri-state value supplied by MainWindow's async
    # probe; tests must seed the resolved state instead of triggering a sync read.
    tab._preprocess_options.apply_gpu_gating(True)

    tab._file_list_widget.add_files(
        [f"C:/documents/document_{index}.pdf" for index in range(500)]
    )

    assert tab._file_list_widget._pending_rows
    assert tab._preprocess_options.get_current_pipeline().value == "MinerU"
    tab._on_start()
    assert tab._worker is not None
    assert tab._worker._preprocess_options.pipeline.value == "MinerU"


def test_status_and_selection_mapping_survive_chunked_population(qapp, qtbot):
    from vibeocr.classic.widgets.batch_file_list_widget import BatchFileListWidget

    widget = BatchFileListWidget()
    qtbot.addWidget(widget)
    paths = [f"C:/images/f_{index}.png" for index in range(1200)]
    selected: list[str] = []
    widget.selection_changed.connect(selected.append)
    widget.add_files(paths)
    widget.update_file_status(paths[-1], "failed", {"error": "bad"})
    qtbot.waitUntil(lambda: not widget._pending_rows, timeout=5000)

    assert widget._files[-1]["status"] == "failed"
    assert widget._table.item(1199, 0).text() == "[X]"
    widget._table.selectRow(1199)
    assert selected[-1] == paths[-1]
    assert widget.get_pending_count() == 1199

    widget._on_clear()
    assert widget.get_file_count() == 0
    assert not widget._path_keys
    assert widget._table.rowCount() == 0


def test_path_normalization_never_resolves_or_touches_filesystem(
    qapp, qtbot, monkeypatch
):
    from vibeocr.classic.widgets.batch_file_list_widget import BatchFileListWidget

    def forbidden_resolve(*_args, **_kwargs):
        raise AssertionError("Path.resolve must not be used for GUI path deduplication")

    monkeypatch.setattr(Path, "resolve", forbidden_resolve)
    widget = BatchFileListWidget()
    qtbot.addWidget(widget)
    widget.add_files(["network/share/../share/a.png", "network/share/a.png"])

    assert widget.get_file_count() == 1
    assert widget._files[0]["path"] == "network/share/../share/a.png"
