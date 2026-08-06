"""Tests for PdfSessionManager(进程化版本)。

新架构:manager 通过 SyncPdfSupervisorClient (supervisor HTTP v2)调用
supervisor 拥有的 PDF 后端子进程,不持 fitz.Document。

注意:这些测试直接注入旧 PdfBackendClient 指向真实 PDF 后端子进程,
作为 supervisor 端到端测试的临时替代(supervisor 进程启动较重)。
标记为 slow,CI 可选跳过。完整迁移后应重写为真实 supervisor 端到端测试。
"""

from __future__ import annotations

import time

import fitz
import pytest

from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

# These are slow integration tests (spawn a real PDF backend child). They are
# skipped by default (addopts = "-m 'not slow'"); run with `-m slow`.
pytestmark = pytest.mark.slow


def _create_test_pdf(path, num_pages=2):
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def manager(qapp):
    # Inject the legacy PdfBackendClient directly so these slow integration
    # tests keep exercising the real PDF backend child without requiring the
    # full supervisor process. Production resolves the transport lazily from
    # the supervisor adapter; this injection is test-only.
    from vibeocr.backend.services.pdf_backend_client import PdfBackendClient

    mgr = PdfSessionManager(parent=qapp, client=PdfBackendClient.instance())
    yield mgr
    mgr.shutdown()


@pytest.fixture
def test_pdf_a(tmp_path):
    return _create_test_pdf(tmp_path / "a.pdf", num_pages=2)


@pytest.fixture
def test_pdf_b(tmp_path):
    return _create_test_pdf(tmp_path / "b.pdf", num_pages=3)


def _wait_signal(qapp, signal, timeout=15.0):
    """等待信号触发,期间处理事件循环。返回是否触发。"""
    fired = [False]

    def _on():
        fired[0] = True

    signal.connect(_on)
    deadline = time.monotonic() + timeout
    try:
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.03)
    finally:
        signal.disconnect(_on)
    return fired[0]


# ---- session 生命周期 --------------------------------------------------


class TestPdfSessionManagerSessions:
    def test_open_session(self, manager, test_pdf_a, qapp):
        fired = [False]
        manager.active_changed.connect(lambda: fired.__setitem__(0, True))
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        assert fired[0], "active_changed 应触发"
        s = manager.active_session
        assert s is not None
        assert s.pdf_document.page_count == 2

    def test_active_session_is_last_opened(self, manager, test_pdf_a, test_pdf_b, qapp):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        assert manager.active_session.file_path.endswith("b.pdf")

    def test_switch_session(self, manager, test_pdf_a, test_pdf_b, qapp):
        path_a = str(test_pdf_a)
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        manager.switch_session(path_a)
        assert manager.active_session.file_path.endswith("a.pdf")

    def test_close_session(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        manager.close_session(path)
        qapp.processEvents()
        assert path not in manager.session_paths
        assert manager.get_session(path) is None

    def test_session_paths(self, manager, test_pdf_a, test_pdf_b, qapp):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        assert len(manager.session_paths) == 2

    def test_get_session(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        s = manager.get_session(path)
        assert s is not None
        assert manager.get_session("nonexistent") is None

    def test_open_nonexistent_emits_open_failed(self, manager, qapp):
        """打开不存在的文件:emit open_failed 信号。"""
        fired = [False]
        manager.open_failed.connect(lambda *a: fired.__setitem__(0, True))
        manager.open_session("/nonexistent/file.pdf")
        qapp.processEvents()
        assert fired[0], "open_failed 应触发"


# ---- 异步批量打开 ------------------------------------------------------


class TestOpenAsync:
    def test_open_sessions_async_emits_session_added(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        fired = [False]
        manager.session_added.connect(lambda *a: fired.__setitem__(0, True))
        manager.open_sessions_async([path])
        # 异步:等 worker 线程完成 open+load
        deadline = time.monotonic() + 25.0
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
        assert fired[0], "session_added 应触发"
        assert path in manager.session_paths

    def test_open_sessions_async_skip_existing(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        fired = [False]
        manager.open_done.connect(lambda: fired.__setitem__(0, True))
        manager.open_sessions_async([path])
        deadline = time.monotonic() + 10.0
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.03)
        assert fired[0], "open_done 应触发(已存在的文件跳过)"


# ---- 插入文件（结构变更）----------------------------------------------


class TestPdfSessionManagerInsert:
    """插入文件/空白页（Bug 3 回归）：dispatch 链路必须端到端工作。

    Bug 3 症状被误报为"插入无响应"。实际 dispatch 正常（页数增加、mutate_done
    触发），问题在 UI 缩略图刷新（见 test_pdf_tab.py::TestThumbnailAutoRender*）。
    此处回归 manager 层：insert_from_async 经 mutate worker → 后端 → diff apply
    完整链路，页数与 thumbnails_invalidated 信号正确。
    """

    def test_insert_from_async_increases_page_count(
        self, manager, test_pdf_a, test_pdf_b, qapp
    ):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        session = manager.active_session
        assert session.pdf_document.page_count == 2

        done = [False]
        manager.mutate_done.connect(lambda *a: done.__setitem__(0, True))
        manager.insert_from_async(str(test_pdf_b), after_index=0)

        deadline = time.monotonic() + 20.0
        while not done[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert done[0], "insert_from_async 应触发 mutate_done"
        assert session.pdf_document.page_count == 5  # 2 + 3

    def test_insert_blank_async_increases_page_count(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        session = manager.active_session
        assert session.pdf_document.page_count == 2

        done = [False]
        manager.mutate_done.connect(lambda *a: done.__setitem__(0, True))
        manager.insert_blank_async(after_index=0)

        deadline = time.monotonic() + 20.0
        while not done[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)

        assert done[0], "insert_blank_async 应触发 mutate_done"
        assert session.pdf_document.page_count == 3  # 2 + 1

    def test_insert_from_async_emits_thumbnails_invalidated(
        self, manager, test_pdf_a, test_pdf_b, qapp
    ):
        """结构变更（插页）应 emit thumbnails_invalidated（全页失效），
        供 PdfTab 触发缩略图重渲（Bug 3 刷新链路的上游）。"""
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        invalidated = []
        manager.thumbnails_invalidated.connect(
            lambda pages: invalidated.append(list(pages))
        )
        manager.insert_from_async(str(test_pdf_b), after_index=0)
        deadline = time.monotonic() + 20.0
        while not invalidated and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
        assert invalidated, "插页应 emit thumbnails_invalidated"
        # 全量失效（结构变更）
        assert len(invalidated[0]) == 5


# ---- 缩略图失效信号 ----------------------------------------------------


class TestRerenderThumbnailsAsync:
    def test_emits_thumbnails_invalidated(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        fired = [False]
        manager.thumbnails_invalidated.connect(lambda *a: fired.__setitem__(0, True))
        manager.rerender_thumbnails_async([0])
        qapp.processEvents()
        assert fired[0], "thumbnails_invalidated 应触发"

    def test_empty_indices_does_not_emit(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        manager.rerender_thumbnails_async([])
        qapp.processEvents()


# ---- 文字层状态 --------------------------------------------------------


class TestPagesWithoutTextLayer:
    def test_returns_empty_for_unknown_session(self, manager):
        assert manager.get_pages_without_text_layer("nonexistent") == []


class TestPdfSessionManagerBlockEdit:
    def test_update_block_text_no_active_session(self, manager):
        """无活动会话时返回 False。"""
        assert manager.update_page_block_text(0, 0, "x") is False


# ---- shutdown ----------------------------------------------------------


class TestPdfSessionManagerShutdown:
    def test_shutdown_clears_sessions(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        manager.shutdown()
        assert len(manager.session_paths) == 0


# ---- 属性 ---------------------------------------------------------------


class TestPdfSessionManagerProperties:
    def test_is_ocr_ready_default_false(self, manager):
        assert manager.is_ocr_ready is False

    def test_is_deskew_running_default_false(self, manager):
        assert manager.is_deskew_running is False

    def test_is_mutate_running_default_false(self, manager):
        assert manager.is_mutate_running is False

    def test_backend_client_exposed(self, manager):
        """manager 暴露 backend_client 供 PdfTab 缩略图/预览渲染用。"""
        assert manager.backend_client is not None

    def test_get_modified_sessions_empty(self, manager):
        assert manager.get_modified_sessions() == []


class TestPdfMutateLifecycle:
    @staticmethod
    def _manager_with_session(qapp):
        from unittest.mock import MagicMock

        mgr = PdfSessionManager(parent=qapp)
        session = MagicMock()
        session.file_path = "a.pdf"
        session.session_id = "sid-a"
        mgr._sessions = {"a.pdf": session}
        mgr._active_path = "a.pdf"
        mgr._client = MagicMock()
        return mgr

    def test_busy_gate_rejects_second_mutate_without_cancelling_first(self, qapp):
        from unittest.mock import MagicMock

        mgr = self._manager_with_session(qapp)
        current = MagicMock()
        mgr._mutate_worker = current
        mgr._mutate_state = "running"

        assert mgr._start_mutate("rotate", {"pages": [0], "angle": 90}) is False
        current.cancel.assert_not_called()
        current.wait.assert_not_called()
        assert mgr._mutate_worker is current

    def test_cancel_is_request_only_and_reference_lives_until_finished(self, qapp):
        from unittest.mock import MagicMock

        mgr = self._manager_with_session(qapp)
        worker = MagicMock()
        mgr._mutate_worker = worker
        mgr._mutate_state = "running"
        mgr._mutate_op = "rotate"
        mgr._mutate_path = "a.pdf"
        mgr._mutate_task_id = 7

        assert mgr._cancel_mutate_worker() is True
        worker.cancel.assert_called_once_with()
        worker.wait.assert_not_called()
        assert mgr._mutate_worker is worker
        assert mgr.mutate_state == "cancelling"

        states = []
        mgr.mutate_state_changed.connect(lambda *args: states.append(args))
        mgr._on_mutate_worker_finished(worker, 7)
        assert mgr._mutate_worker is None
        assert mgr.mutate_state == "cancelled"
        assert states[-1] == ("a.pdf", "rotate", "cancelled")

    def test_shutdown_does_not_wait_for_running_mutate(self, qapp):
        import time
        from unittest.mock import MagicMock

        mgr = self._manager_with_session(qapp)
        worker = MagicMock()
        mgr._mutate_worker = worker
        mgr._mutate_state = "running"
        mgr._mutate_op = "save"
        mgr._mutate_path = "a.pdf"

        started = time.monotonic()
        mgr.shutdown()
        elapsed = time.monotonic() - started

        worker.cancel.assert_called_once_with()
        worker.wait.assert_not_called()
        assert elapsed < 0.2

    def test_late_mutate_signal_from_old_worker_is_ignored(self, qapp):
        from unittest.mock import MagicMock

        mgr = self._manager_with_session(qapp)
        current = MagicMock()
        old = MagicMock()
        mgr._mutate_worker = current
        mgr._mutate_state = "running"
        mgr._mutate_task_id = 2
        mgr._task_generation = 2
        done = []
        mgr.mutate_done.connect(lambda *args: done.append(args))

        mgr._on_mutate_all_done("sid-a", MagicMock(), {}, task_id=1, worker=old)

        assert done == []
        assert mgr._mutate_worker is current


# ---- task generation ---------------------------------------------------


class TestPdfTaskGeneration:
    """PDF runner task generation：旧任务的迟到信号不污染新任务状态。

    根因：OCR/mutate 取消后可继续启动新任务，旧 runner 的 all_done 信号
    无条件清 _ocr_running/_ocr_worker，把新任务状态清掉。引入递增
    task generation，信号带 task_id，槽只接受当前代。
    """

    def test_ocr_done_with_stale_task_id_ignored(self):
        """旧 task_id 的 all_done 信号被忽略，不清 _ocr_running/_ocr_worker"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2  # 当前代
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None  # 模拟无匹配

        # 旧代（task_id=1）的 all_done 信号
        mgr._on_ocr_all_done_signal("session_1", 5, 0, task_id=1)

        # 当前代状态不被旧信号清掉
        assert mgr._ocr_running is True
        assert mgr._ocr_worker is not None

    def test_ocr_done_with_current_task_id_accepted(self):
        """当前 task_id 的 all_done 信号正常清理状态"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_ocr_all_done_signal("session_2", 5, 0, task_id=2)

        assert mgr._ocr_running is False
        # all_done 只结束业务态；QThread 引用保留到原生 finished 槽。
        assert mgr._ocr_worker is not None

    def test_ocr_done_without_task_id_accepted(self):
        """无 task_id 参数（默认 0）时正常处理（向后兼容）"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 1
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_ocr_all_done_signal("session_1", 5, 0)

        assert mgr._ocr_running is False
        # 无 task_id 兼容路径同样不能提前释放仍可能在 finally 的 QThread。
        assert mgr._ocr_worker is not None

    def test_mutate_done_with_stale_task_id_ignored(self):
        """旧 task_id 的 mutate all_done 信号被忽略，不清 _mutate_worker"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2
        mgr._mutate_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_mutate_all_done("session_1", MagicMock(), {}, task_id=1)

        assert mgr._mutate_worker is not None

    def test_task_generation_increments_on_start_ocr(self, qapp):
        """start_ocr 递增 task generation"""
        from unittest.mock import MagicMock, patch

        from PySide6.QtCore import QThread

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = None
        mgr._inference_client = None
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        # active_session 为 None 时 start_ocr 直接返回，不递增
        # 需要有 active session
        mock_session = MagicMock()
        mock_session.session_id = "sid1"
        mock_session.reset_ocr_stats = MagicMock()
        mgr._sessions["/fake.pdf"] = mock_session
        mgr._active_path = "/fake.pdf"
        mgr._inference_client = MagicMock()

        # 本测试只验证 generation，不应泄漏真实 OCR 后台线程到后续测试。
        with (
            patch.object(mgr, "_cancel_ocr"),
            patch.object(QThread, "start") as start_mock,
        ):
            mgr.start_ocr([0, 1])

        assert mgr._task_generation == 1
        start_mock.assert_called_once_with()

    def test_document_parsing_starts_runtime_job_directly(self, qapp):
        from unittest.mock import MagicMock, patch

        from PySide6.QtCore import QThread

        from vibeocr.classic.recognition_settings import OCROptions
        from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = "/fake.pdf"
        mgr._inference_client = MagicMock()
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = "/fake.pdf"
        mgr._sessions[session.file_path] = session

        with patch.object(QThread, "start") as start_mock:
            started = mgr.start_ocr(
                [0],
                ocr_options=OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING),
            )

        assert started is True
        assert mgr._ocr_worker is not None
        assert mgr._ocr_state == "running"
        start_mock.assert_called_once_with()

    def test_ocr_worker_resets_cancel_flag(self, qapp):
        """OCR worker 应在线程内 reset_cancel，清掉残留的后端 cancel 标志。

        回归：reset_cancel 全代码库原本无调用点；一旦某次取消置位了
        cancel_event，会污染后续 delete_text_layers 等协作式取消操作。
        """
        from unittest.mock import MagicMock, patch

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = None
        mgr._inference_client = None
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        mock_session = MagicMock()
        mock_session.session_id = "sid1"
        mock_session.reset_ocr_stats = MagicMock()
        mgr._sessions["/fake.pdf"] = mock_session
        mgr._active_path = "/fake.pdf"
        mgr._inference_client = MagicMock()

        with patch.object(mgr, "_run_ocr"):
            mgr.start_ocr([0])
            worker = mgr._ocr_worker
            assert worker is not None
            assert worker.wait(3000)

        # 关键断言：reset 仍执行，但由 OCR QThread 而非 GUI 入口执行。
        mgr._client.reset_cancel.assert_called_once_with("sid1")


class TestOcrRunnerFailure:
    """OCR runner 未捕获异常时：failed 信号应重置状态并通知 UI 复位。

    根因：_OcrRunner.run() 此前无 except，_run_ocr 末尾 get_model 块只捕获
    PdfBackendError；大文件场景抛非 PdfBackendError（ValidationError/MemoryError）
    时线程静默死亡，all_done/failed 都不发，UI 永久卡在「OCR 进行中」。
    修复后 run() 补 except 发 failed，_on_ocr_failed_signal 重置状态并发 ocr_done。
    """

    def test_failed_signal_resets_state_and_emits_ocr_done(self, qapp):
        """failed 应清业务 running、保留 worker 到 finished，并发 ocr_done。"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._task_generation = 1

        # 构造一个有 5 页的 session
        mock_session = MagicMock()
        mock_pages = [MagicMock() for _ in range(5)]
        mock_session.pdf_document.pages = mock_pages
        mgr._sessions = {"/fake.pdf": mock_session}
        mgr._path_for_session_id = MagicMock(return_value="/fake.pdf")

        captured = []
        mgr.ocr_done = MagicMock()
        mgr.ocr_done.emit = lambda *a: captured.append(a)

        mgr._on_ocr_failed_signal("session_1", "boom")

        assert mgr._ocr_running is False
        # failed 在 worker.run 内发出；引用必须保留到原生 finished。
        assert mgr._ocr_worker is not None
        # ocr_done 以 (0, total) 发出，让 UI 复位
        assert len(captured) == 1
        path, success, fail = captured[0]
        assert path == "/fake.pdf"
        assert success == 0
        assert fail == 5

    def test_failed_signal_no_session_still_resets(self, qapp):
        """无匹配 session 时仍重置业务态且不提前释放 worker。"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = MagicMock(return_value=None)
        mgr.ocr_done = MagicMock()

        # 不应抛异常
        mgr._on_ocr_failed_signal("unknown_session", "error")

        assert mgr._ocr_running is False
        # 无会话时也不能在 QThread.finished 前提前释放引用。
        assert mgr._ocr_worker is not None
        # 无匹配 session 时不发 ocr_done（无 file_path）
        mgr.ocr_done.emit.assert_not_called()


class TestOcrRunnerCancel:
    """OCR 取消应在后台通知后端，不再只设本地 flag。"""

    def test_cancel_notifies_backend(self, qapp):
        """manager 取消应异步调用 client.cancel(sid)。

        回归：旧 _OcrRunner.cancel() 只设本地 _cancelled bool，不通知后端；
        后端 add_text_layer_batch 一直跑完，取消形同虚设。修复后对齐
        PdfIpcMutateWorker.cancel() 的成熟模式。
        """
        from unittest.mock import MagicMock

        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        # 复用 start_ocr 内定义的 _OcrRunner：它捕获 mgr 并持有 sid。
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._RENDER_CONCURRENCY = 1
        client = MagicMock()
        sid = "sid_cancel"

        # 通过 start_ocr 构造 runner，但阻止其真正 start。
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = None
        mgr._inference_client = None
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = client
        mgr._overwrite_text_layer = False
        mgr._pdf_settings = None
        mgr._path_for_session_id = MagicMock(return_value=None)

        mock_session = MagicMock()
        mock_session.session_id = sid
        mock_session.reset_ocr_stats = MagicMock()
        mgr._sessions["/fake.pdf"] = mock_session
        mgr._active_path = "/fake.pdf"
        mgr._inference_client = MagicMock()

        from unittest.mock import patch

        from PySide6.QtCore import QThread

        with patch.object(QThread, "start"):
            mgr.start_ocr([0])

        runner = mgr._ocr_worker
        assert runner is not None, "start_ocr 应创建 runner"

        # 取消：runner 仅设 flag，阻塞 cancel IPC 由独立 control worker 执行。
        mgr._cancel_ocr()
        assert runner._cancelled is True
        deadline = time.monotonic() + 3
        while client.cancel.call_count == 0 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        client.cancel.assert_called_once_with(sid)


class TestExportCancel:
    """export cancel 真正生效：逐文件检查 cancel flag，不继续后续文件。

    根因：_ExportRunner._cancelled 被 cancel() 设置但 run() 从不读取，
    export_all_modified 也不检查它，属于无效取消。
    """

    def test_export_cancel_stops_after_current_file(self):
        """export_all_modified 检查 cancel_check，取消后停止后续文件"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._pdf_settings = None
        mgr._settings_to_dict = lambda s: None
        mgr._client = MagicMock()
        mgr._client.save = MagicMock(return_value=None)

        # 3 个 modified session
        sessions = {}
        for i in range(3):
            mock_s = MagicMock()
            mock_s.session_id = f"sid_{i}"
            mock_s.is_modified = True
            sessions[f"/file_{i}.pdf"] = mock_s
        mgr._sessions = sessions

        # cancel_check 第一次返回 False（处理第一个），之后返回 True
        call_count = [0]

        def cancel_check():
            call_count[0] += 1
            return call_count[0] > 1

        results = mgr.export_all_modified("/tmp/out", cancel_check=cancel_check)

        # 取消后只处理 1 个文件
        assert len(results) == 1
        assert mgr._client.save.call_count == 1

    def test_export_no_cancel_processes_all(self):
        """无取消时处理所有 modified session"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._pdf_settings = None
        mgr._settings_to_dict = lambda s: None
        mgr._client = MagicMock()
        mgr._client.save = MagicMock(return_value=None)

        sessions = {}
        for i in range(3):
            mock_s = MagicMock()
            mock_s.session_id = f"sid_{i}"
            mock_s.is_modified = True
            sessions[f"/file_{i}.pdf"] = mock_s
        mgr._sessions = sessions

        results = mgr.export_all_modified("/tmp/out")
        assert len(results) == 3
        assert mgr._client.save.call_count == 3


class TestOcrPageDoneIncrementalModel:
    """_on_ocr_page_done_signal 应增量把 result.text_blocks 落 model，
    消除预览滞后（此前只在整批结束 get_model 才全量刷新）。"""

    def test_page_done_writes_ocr_blocks_to_model(self, qapp, tmp_path):
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}

        doc = PdfDocument(file_path=str(tmp_path / "x.pdf"))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.pdf_document = doc
        file_path = str(tmp_path / "x.pdf")
        mgr._sessions[file_path] = session

        # 模拟 OCRResult（带 text_blocks + preproc_angle）
        result = MagicMock()
        block = MagicMock()
        block.text = "hello"
        result.text_blocks = [block]
        result.preproc_angle = 90

        # ocr_page_done 是 Signal，需替换为可调用的 mock 以便 _on_ocr_page_done_signal
        # 末尾 emit 不抛异常。
        mgr.ocr_page_done = MagicMock()

        mgr._on_ocr_page_done_signal("sid1", 1, result)

        info = doc.pages[1]
        assert info.has_text_layer is True
        assert info.ocr_text_blocks == [block]
        assert info.ocr_preproc_angle == 90

    def test_page_done_none_result_skips_model_write(self, qapp, tmp_path):
        """result 为 None（失败/空页）时不写 model、不发块，仅转发信号。"""
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}

        doc = PdfDocument(file_path=str(tmp_path / "x.pdf"))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.pdf_document = doc
        file_path = str(tmp_path / "x.pdf")
        mgr._sessions[file_path] = session

        mgr.ocr_page_done = MagicMock()

        mgr._on_ocr_page_done_signal("sid1", 1, None)

        info = doc.pages[1]
        # 不写块
        assert info.ocr_text_blocks == []
        assert info.has_text_layer is False
        # 仍转发信号
        mgr.ocr_page_done.emit.assert_called_once_with(file_path, 1, None)


class TestRunOcrIncrementalSave:
    """_run_ocr 阶段3 写层后应 incremental save + 写 sidecar；
    全部批次已落盘时跳过末尾全量压缩并 mark_completed。"""

    def test_run_ocr_calls_add_text_layer_batch_with_save_and_writes_sidecar(
        self, qapp, tmp_path, monkeypatch
    ):
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager
        from vibeocr.runtime_contracts.pdf import PdfMutationResult

        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [
            PdfPageInfo(page_index=i, rect=(0.0, 0.0, 595.0, 842.0)) for i in range(3)
        ]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.pdf_document = doc
        session.ocr_stats = {"written": 0, "skipped": 0}
        session.add_ocr_stats = MagicMock()
        mgr._sessions[str(pdf_path)] = session
        mgr._overwrite_text_layer = False

        # mock client
        client = MagicMock()
        # render_preview 返回非空 bytes（避免被当渲染失败）
        client.render_preview.return_value = b"\x89PNG fake"
        client.add_text_layer_batch.return_value = PdfMutationResult.from_payload(
            {
                "schema_version": 2,
                "instance_id": "runtime-1",
                "diff": {},
                "extra": {"saved": True},
            }
        )
        mgr._client = client

        # mock OCR service：每页返回带 text_blocks 的 result
        mgr._inference_client = MagicMock()
        block = MagicMock()
        block.text = "t"
        block.score = 0.9
        block.bbox = [0.0, 0.0, 100.0, 100.0]
        block.page_idx = 0
        block.is_manually_edited = False
        block.label = "text"
        block.order = 0
        result = MagicMock()
        result.text_blocks = [block]
        result.preproc_angle = 0
        mgr._recognize_images_via_job = MagicMock(return_value=[result] * 3)

        # sidecar 重定向到 tmp（隔离测试，避免污染真实缓存目录）
        monkeypatch.setattr(
            "vibeocr.classic.ocr_sidecar._sessions_dir",
            lambda: tmp_path / "sessions",
        )

        runner = MagicMock()
        runner._cancelled = False
        runner._task_id = 1
        runner.page_done = MagicMock()
        runner.progress = MagicMock()
        runner.all_done = MagicMock()
        # _run_ocr 通过 runner._render_pool.map 并发渲染；mock 成返回 3 份 PNG bytes。
        runner._render_pool = MagicMock()
        runner._render_pool.map.side_effect = lambda func, indices: [
            func(index) for index in indices
        ]

        mgr._run_ocr(
            runner,
            "sid1",
            [0, 1, 2],
            None,
            {"render_dpi": 200, "max_pixels": 16_000_000},
            False,
        )

        # 关键断言：add_text_layer_batch 被调用且 save=True
        assert client.add_text_layer_batch.called
        _, kwargs = client.add_text_layer_batch.call_args
        assert kwargs.get("save") is True
        # 每批已增量落盘，不再整文档压缩，也不再全量拉取模型。
        client.save.assert_not_called()
        client.get_model.assert_not_called()
        from vibeocr.classic.ocr_sidecar import load_sidecar

        data = load_sidecar(str(pdf_path))
        assert data is not None
        assert data["completed"] is True
        assert doc.is_modified is False
        assert doc.has_structural_change is False
        assert [
            call.kwargs["dpi"] for call in client.render_preview.call_args_list
        ] == [
            200,
            200,
            200,
        ]

    def test_run_ocr_final_save_recovers_unpersisted_batch(
        self, qapp, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager
        from vibeocr.runtime_contracts.pdf import PdfMutationResult

        pdf_path = tmp_path / "fallback.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._active_path = str(pdf_path)
        document = PdfDocument(file_path=str(pdf_path))
        document.pages = [PdfPageInfo(page_index=0)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.pdf_document = document
        session.add_ocr_stats = MagicMock()
        mgr._sessions = {str(pdf_path): session}

        client = MagicMock()
        client.render_preview.return_value = b"png"
        client.add_text_layer_batch.return_value = PdfMutationResult.from_payload(
            {
                "schema_version": 2,
                "instance_id": "runtime-1",
                "diff": {},
                "extra": {"saved": False},
            }
        )
        mgr._client = client
        block = SimpleNamespace(
            text="正文",
            score=0.9,
            bbox=(0, 0, 100, 100),
            polygon=None,
            page_idx=0,
            is_manually_edited=False,
            label="text",
            order=0,
        )
        mgr._inference_client = MagicMock()
        mgr._recognize_images_via_job = MagicMock(
            return_value=[SimpleNamespace(text_blocks=[block], preproc_angle=90)]
        )
        monkeypatch.setattr(
            "vibeocr.classic.ocr_sidecar._sessions_dir",
            lambda: tmp_path / "sessions",
        )

        runner = MagicMock()
        runner._cancelled = False
        runner._task_id = 1
        runner._render_pool.map.side_effect = lambda func, indices: [
            func(index) for index in indices
        ]

        mgr._run_ocr(runner, "sid1", [0], None, {}, False)

        assert client.save.call_args.kwargs["rewrite_text_layers"] is False
        client.get_model.assert_not_called()
        from vibeocr.classic.ocr_sidecar import load_sidecar

        data = load_sidecar(str(pdf_path))
        assert data is not None and data["completed"] is True
        assert data["pages"]["0"]["ocr_preproc_angle"] == 90

    def test_run_ocr_prefetches_next_render_batch_before_current_ocr(
        self, qapp, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        pdf_path = tmp_path / "pipeline.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        mgr._OCR_BATCH_SIZE = 2

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=i) for i in range(3)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.pdf_document = doc
        session.add_ocr_stats = MagicMock()
        mgr._sessions[str(pdf_path)] = session

        events = []

        class RecordingPool:
            def map(self, func, page_indices):
                batch = list(page_indices)
                events.append(("render", batch))
                return iter([f"png-{idx}".encode() for idx in batch])

        class RecordingOcr:
            def recognize(self, images, options, *, cancel_requested):
                events.append(("ocr", len(images)))
                return [SimpleNamespace(text_blocks=[]) for _ in images]

        mgr._client = MagicMock()
        mgr._client.get_model.return_value = MagicMock()
        mgr._inference_client = MagicMock()
        mgr._recognize_images_via_job = RecordingOcr().recognize
        monkeypatch.setattr(
            "vibeocr.classic.pyside.pdf_session_manager.mirror_to_doc",
            lambda _model: doc,
        )
        runner = MagicMock()
        runner._cancelled = False
        runner._task_id = 1
        runner._render_pool = RecordingPool()

        mgr._run_ocr(runner, "sid1", [0, 1, 2], None, {}, False)

        assert events == [
            ("render", [0, 1]),
            ("render", [2]),
            ("ocr", 2),
            ("ocr", 1),
        ]

    def test_run_ocr_repartitions_rendered_bytes_and_isolates_transfer_failure(
        self, qapp, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from vibeocr.classic.pyside.batch_budget import BatchBudget
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        pdf_path = tmp_path / "transfer-budget.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        mgr._ocr_batch_budget_override = BatchBudget(
            max_items=16, max_encoded_bytes=5, max_pixels=1_000_000
        )

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=i) for i in range(3)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.pdf_document = doc
        session.add_ocr_stats = MagicMock()
        mgr._sessions[str(pdf_path)] = session

        class RecordingOcr:
            def __init__(self):
                self.calls = []

            def recognize(self, images, _options, *, cancel_requested):
                batch = list(images)
                self.calls.append(batch)
                if batch == [b"bbbb"]:
                    raise RuntimeError("transfer failed")
                return [SimpleNamespace(text_blocks=[]) for _ in batch]

        ocr = RecordingOcr()
        mgr._inference_client = MagicMock()
        mgr._recognize_images_via_job = ocr.recognize
        mgr._client = MagicMock()
        mgr._client.get_model.return_value = MagicMock()
        monkeypatch.setattr(
            "vibeocr.classic.pyside.pdf_session_manager.mirror_to_doc",
            lambda _model: doc,
        )

        runner = MagicMock()
        runner._cancelled = False
        runner._task_id = 1
        runner._render_pool = MagicMock()
        runner._render_pool.map.return_value = iter([b"aaaa", b"bbbb", b"cc"])

        mgr._run_ocr(runner, "sid1", [0, 1, 2], None, {}, False)

        assert ocr.calls == [[b"aaaa"], [b"bbbb"], [b"cc"]]
        assert [call.args[1] for call in runner.page_done.emit.call_args_list] == [
            0,
            1,
            2,
        ]


class TestStartOcrResumeFilter:
    """start_ocr 应读取 sidecar，过滤掉已落盘页（断点续传）。"""

    def test_start_ocr_skips_pages_in_pending_sidecar(
        self, qapp, tmp_path, monkeypatch
    ):
        from unittest.mock import MagicMock, patch

        from PySide6.QtCore import QThread

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        pdf_path = tmp_path / "r.pdf"
        pdf_path.write_bytes(b"abc")
        monkeypatch.setattr(
            "vibeocr.classic.ocr_sidecar._sessions_dir", lambda: tmp_path / "s"
        )
        # 预置 sidecar：页 0 已落盘，未完成
        from vibeocr.classic.ocr_sidecar import mark_pages_saved

        mark_pages_saved(str(pdf_path), [0], {0: 0})

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.reset_ocr_stats = MagicMock()
        session.pdf_document = doc
        mgr._sessions[str(pdf_path)] = session
        mgr._inference_client = MagicMock()
        mgr._pdf_settings = MagicMock()
        mgr._overwrite_text_layer = False
        mgr._settings_to_dict = MagicMock(return_value={})
        mgr._RENDER_CONCURRENCY = 1

        with (
            patch.object(mgr, "_cancel_ocr"),
            patch.object(QThread, "start"),  # 阻止真线程
        ):
            mgr.start_ocr([0, 1])  # 用户请求 0,1

        # runner 已构造（QThread.start 被 patch 不真跑），读取其 _pages：
        # 页 0 已落盘被过滤
        assert mgr._ocr_worker is not None
        assert mgr._ocr_worker._pages == [1]

    def test_start_ocr_overwrite_skips_filter(self, qapp, tmp_path, monkeypatch):
        """overwrite=True 时不过滤 sidecar，全量 OCR。"""
        from unittest.mock import MagicMock, patch

        from PySide6.QtCore import QThread

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        pdf_path = tmp_path / "r2.pdf"
        pdf_path.write_bytes(b"abc")
        monkeypatch.setattr(
            "vibeocr.classic.ocr_sidecar._sessions_dir", lambda: tmp_path / "s2"
        )
        from vibeocr.classic.ocr_sidecar import mark_pages_saved

        mark_pages_saved(str(pdf_path), [0], {0: 0})

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.reset_ocr_stats = MagicMock()
        session.pdf_document = doc
        mgr._sessions[str(pdf_path)] = session
        mgr._inference_client = MagicMock()
        mgr._pdf_settings = MagicMock()
        mgr._overwrite_text_layer = False
        mgr._settings_to_dict = MagicMock(return_value={})
        mgr._RENDER_CONCURRENCY = 1

        with (
            patch.object(mgr, "_cancel_ocr"),
            patch.object(QThread, "start"),
        ):
            mgr.start_ocr([0, 1], overwrite=True)  # overwrite 不过滤

        assert mgr._ocr_worker is not None
        # overwrite=True → 不过滤，pages 保持 [0, 1]
        assert mgr._ocr_worker._pages == [0, 1]

    def test_start_ocr_all_pages_saved_aborts_gracefully(
        self, qapp, tmp_path, monkeypatch
    ):
        """所有请求页已落盘时，start_ocr 应跳过 OCR、不发 runner、复位 running。

        且必须发 ocr_done（+ ocr_stats_ready）：PdfTab 在 start_ocr 之前已
        _begin_ocr_ui（进度条可见/按钮禁用/格子 processing），若短路 return 不发
        ocr_done，_on_ocr_finished 永不运行，UI 卡死在 0% 进度条 + 蓝格子。
        """
        from unittest.mock import MagicMock, patch

        from PySide6.QtCore import QThread

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager

        pdf_path = tmp_path / "r3.pdf"
        pdf_path.write_bytes(b"abc")
        monkeypatch.setattr(
            "vibeocr.classic.ocr_sidecar._sessions_dir", lambda: tmp_path / "s3"
        )
        from vibeocr.classic.ocr_sidecar import mark_pages_saved

        # 页 0,1 都已落盘
        mark_pages_saved(str(pdf_path), [0, 1], {0: 0, 1: 0})

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        # 新写门会拒绝已有 running；从 idle 进入后 start_ocr 自己置 True，
        # sidecar 全命中短路再负责复位为 False。
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.file_path = str(pdf_path)
        session.reset_ocr_stats = MagicMock()
        # ocr_stats 在短路分支被读取（emit ocr_stats_ready），需为真实 dict
        session.ocr_stats = {"written": 0, "skipped": 0}
        session.pdf_document = doc
        mgr._sessions[str(pdf_path)] = session
        mgr._inference_client = MagicMock()
        mgr._pdf_settings = MagicMock()
        mgr._overwrite_text_layer = False
        mgr._settings_to_dict = MagicMock(return_value={})

        # 捕获短路分支发出的 ocr_done / ocr_stats_ready（镜像 TestOcrRunnerFailure
        # 的 signal-spy 做法：替换 emit 为收集 lambda）。
        ocr_done_calls: list[tuple] = []
        ocr_stats_calls: list[tuple] = []
        mgr.ocr_done = MagicMock()
        mgr.ocr_done.emit = lambda *a: ocr_done_calls.append(a)
        mgr.ocr_stats_ready = MagicMock()
        mgr.ocr_stats_ready.emit = lambda *a: ocr_stats_calls.append(a)

        with (
            patch.object(mgr, "_cancel_ocr"),
            patch.object(QThread, "start") as start_mock,
        ):
            mgr.start_ocr([0, 1])  # 所有页已落盘

        # 应提前返回：无 runner，线程未启动，running 复位
        assert mgr._ocr_worker is None
        start_mock.assert_not_called()
        assert mgr._ocr_running is False
        # 关键：ocr_done 必须发出，否则 PdfTab._on_ocr_finished 不复位 UI
        assert len(ocr_done_calls) == 1, "短路 return 必须发 ocr_done 复位 UI"
        path, success, fail = ocr_done_calls[0]
        assert path == str(pdf_path)
        assert success == 0  # 无事可做
        assert fail == 0
        # ocr_stats_ready 也应发出（与正常完成路径 _on_ocr_all_done_signal 对齐）
        assert len(ocr_stats_calls) == 1
        spath, written, skipped = ocr_stats_calls[0]
        assert spath == str(pdf_path)
        assert written == 0
        assert skipped == 0
