"""批量识别标签页的真实 QThread 生命周期回归测试。"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from vibeocr.classic.views.batch_recognition_tab import (
    _ACTIVE_BATCH_WORKERS,
    BatchRecognitionTab,
    BatchRecognitionWorker,
)


class _BlockingBatchService:
    def __init__(self, *, release_on_cancel: bool = True) -> None:
        self.release_on_cancel = release_on_cancel
        self.started = threading.Event()
        self.release = threading.Event()
        self.second_started = threading.Event()
        self.second_release = threading.Event()
        self.calls = 0
        self.cancel_calls = 0
        self.fail_first = False

    def recognize_batch(self, images, options):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("first batch failed")
        if self.calls == 1:
            self.started.set()
            assert self.release.wait(5), "test service was not released"
        else:
            self.second_started.set()
            assert self.second_release.wait(5), "second test batch was not released"
        return [MagicMock(text="ok") for _ in images]

    def batch_cancel(self) -> None:
        self.cancel_calls += 1
        if self.release_on_cancel:
            self.release.set()
            self.second_release.set()

    def prepare_restart(self) -> None:
        self.started.clear()
        self.release.clear()
        self.second_started.clear()
        self.second_release.clear()
        self.calls = 0


def _make_files(tmp_path, count: int) -> list[str]:
    paths = []
    for index in range(count):
        path = tmp_path / f"batch_{index}.png"
        path.write_bytes(b"fake image")
        paths.append(str(path))
    return paths


def _make_tab(
    qtbot, monkeypatch, service, paths: list[str], *, register: bool = True
) -> BatchRecognitionTab:
    tab = BatchRecognitionTab(backend=MagicMock())
    if register:
        qtbot.addWidget(tab)
    tab._batch_backend = service
    tab._file_list_widget.add_files(paths)
    return tab


def test_cancel_waits_for_real_qthread_finish_before_restart(
    qtbot, monkeypatch, tmp_path
):
    service = _BlockingBatchService(release_on_cancel=True)
    tab = _make_tab(qtbot, monkeypatch, service, _make_files(tmp_path, 1))

    tab._on_start()
    qtbot.waitUntil(service.started.is_set, timeout=2000)
    first_worker = tab._worker
    assert first_worker is not None and first_worker.isRunning()

    tab._on_cancel()
    assert tab._worker is first_worker
    assert tab._run_state == tab.STATE_CANCELLING
    assert not tab._start_btn.isEnabled()
    assert not tab._cancel_btn.isEnabled()

    # cancelling 窗口内再次开始必须被拒绝，不能覆盖运行中引用。
    tab._on_start()
    assert tab._worker is first_worker

    qtbot.waitUntil(lambda: tab._worker is None, timeout=3000)
    assert tab._last_terminal_status == BatchRecognitionWorker.STATUS_CANCELLED
    assert "已取消" in tab._progress_label.text()
    assert "1/1 完成" not in tab._progress_label.text()
    assert tab._start_btn.isEnabled()

    # 原线程 finished 并释放后，待处理文件可以安全重启。
    service.prepare_restart()
    tab._on_start()
    qtbot.waitUntil(service.started.is_set, timeout=2000)
    second_worker = tab._worker
    assert second_worker is not None and second_worker is not first_worker
    service.release.set()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=3000)
    assert tab._last_terminal_status == BatchRecognitionWorker.STATUS_COMPLETED
    assert tab._progress_label.text() == "1/1 完成"


def test_batch_error_keeps_ui_running_and_continues_next_batch(
    qtbot, monkeypatch, tmp_path
):
    service = _BlockingBatchService(release_on_cancel=False)
    service.fail_first = True
    tab = _make_tab(qtbot, monkeypatch, service, _make_files(tmp_path, 17))

    tab._on_start()
    qtbot.waitUntil(service.second_started.is_set, timeout=3000)

    # 第一批 error 已经发出，但第二批仍运行，UI 不得复位或允许重入。
    assert tab._worker is not None and tab._worker.isRunning()
    assert tab._run_state == tab.STATE_RUNNING
    assert not tab._start_btn.isEnabled()
    assert tab._cancel_btn.isEnabled()

    service.second_release.set()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=3000)
    assert tab._last_terminal_status == BatchRecognitionWorker.STATUS_PARTIAL_FAILED
    assert "16 个失败" in tab._progress_label.text()
    assert tab._start_btn.isEnabled()


def test_shutdown_is_bounded_retains_worker_and_ignores_late_signals(
    qtbot, monkeypatch, tmp_path
):
    service = _BlockingBatchService(release_on_cancel=False)
    tab = _make_tab(qtbot, monkeypatch, service, _make_files(tmp_path, 1))

    tab._on_start()
    qtbot.waitUntil(service.started.is_set, timeout=2000)
    worker = tab._worker
    assert worker is not None

    assert tab.shutdown(timeout_ms=10) is False
    assert tab._worker is worker
    assert tab._run_state == tab.STATE_CANCELLING
    assert not tab._start_btn.isEnabled()

    # 关闭开始后，排队中的迟到信号不得再改 UI。
    label_before = tab._progress_label.text()
    worker.progress.emit(1, 1, "完成")
    qtbot.wait(20)
    assert tab._progress_label.text() == label_before

    service.release.set()
    assert tab.drain(timeout_ms=2000) is True
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)
    assert tab._run_state == tab.STATE_SHUTDOWN


def test_drain_from_non_gui_thread_only_waits(qtbot, monkeypatch, tmp_path):
    service = _BlockingBatchService(release_on_cancel=False)
    tab = _make_tab(qtbot, monkeypatch, service, _make_files(tmp_path, 1))
    tab._on_start()
    qtbot.waitUntil(service.started.is_set, timeout=2000)
    tab.request_shutdown()

    gui_ident = threading.get_ident()
    ui_calls: list[int] = []
    original_set_enabled = tab._start_btn.setEnabled

    def tracked_set_enabled(enabled):
        ui_calls.append(threading.get_ident())
        original_set_enabled(enabled)

    monkeypatch.setattr(tab._start_btn, "setEnabled", tracked_set_enabled)
    result: list[bool] = []
    drain_thread = threading.Thread(target=lambda: result.append(tab.drain(2000)))
    drain_thread.start()
    service.release.set()
    drain_thread.join(3)

    assert result == [True]
    assert not ui_calls
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)
    assert threading.get_ident() == gui_ident


def test_timeout_then_widget_destruction_keeps_worker_alive(
    qtbot, monkeypatch, tmp_path
):
    import shiboken6

    service = _BlockingBatchService(release_on_cancel=False)
    tab = _make_tab(
        qtbot,
        monkeypatch,
        service,
        _make_files(tmp_path, 1),
        register=False,
    )
    tab._on_start()
    qtbot.waitUntil(service.started.is_set, timeout=2000)
    worker = tab._worker
    assert worker is not None

    assert tab.shutdown(timeout_ms=10) is False
    assert worker in _ACTIVE_BATCH_WORKERS
    shiboken6.delete(tab)
    assert not shiboken6.isValid(tab)
    assert worker in _ACTIVE_BATCH_WORKERS

    service.release.set()
    qtbot.waitUntil(lambda: worker not in _ACTIVE_BATCH_WORKERS, timeout=3000)


def test_stale_worker_signals_cannot_overwrite_current_run(qtbot, monkeypatch):
    tab = _make_tab(qtbot, monkeypatch, MagicMock(), [])
    options = tab._preprocess_options.get_options()
    old_worker = BatchRecognitionWorker(MagicMock(), [], options)
    current_worker = BatchRecognitionWorker(MagicMock(), [], options)
    old_worker.progress.connect(tab._on_progress)
    old_worker.terminal.connect(tab._on_terminal)
    tab._worker = current_worker
    tab._run_state = tab.STATE_RUNNING
    tab._run_total = 3
    tab._progress_label.setText("current")

    old_worker.progress.emit(3, 3, "完成")
    old_worker.terminal.emit(BatchRecognitionWorker.STATUS_COMPLETED, {})
    qtbot.wait(20)

    assert tab._progress_label.text() == "current"
    assert tab._last_terminal_status is None


def test_cold_backend_cancel_does_not_emit_error(qtbot, tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class ColdService:
        def recognize_batch(self, _images, _options):
            entered.set()
            release.wait(timeout=2)
            raise RuntimeError("backend stopped during cancellation")

        def batch_cancel(self):
            return None

    image = tmp_path / "cold.png"
    image.write_bytes(b"not-an-image")
    worker = BatchRecognitionWorker(
        ColdService(), [{"path": str(image)}], MagicMock()
    )
    errors: list[str] = []
    terminals: list[str] = []
    worker.error.connect(errors.append)
    worker.terminal.connect(lambda status, _results: terminals.append(status))

    worker.start()
    qtbot.waitUntil(entered.is_set, timeout=1000)
    worker.cancel()
    release.set()
    qtbot.waitUntil(lambda: worker not in _ACTIVE_BATCH_WORKERS, timeout=2000)

    assert errors == []
    assert terminals == [BatchRecognitionWorker.STATUS_CANCELLED]


def test_standalone_close_event_requests_shutdown(qtbot, monkeypatch):
    tab = _make_tab(qtbot, monkeypatch, MagicMock(), [])
    request_shutdown = MagicMock(wraps=tab.request_shutdown)
    monkeypatch.setattr(tab, "request_shutdown", request_shutdown)

    tab.close()
    qtbot.waitUntil(lambda: request_shutdown.call_count == 1, timeout=1000)

    assert tab._shutting_down is True
    assert tab._preview_widget._closing is True
