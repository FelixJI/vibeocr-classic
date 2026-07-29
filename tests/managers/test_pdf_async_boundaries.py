"""PDF GUI/IPC 异步边界的精确回归测试。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread

from tests.fakes.sync_supervisor_job_client import (
    FakeSyncSupervisorJobClient,
)
from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.backend.ipc.schemas import ModelDiff, PdfDocumentMirror, PdfPageInfoMirror
from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.backend.models.pdf_session import PdfSession
from vibeocr.classic.pyside.pdf_session_manager import PdfSessionManager


def _session(path: str = "C:/fake.pdf", pages: int = 2) -> PdfSession:
    return PdfSession(
        file_path=path,
        session_id="sid",
        pdf_document=PdfDocument(
            file_path=path,
            pages=[PdfPageInfo(page_index=index) for index in range(pages)],
        ),
    )


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager()
    mgr._client = MagicMock()
    session = _session()
    mgr._sessions = {session.file_path: session}
    mgr._active_path = session.file_path
    yield mgr
    # 测试可能把 MagicMock 塞进 *_worker 字段（直接调 slot 的用例）。这些 mock
    # 不是真 QThread，request_shutdown → _advance_shutdown_session_closes 会误判
    # 它们「已 finished」并 spawn 真实 PdfIpcCloseWorker 线程，导致进程退出时
    # access violation。先把 mock 引用清掉，再走正常 shutdown。
    for attr in (
        "_open_worker", "_mutate_worker", "_ocr_worker",
        "_preflight_worker", "_preview_worker", "_export_worker",
    ):
        worker = getattr(mgr, attr, None)
        if worker is not None and not hasattr(worker, "isFinished"):
            setattr(mgr, attr, None)
    mgr._draining_open_workers = {
        w for w in mgr._draining_open_workers if hasattr(w, "isFinished")
    }
    mgr._draining_preview_workers = {
        w for w in mgr._draining_preview_workers if hasattr(w, "isFinished")
    }
    mgr._control_workers = {
        w for w in mgr._control_workers if hasattr(w, "isFinished")
    }
    mgr.request_shutdown()
    assert mgr.drain(3000)
    # drain() proves native threads are finished, but workers and the manager
    # are released with deleteLater().  Flush DeferredDelete here so hundreds
    # of function-scoped managers do not accumulate stale Qt wrappers and
    # crash the next nested pytest-qt event loop on Windows.
    mgr.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_slow_mineru_preflight_keeps_event_loop_responsive(
    manager, qtbot, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()

    def slow_prepare(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return True, "ok"

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", slow_prepare)
    monkeypatch.setattr(manager, "_is_mineru_first_use", lambda _opts: True)
    manager._inference_client = object()

    assert manager.start_ocr([0], ocr_options=object()) is True
    qtbot.waitUntil(entered.is_set)
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: manager._preflight_worker is not None
        and manager._preflight_worker.isRunning(),
    )

    manager.cancel_ocr()
    release.set()
    qtbot.waitUntil(lambda: manager._preflight_worker is None)
    assert manager._ocr_state == "cancelled"
    manager._client.reset_cancel.assert_not_called()


def test_slow_backend_start_for_open_runs_off_gui(manager, qtbot):
    main_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    start_threads: list[int] = []

    def slow_start():
        start_threads.append(threading.get_ident())
        entered.set()
        release.wait(2)

    manager._client.start.side_effect = slow_start
    manager._client.open_session.side_effect = RuntimeError("stop after start")

    manager.open_sessions_async(["C:/slow-open.pdf"])
    qtbot.waitUntil(entered.is_set)
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: manager._open_worker is not None
        and manager._open_worker.isRunning(),
    )
    release.set()
    qtbot.waitUntil(lambda: manager._open_worker is None)

    assert start_threads and all(ident != main_thread for ident in start_threads)


def test_discarded_doc_opened_closes_orphan_session_in_background(manager, qtbot):
    main_thread = threading.get_ident()
    close_threads: list[int] = []

    def close_session(session_id):
        assert session_id == "orphan-sid"
        close_threads.append(threading.get_ident())

    manager._client.close_session.side_effect = close_session
    manager._open_generation = 8

    manager._on_doc_opened_guarded(
        "C:/orphan.pdf", "orphan-sid", object(), object(), 7
    )
    qtbot.waitUntil(lambda: not manager._close_workers)

    assert "C:/orphan.pdf" not in manager.session_paths
    assert close_threads and all(ident != main_thread for ident in close_threads)


def test_drain_observes_close_worker_added_while_open_worker_finishes(
    manager, qtbot
):
    """GUI poll 不能漏掉旧 open 结束边界新增的 orphan close。"""
    close_entered = threading.Event()
    release_close = threading.Event()

    def slow_close(_session_id):
        close_entered.set()
        release_close.wait(2)

    manager._client.close_session.side_effect = slow_close
    manager._sessions.clear()
    manager._active_path = None
    manager._open_generation = 2

    class LateOpenWorker:
        finished = False

        def isFinished(self):
            return self.finished

    late_open = LateOpenWorker()
    manager._draining_open_workers.add(late_open)
    try:
        manager.request_shutdown()
        assert manager.is_drained() is False

        late_open.finished = True
        manager._draining_open_workers.discard(late_open)
        manager._on_doc_opened_guarded(
            "C:/late.pdf", "late-sid", object(), object(), 1
        )
        qtbot.waitUntil(close_entered.is_set)
        assert manager._close_workers
    finally:
        manager._draining_open_workers.discard(late_open)
        release_close.set()
        for worker in list(manager._close_workers):
            worker.wait(1000)


def test_rapid_second_open_removes_and_closes_partially_loaded_first_session(
    manager, qtbot
):
    first_path = "C:/first.pdf"
    second_path = "C:/second.pdf"
    first_load_entered = threading.Event()
    release_first_load = threading.Event()

    def open_session(path):
        return SimpleNamespace(
            session_id="first-sid" if path == first_path else "second-sid",
            model=PdfDocumentMirror(
                file_path=path,
                pages=[PdfPageInfoMirror(page_index=0)],
            ),
        )

    def load_stream(session_id):
        if session_id == "first-sid":
            first_load_entered.set()
            release_first_load.wait(2)
        return iter(())

    manager._client.open_session.side_effect = open_session
    manager._client.load_stream.side_effect = load_stream

    manager.open_sessions_async([first_path])
    qtbot.waitUntil(first_load_entered.is_set)
    qtbot.waitUntil(lambda: first_path in manager.session_paths)

    manager.open_sessions_async([second_path])
    release_first_load.set()
    qtbot.waitUntil(
        lambda: manager._open_worker is None and not manager._draining_open_workers
    )

    assert first_path not in manager.session_paths
    assert any(
        call.args == ("first-sid",) for call in manager._client.close_session.call_args_list
    )


def test_shutdown_during_open_completion_closes_backend_session_exactly_once(
    manager, qtbot
):
    """取消必须与 open worker 的 incomplete ownership 转移保持原子。"""
    path = "C:/cancel-at-open-completion.pdf"
    load_called = threading.Event()
    allow_load_return = threading.Event()
    worker_before_final_pop = threading.Event()
    allow_final_pop = threading.Event()
    gui_thread_id = threading.get_ident()

    class FinalPopBarrierLock:
        """在 worker 最终 pop 前停住，但允许 GUI 读取 ownership 快照。"""

        def __init__(self):
            self._lock = threading.Lock()

        def __enter__(self):
            if threading.get_ident() != gui_thread_id:
                worker_before_final_pop.set()
                assert allow_final_pop.wait(2)
            self._lock.acquire()
            return self

        def __exit__(self, *_args):
            self._lock.release()

    manager._sessions.clear()
    manager._active_path = None
    manager._client.open_session.return_value = SimpleNamespace(
        session_id="race-sid",
        model=PdfDocumentMirror(
            file_path=path,
            pages=[PdfPageInfoMirror(page_index=0)],
        ),
    )

    def load_stream(_session_id):
        load_called.set()
        assert allow_load_return.wait(2)
        return iter(())

    manager._client.load_stream.side_effect = load_stream
    manager.open_sessions_async([path])
    qtbot.waitUntil(load_called.is_set)
    worker = manager._open_worker
    assert worker is not None
    worker._sessions_lock = FinalPopBarrierLock()

    allow_load_return.set()
    qtbot.waitUntil(worker_before_final_pop.is_set)
    manager.request_shutdown()
    allow_final_pop.set()
    qtbot.waitUntil(manager.is_drained, timeout=3000)

    close_calls = [
        call for call in manager._client.close_session.call_args_list
        if call.args == ("race-sid",)
    ]
    assert len(close_calls) == 1


def test_load_failure_after_doc_opened_removes_and_closes_partial_session(
    manager, qtbot
):
    path = "C:/broken-load.pdf"
    manager._client.open_session.return_value = SimpleNamespace(
        session_id="broken-sid",
        model=PdfDocumentMirror(
            file_path=path,
            pages=[PdfPageInfoMirror(page_index=0)],
        ),
    )
    manager._client.load_stream.side_effect = RuntimeError("stream failed")
    failures: list[tuple[str, str]] = []
    completed: list[str] = []
    manager.open_failed.connect(lambda p, error: failures.append((p, error)))
    manager.load_done.connect(completed.append)

    manager.open_sessions_async([path])
    qtbot.waitUntil(lambda: manager._open_worker is None)

    assert failures and failures[-1][0] == path
    assert path not in manager.session_paths
    assert path not in completed
    assert any(
        call.args == ("broken-sid",)
        for call in manager._client.close_session.call_args_list
    )


def test_export_result_keeps_worker_owned_until_native_finished(manager):
    worker = MagicMock()
    emitted: list[list[str]] = []
    manager.export_done.connect(emitted.append)
    manager._export_worker = worker

    manager._on_export_done(["C:/out.pdf"], worker)

    assert manager._export_worker is worker
    assert emitted == []

    manager._on_export_worker_finished(worker)

    assert manager._export_worker is None
    assert emitted == [["C:/out.pdf"]]
    worker.deleteLater.assert_called_once_with()


def test_unexpected_export_error_emits_terminal_after_native_finished(
    manager, qtbot, tmp_path
):
    """非 PdfBackendError 也必须释放写门并形成业务失败终态。"""
    session = manager.active_session
    assert session is not None
    session.pdf_document.is_modified = True
    manager._client.save.side_effect = MemoryError("export snapshot too large")
    failures: list[str] = []
    completed: list[list[str]] = []
    manager.export_failed.connect(failures.append)
    manager.export_done.connect(completed.append)

    manager.export_all_async(str(tmp_path))
    qtbot.waitUntil(lambda: manager._export_worker is None, timeout=3000)

    assert failures == ["export snapshot too large"]
    assert completed == []
    assert manager._pdf_write_busy() is False


def test_ocr_business_done_keeps_pdf_write_gate_until_thread_finished(
    manager, monkeypatch
):
    import vibeocr.classic.pyside.pdf_session_manager as manager_module

    worker = MagicMock()
    manager._ocr_worker = worker
    manager._ocr_running = True
    manager._ocr_state = "running"
    manager._task_generation = 7
    fake_mutate = MagicMock()
    monkeypatch.setattr(
        manager_module, "PdfIpcMutateWorker", lambda *_args, **_kwargs: fake_mutate
    )

    manager._on_ocr_all_done_signal("sid", 1, 0, task_id=7)

    assert manager._ocr_running is False
    assert manager._start_mutate("rotate", {"pages": [0], "angle": 90}) is False
    fake_mutate.start.assert_not_called()


def test_open_start_failure_reports_every_path_and_finishes(manager, qtbot):
    manager._client.start.side_effect = RuntimeError("backend unavailable")
    failures: list[tuple[str, str]] = []
    done: list[bool] = []
    manager.open_failed.connect(lambda path, error: failures.append((path, error)))
    manager.open_done.connect(lambda: done.append(True))

    manager.open_sessions_async(["C:/a.pdf", "C:/b.pdf"])
    qtbot.waitUntil(lambda: bool(done))

    assert [path for path, _error in failures] == ["C:/a.pdf", "C:/b.pdf"]
    assert all("backend unavailable" in error for _path, error in failures)
    qtbot.waitUntil(lambda: manager._open_worker is None)


def test_preflight_late_success_is_ignored_after_shutdown(manager, qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_prepare(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return True, "late"

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", slow_prepare)
    monkeypatch.setattr(manager, "_is_mineru_first_use", lambda _opts: True)
    manager._inference_client = object()
    run_ocr = MagicMock()
    monkeypatch.setattr(manager, "_run_ocr", run_ocr)

    assert manager.start_ocr([0], ocr_options=object())
    qtbot.waitUntil(entered.is_set)
    manager.request_shutdown()
    release.set()
    qtbot.waitUntil(lambda: manager._preflight_worker is None)

    run_ocr.assert_not_called()
    assert manager._pending_ocr_request is None


def test_preflight_cancel_defers_business_terminal_until_native_finished(manager):
    path = manager.active_session.file_path
    worker = MagicMock()
    worker.is_cancelled = True
    manager._preflight_worker = worker
    manager._preflight_generation = 11
    manager._pending_ocr_request = (path, [0], object(), object(), False)
    manager._ocr_running = True
    manager._ocr_state = "preflight"
    done: list[tuple[str, int, int]] = []
    manager.ocr_done.connect(lambda p, ok, fail: done.append((p, ok, fail)))

    manager.cancel_ocr()

    assert manager._ocr_running is True
    assert manager.is_ocr_running is True
    assert done == []

    manager._on_preflight_finished(worker, 11)

    assert manager._preflight_worker is None
    assert manager._ocr_running is False
    assert manager._ocr_state == "cancelled"
    assert done == [(path, 0, 0)]
    worker.deleteLater.assert_called_once_with()


def test_pdf_shutdown_request_creates_session_close_workers_on_gui_owner(
    manager, monkeypatch
):
    calls: list[tuple[str, QThread]] = []

    def record_close(session_id, *_args, **_kwargs):
        calls.append((session_id, QThread.currentThread()))
        manager._close_started_session_ids.add(session_id)

    monkeypatch.setattr(
        manager,
        "_start_close_worker",
        record_close,
    )

    manager.request_shutdown()

    assert calls == [("sid", manager.thread())]
    before = list(calls)
    assert manager.is_drained() is False
    assert manager.is_drained() is True
    assert calls == before


def test_quick_page_flip_discards_old_preview_and_detects_off_gui(manager, qtbot):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    main_thread = threading.get_ident()
    page_zero_entered = threading.Event()
    release_page_zero = threading.Event()
    call_threads: list[int] = []
    manager.active_session.pdf_document.pages[1].has_text_layer = True
    image = QImage(1, 1, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    valid_png = bytes(buffer.data())

    def render(_sid, page, *, dpi):
        assert dpi == 150
        call_threads.append(threading.get_ident())
        if page == 0:
            page_zero_entered.set()
            release_page_zero.wait(2)
        return valid_png

    def detect(_sid, page):
        call_threads.append(threading.get_ident())
        assert page == 1
        return SimpleNamespace(text_layers=[])

    manager._client.render_preview.side_effect = render
    manager._client.detect_text_layers.side_effect = detect
    ready: list[tuple[int, int, object]] = []
    manager.preview_ready.connect(
        lambda _path, page, generation, png: ready.append((page, generation, png))
    )

    first = manager.request_preview(0)
    qtbot.waitUntil(page_zero_entered.is_set)
    second = manager.request_preview(1)
    qtbot.waitUntil(lambda: any(item[0] == 1 for item in ready))
    release_page_zero.set()
    qtbot.waitUntil(lambda: not manager._draining_preview_workers)

    assert second > first
    assert [item[0] for item in ready] == [1]
    assert call_threads and all(ident != main_thread for ident in call_threads)


def test_block_edit_revision_and_reset_run_in_mutate_worker(manager, qtbot):
    main_thread = threading.get_ident()
    call_threads: list[int] = []

    def reset(_sid):
        call_threads.append(threading.get_ident())

    def update(*_args):
        call_threads.append(threading.get_ident())
        return SimpleNamespace(diff=ModelDiff(), extra=None)

    manager._client.reset_cancel.side_effect = reset
    manager._client.update_block_text.side_effect = update
    results: list[dict] = []
    manager.mutate_done.connect(lambda _path, result: results.append(result))

    assert manager.update_page_block_text_async(0, 0, "new")
    qtbot.waitUntil(lambda: manager._mutate_worker is None)

    assert results[-1]["op"] == "update_block_text"
    assert results[-1]["revision"] == 1
    assert manager._preview_generation >= results[-1]["revision"]
    assert call_threads and all(ident != main_thread for ident in call_threads)


def test_deskew_get_model_runs_in_worker_and_returns_diff(manager, qtbot):
    main_thread = threading.get_ident()
    get_model_threads: list[int] = []
    manager._sessions[manager._active_path] = _session(pages=1)
    manager._inference_client = FakeSyncSupervisorJobClient(
        lambda _index, _request: SimpleNamespace(
            raw_text="",
            text_blocks=[],
            preproc_angle=0,
        )
    )
    manager._client.render_preview.return_value = b"png"

    def get_model(_sid):
        get_model_threads.append(threading.get_ident())
        return PdfDocumentMirror(
            file_path="C:/fake.pdf", pages=[PdfPageInfoMirror(page_index=0)]
        )

    manager._client.get_model.side_effect = get_model

    assert manager.auto_deskew_async([0])
    qtbot.waitUntil(lambda: manager._mutate_worker is None)

    assert get_model_threads and all(
        ident != main_thread for ident in get_model_threads
    )


def test_close_session_is_async_and_does_not_block_gui(manager, qtbot):
    entered = threading.Event()
    release = threading.Event()

    def slow_close(_sid):
        entered.set()
        release.wait(2)

    manager._client.close_session.side_effect = slow_close
    path = manager.active_session.file_path

    assert manager.close_session_async(path)
    qtbot.waitUntil(entered.is_set)
    assert path not in manager.session_paths
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: bool(manager._close_workers),
    )

    release.set()
    qtbot.waitUntil(lambda: not manager._close_workers)


# =============================================================================
# Task 4 覆盖率补充：pdf_session_manager 纯 helper / 属性 / 谓词 / 信号路由槽
# 这些用例不依赖事件循环——manager fixture 提供活跃 session（session_id="sid"），
# 直接调用槽并用 signal.connect(list.append) 观察发射。
# =============================================================================


def test_active_session_and_session_paths_and_get_session(manager):
    """active_session/session_paths/get_session 基本属性 + set_inference_client。"""
    path = "C:/fake.pdf"
    assert manager.active_session is not None
    assert manager.active_session.session_id == "sid"
    assert manager.session_paths == [path]
    assert manager.get_session(path) is manager.active_session
    assert manager.get_session("C:/missing.pdf") is None

    sentinel = object()
    manager.set_inference_client(sentinel)
    assert manager._inference_client is sentinel


def test_active_session_none_when_no_active_path(manager):
    manager._active_path = None
    assert manager.active_session is None


def test_get_modified_sessions_filters_by_is_modified(manager):
    session = manager.active_session
    assert manager.get_modified_sessions() == []
    session.pdf_document.is_modified = True
    modified = manager.get_modified_sessions()
    assert modified == [(session.file_path, session)]


def test_is_deskew_running_and_is_mutate_running_and_state(manager):
    assert manager.is_deskew_running is False
    assert manager.is_mutate_running is False
    assert manager.mutate_state == "idle"

    worker = MagicMock()
    worker._op = "deskew"
    manager._mutate_worker = worker
    assert manager.is_deskew_running is True
    assert manager.is_mutate_running is True

    # 非摆正 worker 不计为 deskew
    worker._op = "rotate"
    assert manager.is_deskew_running is False
    assert manager.is_mutate_running is True

    # control worker（cancel）也算 mutate running
    manager._mutate_worker = None
    assert manager.is_mutate_running is False
    manager._control_workers.add(MagicMock())
    assert manager.is_mutate_running is True


def test_is_ocr_running_and_pdf_write_busy(manager):
    assert manager.is_ocr_running is False
    assert manager._pdf_write_busy() is False

    manager._ocr_running = True
    assert manager.is_ocr_running is True
    assert manager._pdf_write_busy() is True

    manager._ocr_running = False
    manager._ocr_worker = MagicMock()
    assert manager.is_ocr_running is True
    assert manager._pdf_write_busy() is True

    manager._ocr_worker = None
    manager._preflight_worker = MagicMock()
    assert manager.is_ocr_running is True

    manager._preflight_worker = None
    manager._mutate_worker = MagicMock()
    assert manager._pdf_write_busy() is True

    manager._mutate_worker = None
    manager._export_worker = MagicMock()
    assert manager._pdf_write_busy() is True


def test_settings_to_dict_branches(manager):
    """_settings_to_dict: None / to_dict / dict / 其它。"""
    assert manager._settings_to_dict(None) is None

    class WithToDict:
        def to_dict(self):
            return {"a": 1}

    assert manager._settings_to_dict(WithToDict()) == {"a": 1}
    assert manager._settings_to_dict({"x": 2}) == {"x": 2}
    assert manager._settings_to_dict(12345) is None  # 无 to_dict、非 dict


def test_ocr_result_to_dict_serializes_text_blocks(manager):
    from types import SimpleNamespace

    block = SimpleNamespace(
        text="hi",
        score=0.9,
        bbox=(1.0, 2.0, 3.0, 4.0),
        polygon=(1.0, 2.0, 3.0, 4.0),
        page_idx=0,
        is_manually_edited=True,
        label="text",
        order=3,
    )
    result = SimpleNamespace(text_blocks=[block], preproc_angle=90)
    data = manager._ocr_result_to_dict(result)

    assert data["preproc_angle"] == 90
    assert data["text_blocks"][0]["text"] == "hi"
    assert data["text_blocks"][0]["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert data["text_blocks"][0]["polygon"] == [1.0, 2.0, 3.0, 4.0]
    assert data["text_blocks"][0]["page_idx"] == 0
    assert data["text_blocks"][0]["is_manually_edited"] is True
    assert data["text_blocks"][0]["label"] == "text"
    assert data["text_blocks"][0]["order"] == 3

    # 空 bbox/polygon → None；preproc_angle 缺省 0
    block2 = SimpleNamespace(
        text="", score=0.0, bbox=None, polygon=None,
        page_idx=None, is_manually_edited=False, label=None, order=None,
    )
    data2 = manager._ocr_result_to_dict(
        SimpleNamespace(text_blocks=[block2], preproc_angle=None)
    )
    assert data2["text_blocks"][0]["bbox"] is None
    assert data2["text_blocks"][0]["polygon"] is None
    assert data2["preproc_angle"] == 0


def test_path_for_session_id_hit_and_miss(manager):
    assert manager._path_for_session_id("sid") == "C:/fake.pdf"
    assert manager._path_for_session_id("nope") is None


def test_get_ocr_batch_budget_default_and_override(manager):
    from vibeocr.backend.core.batch_budget import BatchBudget

    default = manager._get_ocr_batch_budget()
    assert isinstance(default, BatchBudget)
    # 默认 max_items 来自 _OCR_BATCH_SIZE=16
    assert default.max_items == manager._OCR_BATCH_SIZE

    override = BatchBudget(max_items=3, max_encoded_bytes=100, max_pixels=200)
    manager._ocr_batch_budget_override = override
    assert manager._get_ocr_batch_budget() is override


def test_get_pages_without_text_layer(manager):
    session = manager.active_session
    # 默认两页均无文字层
    assert manager.get_pages_without_text_layer(session.file_path) == [0, 1]

    # 给 page 0 设文字层，应只剩 page 1
    session.pdf_document.pages[0].has_text_layer = True
    assert manager.get_pages_without_text_layer(session.file_path) == [1]

    # 全部有文字层 → 空
    session.pdf_document.pages[1].has_text_layer = True
    assert manager.get_pages_without_text_layer(session.file_path) == []

    # 未知 session_id → []
    assert manager.get_pages_without_text_layer("unknown") == []


def test_is_current_open_and_preview_and_preflight_predicates(manager):
    worker_open = MagicMock()
    manager._open_worker = worker_open
    manager._open_generation = 0
    try:
        assert manager._is_current_open(worker_open, 0) is True

        manager._shutting_down = True
        assert manager._is_current_open(worker_open, 0) is False
        manager._shutting_down = False

        # 非当前 worker / 非当前 generation 均判 false
        other = MagicMock()
        assert manager._is_current_open(other, 0) is False
        assert manager._is_current_open(worker_open, 99) is False
    finally:
        # 还原 _open_worker，避免 fixture teardown 调 cancel_and_snapshot_sessions
        manager._open_worker = None

    # preview 谓词：worker 必须等于当前 _preview_worker
    manager._preview_worker = None
    preview_worker = MagicMock()
    gen = manager._preview_generation
    # 非当前 worker → False
    assert manager._is_current_preview(preview_worker, gen) is False
    manager._preview_worker = preview_worker
    assert manager._is_current_preview(preview_worker, gen) is True
    assert manager._is_current_preview(MagicMock(), gen) is False
    assert manager._is_current_preview(preview_worker, gen + 1) is False
    manager._preview_worker = None

    # preflight 谓词：仅匹配 worker+generation
    preflight_worker = MagicMock()
    manager._preflight_worker = preflight_worker
    manager._preflight_generation = 5
    assert manager._is_current_preflight(preflight_worker, 5) is True
    assert manager._is_current_preflight(preflight_worker, 4) is False
    assert manager._is_current_preflight(MagicMock(), 5) is False
    manager._preflight_worker = None


def test_is_current_mutate_allow_cancelling_branches(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 7
    manager._mutate_state = "running"

    # allow_cancelling=True（默认），running 状态 → True
    assert manager._is_current_mutate(worker, 7) is True
    # 非 running 状态 + allow_cancelling=False → False
    manager._mutate_state = "cancelling"
    assert manager._is_current_mutate(worker, 7, allow_cancelling=False) is False
    # allow_cancelling=True 仍 True
    assert manager._is_current_mutate(worker, 7, allow_cancelling=True) is True

    # 不匹配的 worker → False
    assert manager._is_current_mutate(MagicMock(), 7) is False
    # 不匹配的 task_id → False
    assert manager._is_current_mutate(worker, 99) is False
    # shutting down → False
    manager._shutting_down = True
    manager._mutate_state = "running"
    assert manager._is_current_mutate(worker, 7) is False


def test_on_load_progress_emits(manager):
    events: list[tuple[str, int, int]] = []
    manager.load_progress.connect(lambda p, c, t: events.append((p, c, t)))

    manager._on_load_progress("C:/fake.pdf", 2, 5)

    assert events == [("C:/fake.pdf", 2, 5)]


def test_on_open_failed_pops_partial_and_emits(manager):
    partial_path = "C:/partial.pdf"
    other_path = "C:/other.pdf"
    manager._sessions[partial_path] = _session(path=partial_path)
    manager._sessions[other_path] = _session(path=other_path)
    manager._active_path = partial_path

    removed: list[str] = []
    active_changes: list[str] = []
    failures: list[tuple[str, str]] = []
    manager.session_removed.connect(removed.append)
    manager.active_changed.connect(active_changes.append)
    manager.open_failed.connect(lambda p, e: failures.append((p, e)))

    manager._on_open_failed(partial_path, "boom")

    assert partial_path not in manager._sessions
    assert removed == [partial_path]
    # active 切换到剩余会话
    assert manager._active_path == other_path
    assert active_changes == [other_path]
    assert failures == [(partial_path, "boom")]


def test_on_open_failed_when_partial_was_last_clears_active(manager):
    partial_path = "C:/partial.pdf"
    manager._sessions.clear()
    manager._sessions[partial_path] = _session(path=partial_path)
    manager._active_path = partial_path

    active_changes: list[str] = []
    manager.active_changed.connect(active_changes.append)

    manager._on_open_failed(partial_path, "boom")

    assert manager._active_path is None
    # 无剩余会话时发空串
    assert active_changes == [""]


def test_on_open_all_done_emits_load_done_per_session_then_open_done(manager):
    manager._sessions.clear()
    manager._sessions["C:/a.pdf"] = _session(path="C:/a.pdf")
    manager._sessions["C:/b.pdf"] = _session(path="C:/b.pdf")

    loads: list[str] = []
    opens: list[bool] = []
    manager.load_done.connect(loads.append)
    manager.open_done.connect(lambda: opens.append(True))

    manager._on_open_all_done()

    assert loads == ["C:/a.pdf", "C:/b.pdf"]
    assert opens == [True]


def test_on_close_failed_emits_signal(manager):
    failures: list[tuple[str, str]] = []
    manager.close_failed.connect(lambda p, e: failures.append((p, e)))

    manager._on_close_failed("C:/fake.pdf", "network down")

    assert failures == [("C:/fake.pdf", "network down")]


def test_on_preview_failed_emits_when_current(manager):
    worker = MagicMock()
    manager._preview_worker = worker
    gen = manager._preview_generation

    failures: list[tuple[str, int, int, str]] = []
    manager.preview_failed.connect(
        lambda p, idx, g, e: failures.append((p, idx, g, e))
    )

    manager._on_preview_failed("sid", 3, gen, "render error", worker)

    assert failures == [("C:/fake.pdf", 3, gen, "render error")]


def test_on_preview_failed_ignored_when_stale(manager):
    """过期 generation 的失败信号被丢弃。"""
    worker = MagicMock()
    manager._preview_worker = worker

    failures: list = []
    manager.preview_failed.connect(
        lambda p, idx, g, e: failures.append((p, idx, g, e))
    )

    # 过期 generation
    manager._on_preview_failed("sid", 3, manager._preview_generation + 99, "e", worker)
    # 过期 worker
    manager._on_preview_failed("sid", 3, manager._preview_generation, "e", MagicMock())

    assert failures == []


def test_on_deskew_progress_and_page_done_signal_emit(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 1
    manager._mutate_state = "running"

    progress: list[tuple[str, int, int]] = []
    page_done: list[tuple[str, int, bool]] = []
    manager.deskew_progress.connect(lambda p, c, t: progress.append((p, c, t)))
    manager.deskew_page_done.connect(
        lambda p, idx, corrected: page_done.append((p, idx, corrected))
    )

    manager._on_deskew_progress_signal("sid", 2, 5, worker=worker, task_id=1)
    manager._on_deskew_page_done_signal("sid", 1, True, worker=worker, task_id=1)

    assert progress == [("C:/fake.pdf", 2, 5)]
    assert page_done == [("C:/fake.pdf", 1, True)]


def test_on_deskew_failed_signal_emits(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 1
    manager._mutate_state = "running"

    failures: list[tuple[str, str]] = []
    manager.deskew_failed.connect(lambda p, e: failures.append((p, e)))

    manager._on_deskew_failed_signal("sid", "bad", worker=worker, task_id=1)

    assert failures == [("C:/fake.pdf", "bad")]
    assert manager._mutate_terminal_received is True


def test_on_deskew_progress_ignored_for_stale_worker(manager):
    manager._mutate_worker = MagicMock()
    manager._mutate_task_id = 1

    progress: list = []
    manager.deskew_progress.connect(lambda *args: progress.append(args))

    # 非当前 worker 被丢弃
    manager._on_deskew_progress_signal("sid", 1, 2, worker=MagicMock(), task_id=1)
    # 不匹配的 task_id 被丢弃
    manager._on_deskew_progress_signal("sid", 1, 2, worker=manager._mutate_worker, task_id=99)
    # session_id 不匹配（无对应 file_path）被丢弃
    manager._on_deskew_progress_signal("nope", 1, 2, worker=manager._mutate_worker, task_id=1)

    assert progress == []


def test_on_mutate_progress_and_page_done_emit(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 4
    manager._mutate_state = "running"

    progress: list[tuple[str, int, int]] = []
    done: list[tuple[str, dict]] = []
    manager.mutate_progress.connect(lambda p, c, t: progress.append((p, c, t)))
    manager.mutate_done.connect(lambda p, result: done.append((p, result)))

    manager._on_mutate_progress("sid", 1, 3, worker=worker, task_id=4)
    manager._on_mutate_page_done("sid", 2, {"k": "v"}, worker=worker, task_id=4)

    assert progress == [("C:/fake.pdf", 1, 3)]
    assert done == [("C:/fake.pdf", {"page": 2, "payload": {"k": "v"}})]


def test_on_mutate_failed_emits(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 4
    manager._mutate_state = "running"

    failures: list[tuple[str, str]] = []
    manager.mutate_failed.connect(lambda p, e: failures.append((p, e)))

    manager._on_mutate_failed("sid", "oops", worker=worker, task_id=4)

    assert failures == [("C:/fake.pdf", "oops")]
    assert manager._mutate_terminal_received is True


def test_on_ocr_progress_signal_emits(manager):
    progress: list[tuple[str, int, int]] = []
    manager.ocr_progress.connect(lambda p, c, t: progress.append((p, c, t)))

    manager._on_ocr_progress_signal("sid", 3, 10)

    assert progress == [("C:/fake.pdf", 3, 10)]


def test_on_ocr_progress_signal_ignored_for_unknown_session(manager):
    progress: list = []
    manager.ocr_progress.connect(lambda *args: progress.append(args))

    manager._on_ocr_progress_signal("unknown-sid", 3, 10)

    assert progress == []


def test_on_ocr_failed_signal_emits_done_with_page_total(manager):
    manager._ocr_running = True
    manager._ocr_state = "running"

    done: list[tuple[str, int, int]] = []
    manager.ocr_done.connect(lambda p, s, f: done.append((p, s, f)))

    manager._on_ocr_failed_signal("sid", "runtime error")

    # 状态重置 + 失败计数 = 当前会话页数
    assert manager._ocr_running is False
    assert manager._ocr_state == "completed"
    assert done == [("C:/fake.pdf", 0, 2)]  # fixture 会话有 2 页


def test_on_ocr_failed_signal_cancelling_state(manager):
    manager._ocr_running = True
    manager._ocr_state = "cancelling"

    manager._on_ocr_failed_signal("sid", "e")
    assert manager._ocr_state == "cancelled"


def test_on_ocr_all_done_signal_ignores_stale_task_id(manager):
    manager._task_generation = 5
    manager._ocr_running = True

    done: list = []
    manager.ocr_done.connect(lambda *args: done.append(args))

    # 旧 task_id 被丢弃
    manager._on_ocr_all_done_signal("sid", 1, 0, task_id=3)

    assert manager._ocr_running is True
    assert done == []


def test_on_ocr_all_done_signal_emits_stats_and_done(manager):
    from vibeocr.backend.models.pdf_session import PdfSession

    manager._task_generation = 8
    manager._ocr_running = True
    manager._ocr_state = "running"
    session = manager.active_session
    assert isinstance(session, PdfSession)
    # 注入一些 stats
    session.add_ocr_stats(3, 1)

    stats_ready: list[tuple[str, int, int]] = []
    done: list[tuple[str, int, int]] = []
    manager.ocr_stats_ready.connect(lambda p, w, s: stats_ready.append((p, w, s)))
    manager.ocr_done.connect(lambda p, s, f: done.append((p, s, f)))

    manager._on_ocr_all_done_signal("sid", 5, 1, task_id=8)

    assert manager._ocr_running is False
    assert manager._ocr_state == "completed"
    assert stats_ready == [("C:/fake.pdf", 3, 1)]
    assert done == [("C:/fake.pdf", 5, 1)]


def test_on_ocr_all_done_signal_cancelling_state(manager):
    manager._task_generation = 8
    manager._ocr_state = "cancelling"
    manager._ocr_running = True

    manager._on_ocr_all_done_signal("sid", 0, 0, task_id=8)
    assert manager._ocr_state == "cancelled"


def test_on_ocr_worker_finished_clears_worker_and_state(manager):
    manager._ocr_worker = MagicMock()
    manager._ocr_state = "cancelling"

    manager._on_ocr_worker_finished()

    assert manager._ocr_worker is None
    assert manager._ocr_state == "cancelled"


def test_on_ocr_worker_finished_when_completed_state(manager):
    worker = MagicMock()
    manager._ocr_worker = worker
    manager._ocr_state = "completed"

    manager._on_ocr_worker_finished()

    assert manager._ocr_worker is None
    assert manager._ocr_state == "completed"
    worker.deleteLater.assert_called_once_with()


def test_on_preflight_progress_emits_status(manager):
    worker = MagicMock()
    manager._preflight_worker = worker
    manager._preflight_generation = 2
    manager._ocr_state = "preflight"

    statuses: list[str] = []
    manager.mineru_models_status.connect(statuses.append)

    manager._on_preflight_progress("download", "50%", worker, 2)

    assert statuses == ["[download] 50%"]


def test_on_preflight_progress_ignored_when_not_preflight_state(manager):
    worker = MagicMock()
    manager._preflight_worker = worker
    manager._preflight_generation = 2
    manager._ocr_state = "running"  # 非 preflight

    statuses: list = []
    manager.mineru_models_status.connect(statuses.append)

    manager._on_preflight_progress("download", "50%", worker, 2)

    assert statuses == []


def test_on_preflight_progress_ignored_when_shutting_down(manager):
    worker = MagicMock()
    manager._preflight_worker = worker
    manager._preflight_generation = 2
    manager._ocr_state = "preflight"
    manager._shutting_down = True

    statuses: list = []
    manager.mineru_models_status.connect(statuses.append)

    manager._on_preflight_progress("download", "50%", worker, 2)

    assert statuses == []


def test_on_preflight_completed_stores_result(manager):
    worker = MagicMock()
    worker.is_cancelled = False
    manager._preflight_worker = worker
    manager._preflight_generation = 3
    manager._ocr_state = "preflight"

    manager._on_preflight_completed(True, "ok", worker, 3)

    assert manager._preflight_result == (True, "ok")


def test_on_preflight_completed_ignored_when_cancelled(manager):
    worker = MagicMock()
    worker.is_cancelled = True
    manager._preflight_worker = worker
    manager._preflight_generation = 3
    manager._ocr_state = "preflight"

    manager._on_preflight_completed(True, "ok", worker, 3)

    assert manager._preflight_result is None


def test_on_preflight_completed_ignored_when_stale_generation(manager):
    worker = MagicMock()
    manager._preflight_worker = worker
    manager._preflight_generation = 3
    manager._ocr_state = "preflight"

    manager._on_preflight_completed(True, "ok", worker, 99)

    assert manager._preflight_result is None


def test_on_export_done_and_failed_store_pending(manager):
    worker = MagicMock()
    manager._export_worker = worker

    manager._on_export_done(["C:/a.pdf"], worker)
    assert manager._export_result_pending == ["C:/a.pdf"]

    manager._on_export_failed("boom", worker)
    assert manager._export_error_pending == "boom"


def test_on_export_done_ignored_for_foreign_worker(manager):
    manager._export_worker = MagicMock()
    foreign = MagicMock()

    manager._on_export_done(["C:/a.pdf"], foreign)
    assert manager._export_result_pending is None

    manager._on_export_failed("boom", foreign)
    assert manager._export_error_pending is None


def test_on_export_worker_finished_emits_export_done(manager):
    worker = MagicMock()
    manager._export_worker = worker
    manager._export_result_pending = ["C:/a.pdf"]

    done: list[list[str]] = []
    manager.export_done.connect(done.append)

    manager._on_export_worker_finished(worker)

    assert manager._export_worker is None
    assert done == [["C:/a.pdf"]]
    worker.deleteLater.assert_called_once_with()


def test_on_export_worker_finished_emits_failed_when_error(manager):
    worker = MagicMock()
    manager._export_worker = worker
    manager._export_error_pending = "disk full"

    failures: list[str] = []
    manager.export_failed.connect(failures.append)

    manager._on_export_worker_finished(worker)

    assert failures == ["disk full"]
    assert manager._export_error_pending is None


def test_on_export_worker_finished_ignored_for_foreign_worker(manager):
    manager._export_worker = MagicMock()
    foreign = MagicMock()

    failures: list = []
    manager.export_failed.connect(failures.append)

    manager._on_export_worker_finished(foreign)

    assert failures == []
    foreign.deleteLater.assert_called_once_with()


def test_on_export_worker_finished_silent_when_shutting_down(manager):
    worker = MagicMock()
    manager._export_worker = worker
    manager._export_result_pending = ["C:/a.pdf"]
    manager._export_error_pending = "boom"
    manager._shutting_down = True

    done: list = []
    failures: list = []
    manager.export_done.connect(done.append)
    manager.export_failed.connect(failures.append)

    manager._on_export_worker_finished(worker)

    assert done == []
    assert failures == []


def test_switch_session_branches(manager):
    """switch_session: 缺会话/相同会话/busy/正常 4 分支。"""
    path = manager.active_session.file_path
    # 相同会话 → True，但不切换
    assert manager.switch_session(path) is True

    # 缺会话 → False
    assert manager.switch_session("C:/missing.pdf") is False

    # 新增第二个会话，切换
    second = _session(path="C:/second.pdf")
    manager._sessions[second.file_path] = second
    changes: list[str] = []
    manager.active_changed.connect(changes.append)
    assert manager.switch_session(second.file_path) is True
    assert manager._active_path == second.file_path
    assert changes == [second.file_path]

    # busy 时拒绝切换
    manager._mutate_worker = MagicMock()
    assert manager.switch_session(path) is False


def test_on_mutate_all_done_applies_diff_and_signals(manager):
    """_on_mutate_all_done: 成功应用 diff、转发 save/delete_layer 专用信号。"""
    from vibeocr.backend.ipc.schemas import (
        ModelDiff,
        PdfDocumentMirror,
        PdfPageInfoMirror,
    )

    worker = MagicMock()
    worker._op = "save"
    worker._params = {"path": "/out.pdf", "revision": 0}
    manager._mutate_worker = worker
    manager._mutate_task_id = 9
    manager._task_generation = 9
    manager._mutate_state = "running"

    # 构造一个 full_model diff，使 apply_diff 标记某些页失效
    mirror = PdfDocumentMirror(
        file_path="C:/fake.pdf",
        pages=[
            PdfPageInfoMirror(page_index=0, rotation=90),
            PdfPageInfoMirror(page_index=1),
        ],
    )
    diff = ModelDiff(full_model=mirror)

    invalidated: list[list] = []
    saves: list[str] = []
    mutate_done: list[tuple[str, dict]] = []
    manager.thumbnails_invalidated.connect(invalidated.append)
    manager.save_done.connect(saves.append)
    manager.mutate_done.connect(lambda p, r: mutate_done.append((p, r)))

    manager._on_mutate_all_done("sid", diff, {"path": "/out.pdf"}, task_id=9, worker=worker)

    assert manager._mutate_terminal_received is True
    assert saves == ["C:/fake.pdf"]
    # diff 含 rotation 变化，应触发缩略图失效
    assert invalidated and 0 in invalidated[0]
    assert mutate_done
    assert mutate_done[0][1]["op"] == "save"


def test_on_mutate_all_done_delete_layer_branch(manager):
    from vibeocr.backend.ipc.schemas import (
        ModelDiff,
        PdfDocumentMirror,
        PdfPageInfoMirror,
    )

    worker = MagicMock()
    worker._op = "delete_text_layers"
    worker._params = {"pages": [0]}
    manager._mutate_worker = worker
    manager._mutate_task_id = 9
    manager._task_generation = 9
    manager._mutate_state = "running"

    mirror = PdfDocumentMirror(
        file_path="C:/fake.pdf",
        pages=[PdfPageInfoMirror(page_index=0), PdfPageInfoMirror(page_index=1)],
    )
    diff = ModelDiff(full_model=mirror)

    delete_layer_done: list[tuple[str, list]] = []
    manager.delete_layer_done.connect(lambda p, pages: delete_layer_done.append((p, pages)))

    manager._on_mutate_all_done(
        "sid", diff, {"residual_pages": [1]}, task_id=9, worker=worker
    )

    assert delete_layer_done == [("C:/fake.pdf", [1])]


def test_on_mutate_all_done_drops_stale_task_id(manager, caplog):
    manager._task_generation = 5

    manager._on_mutate_all_done("sid", None, None, task_id=2, worker=MagicMock())

    assert manager._mutate_terminal_received is False


def test_on_mutate_worker_finished_releases_and_emits_state(manager):
    worker = MagicMock()
    worker._op = "rotate"
    manager._mutate_worker = worker
    manager._mutate_task_id = 4
    manager._mutate_state = "running"
    manager._mutate_path = "C:/fake.pdf"
    manager._mutate_op = "rotate"

    changes: list[tuple[str, str, str]] = []
    manager.mutate_state_changed.connect(
        lambda p, op, state: changes.append((p, op, state))
    )

    manager._on_mutate_worker_finished(worker, 4)

    assert manager._mutate_worker is None
    assert manager._mutate_state == "completed"
    assert changes == [("C:/fake.pdf", "rotate", "completed")]
    worker.deleteLater.assert_called_once_with()


def test_on_mutate_worker_finished_cancelling_state(manager):
    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 4
    manager._mutate_state = "cancelling"

    manager._on_mutate_worker_finished(worker, 4)
    assert manager._mutate_state == "cancelled"


def test_on_mutate_worker_finished_ignored_for_foreign_worker(manager):
    manager._mutate_worker = MagicMock()
    foreign = MagicMock()

    manager._on_mutate_worker_finished(foreign, 1)
    # foreign worker 被 deleteLater，当前 worker 不变
    foreign.deleteLater.assert_called_once_with()


def test_on_deskew_all_done_applies_diff_and_emits(manager):
    from vibeocr.backend.ipc.schemas import (
        ModelDiff,
        PdfDocumentMirror,
        PdfPageInfoMirror,
    )

    worker = MagicMock()
    manager._mutate_worker = worker
    manager._mutate_task_id = 1
    manager._mutate_state = "running"

    mirror = PdfDocumentMirror(
        file_path="C:/fake.pdf",
        pages=[PdfPageInfoMirror(page_index=0, rotation=45)],
        # 第二页不变，避免被误判
    )
    diff = ModelDiff(full_model=mirror)

    invalidated: list[list] = []
    deskew_done: list[tuple[str, object]] = []
    manager.thumbnails_invalidated.connect(invalidated.append)
    manager.deskew_done.connect(lambda p, s: deskew_done.append((p, s)))

    summary = {"corrected": 1, "skipped": 1, "corrected_pages": [0], "_diff": diff}

    manager._on_deskew_all_done("sid", summary, worker=worker, task_id=1)

    # _diff 应从 summary 中 pop 出来
    assert "_diff" not in summary
    assert invalidated and 0 in invalidated[0]
    assert deskew_done and deskew_done[0][0] == "C:/fake.pdf"


def test_on_deskew_all_done_ignored_when_stale(manager):
    manager._mutate_worker = MagicMock()
    manager._mutate_task_id = 1

    deskew_done: list = []
    manager.deskew_done.connect(lambda *args: deskew_done.append(args))

    manager._on_deskew_all_done("sid", {}, worker=MagicMock(), task_id=99)
    assert deskew_done == []


# =============================================================================
# Task 4 第二批：覆盖更多中等 ROI 方法（_async 包装、_on_doc_opened、
# _on_page_loaded、_apply_page_loaded、_on_preview_completed、rerender、
# request_preview 早退、open_session 同步、export_all_modified、各 cancel 路径）
# =============================================================================


def test_rerender_thumbnails_async_emits(manager):
    invalidated: list[list[int]] = []
    manager.thumbnails_invalidated.connect(invalidated.append)

    manager.rerender_thumbnails_async([0, 2])
    assert invalidated == [[0, 2]]

    # 空列表不发信号
    manager.rerender_thumbnails_async([])
    assert invalidated == [[0, 2]]


def test_save_async_and_delete_text_layers_and_rotate_wrappers_start_mutate(
    manager, monkeypatch
):
    """save_async / delete_text_layers_async / rotate_pages_async 等都委托 _start_mutate。"""
    started: list[tuple[str, dict]] = []

    def fake_start(op, params):
        started.append((op, params))
        return True

    monkeypatch.setattr(manager, "_start_mutate", fake_start)

    assert manager.save_async("/out.pdf", pdf_settings=None) is True
    manager.delete_text_layers_async([0, 1])  # 返回 None
    manager.rotate_pages_async([0], 90)  # 返回 None
    manager.delete_pages_async([1])  # 返回 None
    manager.insert_blank_async(0)  # 返回 None
    manager.insert_from_async("/src.pdf", 2)  # 返回 None
    manager.move_page_async(0, 1)  # 返回 None
    manager.reorder_async([1, 0])  # 返回 None

    ops = [entry[0] for entry in started]
    params = started
    assert ops == [
        "save", "delete_text_layers", "rotate", "delete_pages",
        "insert_blank", "insert_from", "move_page", "reorder",
    ]
    # 抽查几个参数
    assert params[0][1] == {"path": "/out.pdf", "pdf_settings": None}
    assert params[2][1] == {"pages": [0], "angle": 90}
    assert params[4][1] == {"after_index": 0, "width": 612.0, "height": 792.0}
    assert params[6][1] == {"from_index": 0, "to_index": 1}


def test_save_async_passes_pdf_settings_to_dict(manager, monkeypatch):
    """save_async 走 _settings_to_dict，None 时不报错。"""
    captured: list[dict] = []

    def fake_start(op, params):
        captured.append(params)
        return True

    monkeypatch.setattr(manager, "_start_mutate", fake_start)
    manager.save_async()
    assert captured[-1] == {"path": None, "pdf_settings": None}


def test_close_session_async_delegates_to_close_session(manager, monkeypatch):
    monkeypatch.setattr(manager, "close_session", lambda p: True)
    assert manager.close_session_async("C:/fake.pdf") is True


def test_update_page_block_text_async_increments_revision(manager, monkeypatch):
    started: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        manager,
        "_start_mutate",
        lambda op, params: started.append((op, params)) or True,
    )
    initial_revision = manager._edit_revision

    assert manager.update_page_block_text_async(0, 1, "new text")

    op, params = started[0]
    assert op == "update_block_text"
    assert params == {
        "page": 0,
        "block_index": 1,
        "new_text": "new text",
        "revision": initial_revision + 1,
    }
    # _preview_generation 也跟着推进
    assert manager._preview_generation >= initial_revision + 1


def test_apply_page_loaded_updates_session_model(manager):
    """_apply_page_loaded: dict mirror → 更新对应页 PageInfo。"""
    from vibeocr.backend.ipc.schemas import PdfPageInfoMirror

    session = manager.active_session
    page_mirror = PdfPageInfoMirror(
        page_index=0, rotation=90, has_text_layer=True
    ).model_dump(mode="json")

    assert 0 not in session.loaded_pages
    manager._apply_page_loaded(session, 0, page_mirror)

    assert 0 in session.loaded_pages
    assert session.pdf_document.pages[0].rotation == 90
    assert session.pdf_document.pages[0].has_text_layer is True


def test_apply_page_loaded_ignores_non_dict(manager):
    session = manager.active_session
    manager._apply_page_loaded(session, 0, "not a dict")
    # 无变化
    assert 0 not in session.loaded_pages


def test_apply_page_loaded_ignores_out_of_range_index(manager):
    from vibeocr.backend.ipc.schemas import PdfPageInfoMirror

    session = manager.active_session
    page_mirror = PdfPageInfoMirror(page_index=99).model_dump(mode="json")

    manager._apply_page_loaded(session, 99, page_mirror)
    assert 99 not in session.loaded_pages


def test_on_page_loaded_emits_signals(manager):
    from vibeocr.backend.ipc.schemas import PdfPageInfoMirror

    page_mirror = PdfPageInfoMirror(page_index=0, has_text_layer=True).model_dump(
        mode="json"
    )
    loaded: list[tuple[str, int]] = []
    progress: list[tuple[str, int, int]] = []
    manager.page_loaded.connect(lambda p, idx: loaded.append((p, idx)))
    manager.load_progress.connect(lambda p, c, t: progress.append((p, c, t)))

    manager._on_page_loaded("C:/fake.pdf", 0, page_mirror)

    assert loaded == [("C:/fake.pdf", 0)]
    assert progress == [("C:/fake.pdf", 1, 2)]  # 1 loaded, 2 total


def test_on_page_loaded_ignored_when_session_missing(manager):
    loaded: list = []
    manager.page_loaded.connect(lambda *args: loaded.append(args))

    manager._on_page_loaded("C:/missing.pdf", 0, {"page_index": 0})
    assert loaded == []


def test_on_doc_opened_creates_session_and_sets_active(manager):
    """_on_doc_opened: 创建占位 session + emit session_added/active_changed。"""
    from vibeocr.backend.ipc.schemas import PdfDocumentMirror, PdfPageInfoMirror

    # 清掉活跃会话，使新会话成为首个 active
    manager._sessions.clear()
    manager._active_path = None

    full_model = PdfDocumentMirror(
        file_path="C:/new.pdf",
        pages=[PdfPageInfoMirror(page_index=0), PdfPageInfoMirror(page_index=1)],
    )
    added: list[str] = []
    active_changes: list[str] = []
    manager.session_added.connect(added.append)
    manager.active_changed.connect(active_changes.append)

    manager._on_doc_opened("C:/new.pdf", "new-sid", full_model)

    assert "C:/new.pdf" in manager._sessions
    session = manager._sessions["C:/new.pdf"]
    assert session.session_id == "new-sid"
    assert session.pdf_document.page_count == 2
    assert added == ["C:/new.pdf"]
    # 无活跃会话时新会话变 active
    assert manager._active_path == "C:/new.pdf"
    assert active_changes == ["C:/new.pdf"]


def test_on_doc_opened_does_not_change_active_when_one_exists(manager):
    from vibeocr.backend.ipc.schemas import PdfDocumentMirror, PdfPageInfoMirror

    full_model = PdfDocumentMirror(
        file_path="C:/new.pdf", pages=[PdfPageInfoMirror(page_index=0)]
    )
    active_before = manager._active_path

    manager._on_doc_opened("C:/new.pdf", "new-sid", full_model)

    # 已有活跃会话，不切换 active
    assert manager._active_path == active_before


def test_on_preview_completed_updates_text_layers_and_emits(manager):
    """_on_preview_completed: layers 非 None → 更新 page.text_layers。"""
    from vibeocr.backend.ipc.schemas import TextLayerInfoMirror

    worker = MagicMock()
    manager._preview_worker = worker
    gen = manager._preview_generation

    ready: list[tuple[str, int, int, object]] = []
    manager.preview_ready.connect(
        lambda p, idx, g, png: ready.append((p, idx, g, png))
    )

    png_bytes = b"\x89PNG"
    layers = [TextLayerInfoMirror(
        index=0, text_preview="hello", char_count=5, bbox=(0.0, 0.0, 1.0, 1.0), color_id=1
    )]

    manager._on_preview_completed("sid", 0, gen, png_bytes, layers, worker)

    assert ready == [("C:/fake.pdf", 0, gen, png_bytes)]
    page = manager.active_session.pdf_document.pages[0]
    assert page.has_text_layer is True
    assert len(page.text_layers) == 1


def test_on_preview_completed_without_layers_still_emits(manager):
    worker = MagicMock()
    manager._preview_worker = worker
    gen = manager._preview_generation

    ready: list = []
    manager.preview_ready.connect(lambda *args: ready.append(args))

    manager._on_preview_completed("sid", 0, gen, b"png", None, worker)

    assert len(ready) == 1


def test_on_preview_completed_ignored_when_stale(manager):
    worker = MagicMock()
    manager._preview_worker = worker

    ready: list = []
    manager.preview_ready.connect(lambda *args: ready.append(args))

    # 过期 generation + 过期 worker
    manager._on_preview_completed("sid", 0, 99, b"png", None, worker)
    manager._on_preview_completed("sid", 0, manager._preview_generation, b"png", None, MagicMock())

    assert ready == []


def test_on_preview_completed_skips_unknown_session(manager):
    worker = MagicMock()
    manager._preview_worker = worker
    gen = manager._preview_generation

    ready: list = []
    manager.preview_ready.connect(lambda *args: ready.append(args))

    manager._on_preview_completed("unknown-sid", 0, gen, b"png", None, worker)
    assert ready == []


def test_export_all_modified_skips_unmodified_and_handles_cancel(manager, tmp_path):
    """export_all_modified: 只导出 modified，cancel_check 停止后续。"""
    session = manager.active_session
    session.pdf_document.is_modified = True

    # 第二个 modified 会话
    second_path = "C:/second.pdf"
    second = _session(path=second_path)
    second.pdf_document.is_modified = True
    manager._sessions[second_path] = second

    exported: list[str] = []
    manager._client.save.side_effect = lambda sid, path, pdf_settings=None: exported.append(path)

    result = manager.export_all_modified(str(tmp_path))

    assert len(result) == 2
    assert manager._client.save.call_count == 2
    # 无 modified 会话时不调 save


def test_export_all_modified_skips_unmodified(manager, tmp_path):
    # 默认会话未 modified → 不导出
    result = manager.export_all_modified(str(tmp_path))
    assert result == []
    manager._client.save.assert_not_called()


def test_export_all_modified_cancel_stops_early(manager, tmp_path):
    session = manager.active_session
    session.pdf_document.is_modified = True

    calls = {"count": 0}

    def slow_save(sid, path, pdf_settings=None):
        calls["count"] += 1

    manager._client.save.side_effect = slow_save

    # cancel_check 在第一次后返回 True
    flag = {"on": False}

    def cancel_check():
        return flag["on"]

    # 注入第二个 modified 让 cancel 有意义
    second_path = "C:/second.pdf"
    second = _session(path=second_path)
    second.pdf_document.is_modified = True
    manager._sessions[second_path] = second

    def save_then_cancel(sid, path, pdf_settings=None):
        flag["on"] = True  # 第一个导出后取消后续

    manager._client.save.side_effect = save_then_cancel

    result = manager.export_all_modified(str(tmp_path), cancel_check=cancel_check)

    # 只导出第一个，第二个被 cancel 跳过
    assert len(result) == 1


def test_export_all_modified_handles_pdf_backend_error(manager, tmp_path):
    from vibeocr.classic.pdf_client import PdfBackendError

    session = manager.active_session
    session.pdf_document.is_modified = True
    manager._client.save.side_effect = PdfBackendError("export failed")

    # 单文件后端错误不中断，返回空 exported
    result = manager.export_all_modified(str(tmp_path))
    assert result == []


def test_export_all_modified_collides_filename(manager, tmp_path):
    """目标已存在时文件名加后缀。"""
    session = manager.active_session
    session.pdf_document.is_modified = True
    # 预先创建同名文件，触发 _N 后缀
    (tmp_path / "fake.pdf").write_text("x", encoding="utf-8")

    manager._client.save.side_effect = lambda sid, path, pdf_settings=None: None
    result = manager.export_all_modified(str(tmp_path))
    assert len(result) == 1
    assert "fake_1.pdf" in result[0]


def test_ensure_client_returns_cached(manager):
    """_ensure_client: 已设 _client 时直接返回，不查 adapter。"""
    sentinel = object()
    manager._client = sentinel
    assert manager._ensure_client() is sentinel


def test_ensure_inference_client_returns_cached(manager):
    sentinel = object()
    manager._inference_client = sentinel
    assert manager._ensure_inference_client() is sentinel


def test_close_session_removes_and_starts_close_worker(manager, monkeypatch):
    """close_session: 从 _sessions 移除 + 发 session_removed + 启动 close worker。"""
    path = manager.active_session.file_path
    started: list[str] = []

    def fake_close_worker(session_id, file_path=""):
        started.append((session_id, file_path))
        return

    monkeypatch.setattr(manager, "_start_close_worker", fake_close_worker)

    removed: list[str] = []
    active_changes: list[str] = []
    manager.session_removed.connect(removed.append)
    manager.active_changed.connect(active_changes.append)

    assert manager.close_session(path) is True
    assert path not in manager._sessions
    assert removed == [path]
    # 单一会话移除后 active 变空
    assert manager._active_path is None
    assert active_changes == [""]
    assert started == [("sid", path)]


def test_close_session_rejects_when_busy(manager):
    path = manager.active_session.file_path
    manager._mutate_worker = MagicMock()
    assert manager.close_session(path) is False
    assert path in manager._sessions


def test_close_session_unknown_path_returns_false(manager):
    assert manager.close_session("C:/missing.pdf") is False


def test_cancel_deskew_delegates_to_cancel_mutate(manager, monkeypatch):
    called = {"x": False}

    def fake_cancel():
        called["x"] = True

    monkeypatch.setattr(manager, "_cancel_mutate_worker", fake_cancel)
    manager.cancel_deskew()
    assert called["x"] is True


def test_cancel_ocr_delegates_to_cancel_ocr_internal(manager, monkeypatch):
    called = {"x": False}
    monkeypatch.setattr(manager, "_cancel_ocr", lambda: called.__setitem__("x", True))
    manager.cancel_ocr()
    assert called["x"] is True


def test_cancel_preview_no_op_when_no_worker(manager):
    manager._preview_worker = None
    # 不抛异常
    manager.cancel_preview()


def test_cancel_preview_cancels_worker(manager):
    worker = MagicMock()
    manager._preview_worker = worker
    gen_before = manager._preview_generation

    manager.cancel_preview()

    assert manager._preview_worker is None
    worker.cancel.assert_called_once_with()
    assert manager._preview_generation > gen_before
    assert worker in manager._draining_preview_workers


def test_release_control_worker_discards(manager):
    worker = MagicMock()
    manager._control_workers.add(worker)

    manager._release_control_worker(worker)

    assert worker not in manager._control_workers
    worker.deleteLater.assert_called_once_with()


def test_request_backend_cancel_async_starts_cancel_worker(manager, monkeypatch):
    created: list = []

    class FakeCancelWorker:
        def __init__(self, client, session_id, parent=None):
            self.client = client
            self.session_id = session_id
            self.finished = MagicMock()
            created.append(self)

        def start(self):
            pass

        def isFinished(self):
            return True

    monkeypatch.setattr(
        "vibeocr.classic.pyside.pdf_session_manager.PdfIpcCancelWorker", FakeCancelWorker
    )

    manager._request_backend_cancel_async("sid")

    assert len(created) == 1
    assert created[0].session_id == "sid"
    assert created[0] in manager._control_workers


def test_start_mutate_rejects_when_shutting_down(manager):
    manager._shutting_down = True
    assert manager._start_mutate("rotate", {"pages": [0], "angle": 90}) is False


def test_start_mutate_rejects_when_no_active_session(manager):
    manager._active_path = "C:/missing.pdf"  # 不在 _sessions
    assert manager._start_mutate("rotate", {"pages": [0], "angle": 90}) is False


def test_start_mutate_rejects_when_busy(manager):
    manager._mutate_worker = MagicMock()
    assert manager._start_mutate("rotate", {"pages": [0], "angle": 90}) is False


def test_auto_deskew_async_rejects_when_no_session(manager):
    manager._active_path = "C:/missing.pdf"
    assert manager.auto_deskew_async([0]) is False


def test_auto_deskew_async_rejects_when_not_ocr_ready(manager, monkeypatch):
    # is_ocr_ready 是 property；monkeypatch 自动还原
    monkeypatch.setattr(
        type(manager), "is_ocr_ready", property(lambda self: False)
    )
    assert manager.auto_deskew_async([0]) is False


def test_start_ocr_rejects_when_no_active_session(manager):
    manager._active_path = "C:/missing.pdf"
    assert manager.start_ocr([0]) is False


def test_start_ocr_rejects_when_busy(manager):
    # is_ocr_ready 依赖 _inference_client；置非空使谓词 True，从而走到 busy 检查
    manager._inference_client = object()
    manager._mutate_worker = MagicMock()
    assert manager.start_ocr([0]) is False


def test_open_sessions_async_rejects_when_shutting_down(manager):
    manager._shutting_down = True
    progress: list = []
    manager.open_done.connect(lambda: progress.append(True))
    manager.open_sessions_async(["C:/new.pdf"])
    assert progress == []


def test_open_sessions_async_rejects_when_busy(manager):
    manager._mutate_worker = MagicMock()
    progress: list = []
    manager.open_done.connect(lambda: progress.append(True))
    manager.open_sessions_async(["C:/new.pdf"])
    assert progress == []


def test_open_sessions_async_switches_when_all_existing(manager):
    """所有路径都已打开 → switch + open_done。"""
    existing = manager.active_session.file_path
    done: list[bool] = []
    manager.open_done.connect(lambda: done.append(True))

    manager.open_sessions_async([existing])
    assert done == [True]


def test_export_all_async_rejects_when_busy(manager):
    manager._mutate_worker = MagicMock()
    done: list = []
    manager.export_done.connect(lambda *args: done.append(args))
    manager.export_all_async("/out")
    assert done == []


def test_export_all_async_emits_empty_when_no_modified(manager):
    done: list = []
    manager.export_done.connect(done.append)
    manager.export_all_async("/out")
    assert done == [[]]


# =============================================================================
# _on_preflight_finished 各分支（cancelled / 无 request / 失败 / 成功切走 / 成功启动）
# =============================================================================


def _setup_preflight(manager):
    """构造一个匹配当前 preflight generation 的 worker + 状态。"""
    worker = MagicMock()
    worker.is_cancelled = False
    manager._preflight_worker = worker
    manager._preflight_generation = 11
    manager._ocr_running = True
    manager._ocr_state = "preflight"
    manager._pending_ocr_request = (
        "C:/fake.pdf", [0], object(), object(), False
    )
    manager._preflight_result = (True, "ok")
    manager._preflight_cancel_path = None
    return worker


def test_on_preflight_finished_stale_generation_deleteLater(manager):
    worker = _setup_preflight(manager)
    # 过期 generation → deleteLater 返回
    manager._on_preflight_finished(worker, 99)
    worker.deleteLater.assert_called_once_with()
    assert manager._preflight_worker is worker  # 未清理（非当前）


def test_on_preflight_finished_worker_cancelled(manager):
    worker = _setup_preflight(manager)
    worker.is_cancelled = True
    manager._preflight_cancel_path = "C:/fake.pdf"

    done: list[tuple[str, int, int]] = []
    manager.ocr_done.connect(lambda p, s, f: done.append((p, s, f)))

    manager._on_preflight_finished(worker, 11)

    assert manager._ocr_running is False
    assert manager._ocr_state == "cancelled"
    assert manager._preflight_worker is None
    assert done == [("C:/fake.pdf", 0, 0)]


def test_on_preflight_finished_cancelled_with_path_but_shutting_down(manager):
    worker = _setup_preflight(manager)
    worker.is_cancelled = True
    manager._preflight_cancel_path = "C:/fake.pdf"
    manager._shutting_down = True

    done: list = []
    manager.ocr_done.connect(lambda *args: done.append(args))

    manager._on_preflight_finished(worker, 11)
    # shutting_down 时不发 ocr_done
    assert done == []
    assert manager._ocr_state == "cancelled"


def test_on_preflight_finished_cancelled_no_path(manager):
    worker = _setup_preflight(manager)
    worker.is_cancelled = True
    manager._preflight_cancel_path = None  # 无 cancel_path

    done: list = []
    manager.ocr_done.connect(lambda *args: done.append(args))

    manager._on_preflight_finished(worker, 11)
    assert done == []  # 无 path 不发


def test_on_preflight_finished_no_request(manager):
    worker = _setup_preflight(manager)
    manager._pending_ocr_request = None

    manager._on_preflight_finished(worker, 11)
    assert manager._ocr_running is False
    assert manager._ocr_state == "cancelled"


def test_on_preflight_finished_failed_result(manager):
    worker = _setup_preflight(manager)
    manager._preflight_result = (False, "download error")

    done: list[tuple[str, int, int]] = []
    statuses: list[str] = []
    manager.ocr_done.connect(lambda p, s, f: done.append((p, s, f)))
    manager.mineru_models_status.connect(statuses.append)

    manager._on_preflight_finished(worker, 11)

    assert manager._ocr_state == "completed"
    assert done == [("C:/fake.pdf", 0, 1)]
    assert any("download error" in s for s in statuses)


def test_on_preflight_finished_none_result(manager):
    worker = _setup_preflight(manager)
    manager._preflight_result = None

    statuses: list[str] = []
    done: list = []
    manager.mineru_models_status.connect(statuses.append)
    manager.ocr_done.connect(lambda *args: done.append(args))

    manager._on_preflight_finished(worker, 11)
    assert done == [("C:/fake.pdf", 0, 1)]
    assert any("未返回结果" in s for s in statuses)


def test_on_preflight_finished_success_active_changed(manager):
    """成功但 active_path 已切走 → cancelled + ocr_done(0,0)。"""
    worker = _setup_preflight(manager)
    # 把 request 的 path 设成与当前 active 不同
    manager._pending_ocr_request = (
        "C:/other.pdf", [0], object(), object(), False
    )

    done: list[tuple[str, int, int]] = []
    manager.ocr_done.connect(lambda p, s, f: done.append((p, s, f)))

    manager._on_preflight_finished(worker, 11)
    assert manager._ocr_state == "cancelled"
    assert done == [("C:/other.pdf", 0, 0)]


def test_on_preflight_finished_success_starts_ocr(manager, monkeypatch):
    """成功且 active 匹配 → 调 start_ocr。"""
    worker = _setup_preflight(manager)
    monkeypatch.setattr(manager, "start_ocr", lambda *args, **kw: True)

    statuses: list[str] = []
    manager.mineru_models_status.connect(statuses.append)

    manager._on_preflight_finished(worker, 11)

    assert any("就绪" in s for s in statuses)
    assert manager._ocr_running is False


def test_on_preflight_finished_success_start_ocr_fails(manager, monkeypatch):
    """start_ocr 返回 False → cancelled + ocr_done(0,0)。"""
    worker = _setup_preflight(manager)
    monkeypatch.setattr(manager, "start_ocr", lambda *args, **kw: False)

    done: list = []
    manager.ocr_done.connect(lambda *args: done.append(args))

    manager._on_preflight_finished(worker, 11)
    assert manager._ocr_state == "cancelled"
    assert done == [("C:/fake.pdf", 0, 0)]


def test_is_mineru_first_use_none_options(manager):
    """ocr_options 为 None → False。"""
    assert manager._is_mineru_first_use(None) is False


# =============================================================================
# _run_deskew 早退分支（session 不匹配 / 空页列表）
# =============================================================================


def test_run_deskew_returns_when_session_missing(manager):
    """_run_deskew: session 不匹配 → 直接 return（不调 client）。"""
    runner = MagicMock()
    manager._run_deskew(runner, "unknown-sid", [0])
    runner.all_done.emit.assert_not_called()


def test_run_deskew_empty_pages_emits_all_done(manager):
    """_run_deskew: 空页列表 → emit all_done 汇总（corrected=0）。"""
    runner = MagicMock()
    session = manager.active_session

    manager._run_deskew(runner, session.session_id, [])

    runner.all_done.emit.assert_called_once()
    sid, summary = runner.all_done.emit.call_args.args
    assert sid == session.session_id
    assert summary == {"corrected": 0, "skipped": 0, "corrected_pages": []}


def test_run_deskew_happy_path_rotates_and_emits(manager, monkeypatch):
    """_run_deskew 全流程：渲染→识别(角度)→旋转→get_model→all_done。"""
    from types import SimpleNamespace

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    # _render_pool.map 返回 2 张有效 PNG
    runner._render_pool.map.return_value = iter([b"png0", b"png1"])

    # recognize 返回两个结果：page0 角度 90（需旋转），page1 角度 0（跳过）
    results = [
        SimpleNamespace(preproc_angle=90),
        SimpleNamespace(preproc_angle=0),
    ]
    monkeypatch.setattr(manager, "_recognize_images_via_job", lambda *a, **k: results)

    from vibeocr.backend.ipc.schemas import PdfDocumentMirror
    manager._client.get_model.return_value = PdfDocumentMirror(
        file_path="C:/fake.pdf", pages=[]
    )

    manager._deskew_corrected = []
    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_deskew(runner, session.session_id, [0, 1])

    # page0 角度 90 → correction=270 → rotate 被调，corrected
    manager._client.rotate.assert_called_once_with(session.session_id, [0], 270)
    # page1 角度 0 → 不旋转
    assert manager._client.rotate.call_count == 1
    # page_done：page0 corrected=True，page1 corrected=False
    assert (session.session_id, 0, True) in page_done_emits
    assert (session.session_id, 1, False) in page_done_emits
    # all_done 携带 corrected=1
    runner.all_done.emit.assert_called_once()
    summary = runner.all_done.emit.call_args.args[1]
    assert summary["corrected"] == 1
    assert summary["skipped"] == 1
    assert 0 in summary["corrected_pages"]


def test_run_deskew_render_failure_marks_page_failed(manager, monkeypatch):
    """渲染返回 None → 该页 page_failed，不识别，page_done corrected=False。"""
    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    # 渲染失败（None）
    runner._render_pool.map.return_value = iter([None])

    recognized: list = []
    monkeypatch.setattr(
        manager, "_recognize_images_via_job", lambda *a, **k: recognized.extend(a) or []
    )
    from vibeocr.backend.ipc.schemas import PdfDocumentMirror
    manager._client.get_model.return_value = PdfDocumentMirror(
        file_path="C:/fake.pdf", pages=[]
    )
    manager._deskew_corrected = []

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_deskew(runner, session.session_id, [0])

    # 渲染失败 → 不识别（recognize 未被调），page_done corrected=False
    assert recognized == []
    manager._client.rotate.assert_not_called()
    assert (session.session_id, 0, False) in page_done_emits


def test_run_deskew_recognize_failure_marks_all_failed(manager, monkeypatch):
    """_recognize_images_via_job 抛异常 → 该批所有有效页 page_failed。"""
    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._render_pool.map.return_value = iter([b"png"])

    def boom(*a, **k):
        raise RuntimeError("recognize failed")

    monkeypatch.setattr(manager, "_recognize_images_via_job", boom)
    from vibeocr.backend.ipc.schemas import PdfDocumentMirror
    manager._client.get_model.return_value = PdfDocumentMirror(
        file_path="C:/fake.pdf", pages=[]
    )
    manager._deskew_corrected = []

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_deskew(runner, session.session_id, [0])

    # 识别失败 → 不旋转，page_done corrected=False
    manager._client.rotate.assert_not_called()
    assert (session.session_id, 0, False) in page_done_emits


def test_run_deskew_rotate_failure_emits_not_corrected(manager, monkeypatch):
    """rotate 抛异常 → page_done corrected=False（不进 corrected_pages）。"""
    from types import SimpleNamespace

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._render_pool.map.return_value = iter([b"png"])
    monkeypatch.setattr(
        manager,
        "_recognize_images_via_job",
        lambda *a, **k: [SimpleNamespace(preproc_angle=90)],
    )
    manager._client.rotate.side_effect = RuntimeError("rotate broke")
    from vibeocr.backend.ipc.schemas import PdfDocumentMirror
    manager._client.get_model.return_value = PdfDocumentMirror(
        file_path="C:/fake.pdf", pages=[]
    )
    manager._deskew_corrected = []

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_deskew(runner, session.session_id, [0])

    assert (session.session_id, 0, False) in page_done_emits
    # rotate 失败 → 不进 corrected_pages
    summary = runner.all_done.emit.call_args.args[1]
    assert summary["corrected"] == 0


def test_run_deskew_get_model_failure_still_emits(manager, monkeypatch):
    """末尾 get_model 失败 → diff=None，仍发 all_done。"""
    from types import SimpleNamespace

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._render_pool.map.return_value = iter([b"png"])
    monkeypatch.setattr(
        manager,
        "_recognize_images_via_job",
        lambda *a, **k: [SimpleNamespace(preproc_angle=0)],
    )
    manager._client.get_model.side_effect = RuntimeError("model fetch failed")
    manager._deskew_corrected = []

    manager._run_deskew(runner, session.session_id, [0])

    runner.all_done.emit.assert_called_once()
    summary = runner.all_done.emit.call_args.args[1]
    # diff 被弹出存为 _diff
    assert summary["_diff"] is None


def test_run_deskew_cancelled_skips_remaining(manager, monkeypatch):
    """runner._cancelled=True → 循环 break，不调 get_model（diff=None）。"""
    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = True
    runner._render_pool.map.return_value = iter([b"png"])

    manager._deskew_corrected = []

    manager._run_deskew(runner, session.session_id, [0])

    # 取消时 get_model 不被调
    manager._client.get_model.assert_not_called()
    runner.all_done.emit.assert_called_once()
    summary = runner.all_done.emit.call_args.args[1]
    assert summary["_diff"] is None


# =============================================================================
# _run_ocr 早退 + happy path（覆盖主循环渲染/识别/写层/统计/终态）
# =============================================================================


def test_run_ocr_returns_when_session_missing(manager):
    """_run_ocr: session 不匹配 → 直接 return。"""
    runner = MagicMock()
    manager._run_ocr(runner, "unknown-sid", [0], None, {}, False)
    runner.progress.emit.assert_not_called()
    runner.all_done.emit.assert_not_called()


def test_run_ocr_happy_path_writes_layer_and_emits(manager, monkeypatch):
    """_run_ocr 全流程：渲染→识别(有 text_blocks)→写层(saved)→page_done/all_done。

    覆盖主循环、写层成功、sidecar mark_pages_saved/mark_completed、终态 all_done。
    """
    from types import SimpleNamespace

    from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._task_id = 1
    # 渲染返回 1 张 PNG
    runner._render_pool.map.return_value = iter([b"\x89PNG fake"])

    # 识别返回带 text_blocks 的结果（触发写层）
    block = TextBlock(text="hello", score=0.9, bbox=(0.0, 0.0, 1.0, 1.0))
    ocr_result = OCRResult(raw_text="hello", text_blocks=[block], preproc_angle=0)
    monkeypatch.setattr(
        manager, "_recognize_images_via_job", lambda *a, **k: [ocr_result]
    )

    # 写层返回 saved=True（触发 sidecar mark_pages_saved + mark_completed）
    manager._client.add_text_layer_batch.return_value = SimpleNamespace(
        diff=None, extra={"saved": True}
    )

    # sidecar 文件系统操作 → monkeypatch 成 no-op
    monkeypatch.setattr("vibeocr.classic.pyside.pdf_session_manager.ocr_sidecar.mark_pages_saved", lambda *a, **k: None)
    monkeypatch.setattr("vibeocr.classic.pyside.pdf_session_manager.ocr_sidecar.mark_completed", lambda *a, **k: None)

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_ocr(runner, session.session_id, [0], None, {}, False)

    # 写层被调
    manager._client.add_text_layer_batch.assert_called_once()
    # page_done 携带 ocr_result（写层成功）
    assert any(call[2] is ocr_result for call in page_done_emits)
    # all_done 携带 success=1 fail=0 + task_id
    runner.all_done.emit.assert_called_once()
    args = runner.all_done.emit.call_args.args
    assert args[0] == session.session_id
    assert args[1] == 1  # success
    assert args[2] == 0  # fail
    assert args[3] == 1  # task_id
    # 文档标记为未修改（写层成功 + finalized）
    assert session.pdf_document.is_modified is False


def test_run_ocr_empty_result_page_counted_as_fail(manager, monkeypatch):
    """识别结果无 text_blocks → 不写层，page_done=None，stats 记 skipped。

    注：空结果页只更新 ocr_stats(skipped)，不计入 all_done 的 success/fail 计数。
    """
    from vibeocr.backend.models.ocr_result import OCRResult

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._task_id = 1
    runner._render_pool.map.return_value = iter([b"png"])

    # 无 text_blocks 的空结果
    monkeypatch.setattr(
        manager, "_recognize_images_via_job", lambda *a, **k: [OCRResult(raw_text="")]
    )

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_ocr(runner, session.session_id, [0], None, {}, False)

    # 无 text_blocks → 不写层
    manager._client.add_text_layer_batch.assert_not_called()
    # page_done 携带 None（空页）
    assert (session.session_id, 0, None) in page_done_emits
    # all_done: 空结果页不计 success/fail → (0, 0)
    args = runner.all_done.emit.call_args.args
    assert (args[1], args[2]) == (0, 0)
    # 但 stats 记录了 skipped
    assert session.ocr_stats["skipped"] == 1


def test_run_ocr_render_failure_marks_page_failed(manager, monkeypatch):
    """渲染失败（None）→ 该页 page_failed，不识别，fail+1，page_done=None。"""
    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._task_id = 1
    runner._render_pool.map.return_value = iter([None])

    recognized: list = []
    monkeypatch.setattr(
        manager, "_recognize_images_via_job", lambda *a, **k: recognized.extend(a) or []
    )

    page_done_emits: list = []
    runner.page_done.emit.side_effect = lambda *a: page_done_emits.append(a)

    manager._run_ocr(runner, session.session_id, [0], None, {}, False)

    assert recognized == []  # 渲染失败不识别
    manager._client.add_text_layer_batch.assert_not_called()
    assert (session.session_id, 0, None) in page_done_emits
    # 渲染失败计入 fail
    args = runner.all_done.emit.call_args.args
    assert (args[1], args[2]) == (0, 1)


def test_run_ocr_write_layer_failure_triggers_final_save(manager, monkeypatch):
    """写层失败(saved=False) + 有 success → 末尾全量 save。"""
    from types import SimpleNamespace

    from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = False
    runner._task_id = 1
    runner._render_pool.map.return_value = iter([b"png"])

    block = TextBlock(text="hi", score=0.9, bbox=(0.0, 0.0, 1.0, 1.0))
    monkeypatch.setattr(
        manager, "_recognize_images_via_job", lambda *a, **k: [OCRResult(text_blocks=[block])]
    )
    # 写层返回 saved=False（触发末尾 save）
    manager._client.add_text_layer_batch.return_value = SimpleNamespace(
        extra={"saved": False}
    )
    monkeypatch.setattr("vibeocr.classic.pyside.pdf_session_manager.ocr_sidecar.mark_pages_saved", lambda *a, **k: None)
    monkeypatch.setattr("vibeocr.classic.pyside.pdf_session_manager.ocr_sidecar.refresh_baseline", lambda *a, **k: None)
    monkeypatch.setattr("vibeocr.classic.pyside.pdf_session_manager.ocr_sidecar.mark_completed", lambda *a, **k: None)

    manager._run_ocr(runner, session.session_id, [0], None, {}, False)

    # 末尾 save 被调（rewrite_text_layers=False）
    manager._client.save.assert_called_once()
    args = manager._client.save.call_args.args
    assert args[0] == session.session_id


def test_run_ocr_cancelled_breaks_loop(manager, monkeypatch):
    """runner._cancelled=True → 循环不进，仍发 all_done(0,0)。"""
    session = manager.active_session
    runner = MagicMock()
    runner._cancelled = True
    runner._task_id = 1

    manager._run_ocr(runner, session.session_id, [0], None, {}, False)

    # 取消时不渲染/识别/写层
    runner._render_pool.map.assert_not_called()
    manager._client.add_text_layer_batch.assert_not_called()
    runner.all_done.emit.assert_called_once()
    args = runner.all_done.emit.call_args.args
    assert (args[1], args[2]) == (0, 0)
