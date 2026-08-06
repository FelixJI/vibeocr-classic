"""PySide PDF 多文件会话管理器（supervisor HTTP v2 transport 版本）。

管理 PdfSession 集合,通过 SyncPdfSupervisorClient (httpx 经 supervisor
HTTP v2 代理)调用 supervisor 拥有的 PDF 后端子进程,中转信号到 UI。fitz
调用全部在后端子进程,主进程零 fitz 直接访问。

GUI 不再实例化 PdfBackendClient (ADR §"Transport"; plan §6/§7A): supervisor
是 PDF child 的唯一 owner。OCR/deskew 编排通过通用 job 接口完成
渲染→submit/observe/command→写层。

所有原有 Qt 信号签名保留不变,PdfTab 侧无需改信号连接。
同步页操作(旋转/删除/插入/重排)改为异步:manager 发 *_async,完成后
通过 mutate_done / thumbnails_invalidated 等信号通知 UI。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from vibeocr.classic.pyside.batch_budget import (
    BatchBudget,
    BatchEntry,
    image_pixel_count,
    partition_batches,
)

# Transport: the GUI no longer talks to the PDF child directly. It goes
# through the supervisor HTTP v2 client (ADR §"Transport"; plan §6/§7A).
# PdfBackendError is re-exported by the supervisor client for compat so
# existing ``except PdfBackendError`` sites keep matching.
from vibeocr.classic.pdf_client import PdfBackendError
from vibeocr.classic import ocr_sidecar
from vibeocr.classic.pdf_workspace import (
    PdfSession,
    apply_model_diff,
    coerce_document_mirror,
    document_from_mirror,
    page_from_mirror,
    text_layer_from_mirror,
)
from vibeocr.classic.recognition_settings import OCROptions, PdfGlobalSettings
from vibeocr.classic.pyside.pdf_ipc_worker import (
    MinerUPreflightWorker,
    PdfIpcCancelWorker,
    PdfIpcCloseWorker,
    PdfIpcMutateWorker,
    PdfIpcOpenWorker,
    PdfIpcPreviewWorker,
)
from vibeocr.runtime_contracts.pdf import (
    PdfDocumentMirror,
    PdfModelDiff,
    PdfPageInfoMirror,
)

logger = logging.getLogger(__name__)


class PdfSessionManager(QObject):
    """PDF 多文件会话管理器(进程化)。

    信号签名与旧版完全一致,PdfTab 无需改信号连接:
        session_added(file_path)
        session_removed(file_path)
        active_changed(file_path)
        page_loaded(file_path, page_index)
        load_progress(file_path, loaded, total)
        load_done(file_path)
        ocr_page_done(file_path, page_index, result)
        ocr_progress(file_path, current, total)
        ocr_done(file_path, success, fail)
        mutate_progress / mutate_done / mutate_failed
        save_done / delete_layer_done
        deskew_page_done / deskew_progress / deskew_done / deskew_failed
        thumbnails_invalidated(page_indices)
        ...
    """

    session_added = Signal(str)
    session_removed = Signal(str)
    active_changed = Signal(str)
    page_loaded = Signal(str, int)
    load_progress = Signal(str, int, int)
    load_done = Signal(str)
    ocr_page_done = Signal(str, int, object)
    ocr_progress = Signal(str, int, int)
    ocr_done = Signal(str, int, int)
    ocr_stats_ready = Signal(str, int, int)
    ocr_write_error = Signal(str, str)  # (file_path, error_message) — 写层失败详情
    mineru_models_status = Signal(str)
    render_progress = Signal(str, int, int)
    mutate_progress = Signal(str, int, int)
    mutate_done = Signal(str, object)
    mutate_failed = Signal(str, str)
    mutate_state_changed = Signal(str, str, str)  # file_path, op, state
    save_done = Signal(str)
    delete_layer_done = Signal(str, list)
    export_progress = Signal(int, int, str)
    export_done = Signal(list)
    export_failed = Signal(str)
    deskew_page_done = Signal(str, int, bool)
    deskew_progress = Signal(str, int, int)
    deskew_done = Signal(str, object)
    deskew_failed = Signal(str, str)
    open_progress = Signal(int, int)
    open_failed = Signal(str, str)
    thumbnails_invalidated = Signal(list)
    open_done = Signal()
    preview_ready = Signal(str, int, int, object)
    preview_failed = Signal(str, int, int, str)
    close_done = Signal(str)
    close_failed = Signal(str, str)

    def __init__(
        self,
        parent=None,
        *,
        client: Any = None,
        inference_client: Any = None,
    ) -> None:
        """Create the manager.

        ``client`` is normally None in production: the transport is lazily
        resolved from the global supervisor adapter on first use (see
        :meth:`_ensure_client`). Tests that want to bypass the supervisor
        (e.g. legacy integration tests that point at a real PDF backend child
        directly, or unit tests with a fake client) pass it explicitly.
        """
        super().__init__(parent)
        self._sessions: dict[str, PdfSession] = {}
        self._active_path: str | None = None
        self._open_worker: PdfIpcOpenWorker | None = None
        self._open_generation = 0
        self._draining_open_workers: set[PdfIpcOpenWorker] = set()
        self._preview_worker: PdfIpcPreviewWorker | None = None
        self._preview_generation = 0
        self._edit_revision = 0
        self._draining_preview_workers: set[PdfIpcPreviewWorker] = set()
        self._close_workers: set[PdfIpcCloseWorker] = set()
        self._control_workers: set[PdfIpcCancelWorker] = set()
        self._close_started_session_ids: set[str] = set()
        self._mutate_worker: PdfIpcMutateWorker | None = None
        self._mutate_state: str = "idle"
        self._mutate_op: str = ""
        self._mutate_path: str = ""
        self._mutate_task_id: int = 0
        self._mutate_terminal_received: bool = False
        self._shutting_down: bool = False
        self._export_worker: QThread | None = None
        self._export_result_pending: list[str] | None = None
        self._export_error_pending: str | None = None
        # OCR inference transport.  PDF rendering/editing uses the dedicated
        # PDF session client, while rendered page bytes enter the same generic
        # submit/observe/command job interface as Single and Batch.
        self._inference_client: Any = inference_client
        self._pdf_settings: PdfGlobalSettings | None = None
        self._overwrite_text_layer: bool = False
        self._ocr_running: bool = False
        self._ocr_cancelled: bool = False
        self._ocr_state: str = "idle"
        self._ocr_worker: QThread | None = None
        self._preflight_worker: MinerUPreflightWorker | None = None
        self._preflight_generation = 0
        self._preflight_result: tuple[bool, str] | None = None
        self._preflight_cancel_path: str | None = None
        self._pending_ocr_request: (
            tuple[str, list[int], object, object, bool] | None
        ) = None
        # PDF backend transport: lazily resolved from the supervisor adapter.
        # The supervisor owns the PDF child process; we hold a
        # SyncPdfSupervisorClient (vibeocr.classic.pdf_client) for the
        # lifetime of the manager. ``self._client`` is a cached property —
        # ``_ensure_client`` resolves it on first use so PDF tab construction
        # does not require the supervisor to be up yet (lazy tab can build
        # before SupervisorStartTask completes). Tests inject a client
        # directly to bypass the supervisor.
        self._client: Any = client
        # task generation：每类操作（OCR/mutate/export）启动时递增，
        # 信号携带 task_id，done 槽只接受当前代，避免旧任务的迟到信号
        # 清掉新任务状态（ABA/代际竞态）。
        self._task_generation: int = 0
        self._shutdown_stable_polls = 0
        self._shutdown_finalized = False

    def _ensure_client(self) -> Any:
        """Lazily resolve the SyncPdfSupervisorClient from the global adapter.

        Returns the cached client; raises ``PdfBackendError`` if the
        supervisor has not provided a PDF client factory (e.g. supervisor
        startup failed or has not completed). Workers call this at the start
        of each operation rather than holding a stale reference.
        """
        if self._client is not None:
            return self._client
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        client = get_supervisor_adapter().pdf_sync_client
        if client is None:
            raise PdfBackendError(
                "PDF backend unavailable: supervisor not started or no PDF "
                "client factory installed. Open the PDF tab after the OCR "
                "backend finishes starting."
            )
        self._client = client
        return client

    def _ensure_inference_client(self) -> Any:
        if self._inference_client is not None:
            return self._inference_client
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        client = get_supervisor_adapter().inference_sync_client
        if client is None:
            raise PdfBackendError(
                "PDF OCR unavailable: supervisor generic job client is not ready"
            )
        self._inference_client = client
        return client

    # ---- 属性 -----------------------------------------------------------

    @property
    def active_session(self) -> PdfSession | None:
        if self._active_path is not None:
            return self._sessions.get(self._active_path)
        return None

    @property
    def session_paths(self) -> list[str]:
        return list(self._sessions.keys())

    def get_session(self, file_path: str) -> PdfSession | None:
        return self._sessions.get(file_path)

    def set_inference_client(self, client: Any) -> None:
        """Inject the generic sync job client (primarily for tests)."""
        self._inference_client = client

    @property
    def is_ocr_ready(self) -> bool:
        if self._inference_client is not None:
            return True
        try:
            from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

            adapter = get_supervisor_adapter()
            return adapter.is_started and adapter.inference_sync_client is not None
        except Exception:
            return False

    def _recognize_images_via_job(
        self,
        images: list[bytes],
        ocr_options: OCROptions,
        *,
        cancel_requested,
    ) -> list[Any | None]:
        """Run one transport chunk as a generic keyed supervisor job."""
        import time
        from uuid import uuid4

        from vibeocr.classic.recognition_result import ocr_result_from_payload
        from vibeocr.runtime_contracts import (
            TERMINAL_JOB_STATES,
            ItemState,
            JobCommand,
            JobCommandKind,
            JobKind,
            JobPriority,
            SubmitItem,
            SubmitRequest,
        )

        pipeline_selection = ocr_options.to_pipeline_selection()
        request_id = str(uuid4())
        submit_items = tuple(
            SubmitItem(
                client_item_key=f"{request_id}:{index}",
                ordinal=index,
                display_name=f"page-{index}.png",
                source={
                    "type": "upload.v1",
                    "attachment": f"page-{index}",
                },
            )
            for index in range(len(images))
        )
        request = SubmitRequest(
            request_id=request_id,
            kind=JobKind.RECOGNITION,
            priority=JobPriority.BACKGROUND,
            pipeline=pipeline_selection,
            items=submit_items,
        )
        attachments = {
            f"page-{index}": ("image/png", image) for index, image in enumerate(images)
        }
        client = self._ensure_inference_client()
        ref = client.submit(request, attachments)
        last_sequence = 0
        while True:
            if cancel_requested():
                client.command(
                    JobCommand(
                        command_id=str(uuid4()),
                        kind=JobCommandKind.CANCEL,
                        job_id=ref.job_id,
                    )
                )
                return [None] * len(images)
            update = client.observe(ref.job_id, after_sequence=last_sequence)
            if update.snapshot.state in TERMINAL_JOB_STATES:
                break
            last_sequence = update.through_sequence
            time.sleep(0.02)

        # Re-observe from zero to obtain every terminal item outcome regardless
        # of how many progress deltas the polling loop consumed.
        final = client.observe(ref.job_id, after_sequence=0)
        outcomes = {outcome.item_id: outcome for outcome in final.outcomes}
        by_key = {item.client_item_key: item.item_id for item in ref.items}
        results: list[Any | None] = []
        for submit_item in submit_items:
            outcome = outcomes.get(by_key.get(submit_item.client_item_key, ""))
            if (
                outcome is None
                or outcome.state is not ItemState.SUCCEEDED
                or outcome.payload is None
            ):
                results.append(None)
            else:
                results.append(
                    ocr_result_from_payload(outcome.payload_type, outcome.payload)
                )
        return results

    def get_modified_sessions(self) -> list[tuple[str, PdfSession]]:
        return [(p, s) for p, s in self._sessions.items() if s.is_modified]

    @property
    def is_deskew_running(self) -> bool:
        """是否正在跑摆正(供 PdfTab cancel 路由判断)。"""
        return self._mutate_worker is not None and self._mutate_worker._op == "deskew"

    @property
    def is_mutate_running(self) -> bool:
        return self._mutate_worker is not None or bool(
            getattr(self, "_control_workers", set())
        )

    @property
    def mutate_state(self) -> str:
        return self._mutate_state

    @property
    def is_ocr_running(self) -> bool:
        # all_done/failed 属于业务终态信号，发出后 QThread 还可能在 finally 中
        # drain 渲染线程池；直到原生 finished 清除引用前，都仍占用 PDF 写门。
        return (
            self._ocr_running
            or self._ocr_worker is not None
            or self._preflight_worker is not None
        )

    def _pdf_write_busy(self) -> bool:
        """业务 done 不释放写门；以原生 worker finished/引用清理为边界。"""
        return bool(
            self._mutate_worker is not None
            or getattr(self, "_control_workers", set())
            or self.is_ocr_running
            or self._export_worker is not None
        )

    @property
    def backend_client(self) -> Any:
        """暴露 client 供 PdfTab 直接调渲染(缩略图/预览,这些是同步快调用)。

        返回的是 :class:`~vibeocr.classic.pdf_client.SyncPdfSupervisorClient`
        (supervisor HTTP v2 transport)。类型注解用 Any 以避免本模块顶层
        import supervisor client (启动期重链)；PdfTab 只调 render_thumbnail /
        render_preview 这两个同步方法。
        """
        return self._ensure_client()

    # ---- session lifecycle ---------------------------------------------

    def open_session(self, file_path: str) -> PdfSession | None:
        """同步打开单个文件(GUI 线程会短暂阻塞,适合已知小文件)。

        批量打开用 open_sessions_async。本方法保留供测试/特殊路径。
        同步跑完流式 load(逐页检测),适合小文件。
        """
        if file_path in self._sessions:
            self.switch_session(file_path)
            return self._sessions[file_path]
        try:
            client = self._ensure_client()
            client.start()
            open_resp = client.open_session(file_path)
            session = self._make_session(
                file_path, open_resp.session_id, open_resp.model
            )
            # 流式 load 逐页填充(同步,小文件可接受)
            for ev in client.load_stream(open_resp.session_id):
                if ev.page_index is not None and ev.page_payload is not None:
                    self._apply_page_loaded(session, ev.page_index, ev.page_payload)
                if ev.message == "done":
                    break
            self._sessions[file_path] = session
            self._active_path = file_path
            self.session_added.emit(file_path)
            self.active_changed.emit(file_path)
            self.load_done.emit(file_path)
            return session
        except Exception as e:
            logger.error("打开 %s 失败: %s", file_path, e)
            self.open_failed.emit(file_path, str(e))
            return None

    def open_sessions_async(self, paths: list[str]) -> None:
        """批量异步打开(后台 IPC open + load)。"""
        if self._shutting_down or self._pdf_write_busy():
            return
        new_paths = [p for p in paths if p not in self._sessions]
        if not new_paths:
            if paths:
                self.switch_session(paths[0])
            self.open_done.emit()
            return

        self._cancel_open_worker()
        self._open_generation += 1
        generation = self._open_generation
        worker = PdfIpcOpenWorker(self._ensure_client(), new_paths)
        worker.doc_opened.connect(
            lambda path, sid, model, w=worker, gen=generation: (
                self._on_doc_opened_guarded(path, sid, model, w, gen)
            )
        )
        worker.page_loaded.connect(
            lambda path, page, payload, w=worker, gen=generation: (
                self._on_page_loaded_guarded(path, page, payload, w, gen)
            )
        )
        worker.load_progress.connect(
            lambda path, current, total, w=worker, gen=generation: (
                self._on_load_progress_guarded(path, current, total, w, gen)
            )
        )
        worker.open_failed.connect(
            lambda path, error, w=worker, gen=generation: self._on_open_failed_guarded(
                path, error, w, gen
            )
        )
        worker.open_progress.connect(
            lambda current, total, w=worker, gen=generation: (
                self._on_open_progress_guarded(current, total, w, gen)
            )
        )
        worker.all_done.connect(
            lambda w=worker, gen=generation: self._on_open_all_done_guarded(w, gen)
        )
        worker.finished.connect(lambda w=worker: self._release_open_worker(w))
        self._open_worker = worker
        worker.start()

    def _make_session(
        self, file_path: str, session_id: str, full_model: PdfDocumentMirror
    ) -> PdfSession:
        """从占位 model 创建 session(open 后立即调用,load 尚未跑)。

        page_infos 是占位(rotation=0,has_text_layer=False),逐页真实信息
        由后续 page_loaded 信号流式更新。
        """
        pdf_doc = document_from_mirror(full_model)
        return PdfSession(
            file_path=file_path, session_id=session_id, pdf_document=pdf_doc
        )

    def _apply_page_loaded(
        self, session: PdfSession, page_index: int, page_mirror: object
    ) -> None:
        """把单页 load 结果 apply 到 session model(就地更新该页 PageInfo)。"""
        if not isinstance(page_mirror, dict):
            return
        # page_mirror 是 ProgressEvent.page_payload 的 JSON object。
        mirror = PdfPageInfoMirror.from_payload(page_mirror)
        if 0 <= page_index < len(session.pdf_document.pages):
            session.pdf_document.pages[page_index] = page_from_mirror(mirror)
            session.loaded_pages.add(page_index)

    def _on_doc_opened(
        self, file_path: str, session_id: str, full_model: object
    ) -> None:
        """PdfIpcOpenWorker 阶段 1 回调:open 完成,立即创建占位 session。

        此时 model 是占位(页数已有,但 rotation/has_text_layer 是默认值),
        逐页真实信息由后续 page_loaded 信号流式填充。
        """
        full_model = coerce_document_mirror(full_model)
        session = self._make_session(file_path, session_id, full_model)
        self._sessions[file_path] = session

        prev_active = self._active_path
        self.session_added.emit(file_path)

        # 第一个成功打开的新文件成为 active(UI 立刻显示页数 + 占位缩略图)
        if prev_active is None:
            self._active_path = file_path
            self.active_changed.emit(file_path)

    def _on_page_loaded(
        self, file_path: str, page_index: int, page_mirror: object
    ) -> None:
        """PdfIpcOpenWorker 阶段 2 回调:单页文字层检测完成,流式更新 UI。"""
        session = self._sessions.get(file_path)
        if session is None:
            return
        self._apply_page_loaded(session, page_index, page_mirror)
        total = session.pdf_document.page_count
        loaded = len(session.loaded_pages)
        self.page_loaded.emit(file_path, page_index)
        self.load_progress.emit(file_path, loaded, total)

    def _on_load_progress(self, file_path: str, current: int, total: int) -> None:
        """PdfIpcOpenWorker load 进度(批量文件场景)。"""
        self.load_progress.emit(file_path, current, total)

    def _on_open_failed(self, file_path: str, error: str) -> None:
        logger.warning("异步打开失败 %s: %s", file_path, error)
        partial = self._sessions.pop(file_path, None)
        if partial is not None:
            self.session_removed.emit(file_path)
            if self._active_path == file_path:
                self._active_path = next(reversed(self._sessions), None)
                self.active_changed.emit(self._active_path or "")
        self.open_failed.emit(file_path, error)

    def _on_open_all_done(self) -> None:
        # 所有文件 load 完成后,逐个发 load_done
        for path in list(self._sessions.keys()):
            self.load_done.emit(path)
        self.open_done.emit()

    def _is_current_open(self, worker: PdfIpcOpenWorker, generation: int) -> bool:
        return (
            not self._shutting_down
            and worker is self._open_worker
            and generation == self._open_generation
        )

    def _on_doc_opened_guarded(self, path, sid, model, worker, generation) -> None:
        if self._is_current_open(worker, generation):
            self._on_doc_opened(path, sid, model)
        else:
            # open 已在旧 worker 内成功，但排队信号到达时 generation 已淘汰。
            # 本地不能接纳该 session，必须异步通知后端回收，避免隐形泄漏。
            self._start_close_worker(sid)

    def _on_page_loaded_guarded(self, path, page, payload, worker, generation) -> None:
        if self._is_current_open(worker, generation):
            self._on_page_loaded(path, page, payload)

    def _on_load_progress_guarded(
        self, path, current, total, worker, generation
    ) -> None:
        if self._is_current_open(worker, generation):
            self._on_load_progress(path, current, total)

    def _on_open_failed_guarded(self, path, error, worker, generation) -> None:
        if self._is_current_open(worker, generation):
            self._on_open_failed(path, error)

    def _on_open_progress_guarded(self, current, total, worker, generation) -> None:
        if self._is_current_open(worker, generation):
            self.open_progress.emit(current, total)

    def _on_open_all_done_guarded(self, worker, generation) -> None:
        if self._is_current_open(worker, generation):
            self._on_open_all_done()

    def _release_open_worker(self, worker: PdfIpcOpenWorker) -> None:
        if worker is self._open_worker:
            self._open_worker = None
        self._draining_open_workers.discard(worker)
        worker.deleteLater()

    def _cancel_open_worker(self, *, wait: bool = False) -> None:
        """取消旧 open；始终保留所有权到 finished，GUI 线程不等待。"""
        w = self._open_worker
        if w is not None:
            self._open_worker = None
            self._open_generation += 1
            self._draining_open_workers.add(w)
            opened, incomplete = w.cancel_and_snapshot_sessions()
            started_ids = self._close_started_session_ids
            removed_active = False
            for path, session_id in incomplete.items():
                # 半加载会话由 open worker 在退出前串行 close；登记 ownership
                # 防止迟到 doc_opened GUI 回调再创建重复 close worker。
                started_ids.add(session_id)
                session = self._sessions.get(path)
                if session is not None and session.session_id == session_id:
                    self._sessions.pop(path, None)
                    self.session_removed.emit(path)
                    removed_active |= self._active_path == path
            for path, session_id in opened.items():
                if path in incomplete:
                    continue
                session = self._sessions.get(path)
                if session is None or session.session_id != session_id:
                    # load 已完成但 doc_opened 仍在 GUI 队列中：manager 必须显式
                    # 接管 orphan close，shutdown drain 才能观察到它。
                    self._start_close_worker(session_id)
            if removed_active:
                self._active_path = next(reversed(self._sessions), None)
                self.active_changed.emit(self._active_path or "")

    def switch_session(self, file_path: str) -> bool:
        if file_path not in self._sessions:
            return False
        if self._active_path == file_path:
            return True
        # 切换会话不能与旧会话写操作并发。UI 在保存 continuation 或当前
        # 操作 finished 后重试，不在这里取消后立即切换。
        if self._pdf_write_busy():
            return False
        self._active_path = file_path
        self.active_changed.emit(file_path)
        return True

    def close_session(self, file_path: str) -> bool:
        """从 UI 立即移除会话，并在线程中通知后端关闭。"""
        session = self._sessions.get(file_path)
        if session is None:
            return False
        if self._pdf_write_busy():
            return False
        self._sessions.pop(file_path, None)
        self.session_removed.emit(file_path)

        if self._active_path == file_path:
            self._active_path = None
            if self._sessions:
                last_path = list(self._sessions.keys())[-1]
                self._active_path = last_path
                self.active_changed.emit(last_path)
            else:
                self.active_changed.emit("")
        self._start_close_worker(session.session_id, file_path)
        return True

    def _start_close_worker(self, session_id: str, file_path: str = "") -> None:
        started_ids = getattr(self, "_close_started_session_ids", None)
        if started_ids is None:
            started_ids = self._close_started_session_ids = set()
        if session_id in started_ids:
            return
        worker = PdfIpcCloseWorker(self._ensure_client(), session_id)
        started_ids.add(session_id)
        self._close_workers.add(worker)
        worker.completed.connect(
            lambda _sid, path=file_path: self.close_done.emit(path)
        )
        worker.failed.connect(
            lambda _sid, error, path=file_path: self._on_close_failed(path, error)
        )
        worker.finished.connect(lambda w=worker: self._release_close_worker(w))
        worker.start()

    def _on_close_failed(self, file_path: str, error: str) -> None:
        logger.warning("后端关闭 session 失败: %s", error)
        self.close_failed.emit(file_path, error)

    def _release_close_worker(self, worker: PdfIpcCloseWorker) -> None:
        self._close_workers.discard(worker)
        if not any(
            session.session_id == worker.session_id
            for session in self._sessions.values()
        ):
            self._close_started_session_ids.discard(worker.session_id)
        worker.deleteLater()

    def rerender_thumbnails_async(self, page_indices: list[int]) -> None:
        if page_indices:
            self.thumbnails_invalidated.emit(page_indices)

    # ---- 文字层检测(预览按需)------------------------------------------

    def request_preview(self, page_index: int, *, revision: int = 0) -> int:
        """异步渲染预览并按需检测文字层，返回本次 generation。"""
        session = self.active_session
        if session is None:
            return 0
        page = session.pdf_document.get_page(page_index)
        if page is None:
            return 0
        self._preview_generation = max(self._preview_generation + 1, revision)
        generation = self._preview_generation
        old = self._preview_worker
        if old is not None:
            self._preview_worker = None
            self._draining_preview_workers.add(old)
            old.cancel()
        worker = PdfIpcPreviewWorker(
            self._ensure_client(),
            session.session_id,
            page_index,
            generation,
            bool(page.has_text_layer and not page.text_layers),
        )
        worker.completed.connect(
            lambda sid, idx, gen, png, layers, w=worker: self._on_preview_completed(
                sid, idx, gen, png, layers, w
            )
        )
        worker.failed.connect(
            lambda sid, idx, gen, error, w=worker: self._on_preview_failed(
                sid, idx, gen, error, w
            )
        )
        worker.finished.connect(lambda w=worker: self._release_preview_worker(w))
        self._preview_worker = worker
        worker.start()
        return generation

    def _is_current_preview(self, worker, generation: int) -> bool:
        return (
            not self._shutting_down
            and worker is self._preview_worker
            and generation == self._preview_generation
        )

    def _on_preview_completed(
        self, session_id, page_index, generation, png, layers, worker
    ) -> None:
        if not self._is_current_preview(worker, generation):
            return
        file_path = self._path_for_session_id(session_id)
        if file_path is None:
            return
        if layers is not None:
            infos = [text_layer_from_mirror(item) for item in layers]
            session = self._sessions[file_path]
            page = session.pdf_document.get_page(page_index)
            if page is not None:
                page.text_layers = infos
                page.has_text_layer = bool(infos)
        self.preview_ready.emit(file_path, page_index, generation, png)

    def _on_preview_failed(
        self, session_id, page_index, generation, error, worker
    ) -> None:
        if not self._is_current_preview(worker, generation):
            return
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.preview_failed.emit(file_path, page_index, generation, error)

    def _release_preview_worker(self, worker: PdfIpcPreviewWorker) -> None:
        if worker is self._preview_worker:
            self._preview_worker = None
        self._draining_preview_workers.discard(worker)
        worker.deleteLater()

    def cancel_preview(self) -> None:
        worker = self._preview_worker
        if worker is None:
            return
        self._preview_worker = None
        self._preview_generation += 1
        self._draining_preview_workers.add(worker)
        worker.cancel()

    # ---- 变更操作(异步,通过 IPC mutate worker)---------------------------

    def _start_mutate(self, op: str, params: dict[str, Any]) -> bool:
        """启动通用变更 worker；已有写任务时拒绝重入。"""
        session = self.active_session
        if session is None or self._shutting_down:
            return False
        if self._pdf_write_busy():
            return False
        # 递增 task generation，使旧 runner 的迟到信号被 done 槽丢弃
        self._task_generation += 1
        current_task_id = self._task_generation
        worker = PdfIpcMutateWorker(
            self._ensure_client(), session.session_id, op, params
        )
        worker._task_id = current_task_id  # type: ignore[attr-defined]
        worker.progress.connect(
            lambda sid, current, total, w=worker, tid=current_task_id: (
                self._on_mutate_progress(sid, current, total, worker=w, task_id=tid)
            )
        )
        worker.page_done.connect(
            lambda sid, page, payload, w=worker, tid=current_task_id: (
                self._on_mutate_page_done(sid, page, payload, worker=w, task_id=tid)
            )
        )
        worker.all_done.connect(
            lambda sid, diff, extra, w=worker, tid=current_task_id: (
                self._on_mutate_all_done(sid, diff, extra, task_id=tid, worker=w)
            )
        )
        worker.failed.connect(
            lambda sid, error, w=worker, tid=current_task_id: self._on_mutate_failed(
                sid, error, task_id=tid, worker=w
            )
        )
        worker.finished.connect(
            lambda w=worker, tid=current_task_id: self._on_mutate_worker_finished(
                w, tid
            )
        )
        self._mutate_worker = worker
        self._mutate_state = "running"
        self._mutate_op = op
        self._mutate_path = session.file_path
        self._mutate_task_id = current_task_id
        self._mutate_terminal_received = False
        self.mutate_state_changed.emit(session.file_path, op, "running")
        worker.start()
        return True

    def _reset_backend_cancel(self, session_id: str, operation: str) -> None:
        """仅供 worker 线程调用的阻塞 IPC 边界。"""
        try:
            self._ensure_client().reset_cancel(session_id)
        except Exception:
            logger.debug("%s reset_cancel 失败（忽略）", operation, exc_info=True)

    def _cancel_mutate_worker(self) -> bool:
        """仅请求取消；引用保留到 QThread.finished，GUI 线程绝不 wait。"""
        w = self._mutate_worker
        if w is None:
            return False
        if self._mutate_state == "running":
            self._mutate_state = "cancelling"
            self.mutate_state_changed.emit(
                self._mutate_path, self._mutate_op, "cancelling"
            )
            w.cancel()
            session_id = getattr(w, "session_id", getattr(w, "_sid", ""))
            if session_id:
                self._request_backend_cancel_async(session_id)
        return True

    def _request_backend_cancel_async(self, session_id: str) -> None:
        worker = PdfIpcCancelWorker(self._ensure_client(), session_id)
        workers = getattr(self, "_control_workers", None)
        if workers is None:
            workers = self._control_workers = set()
        workers.add(worker)
        worker.finished.connect(lambda w=worker: self._release_control_worker(w))
        worker.start()

    def _release_control_worker(self, worker: PdfIpcCancelWorker) -> None:
        self._control_workers.discard(worker)
        worker.deleteLater()

    def save_async(self, path: str | None = None, pdf_settings=None) -> bool:
        """异步保存。pdf_settings 转 dict 传后端。"""
        settings_dict = self._settings_to_dict(pdf_settings)
        return self._start_mutate("save", {"path": path, "pdf_settings": settings_dict})

    def delete_text_layers_async(self, page_indices: list[int]) -> None:
        # 仅改内存模型（后端 s.doc / s.pdf_document + is_modified=True），
        # 不写磁盘——磁盘文件仍保留旧文字层，直到显式 save_async。故此处
        # 无需 invalidate sidecar：sidecar 追踪的是「磁盘上哪些页已落盘 OCR
        # 文字层」，而磁盘状态未被本操作改变。用户删除文字层后崩溃（未保存）
        # 时，编辑丢失是既有的「未保存改动随崩溃丢失」行为（与本特性无关），
        # sidecar 仍准确反映磁盘真实状态（旧层仍在），续传跳过该页是正确的。
        self._start_mutate("delete_text_layers", {"pages": page_indices})

    def rotate_pages_async(self, page_indices: list[int], angle: int) -> None:
        self._start_mutate("rotate", {"pages": page_indices, "angle": angle})

    def delete_pages_async(self, page_indices: list[int]) -> None:
        self._start_mutate("delete_pages", {"pages": page_indices})

    def insert_blank_async(
        self, after_index: int, width: float = 612.0, height: float = 792.0
    ) -> None:
        self._start_mutate(
            "insert_blank",
            {"after_index": after_index, "width": width, "height": height},
        )

    def insert_from_async(self, source_path: str, after_index: int) -> None:
        self._start_mutate(
            "insert_from", {"source_path": source_path, "after_index": after_index}
        )

    def move_page_async(self, from_index: int, to_index: int) -> None:
        self._start_mutate(
            "move_page", {"from_index": from_index, "to_index": to_index}
        )

    def reorder_async(self, new_order: list[int]) -> None:
        self._start_mutate("reorder", {"new_order": new_order})

    # ---- 摆正(主进程编排:后端渲染 → OCR 方向检测 → 后端旋转)----------

    def auto_deskew_async(self, page_indices: list[int]) -> bool:
        """异步自动摆正。主进程编排三步:
        1. 后端渲染页 → 2. OCR 方向检测 → 3. 后端按角度旋转 + 文字层同步。
        """
        session = self.active_session
        if session is None or not self.is_ocr_ready or self._shutting_down:
            return False
        if self._pdf_write_busy():
            return False
        self._task_generation += 1
        current_task_id = self._task_generation
        self._deskew_pages = list(page_indices)
        self._deskew_corrected: list[int] = []
        self._deskew_cancelled = False
        # 复用 mutate worker 槽位,但用专用 runner(见 _run_deskew)
        from PySide6.QtCore import QThread

        class _DeskewRunner(QThread):
            progress = Signal(str, int, int)  # current, total(用 2*total 兼容阶段)
            page_done = Signal(str, int, bool)
            all_done = Signal(str, object)
            failed = Signal(str, str)

            def __init__(self, mgr, sid, pages):
                super().__init__()
                self._op = "deskew"
                self._mgr = mgr
                self._sid = sid
                self._pages = pages
                self._cancelled = False
                # 复用渲染线程池（与 _OcrRunner 同理：跨批复用 httpx 连接）。
                from concurrent.futures import ThreadPoolExecutor

                self._render_pool = ThreadPoolExecutor(
                    max_workers=mgr._RENDER_CONCURRENCY,
                    thread_name_prefix="deskew-render",
                )

            def cancel(self):
                self._cancelled = True

            def run(self):
                try:
                    self._mgr._reset_backend_cancel(self._sid, "deskew")
                    if self._cancelled:
                        return
                    self._mgr._run_deskew(self, self._sid, self._pages)
                except Exception as e:
                    self.failed.emit(self._sid, str(e))
                finally:
                    self._render_pool.shutdown(wait=True)

        self._mutate_worker = _DeskewRunner(self, session.session_id, page_indices)  # type: ignore[assignment]
        self._mutate_worker._task_id = current_task_id  # type: ignore[attr-defined]
        # 与 OCR/mutate 一致：runner 信号携带 session_id，须经 _path_for_session_id
        # 翻译成 file_path 再转发给 UI（UI 处理器按 file_path 匹配活跃会话）。
        # 否则 session_id（uuid hex 串）永远 != file_path，UI 处理器全部 early-return，
        # 进度条停滞、完成汇总（"已摆正 N 页"）永不弹出。
        worker = self._mutate_worker
        worker.progress.connect(  # type: ignore[attr-defined]
            lambda sid, current, total, w=worker, tid=current_task_id: (
                self._on_deskew_progress_signal(
                    sid, current, total, worker=w, task_id=tid
                )
            )
        )
        worker.page_done.connect(  # type: ignore[attr-defined]
            lambda sid, page, corrected, w=worker, tid=current_task_id: (
                self._on_deskew_page_done_signal(
                    sid, page, corrected, worker=w, task_id=tid
                )
            )
        )
        worker.all_done.connect(  # type: ignore[attr-defined]
            lambda sid, summary, w=worker, tid=current_task_id: (
                self._on_deskew_all_done(sid, summary, worker=w, task_id=tid)
            )
        )
        worker.failed.connect(  # type: ignore[attr-defined]
            lambda sid, error, w=worker, tid=current_task_id: (
                self._on_deskew_failed_signal(sid, error, worker=w, task_id=tid)
            )
        )
        worker.finished.connect(  # type: ignore[attr-defined]
            lambda w=worker, tid=current_task_id: self._on_mutate_worker_finished(
                w, tid
            )
        )
        self._mutate_state = "running"
        self._mutate_op = "deskew"
        self._mutate_path = session.file_path
        self._mutate_task_id = current_task_id
        self._mutate_terminal_received = False
        self.mutate_state_changed.emit(session.file_path, "deskew", "running")
        self._mutate_worker.start()  # type: ignore[attr-defined]
        return True

    def _run_deskew(self, runner, session_id: str, page_indices: list[int]) -> None:
        """在 deskew runner 线程内:分批 [并发渲染 → 批量 OCR 方向检测 → 逐页旋转]。

        与 _run_ocr 共用批大小/渲染并发/子步进度（性能1/性能2），仅 DPI 与最终
        动作不同：摆正只需 preproc_angle，用 150dpi（OCR 提取文字需 300dpi）；
        识别后逐页按角度旋转（fitz 写不可并发，串行）。

        旧实现逐页串行：渲染 → 主进程 PIL+numpy 解码 → 单页 recognize（N 次 IPC
        往返）→ rotate。重构后复用 OCR 的批量化路径，省去主进程解码与逐页 IPC。
        """
        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(page_indices)
        if total == 0:
            runner.all_done.emit(
                session_id,
                {"corrected": 0, "skipped": 0, "corrected_pages": []},
            )
            return

        client = self._ensure_client()
        dpi = self._DESKEW_DPI
        batch_size = self._OCR_BATCH_SIZE
        substeps = self._OCR_PROGRESS_SUBSTEPS
        progress_total = total * substeps
        progress = 0

        def _emit_progress() -> None:
            runner.progress.emit(session_id, progress, progress_total)

        def _render_page(idx: int) -> bytes | None:
            """渲染单页 dpi → 原始 PNG bytes（不在主进程解码，性能1）。"""
            try:
                return client.render_preview(session_id, idx, dpi=dpi)
            except Exception as e:
                logger.error("摆正渲染页 %d 失败: %s", idx, e)
                return None

        # 方向检测选项：只要角度，关掉去扭曲/文本行方向（更快）
        angle_opts = OCROptions(
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

        for batch_start in range(0, total, batch_size):
            if runner._cancelled:
                break
            batch_pages = page_indices[batch_start : batch_start + batch_size]

            # 阶段1：并发渲染（复用 runner 线程池，结果按 batch_pages 顺序对齐）
            images: list[bytes | None] = [None] * len(batch_pages)
            if not runner._cancelled:
                rendered = runner._render_pool.map(_render_page, batch_pages)
                for i, png in enumerate(rendered):
                    images[i] = png
            page_failed = [png is None for png in images]
            progress += len(batch_pages)  # 渲染子步
            _emit_progress()

            # 阶段2：方向检测 job（跳过渲染失败的页）
            valid_indices = [i for i, png in enumerate(images) if png is not None]
            angles_map: dict[int, int] = {}
            if valid_indices and not runner._cancelled:
                valid_images = [images[i] for i in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._recognize_images_via_job(
                        valid_images,
                        angle_opts,
                        cancel_requested=lambda: runner._cancelled,
                    )
                    for vi, res in zip(valid_indices, batch_results):
                        if res is None:
                            page_failed[vi] = True
                        else:
                            angles_map[vi] = int(getattr(res, "preproc_angle", 0) or 0)
                except Exception as e:
                    logger.error(
                        "摆正批量方向检测失败(批起始页 %d): %s", batch_pages[0], e
                    )
                    for vi in valid_indices:
                        page_failed[vi] = True
            progress += len(valid_indices)  # 识别子步
            _emit_progress()

            # 阶段3：逐页旋转（fitz 写不可并发，串行）
            for i, idx in enumerate(batch_pages):
                if runner._cancelled:
                    progress += 1
                    _emit_progress()
                    continue
                angle = angles_map.get(i, 0) if not page_failed[i] else 0
                correction = (-int(angle)) % 360
                if correction != 0:
                    try:
                        client.rotate(session_id, [idx], correction)
                        self._deskew_corrected.append(idx)
                        runner.page_done.emit(session_id, idx, True)
                    except Exception as e:
                        logger.error("摆正旋转页 %d 失败: %s", idx, e)
                        runner.page_done.emit(session_id, idx, False)
                else:
                    runner.page_done.emit(session_id, idx, False)
                progress += 1  # 旋转子步
                _emit_progress()

        diff = None
        if not runner._cancelled:
            try:
                full_model = client.get_model(session_id)
                diff = PdfModelDiff(full_model=full_model)
            except Exception as exc:
                logger.error("摆正后在线程中刷新 model 失败: %s", exc)
        runner.all_done.emit(
            session_id,
            {
                "corrected": len(self._deskew_corrected),
                "skipped": total - len(self._deskew_corrected),
                "corrected_pages": list(self._deskew_corrected),
                "_diff": diff,
            },
        )

    def _on_deskew_progress_signal(
        self,
        session_id: str,
        current: int,
        total: int,
        *,
        worker=None,
        task_id: int = 0,
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_progress.emit(file_path, current, total)

    def _on_deskew_page_done_signal(
        self,
        session_id: str,
        page_index: int,
        was_corrected: bool,
        *,
        worker=None,
        task_id: int = 0,
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_page_done.emit(file_path, page_index, was_corrected)

    def _on_deskew_all_done(
        self, session_id: str, summary: object, *, worker=None, task_id: int = 0
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        self._mutate_terminal_received = True
        session = self._sessions.get(self._active_path or "")
        if session is not None and isinstance(summary, dict):
            diff = summary.pop("_diff", None)
            if isinstance(diff, PdfModelDiff):
                invalidated = apply_model_diff(session.pdf_document, diff)
                if invalidated:
                    self.thumbnails_invalidated.emit(invalidated)
        # 翻译 session_id → file_path，UI 处理器按 file_path 匹配
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_done.emit(file_path, summary)

    def _on_deskew_failed_signal(
        self, session_id: str, error: str, *, worker=None, task_id: int = 0
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        self._mutate_terminal_received = True
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_failed.emit(file_path, error)

    def cancel_deskew(self) -> None:
        self._cancel_mutate_worker()

    # ---- mutate worker 信号处理 -----------------------------------------

    def _is_current_mutate(
        self, worker=None, task_id: int = 0, *, allow_cancelling: bool = True
    ) -> bool:
        if getattr(self, "_shutting_down", False):
            return False
        current = getattr(self, "_mutate_worker", None)
        if worker is not None and worker is not current:
            return False
        generation = getattr(
            self, "_mutate_task_id", getattr(self, "_task_generation", 0)
        )
        if task_id and generation and task_id != generation:
            return False
        state = getattr(self, "_mutate_state", "running")
        return allow_cancelling or state == "running"

    def _on_mutate_progress(
        self,
        session_id: str,
        current: int,
        total: int,
        *,
        worker=None,
        task_id: int = 0,
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        # 找到 session_id 对应的 file_path
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_progress.emit(file_path, current, total)

    def _on_mutate_page_done(
        self,
        session_id: str,
        page_index: int,
        payload: object,
        *,
        worker=None,
        task_id: int = 0,
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_done.emit(file_path, {"page": page_index, "payload": payload})

    def _on_mutate_all_done(
        self,
        session_id: str,
        diff: object,
        extra: object,
        task_id: int = 0,
        *,
        worker=None,
    ) -> None:
        # task_id 默认 0 时从 sender 读取（真实信号连接路径）
        if task_id == 0:
            sender = self.sender()
            if sender is not None and hasattr(sender, "_task_id"):
                task_id = sender._task_id  # type: ignore[attr-defined]
        # 只接受当前代的信号，丢弃旧任务的迟到信号
        if task_id != 0 and task_id != self._task_generation:
            logger.debug(
                f"忽略旧任务 task_id={task_id} 的迟到 mutate all_done（当前代={self._task_generation}）"
            )
            return
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        self._mutate_terminal_received = True
        file_path = self._path_for_session_id(session_id)
        if file_path is None:
            return
        session = self._sessions[file_path]
        assert isinstance(diff, PdfModelDiff)
        invalidated = apply_model_diff(session.pdf_document, diff)
        if invalidated:
            self.thumbnails_invalidated.emit(invalidated)

        extra_dict = dict(extra) if isinstance(extra, Mapping) else {}
        # 按操作类型转发专用信号
        if "residual_pages" in extra_dict:
            self.delete_layer_done.emit(file_path, extra_dict["residual_pages"])
        elif "path" in extra_dict:
            self.save_done.emit(file_path)
        op = getattr(worker, "_op", self._mutate_op)
        params = getattr(worker, "_params", {})
        self.mutate_done.emit(
            file_path,
            {
                "diff_applied": True,
                "extra": extra_dict,
                "op": op,
                "page": params.get("page"),
                "revision": params.get("revision", 0),
            },
        )

    def _on_mutate_failed(
        self,
        session_id: str,
        error: str,
        task_id: int = 0,
        *,
        worker=None,
    ) -> None:
        if not self._is_current_mutate(worker, task_id, allow_cancelling=False):
            return
        self._mutate_terminal_received = True
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_failed.emit(file_path, error)

    def _on_mutate_worker_finished(self, worker, task_id: int) -> None:
        """仅在 QThread.finished 后释放当前写 worker，并发布最终状态。"""
        if worker is not getattr(self, "_mutate_worker", None):
            worker.deleteLater()
            return
        if task_id != getattr(self, "_mutate_task_id", task_id):
            worker.deleteLater()
            return

        state = "cancelled" if self._mutate_state == "cancelling" else "completed"
        file_path = self._mutate_path
        op = self._mutate_op
        self._mutate_worker = None
        self._mutate_state = state
        self._mutate_op = ""
        self._mutate_path = ""
        self._mutate_terminal_received = False
        worker.deleteLater()
        self.mutate_state_changed.emit(file_path, op, state)

    def _path_for_session_id(self, session_id: str) -> str | None:
        for path, session in self._sessions.items():
            if session.session_id == session_id:
                return path
        return None

    # ---- 文字块编辑(双击改字,仅内存模型)-------------------------------
    # 注：以下块编辑只改内存模型（后端规范模型 + 本地 mirror），不写磁盘，
    # 故不影响 sidecar（sidecar 追踪磁盘落盘状态）。崩溃丢失未保存的块编辑
    # 是既有行为，与本特性无关。

    def update_page_block_text(
        self, page_index: int, block_index: int, new_text: str
    ) -> bool:
        """异步更新某页某块文字；revision 用于淘汰编辑前的预览。"""
        self._edit_revision += 1
        self._preview_generation = max(
            self._preview_generation + 1, self._edit_revision
        )
        return self._start_mutate(
            "update_block_text",
            {
                "page": page_index,
                "block_index": block_index,
                "new_text": new_text,
                "revision": self._edit_revision,
            },
        )

    def update_page_block_text_async(
        self, page_index: int, block_index: int, new_text: str
    ) -> bool:
        return self.update_page_block_text(page_index, block_index, new_text)

    def close_session_async(self, file_path: str) -> bool:
        return self.close_session(file_path)

    # ---- OCR(主进程编排:后端渲染 → 主进程 OCR → 后端写文字层)--------

    def start_ocr(
        self,
        page_indices: list[int],
        ocr_options: OCROptions | None = None,
        pdf_settings: PdfGlobalSettings | None = None,
        overwrite: bool = False,
        *,
        _preflight_complete: bool = False,
    ) -> bool:
        if pdf_settings is None:
            pdf_settings = PdfGlobalSettings()
        session = self.active_session
        if session is None or not self.is_ocr_ready:
            return False

        # OCR 会写入文字层，必须与通用 mutate 共用独占写门。
        if (
            getattr(self, "_shutting_down", False)
            or getattr(self, "_mutate_worker", None) is not None
            or getattr(self, "_ocr_worker", None) is not None
            or getattr(self, "_preflight_worker", None) is not None
            or bool(getattr(self, "_ocr_running", False))
            or bool(getattr(self, "_control_workers", set()))
        ):
            return False

        if not _preflight_complete and self._is_mineru_first_use(ocr_options):
            return self._start_mineru_preflight(
                session.file_path,
                list(page_indices),
                ocr_options,
                pdf_settings,
                overwrite,
            )

        self._pdf_settings = pdf_settings
        self._overwrite_text_layer = overwrite
        session.reset_ocr_stats()
        self._ocr_running = True
        self._ocr_cancelled = False
        self._ocr_state = "running"
        # 清掉可能残留的后端 cancel 标志：上一次取消（OCR/mutate）置位的
        # cancel_event 若不清，会让本次 OCR 写层时后端 add_text_layer_batch
        # 立即停在页边界（协作式取消语义）。reset_cancel 原本全代码库无调用点，
        # 属遗漏。
        # 断点续传：读取 sidecar，过滤掉已增量落盘的页（崩溃恢复）。
        # overwrite=True 时不过滤（用户明确要求重写）。
        if not overwrite and session.file_path:
            try:
                pending = ocr_sidecar.restore_pending_pages(session.file_path)
                if pending:
                    already = set(pending.keys())
                    page_indices = [p for p in page_indices if p not in already]
                    if not page_indices:
                        logger.info("start_ocr: 所有请求页已落盘（sidecar），跳过 OCR")
                        self._ocr_running = False
                        # 调用方（PdfTab）在 start_ocr 之前已 _begin_ocr_ui：进度条
                        # 已显示、按钮已禁用、格子已置 processing。这里提前 return 不
                        # 构造 runner，故 all_done 永不发出，ocr_done 不会触发，
                        # _on_ocr_finished 不会复位 UI → 用户卡在 0% 进度条 + 蓝格子。
                        # 镜像 _on_ocr_all_done_signal 的收尾：发 ocr_stats_ready +
                        # ocr_done（0 成功 0 失败：无事可做），让 UI 正常复位。
                        stats = session.ocr_stats
                        self.ocr_stats_ready.emit(
                            session.file_path, stats["written"], stats["skipped"]
                        )
                        self.ocr_done.emit(session.file_path, 0, 0)
                        self._ocr_state = "completed"
                        return True
                    logger.info(
                        "start_ocr: sidecar 续传，跳过已落盘页 %s",
                        sorted(already),
                    )
            except Exception:
                logger.debug("start_ocr: sidecar 读取失败，全量 OCR", exc_info=True)
        # 递增 task generation，使旧 runner 的迟到信号被 done 槽丢弃
        self._task_generation += 1
        current_task_id = self._task_generation

        # 后台线程编排 OCR 流程
        from PySide6.QtCore import QThread

        ocr_options_ref = ocr_options
        settings_dict = self._settings_to_dict(pdf_settings)

        class _OcrRunner(QThread):
            page_done = Signal(str, int, object)
            progress = Signal(str, int, int)
            all_done = Signal(str, int, int, int)  # session_id, success, fail, task_id
            failed = Signal(str, str)

            def __init__(self, mgr, sid, pages, opts, sdict, overwrite_, task_id):
                super().__init__()
                self._mgr = mgr
                self._client = mgr._ensure_client()
                self._sid = sid
                self._pages = pages
                self._opts = opts
                self._sdict = sdict
                self._overwrite = overwrite_
                self._task_id = task_id
                self._cancelled = False
                self._success = 0
                self._fail = 0
                # runner 生命周期内复用一个渲染线程池：跨批次复用同一组工作
                # 线程，从而复用 PdfBackendClient 按线程 ident 缓存的 httpx
                # Client（每线程 1 个 Client，4 线程跨 N 批始终命中同一组连接
                # 池），避免每批 16 页都重建线程池 + 新建 TCP 连接。
                from concurrent.futures import ThreadPoolExecutor

                self._render_pool = ThreadPoolExecutor(
                    max_workers=mgr._RENDER_CONCURRENCY,
                    thread_name_prefix="ocr-render",
                )

            def cancel(self):
                self._cancelled = True

            def run(self):
                try:
                    self._mgr._reset_backend_cancel(self._sid, "OCR")
                    if self._cancelled:
                        self.all_done.emit(
                            self._sid, self._success, self._fail, self._task_id
                        )
                        return
                    self._mgr._run_ocr(
                        self,
                        self._sid,
                        self._pages,
                        self._opts,
                        self._sdict,
                        self._overwrite,
                    )
                except Exception as e:
                    # _run_ocr 末尾已对已知失败点做了 try/except，但末尾的
                    # get_model/document projection 在大文件场景仍可能抛非
                    # PdfBackendError（pydantic.ValidationError / MemoryError /
                    # httpx 传输错误）。此前此处无 except，异常逃逸 → QThread
                    # 静默死亡 → all_done/failed 都不发 → UI 永久卡在「OCR 进行中」。
                    # 与 _DeskewRunner.run() 对齐：捕获后发 failed，让槽重置 UI。
                    logger.exception("_run_ocr 未捕获异常，发 failed 信号: %s", e)
                    self.failed.emit(self._sid, str(e))
                finally:
                    # runner 退出即关闭线程池，释放 4 个工作线程及其 httpx Client。
                    logger.info("[OCR] _render_pool.shutdown(wait=True) 前")
                    self._render_pool.shutdown(wait=True)
                    logger.info("[OCR] _render_pool.shutdown(wait=True) 后")

        self._ocr_worker = _OcrRunner(
            self,
            session.session_id,
            page_indices,
            ocr_options_ref,
            settings_dict,
            overwrite,
            current_task_id,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done_signal)
        self._ocr_worker.progress.connect(self._on_ocr_progress_signal)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done_signal)
        # failed 信号此前只连了一个记日志的 lambda，不清状态/不发 ocr_done，UI 永久卡死。
        # 改连 _on_ocr_failed_signal：重置 _ocr_running/_ocr_worker 并发 ocr_done
        # 让 PdfTab._on_ocr_finished 复位 UI（隐进度条、启用按钮）。
        self._ocr_worker.failed.connect(self._on_ocr_failed_signal)
        # QThread 生命周期安全：finished 信号在 run() 完整返回后才发出（晚于
        # all_done——all_done 是 run() 末尾发的，之后还要跑 finally 里的
        # _render_pool.shutdown）。这里在 finished 时才清 _ocr_worker 引用并
        # deleteLater，避免在 run() 仍在 finally 中（thread 仍活）时丢弃 Python
        # 引用→GC 在活线程上销毁 QThread→Qt 原生崩（0xC0000409）。
        self._ocr_worker.finished.connect(self._on_ocr_worker_finished)
        self._ocr_worker.start()
        return True

    # 三层批关系（性能2）：
    #   页批(此处 16) ≥ 传输批(SHM 一条消息装下的页数) ≥ 计算批(GPU predict)。
    #   计算批 = text_recognition_batch_size=8（pipeline_ocr.py，GPU）；
    #   传输批同时受 SHM/编码字节和 64MP 像素预算限制；A4 300 DPI 约
    #   8.7MP/页，因此一个 16 页外批通常被拆成约 7+7+2。这里的 16 主要
    #   控制渲染预取与内存上限，不代表 16 页同时在 GPU 上推理。
    _OCR_BATCH_SIZE = 16
    # 渲染并发线程数。后端 fitz 栅格化由 fitz_lock 串行化，但 PIL/PNG 编码 +
    # HTTP 往返可并行，N 并发可掩盖单页往返延迟。httpx Client 按线程独立(见
    # PdfBackendClient._ensure_started)，故可安全并发调用 render_preview。
    _RENDER_CONCURRENCY = 4
    # 进度子步数：每页拆成 渲染/识别/写层 3 个子步，让进度条在整批渲染/识别
    # 期间也能推进（而非只在写层时跳变），避免长时间静止被误判为卡死。
    # UI（PdfTab._begin_ocr_ui）的进度条范围须用 同样的子步数。
    _OCR_PROGRESS_SUBSTEPS = 3

    def _get_ocr_batch_budget(self) -> BatchBudget:
        override = getattr(self, "_ocr_batch_budget_override", None)
        if isinstance(override, BatchBudget):
            return override
        default = BatchBudget.ocr_default()
        # 保留测试/低资源部署对页批上限的实例级覆盖，同时让字节与像素预算
        # 始终来自集中 Constants。
        return BatchBudget(
            max_items=int(getattr(self, "_OCR_BATCH_SIZE", default.max_items)),
            max_encoded_bytes=default.max_encoded_bytes,
            max_pixels=default.max_pixels,
        )

    # ---- 自动摆正(与 OCR 共用批/并发/子步，仅 DPI 与动作不同)-----------
    # 摆正只需方向检测，150dpi 足够（OCR 需 300dpi 提取文字）；低 DPI 渲染更快、
    # PNG 更小、传输更省。批大小/并发/子步复用 OCR 的常量，保证两路径行为一致，
    # 也让进度模型统一（每页 渲染/识别/旋转 3 子步）。
    _DESKEW_DPI = 150

    def _run_ocr(
        self,
        runner,
        session_id: str,
        pages: list[int],
        ocr_options: OCROptions | None,
        settings_dict: dict,
        overwrite: bool,
    ) -> None:
        """在 OCR runner 线程内执行带一批预取的渲染/OCR/写层流水线。

        - 渲染:线程池并发调 render_preview(后端 fitz_lock 串行化栅格化，
          PNG 编码并行)，结果按页序对齐；返回原始 PNG bytes，不在主进程解码
          （由 worker 子进程解码一次，避免 PNG 双重编解码，性能1）。
        - 识别:每个 transport chunk 提交一个逻辑 job；supervisor 负责计算微批。
        - 流水:当前批 OCR 时预取下一批渲染，重叠 PDF 栅格/PNG 与 GPU 计算。
        - 写层:整批 add_text_layer_batch，共享字体并一次增量落盘。
        """
        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(pages)
        success = 0
        fail = 0
        done = 0  # 已写层页数(跨批次累计，用于 page_done 与最终统计)
        opts = ocr_options if ocr_options is not None else OCROptions()
        pdf_settings = PdfGlobalSettings.from_dict(settings_dict or {})
        batch_budget = self._get_ocr_batch_budget()
        batch_size = batch_budget.max_items
        client = self._ensure_client()
        # 进度按子步计：每页 渲染/识别/写层 各 1 步，total_steps = 页数 × 子步数。
        # 这样整批渲染/识别完成后进度也会推进，避免长时间静止被误判为卡死。
        substeps = self._OCR_PROGRESS_SUBSTEPS
        progress_total = total * substeps
        progress = 0
        all_write_batches_persisted = True
        unpersisted_pages: dict[int, int] = {}

        def _emit_progress() -> None:
            runner.progress.emit(session_id, progress, progress_total)

        def _render_page(idx: int) -> bytes | None:
            """按 PDF 设置渲染单页 → 原始 PNG bytes。

            不在主进程解码为 ndarray：job upload 对 bytes 输入原样透传，
            由 supervisor worker 的 _to_ndarray
            解码一次即可。省去主进程的 PNG 解码 + 重新 PNG 编码（性能1）。
            """
            try:
                dpi = pdf_settings.render_dpi
                page_info = session.pdf_document.get_page(idx)
                if page_info is not None:
                    x0, y0, x1, y1 = page_info.rect
                    width = abs(x1 - x0)
                    height = abs(y1 - y0)
                    if width > 0 and height > 0:
                        dpi = pdf_settings.adjust_dpi(width, height)
                return client.render_preview(session_id, idx, dpi=dpi)
            except Exception as e:
                logger.error("渲染页 %d 失败: %s", idx, e)
                return None

        page_batches = [
            pages[start : start + batch_size] for start in range(0, total, batch_size)
        ]
        render_iter = None
        for batch_number, batch_pages in enumerate(page_batches):
            if runner._cancelled:
                break

            import time as _time

            _batch_start = _time.monotonic()
            _stage_render_start = _batch_start

            # 阶段1：并发渲染(线程池，结果按 batch_pages 顺序对齐)
            images: list[bytes | None] = [None] * len(batch_pages)
            if not runner._cancelled:
                # 复用 runner 生命周期内的线程池（_OcrRunner.__init__ 创建），
                # 不再每批新建/销毁。除首批外，render_iter 已在上一批 OCR
                # 开始前提交，消费时多数页面已经完成渲染。
                if render_iter is None:
                    render_iter = runner._render_pool.map(_render_page, batch_pages)
                for i, arr in enumerate(render_iter):
                    images[i] = arr
                render_iter = None
            page_failed = [arr is None for arr in images]
            # 渲染子步进度：本批每页 +1（含渲染失败的页，它们仍“处理完”了渲染阶段）
            progress += len(batch_pages)
            _emit_progress()
            _render_elapsed = _time.monotonic() - _stage_render_start

            # 提前提交下一批渲染。ThreadPoolExecutor.map 会立即排队所有任务，
            # iterator 留到下一轮再消费；当前线程随即进入 WorkerHost 批量 OCR，
            # 从而让 PDF 栅格/PNG 编码与 OCR 子进程/GPU 计算重叠。仅预取一批，
            # 把额外峰值内存限制在最多 2×_OCR_BATCH_SIZE 页。
            if not runner._cancelled and batch_number + 1 < len(page_batches):
                render_iter = runner._render_pool.map(
                    _render_page, page_batches[batch_number + 1]
                )

            # 阶段2：批量识别(单次 predict，跳过渲染失败的页)
            _stage_ocr_start = _time.monotonic()
            valid_indices = [i for i, img in enumerate(images) if img is not None]
            results_map: dict[int, Any] = {}
            if valid_indices and not runner._cancelled:
                transfer_entries = [
                    BatchEntry(
                        value=i,
                        encoded_bytes=len(images[i]),  # type: ignore[arg-type]
                        pixels=image_pixel_count(images[i]),  # type: ignore[arg-type]
                    )
                    for i in valid_indices
                ]
                transfer_batches = partition_batches(transfer_entries, batch_budget)
                for transfer_index, chunk in enumerate(transfer_batches):
                    transfer_indices = chunk.values
                    valid_images = [images[i] for i in transfer_indices]
                    logger.info(
                        "[OCR] 提交传输批次",
                        extra={
                            "batch": {
                                "render_index": batch_number,
                                "transfer_index": transfer_index,
                                "items": len(transfer_indices),
                                "encoded_bytes": chunk.encoded_bytes,
                                "pixels": chunk.pixels,
                                "oversized_single": chunk.oversized_single,
                            }
                        },
                    )
                    try:
                        batch_results = self._recognize_images_via_job(
                            valid_images,
                            opts,
                            cancel_requested=lambda: runner._cancelled,
                        )
                        for vi, res in zip(transfer_indices, batch_results):
                            if res is None:
                                page_failed[vi] = True
                            else:
                                results_map[vi] = res
                    except Exception as e:
                        logger.error(
                            "批量识别失败(render_batch=%d, transfer_batch=%d): %s",
                            batch_number,
                            transfer_index,
                            e,
                        )
                        for vi in transfer_indices:
                            page_failed[vi] = True
            # 识别子步进度：仅识别成功的页（渲染失败的页不再走识别）
            progress += len(valid_indices)
            _emit_progress()
            _ocr_elapsed = _time.monotonic() - _stage_ocr_start
            _ocr_pages = len(valid_indices)
            if _ocr_pages > 0:
                logger.info(
                    "[OCR] 批 %d (起始页 %d, %d 页) 识别耗时 %.2fs (%.2fs/页)",
                    batch_number,
                    batch_pages[0],
                    _ocr_pages,
                    _ocr_elapsed,
                    _ocr_elapsed / _ocr_pages,
                )

            # 阶段3：批量写层（一次 HTTP，共享聚合子集字体）+ 逐页进度信号。
            # 先收集本批要写层的有效页，一次 add_text_layer_batch 调用让后端聚合
            # 所有页字符解析单一子集字体（避免逐页各解析一份放大体积），写层返回
            # 后再逐页发 page_done 信号，保持 UI 流式反馈不变。
            # 取消/失败/空结果页不进 batch，单独处理。
            _stage_write_start = _time.monotonic()
            write_items: list[dict] = []  # [{page, ocr_result, result_ref, list_idx}]
            for i, idx in enumerate(batch_pages):
                if runner._cancelled or page_failed[i]:
                    continue
                result = results_map.get(i)
                if result is not None and result.text_blocks:
                    write_items.append(
                        {
                            "page": idx,
                            "ocr_result": self._ocr_result_to_dict(result),
                            "_result": result,
                            "_list_idx": i,
                        }
                    )

            write_page_results: dict[int, bool] = {}  # page -> ok
            batch_persisted = False
            batch_write_error: str | None = None
            if write_items and not runner._cancelled:
                try:
                    wire_items = [
                        {
                            key: value
                            for key, value in item.items()
                            if not key.startswith("_")
                        }
                        for item in write_items
                    ]
                    resp = self._ensure_client().add_text_layer_batch(
                        session_id,
                        wire_items,
                        settings_dict,
                        overwrite,
                        save=True,
                    )
                    batch_persisted = bool(
                        (resp.operation_extra or {}).get("saved", False)
                    )
                    for item in write_items:
                        write_page_results[item["page"]] = True
                except Exception as e:
                    logger.error("批量写文字层失败(批起始页 %d): %s", batch_pages[0], e)
                    batch_write_error = str(e)
                    # 整批写层失败：标记这些页失败
                    for item in write_items:
                        write_page_results[item["page"]] = False

            if write_items and not batch_persisted:
                all_write_batches_persisted = False
                unpersisted_pages.update(
                    {
                        item["page"]: int(
                            getattr(item["_result"], "preproc_angle", 0) or 0
                        )
                        for item in write_items
                        if write_page_results.get(item["page"], False)
                    }
                )

            # 把后端写层错误详情通知 UI（此前只记日志，用户看不到原因，
            # 只看到"失败 N 页"无法排查）。取 file_path 翻译 session_id。
            if batch_write_error:
                fp = self._path_for_session_id(session_id)
                if fp:
                    self.ocr_write_error.emit(fp, batch_write_error)

            # 本批 incremental save 成功 → 写 sidecar 标记已落盘页（断点续传）
            # sidecar 是"尽力而为"：写入失败只记日志，不阻断 OCR 主流程。
            if batch_persisted and session.file_path:
                try:
                    angles = {
                        item["page"]: int(
                            getattr(item["_result"], "preproc_angle", 0) or 0
                        )
                        for item in write_items
                        if write_page_results.get(item["page"], False)
                    }
                    saved_pages = list(angles.keys())
                    if saved_pages:
                        ocr_sidecar.mark_pages_saved(
                            session.file_path, saved_pages, angles
                        )
                except Exception:
                    logger.debug("sidecar mark_pages_saved 失败（忽略）", exc_info=True)

            # 逐页发 page_done + 进度信号（保持 UI 流式反馈）
            for i, idx in enumerate(batch_pages):
                if runner._cancelled:
                    done += 1
                    progress += 1
                    _emit_progress()
                    continue
                if page_failed[i]:
                    fail += 1
                    session.add_ocr_stats(0, 1)
                    runner.page_done.emit(session_id, idx, None)
                    done += 1
                    progress += 1
                    _emit_progress()
                    continue
                result = results_map.get(i)
                if (
                    result is not None
                    and result.text_blocks
                    and write_page_results.get(idx, False)
                ):
                    session.add_ocr_stats(len(result.text_blocks), 0)
                    success += 1
                    runner.page_done.emit(session_id, idx, result)
                elif (
                    result is not None
                    and result.text_blocks
                    and not write_page_results.get(idx, False)
                ):
                    # 有结果但写层失败
                    fail += 1
                    runner.page_done.emit(session_id, idx, None)
                else:
                    # 无文本块的空结果页
                    session.add_ocr_stats(0, 1)
                    runner.page_done.emit(session_id, idx, None)
                done += 1
                progress += 1
                _emit_progress()

            _write_elapsed = _time.monotonic() - _stage_write_start
            _batch_total = _time.monotonic() - _batch_start
            logger.info(
                "[OCR] 批 %d 完成：渲染 %.2fs | 识别 %.2fs | 写层 %.2fs | 总计 %.2fs "
                "(%d 页，写层 %d 块)",
                batch_number,
                _render_elapsed,
                _ocr_elapsed,
                _write_elapsed,
                _batch_total,
                len(batch_pages),
                len(write_items),
            )

        # 每批 add_text_layer_batch(save=True) 已经安全增量落盘。全部批次均落盘
        # 时，末尾再次全量重写整份数百页 PDF 只是在合并字体
        # 子集/回收对象，现场耗时 453 秒且不影响正确性，因此直接完成。只有某批
        # 未能持久化时才回退到一次最终保存，保证内存中的文字层不会丢失。
        finalized = all_write_batches_persisted
        if (
            not runner._cancelled
            and success > 0
            and session.file_path
            and not all_write_batches_persisted
        ):
            try:
                logger.info("[OCR] 存在未落盘批次，执行末尾保存")
                runner.progress.emit(session_id, 0, 0)  # 不确定进度（SAVE 态）
                self._ensure_client().save(
                    session_id,
                    None,
                    settings_dict,
                    rewrite_text_layers=False,
                )
                finalized = True
                logger.info("[OCR] 末尾保存完成")
                # 全量保存可能使文件缩小，刷新 sidecar 基线后才能继续校验。
                try:
                    ocr_sidecar.refresh_baseline(session.file_path)
                except Exception:
                    logger.debug("sidecar refresh_baseline 失败（忽略）", exc_info=True)
                if unpersisted_pages:
                    try:
                        ocr_sidecar.mark_pages_saved(
                            session.file_path,
                            list(unpersisted_pages),
                            unpersisted_pages,
                        )
                    except Exception:
                        logger.debug(
                            "sidecar 补记末尾保存页失败（忽略）", exc_info=True
                        )
            except Exception as e:
                logger.error("OCR 末尾保存失败（已落盘批次仍安全）: %s", e)
        elif not runner._cancelled and success > 0:
            logger.info("[OCR] 所有批次已增量落盘，跳过末尾整文档压缩")

        if finalized:
            session.pdf_document.is_modified = False
            session.pdf_document.has_structural_change = False
        if finalized and not runner._cancelled and fail == 0 and session.file_path:
            try:
                ocr_sidecar.mark_completed(session.file_path)
            except Exception:
                logger.debug("sidecar mark_completed 失败（忽略）", exc_info=True)

        # page_done 已逐页同步本地 PdfDocument；这里不再 get_model 全量拉回全部
        # OCR 块，避免大文档再次超过 WorkerHost 8 MiB 控制帧上限。
        logger.info("[OCR] all_done.emit 前")
        runner.all_done.emit(session_id, success, fail, runner._task_id)
        logger.info("[OCR] all_done.emit 后")

    def _on_ocr_page_done_signal(
        self, session_id: str, page_index: int, result: object
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            # 增量落 model：把 result.text_blocks 立即写入该页 PdfPageInfo，
            # 消除预览滞后（此前只在整批结束 get_model 才全量刷新）。
            # result 为 None（失败/空页）时跳过，仅转发信号。
            if result is not None:
                session = self._sessions.get(file_path)
                if session is not None:
                    info = session.pdf_document.get_page(page_index)
                    if info is not None:
                        info.ocr_text_blocks = list(
                            getattr(result, "text_blocks", []) or []
                        )
                        info.ocr_preproc_angle = int(
                            getattr(result, "preproc_angle", 0) or 0
                        )
                        if info.ocr_text_blocks:
                            info.has_text_layer = True
            self.ocr_page_done.emit(file_path, page_index, result)

    def _on_ocr_progress_signal(
        self, session_id: str, current: int, total: int
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.ocr_progress.emit(file_path, current, total)

    def _on_ocr_all_done_signal(
        self, session_id: str, success: int, fail: int, task_id: int = 0
    ) -> None:
        logger.info("[OCR] _on_ocr_all_done_signal 进入（主线程）")
        # 只接受当前代的信号，丢弃旧任务的迟到信号
        if task_id != 0 and task_id != self._task_generation:
            logger.debug(
                f"忽略旧任务 task_id={task_id} 的迟到 all_done（当前代={self._task_generation}）"
            )
            return
        self._ocr_running = False
        self._ocr_state = (
            "cancelled"
            if getattr(self, "_ocr_state", "running") == "cancelling"
            else "completed"
        )
        # 不在此清 _ocr_worker：all_done 是 worker 的 run() 末尾发的，之后还要
        # 跑 finally 里的 _render_pool.shutdown(wait=True)，run() 尚未返回、
        # QThread 仍活。此时丢弃 Python 引用→GC 销毁活 QThread→Qt 原生崩。
        # _ocr_worker 引用改在 _on_ocr_worker_finished（finished 信号，run()
        # 完整返回后才发）里清。
        file_path = self._path_for_session_id(session_id)
        if file_path:
            session = self._sessions[file_path]
            stats = session.ocr_stats
            logger.info(
                "[OCR] ocr_stats_ready.emit 前 (written=%s, skipped=%s)",
                stats["written"],
                stats["skipped"],
            )
            self.ocr_stats_ready.emit(file_path, stats["written"], stats["skipped"])
            logger.info("[OCR] ocr_done.emit 前 (success=%d, fail=%d)", success, fail)
            self.ocr_done.emit(file_path, success, fail)
            logger.info("[OCR] _on_ocr_all_done_signal 完成")

    def _on_ocr_worker_finished(self) -> None:
        """QThread.finished 槽：run() 完整返回（含 finally 的 shutdown）后才触发。

        此时线程确已结束，丢弃 Python 引用安全。配合下面 deleteLater 让 Qt
        在事件循环里清理 QThread 对象（不再依赖 GC 抢跑）。
        """
        logger.info("[OCR] _on_ocr_worker_finished（QThread 已结束，清引用）")
        w = getattr(self, "_ocr_worker", None)
        if w is not None:
            try:
                w.deleteLater()
            except Exception:
                pass
        self._ocr_worker = None
        if getattr(self, "_ocr_state", "") == "cancelling":
            self._ocr_state = "cancelled"

    def _on_ocr_failed_signal(self, session_id: str, error: str) -> None:
        """OCR runner 未捕获异常时调用：重置内部状态并通知 UI 复位。

        此前 failed 信号只连了一个记日志的 lambda，_ocr_running / _ocr_worker
        不清、ocr_done 不发，UI 永久卡在「OCR 进行中」（进度条不隐、按钮禁用）。
        这里复用 _on_ocr_all_done_signal 的清理逻辑，并以 (0, total) 失败计数
        发 ocr_done，让 PdfTab._on_ocr_finished 隐藏进度条、启用按钮。
        total 取当前会话页数（无会话时退化为 0）。
        """
        logger.error("OCR runner 失败: %s", error)
        self._ocr_running = False
        self._ocr_state = (
            "cancelled"
            if getattr(self, "_ocr_state", "running") == "cancelling"
            else "completed"
        )
        # 不在此清 _ocr_worker（同 _on_ocr_all_done_signal 的理由：failed 是
        # run() 里发的，之后还有 finally，QThread 仍活）。引用在 finished 槽清。
        file_path = self._path_for_session_id(session_id)
        if file_path:
            session = self._sessions.get(file_path)
            total = len(session.pdf_document.pages) if session else 0
            self.ocr_done.emit(file_path, 0, total)

    def cancel_ocr(self) -> None:
        self._cancel_ocr()

    def _cancel_ocr(self) -> None:
        self._ocr_cancelled = True
        preflight = getattr(self, "_preflight_worker", None)
        if preflight is not None:
            request = self._pending_ocr_request
            self._pending_ocr_request = None
            self._preflight_cancel_path = request[0] if request is not None else None
            # asyncio/QThread 的取消请求不是原生终态。保持写门与 UI busy，
            # 直到 QThread.finished；否则下一次 OCR 会撞上仍在下载的 worker。
            self._ocr_running = True
            self._ocr_state = "cancelling"
            preflight.cancel()
        w = getattr(self, "_ocr_worker", None)
        if w is not None and hasattr(w, "cancel"):
            self._ocr_state = "cancelling"
            w.cancel()
            session_id = getattr(w, "_sid", "")
            if session_id:
                self._request_backend_cancel_async(session_id)
            # 请求式取消：引用由 QThread.finished 槽释放，GUI 线程不等待。

    def get_pages_without_text_layer(self, session_id: str) -> list[int]:
        """返回该 session 中所有无文字层的页索引列表。"""
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return [
            p.page_index for p in session.pdf_document.pages if not p.has_text_layer
        ]

    # ---- MinerU 模型准备 ---------------------------------------------

    def _start_mineru_preflight(
        self,
        file_path: str,
        page_indices: list[int],
        ocr_options: object,
        pdf_settings: object,
        overwrite: bool,
    ) -> bool:
        if self._preflight_worker is not None:
            return False
        self._task_generation += 1
        self._preflight_generation = self._task_generation
        generation = self._preflight_generation
        self._preflight_result = None
        self._preflight_cancel_path = None
        self._pending_ocr_request = (
            file_path,
            page_indices,
            ocr_options,
            pdf_settings,
            overwrite,
        )
        self._ocr_running = True
        self._ocr_cancelled = False
        self._ocr_state = "preflight"
        worker = MinerUPreflightWorker()
        worker.progress.connect(
            lambda stage, message, w=worker, gen=generation: (
                self._on_preflight_progress(stage, message, w, gen)
            )
        )
        worker.completed.connect(
            lambda ok, message, w=worker, gen=generation: self._on_preflight_completed(
                ok, message, w, gen
            )
        )
        worker.finished.connect(
            lambda w=worker, gen=generation: self._on_preflight_finished(w, gen)
        )
        self._preflight_worker = worker
        self.mineru_models_status.emit(
            "首次使用文档解析，正在下载 MinerU 模型（约数 GB）..."
        )
        worker.start()
        return True

    def _is_current_preflight(self, worker, generation: int) -> bool:
        return (
            worker is self._preflight_worker
            and generation == self._preflight_generation
        )

    def _on_preflight_progress(
        self, stage: str, message: str, worker, generation: int
    ) -> None:
        if (
            self._is_current_preflight(worker, generation)
            and not self._shutting_down
            and self._ocr_state == "preflight"
        ):
            self.mineru_models_status.emit(f"[{stage}] {message}")

    def _on_preflight_completed(
        self, ok: bool, message: str, worker, generation: int
    ) -> None:
        if not self._is_current_preflight(worker, generation):
            return
        if self._shutting_down or worker.is_cancelled or self._ocr_state != "preflight":
            return
        # completed 是业务结果，不是原生线程终态。只暂存；真正继续 OCR、
        # 发布失败或取消均在 finished 槽完成。
        self._preflight_result = (bool(ok), str(message))

    def _on_preflight_finished(self, worker, generation: int) -> None:
        if not self._is_current_preflight(worker, generation):
            worker.deleteLater()
            return

        request = self._pending_ocr_request
        cancel_path = self._preflight_cancel_path
        result = self._preflight_result
        was_cancelled = bool(
            worker.is_cancelled
            or self._ocr_state == "cancelling"
            or self._shutting_down
        )
        self._preflight_worker = None
        self._pending_ocr_request = None
        self._preflight_result = None
        self._preflight_cancel_path = None
        worker.deleteLater()

        if was_cancelled:
            self._ocr_running = False
            self._ocr_state = "cancelled"
            if cancel_path and not self._shutting_down:
                self.ocr_done.emit(cancel_path, 0, 0)
            return

        if request is None:
            self._ocr_running = False
            self._ocr_state = "cancelled"
            return

        file_path, pages, options, settings, overwrite = request
        if result is None or not result[0]:
            message = result[1] if result is not None else "模型准备线程未返回结果"
            self._ocr_running = False
            self._ocr_state = "completed"
            self.mineru_models_status.emit(f"模型下载失败: {message}")
            self.ocr_done.emit(file_path, 0, 1)
            return

        self.mineru_models_status.emit("MinerU 模型准备就绪")
        self._ocr_running = False
        if self._active_path != file_path:
            self._ocr_state = "cancelled"
            self.ocr_done.emit(file_path, 0, 0)
            return
        started = self.start_ocr(
            pages,
            options,
            settings,
            overwrite,
            _preflight_complete=True,
        )
        if not started:
            self._ocr_state = "cancelled"
            self.ocr_done.emit(file_path, 0, 0)

    def _is_mineru_first_use(self, ocr_options: OCROptions | None) -> bool:
        if ocr_options is None:
            return False
        try:
            from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

            if ocr_options.pipeline != OCRPipeline.DOCUMENT_PARSING:
                return False
            from vibeocr.backend.pipeline_status import is_pipeline_ever_succeeded
            from vibeocr.classic.app_paths import get_install_root

            return not is_pipeline_ever_succeeded("MinerU", get_install_root())
        except Exception:
            return False

    # ---- 批量导出 -------------------------------------------------------

    def export_all_modified(self, output_dir: str, cancel_check=None) -> list[str]:
        """同步批量导出所有 modified session(走 IPC save 到目标路径)。

        Args:
            output_dir: 输出目录
            cancel_check: 可选的无参可调用，返回 True 时停止导出后续文件
        """
        exported: list[str] = []
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for file_path, session in self._sessions.items():
            if not session.is_modified:
                continue
            # 逐文件检查取消标志
            if cancel_check and cancel_check():
                logger.info("导出已取消，停止后续文件")
                break
            name = Path(file_path).name
            dest = out / name
            if dest.exists():
                stem = dest.stem
                counter = 1
                while (out / f"{stem}_{counter}{dest.suffix}").exists():
                    counter += 1
                dest = out / f"{stem}_{counter}{dest.suffix}"
            try:
                settings_dict = self._settings_to_dict(self._pdf_settings)
                self._ensure_client().save(
                    session.session_id, path=str(dest), pdf_settings=settings_dict
                )
                exported.append(str(dest))
            except PdfBackendError as e:
                logger.error("导出失败 %s: %s", file_path, e)
        return exported

    def export_all_async(self, output_dir: str) -> None:
        """异步批量导出。保留 PdfExportWorker 接口,但内部走 IPC。

        简化:复用同步 export_all_modified 在后台线程跑。
        """
        from PySide6.QtCore import QThread

        if self._shutting_down or self._pdf_write_busy():
            return
        session_snapshots = tuple(
            (file_path, session.session_id)
            for file_path, session in self.get_modified_sessions()
        )
        if not session_snapshots:
            self.export_done.emit([])
            return
        settings_snapshot = self._settings_to_dict(self._pdf_settings)
        client = self._ensure_client()

        class _ExportRunner(QThread):
            progress = Signal(int, int, str)
            done = Signal(list)
            failed = Signal(str)

            def __init__(self, backend, items, settings, out_dir):
                super().__init__()
                self._backend = backend
                self._items = items
                self._settings = settings
                self._out = out_dir
                self._cancelled = False

            def cancel(self):
                self._cancelled = True

            def run(self):
                try:
                    exported: list[str] = []
                    out = Path(self._out)
                    out.mkdir(parents=True, exist_ok=True)
                    total = len(self._items)
                    for index, (file_path, session_id) in enumerate(
                        self._items, start=1
                    ):
                        if self._cancelled:
                            break
                        dest = out / Path(file_path).name
                        if dest.exists():
                            stem = dest.stem
                            counter = 1
                            while (out / f"{stem}_{counter}{dest.suffix}").exists():
                                counter += 1
                            dest = out / f"{stem}_{counter}{dest.suffix}"
                        try:
                            self._backend.save(
                                session_id,
                                path=str(dest),
                                pdf_settings=self._settings,
                            )
                        except PdfBackendError as exc:
                            # 可预期的单文件后端错误不终止整个批次。
                            logger.error("导出失败 %s: %s", file_path, exc)
                        else:
                            exported.append(str(dest))
                        self.progress.emit(index, total, file_path)
                    self.done.emit(exported)
                except Exception as exc:
                    # ValidationError/MemoryError/本地文件系统错误等也必须形成业务
                    # 终态；否则只有原生 finished，PdfTab 会永久停在导出中。
                    logger.exception("批量导出异常终止")
                    self.failed.emit(str(exc) or type(exc).__name__)

        worker = _ExportRunner(
            client,
            session_snapshots,
            settings_snapshot,
            output_dir,
        )
        worker.done.connect(lambda paths, w=worker: self._on_export_done(paths, w))
        worker.failed.connect(lambda error, w=worker: self._on_export_failed(error, w))
        worker.finished.connect(lambda w=worker: self._on_export_worker_finished(w))
        self._export_result_pending = None
        self._export_error_pending = None
        self._export_worker = worker
        worker.start()

    def _on_export_done(self, exported_paths: list, worker) -> None:
        """业务结果先暂存；QThread.run 尚可能处于返回/析构窗口。"""
        if worker is self._export_worker:
            self._export_result_pending = list(exported_paths)

    def _on_export_failed(self, error: str, worker) -> None:
        """暂存意外失败；等原生 ``finished`` 后再释放写门并通知 UI。"""
        if worker is self._export_worker:
            self._export_error_pending = error

    def _on_export_worker_finished(self, worker) -> None:
        """原生 finished 才释放所有权、允许下一次导出。"""
        if worker is not self._export_worker:
            worker.deleteLater()
            return
        result = self._export_result_pending
        error = self._export_error_pending
        self._export_result_pending = None
        self._export_error_pending = None
        self._export_worker = None
        worker.deleteLater()
        if error is not None and not self._shutting_down:
            self.export_failed.emit(error)
        elif result is not None and not self._shutting_down:
            self.export_done.emit(result)

    # ---- 辅助 -----------------------------------------------------------

    def _settings_to_dict(self, settings) -> dict[str, Any] | None:
        """PdfGlobalSettings → dict(传后端)。"""
        if settings is None:
            return None
        if hasattr(settings, "to_dict"):
            return settings.to_dict()
        if isinstance(settings, dict):
            return settings
        return None

    def _ocr_result_to_dict(self, result) -> dict[str, Any]:
        """OCRResult → dict(传后端 add_text_layer)。

        必须带上 preproc_angle：OCR 预处理旋转了图像时，bbox 在旋转后空间，
        后端 add_text_layer → _denormalize_and_unrotate_bbox 需要该角度把
        bbox 逆变换回页面坐标。此前漏传导致 angle 恒为 0，开启文档方向分类
        时文字层坐标严重偏离（90° 时 X 轴可偏移数百点）。
        """
        return {
            "text_blocks": [
                {
                    "text": b.text,
                    "score": b.score,
                    "bbox": list(b.bbox) if b.bbox else None,
                    "polygon": list(b.polygon) if b.polygon else None,
                    "page_idx": b.page_idx,
                    "is_manually_edited": b.is_manually_edited,
                    "label": b.label,
                    "order": b.order,
                }
                for b in result.text_blocks
            ],
            "preproc_angle": int(getattr(result, "preproc_angle", 0) or 0),
        }

    # ---- cleanup --------------------------------------------------------

    def request_shutdown(self) -> None:
        """只发协作取消请求；不在 GUI 线程等待任何 QThread。"""
        assert QThread.currentThread() is self.thread()
        if self._shutting_down:
            return
        self._shutting_down = True
        self._cancel_mutate_worker()
        self._cancel_ocr()
        self._cancel_open_worker(wait=False)
        preview = self._preview_worker
        if preview is not None:
            self._preview_worker = None
            self._preview_generation += 1
            self._draining_preview_workers.add(preview)
            preview.cancel()
        if self._export_worker is not None:
            cancel = getattr(self._export_worker, "cancel", None)
            if callable(cancel):
                cancel()
        # idle 场景可立即在 owner GUI 线程创建 close worker；有写/打开任务时
        # 由后续 GUI poll 在它们 native-finished 后推进，绝不并发关闭 session。
        self._advance_shutdown_session_closes()

    def _shutdown_operation_workers(self) -> set[QThread]:
        workers = {
            worker
            for worker in (
                self._open_worker,
                self._mutate_worker,
                self._ocr_worker,
                self._preflight_worker,
                self._preview_worker,
                self._export_worker,
            )
            if worker is not None
        }
        workers.update(tuple(self._draining_open_workers))
        workers.update(tuple(self._draining_preview_workers))
        workers.update(tuple(self._control_workers))
        return workers

    def _advance_shutdown_session_closes(self) -> None:
        """GUI owner 状态机：业务 worker 全停后才创建 session close worker。"""
        assert QThread.currentThread() is self.thread()
        if any(
            not worker.isFinished() for worker in self._shutdown_operation_workers()
        ):
            return
        for session in list(self._sessions.values()):
            if session.session_id not in self._close_started_session_ids:
                self._start_close_worker(session.session_id)

    def is_drained(self) -> bool:
        """GUI 线程零等待推进/探测；连续两轮稳定后才确认全部排空。"""
        assert QThread.currentThread() is self.thread()
        if self._shutdown_finalized:
            return True
        if not self._shutting_down:
            return False

        self._advance_shutdown_session_closes()
        workers = self._shutdown_operation_workers() | set(self._close_workers)
        if any(not worker.isFinished() for worker in workers):
            self._shutdown_stable_polls = 0
            return False
        if any(
            session.session_id not in self._close_started_session_ids
            for session in self._sessions.values()
        ):
            self._shutdown_stable_polls = 0
            return False

        # 给 queued doc_opened/open_failed/finished 回调至少一个事件循环周期，
        # 防止检查边界刚加入 orphan close worker。
        self._shutdown_stable_polls += 1
        if self._shutdown_stable_polls < 2:
            return False
        self._sessions.clear()
        self._close_started_session_ids.clear()
        self._active_path = None
        self._shutdown_finalized = True
        return True

    def drain(self, timeout_ms: int) -> bool:
        """兼容独立调用；生产 MainWindow 只使用 GUI ``is_drained`` 轮询。"""
        import time

        from PySide6.QtCore import QCoreApplication

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        if QThread.currentThread() is not self.thread():
            # 非 owner 线程绝不推进/修改 QObject 状态；调用方应改用 GUI poll。
            return False
        while True:
            if self.is_drained():
                return True
            if timeout_ms <= 0 or time.monotonic() >= deadline:
                return False
            QCoreApplication.processEvents()
            # 最多让出一小片，避免兼容入口形成长时间 GUI 硬等待。
            QThread.msleep(5)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """兼容独立调用：请求取消后按单一预算等待。"""
        self.request_shutdown()
        return self.drain(timeout_ms)
