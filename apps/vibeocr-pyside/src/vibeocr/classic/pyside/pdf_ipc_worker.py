"""PySide PDF RPC worker：在线程中调用 supervisor PDF transport。

PDF 模块进程化后,所有 fitz 操作在 supervisor 拥有的 PDF 子进程。主进程
通过 SyncPdfSupervisorClient (vibeocr.classic.pdf_client,httpx 经
supervisor HTTP v2 代理)调用,这些调用是阻塞的,不能在 GUI 线程跑。本
worker 包装常见的长耗时 IPC 操作(批量打开/加载/变更/删除文字层/保存/OCR
写层),把结果转成 Qt 信号。

协作式取消:通过 cancel_event 标志,后端侧也有 cancel_event(POST /cancel)。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)


class PdfIpcPreviewWorker(QThread):
    """后台渲染预览，并按需获取文字层详情。"""

    completed = Signal(str, int, int, object, object)
    failed = Signal(str, int, int, str)

    def __init__(
        self,
        client: Any,
        session_id: str,
        page_index: int,
        generation: int,
        detect_text: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id
        self._page_index = page_index
        self._generation = generation
        self._detect_text = detect_text
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            png = self._client.render_preview(
                self._session_id, self._page_index, dpi=150
            )
            image = QImage.fromData(png, "PNG")
            if image.isNull():
                raise RuntimeError("PDF 预览 PNG 解码失败")
            layers = None
            if self._detect_text and not self._cancelled:
                layers = self._client.detect_text_layers(
                    self._session_id, self._page_index
                ).text_layers
            if not self._cancelled:
                self.completed.emit(
                    self._session_id,
                    self._page_index,
                    self._generation,
                    image,
                    layers,
                )
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(
                    self._session_id,
                    self._page_index,
                    self._generation,
                    str(exc),
                )


class PdfIpcCloseWorker(QThread):
    """后台关闭一个后端 PDF session。"""

    completed = Signal(str)
    failed = Signal(str, str)

    def __init__(self, client: Any, session_id: str, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        try:
            self._client.close_session(self._session_id)
            self.completed.emit(self._session_id)
        except Exception as exc:
            self.failed.emit(self._session_id, str(exc))


class PdfIpcCancelWorker(QThread):
    """后台发送协作取消请求，避免 cancel() 调用者线程执行同步 IPC。"""

    def __init__(self, client: Any, session_id: str, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id

    def run(self) -> None:
        try:
            self._client.cancel(self._session_id)
        except Exception:
            logger.debug("[ipc-cancel] 通知后端取消失败（忽略）", exc_info=True)


class PdfIpcOpenWorker(QThread):
    """批量打开 PDF(后台 IPC open + 流式 load)。

    两阶段渐进展示:
    1. open_session(快,fitz.open + 占位页)→ 立即 emit doc_opened(占位 model)
       主进程收到后立刻创建 session + 显示页数 + 占位缩略图
    2. load_stream(逐页文字层检测)→ 每页 emit page_loaded(page_index + page_mirror)
       主进程逐页染色文字层状态,无需等全部检测完

    Signals:
        doc_opened(file_path, session_id, model_mirror_dict)  open 完成(占位)
        page_loaded(file_path, page_index, page_mirror_dict)  单页 load 完成
        load_progress(file_path, current, total)              load 进度
        open_failed(file_path, error_msg)
        open_progress(current, total)                         批量文件进度
        all_done()
    """

    doc_opened = Signal(str, str, object)  # (file_path, session_id, 占位 full_model)
    page_loaded = Signal(str, int, object)  # (file_path, page_index, page_mirror_dict)
    load_progress = Signal(str, int, int)  # (file_path, current, total)
    open_failed = Signal(str, str)
    open_progress = Signal(int, int)
    all_done = Signal()

    def __init__(
        self,
        client: Any,
        paths: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._paths = paths
        self._cancelled = False
        # worker 线程内先登记、再发 doc_opened。manager 可在 queued GUI 回调
        # 尚未送达时取得后端会话所有权，避免 shutdown 漏掉 orphan session。
        self._opened_sessions: dict[str, str] = {}
        self._incomplete_sessions: dict[str, str] = {}
        self._sessions_lock = threading.Lock()

    def cancel(self) -> None:
        self.cancel_and_snapshot_sessions()

    def cancel_and_snapshot_sessions(
        self,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """原子取消并返回 manager 判定 close ownership 所需的快照。"""
        with self._sessions_lock:
            self._cancelled = True
            return dict(self._opened_sessions), dict(self._incomplete_sessions)

    @property
    def is_cancelled(self) -> bool:
        with self._sessions_lock:
            return self._cancelled

    @property
    def opened_sessions(self) -> dict[str, str]:
        with self._sessions_lock:
            return dict(self._opened_sessions)

    @property
    def incomplete_sessions(self) -> dict[str, str]:
        with self._sessions_lock:
            return dict(self._incomplete_sessions)

    def _cancelled_session_owned_by_worker(self, path: str) -> str | None:
        """若已取消，返回仍由 worker 负责回收的后端 session。"""
        with self._sessions_lock:
            if not self._cancelled:
                return None
            return self._incomplete_sessions.get(path)

    def _complete_load_or_keep_cancel_ownership(self, path: str) -> str | None:
        """原子决定正常移交给 manager，或保留取消回收 ownership。"""
        with self._sessions_lock:
            session_id = self._incomplete_sessions.get(path)
            if self._cancelled:
                return session_id
            self._incomplete_sessions.pop(path, None)
            return None

    def run(self) -> None:
        total = len(self._paths)
        try:
            try:
                # backend 进程启动/健康等待可能阻塞数秒，必须留在 worker 内。
                self._client.start()
            except Exception as exc:
                logger.error("[ipc-open] 启动 PDF 后端失败: %s", exc)
                for n, path in enumerate(self._paths):
                    self.open_failed.emit(path, str(exc))
                    self.open_progress.emit(n + 1, total)
                return

            for n, path in enumerate(self._paths):
                if self.is_cancelled:
                    break
                try:
                    # 阶段 1:open(快)→ 立即 emit 占位 model
                    open_resp = self._client.open_session(path)
                    with self._sessions_lock:
                        self._opened_sessions[path] = open_resp.session_id
                        self._incomplete_sessions[path] = open_resp.session_id
                    cancelled_session_id = self._cancelled_session_owned_by_worker(path)
                    if cancelled_session_id is not None:
                        # open 已成功但请求已失效，在线程内回收后端 session。
                        try:
                            self._client.close_session(cancelled_session_id)
                        except Exception:
                            logger.debug("[ipc-open] 回收取消会话失败", exc_info=True)
                        break
                    self.doc_opened.emit(path, open_resp.session_id, open_resp.model)

                    # 阶段 2:流式 load → 逐页 emit
                    for ev in self._client.load_stream(open_resp.session_id):
                        if self.is_cancelled:
                            break
                        if ev.page_index is not None:
                            self.page_loaded.emit(path, ev.page_index, ev.page_payload)
                        if ev.total > 0:
                            self.load_progress.emit(path, ev.current, ev.total)
                        if ev.message == "done":
                            break
                    cancelled_session_id = self._complete_load_or_keep_cancel_ownership(
                        path
                    )
                    if cancelled_session_id is not None:
                        # doc_opened 可能已被 GUI 接纳；取消的半加载会话不能继续
                        # 留在本地模型或后端。close 归 open worker 自身串行完成，
                        # drain 等待该 worker 即同时覆盖回收。
                        try:
                            self._client.close_session(cancelled_session_id)
                        except Exception:
                            logger.debug("[ipc-open] 回收半加载会话失败", exc_info=True)
                        break
                except Exception as e:
                    logger.error("[ipc-open] 打开 %s 失败: %s", path, e)
                    with self._sessions_lock:
                        incomplete_session_id = self._incomplete_sessions.get(path)
                    if incomplete_session_id is not None:
                        # 阶段 1 已成功、阶段 2 load 失败：与取消路径相同，
                        # worker 在退出前回收后端半会话。
                        try:
                            self._client.close_session(incomplete_session_id)
                        except Exception:
                            logger.debug(
                                "[ipc-open] 回收加载失败会话失败", exc_info=True
                            )
                    self.open_failed.emit(path, str(e))
                self.open_progress.emit(n + 1, total)
        finally:
            self.all_done.emit()


class PdfIpcMutateWorker(QThread):
    """通用变更操作(后台 IPC),支持流式进度。

    Signals:
        progress(session_id, current, total)
        page_done(session_id, page_index, payload)   逐页结果(可选)
        all_done(session_id, diff, extra)
        failed(session_id, error_msg)
    """

    progress = Signal(str, int, int)
    page_done = Signal(str, int, object)
    all_done = Signal(str, object, object)  # (session_id, diff, extra)
    failed = Signal(str, str)

    def __init__(
        self,
        client: Any,
        session_id: str,
        op: str,
        params: dict[str, Any],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id
        self._op = op  # "rotate" / "delete_pages" / "save" / "delete_text_layers" / ...
        self._params = params
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        try:
            # reset_cancel 也是阻塞 IPC，必须和实际写操作一起留在线程内。
            try:
                self._client.reset_cancel(self._session_id)
            except Exception:
                logger.debug("[ipc-mutate] reset_cancel 失败（忽略）", exc_info=True)
            if self._cancelled:
                return
            if self._op == "delete_text_layers":
                # 流式:迭代 ProgressEvent
                pages = self._params.get("pages", [])
                for ev in self._client.delete_text_layers_stream(
                    self._session_id, pages
                ):
                    if self._cancelled:
                        break
                    if ev.page_index is not None:
                        self.page_done.emit(
                            self._session_id, ev.page_index, ev.page_payload
                        )
                    if ev.total > 0:
                        self.progress.emit(self._session_id, ev.current, ev.total)
                # 流结束后取一次 model 拿 diff(删除文字层改变 has_text_layer)
                # 简化:用 get_model 构造 full diff
                from vibeocr.runtime_contracts.pdf import PdfModelDiff

                full_model = self._client.get_model(self._session_id)
                extra = {"residual_pages": []}
                self.all_done.emit(
                    self._session_id,
                    PdfModelDiff(full_model=full_model),
                    extra,
                )
                return

            # 非流式:单次调用
            resp = self._call_op()
            diff = getattr(resp, "diff", None)
            if hasattr(resp, "operation_extra"):
                extra = resp.operation_extra
            else:
                # Transitional fake/legacy adapters used ``extra`` directly.
                extra = getattr(resp, "extra", None)
            # 保存的 path 字段
            if hasattr(resp, "path"):
                extra = {"path": resp.path}
            self.all_done.emit(self._session_id, diff, extra)
        except Exception as e:
            logger.error("[ipc-mutate] %s 失败: %s", self._op, e)
            self.failed.emit(self._session_id, str(e))

    def _call_op(self):
        c = self._client
        sid = self._session_id
        p = self._params
        if self._op == "rotate":
            return c.rotate(sid, p["pages"], p["angle"])
        if self._op == "delete_pages":
            return c.delete_pages(sid, p["pages"])
        if self._op == "insert_blank":
            return c.insert_blank(
                sid, p["after_index"], p.get("width", 612.0), p.get("height", 792.0)
            )
        if self._op == "insert_from":
            return c.insert_from(sid, p["source_path"], p["after_index"])
        if self._op == "move_page":
            return c.move_page(sid, p["from_index"], p["to_index"])
        if self._op == "reorder":
            return c.reorder(sid, p["new_order"])
        if self._op == "save":
            return c.save(sid, p.get("path"), p.get("pdf_settings"))
        if self._op == "add_text_layer":
            return c.add_text_layer(
                sid,
                p["page"],
                p["ocr_result"],
                p.get("pdf_settings"),
                p.get("overwrite", False),
            )
        if self._op == "rewrite_text_layer":
            return c.rewrite_text_layer(
                sid,
                p["page"],
                p["text_blocks"],
                p.get("preproc_angle", 0),
                p.get("pdf_settings"),
            )
        if self._op == "update_block_text":
            return c.update_block_text(sid, p["page"], p["block_index"], p["new_text"])
        raise ValueError(f"未知 op: {self._op}")
