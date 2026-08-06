"""The PySide batch tab submits one logical job and never owns microbatches."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from vibeocr.classic.recognition_settings import OCROptions
from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab


def test_loaded_batch_is_submitted_once_with_all_inputs(qtbot) -> None:
    tab = BatchRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    tab._supervisor_generation = 1
    tab._run_state = tab.STATE_RUNNING
    calls: list[tuple[list, object]] = []

    class Adapter:
        def submit_recognition(self, uploads, **kwargs):
            calls.append((uploads, kwargs["pipeline"]))
            return 1

    loaded = [
        ({"path": "C:/inputs/a.png"}, b"a"),
        ({"path": "C:/inputs/b.png"}, b"b"),
    ]
    tab._submit_loaded_supervisor_inputs(1, Adapter(), OCROptions(), (loaded, []))

    assert len(calls) == 1
    assert [upload[0] for upload in calls[0][0]] == ["a.png", "b.png"]
    assert calls[0][1].pipeline_id == "OCR"


def test_submit_recognition_sync_exception_does_not_freeze_start(qtbot) -> None:
    """adapter.submit_recognition 同步抛异常时，应走失败路径复位状态，
    而非让 _run_state 卡在 STATE_RUNNING、Start 按钮永久禁用。"""
    tab = BatchRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    tab._supervisor_generation = 1
    tab._run_state = tab.STATE_RUNNING

    failed: list[tuple[int, str]] = []
    tab._fail_supervisor_submission = lambda gen, err: failed.append((gen, err))

    class Adapter:
        def submit_recognition(self, uploads, **kwargs):
            raise RuntimeError("submit boom")

    loaded = [({"path": "C:/inputs/a.png"}, b"a")]
    tab._submit_loaded_supervisor_inputs(1, Adapter(), OCROptions(), (loaded, []))

    # 应捕获异常并走统一失败路径，而非逃出 slot
    assert failed and failed[0][0] == 1
    assert "submit boom" in failed[0][1]


def test_unknown_result_payload_type_fails_item_and_finishes_batch(qtbot) -> None:
    tab = BatchRecognitionTab(backend=object())
    qtbot.addWidget(tab)
    tab._supervisor_job_id = "job-1"
    tab._shutting_down = False
    tab._supervisor_files = [{"path": "C:/inputs/a.png"}]
    tab._supervisor_results = {}
    tab._result_snapshots = {}
    tab._supervisor_adapter = MagicMock()
    tab._file_list_widget.update_file_status = MagicMock()
    tab._finish_supervisor_batch = MagicMock()

    tab._on_supervisor_result(
        "job-1",
        [
            {
                "error_code": None,
                "payload_type": "future-ocr.v2",
                "payload": {"raw_text": "future"},
            }
        ],
    )

    assert "协议不兼容" in tab._supervisor_results["C:/inputs/a.png"]["error"]
    tab._file_list_widget.update_file_status.assert_called_once()
    tab._finish_supervisor_batch.assert_called_once_with()


def test_batch_tab_contains_no_private_http_transport() -> None:
    source = inspect.getsource(
        __import__(
            "vibeocr.classic.views.batch_recognition_tab",
            fromlist=["BatchRecognitionTab"],
        )
    )
    assert "httpx" not in source
    assert "_base_url" not in source
    assert "_token" not in source
