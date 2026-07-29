"""PySide 缩略图 RPC worker：并发取 PNG，主线程构造 QPixmap。

替代原 ThumbnailRenderWorker(持 doc + doc_lock)。进程化后主进程不持 fitz,
缩略图渲染走 IPC:queue 投页索引 → 线程池并发调 client.render_thumbnail(sid, page)
拿 PNG 字节 → emit thumbnail_ready(主线程 loadFromData 构 QPixmap)。

并发模型:单个 QThread.run() 持有一个 ThreadPoolExecutor(max_workers=N),
从 queue 取页索引提交到线程池并发渲染。后端 fitz 栅格化由 per-session
fitz_lock 串行化,PIL 缩放/PNG 编码并行,客户端 HTTP 由每线程独立 httpx
Client 隔离,整体提速首屏与滚动渲染。

QPixmap 线程安全:worker 只回传 PNG bytes,QPixmap 构造在主线程
_on_thumbnail_ready 里完成(AutoConnection 自动排队到主线程)。

generation 校验:请求带 gen,响应带 gen;ThumbnailModel 只在 gen 匹配时入缓存,
丢弃失效后仍在途的旧渲染结果(旋转/删除导致的 ABA 问题)。
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

_STOP = object()

# 缩略图并发渲染线程数。后端 fitz 栅格化已串行化(fitz_lock),瓶颈主要在
# HTTP 往返 + PIL 缩放/PNG 编码,4 并发足以掩盖单页延迟。过高会争 CPU。
_THUMB_CONCURRENCY = 4


class ThumbnailIpcWorker(QThread):
    """缩略图 IPC 渲染 worker(多线程并发)。

    Signals:
        thumbnail_ready(page_index, png_bytes, gen)
            png_bytes 为后端返回的 PNG 字节流,由主线程 loadFromData 构 QPixmap。
    """

    thumbnail_ready = Signal(int, object, int)  # (page_index, png_bytes, gen)

    def __init__(
        self,
        client: Any,
        session_id: str,
        size: int = 160,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id
        self._size = size
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[int] = set()
        self._pending_lock = threading.Lock()
        self._gen_map: dict[int, int] = {}  # page_index → 最新 gen
        self._cancelled = False

    def request(self, page_index: int, gen: int = 0) -> None:
        """请求渲染页。gen 用于失效后丢弃旧结果。"""
        with self._pending_lock:
            if page_index in self._pending:
                # 已在队列:更新 gen(取较大值,确保新 invalidate 生效)
                self._gen_map[page_index] = max(self._gen_map.get(page_index, 0), gen)
                return
            self._pending.add(page_index)
            self._gen_map[page_index] = gen
        self._queue.put(page_index)

    def cancel(self) -> None:
        """取消:设标志 + 投哨兵,worker 主循环退出后等线程池收尾。"""
        self._cancelled = True
        self._queue.put(_STOP)

    def _render_one(self, page_index: int) -> None:
        """线程池 worker:渲染单页 → emit PNG bytes。取消则尽早返回。

        _pending.discard 推迟到 emit 之后,避免 in-flight 期间同页被重新入队
        (重复渲染浪费)。gen 用请求入队时的快照,_on_thumbnail_ready 会校验。
        """
        if self._cancelled:
            with self._pending_lock:
                self._pending.discard(page_index)
            return
        try:
            with self._pending_lock:
                gen = self._gen_map.get(page_index, 0)
            png_bytes = self._client.render_thumbnail(
                self._session_id, page_index, size=self._size
            )
            if not self._cancelled:
                self.thumbnail_ready.emit(page_index, png_bytes, gen)
        except Exception as e:
            logger.error("[thumb-ipc] 渲染页 %d 失败: %s", page_index, e)
        finally:
            with self._pending_lock:
                self._pending.discard(page_index)

    def run(self) -> None:
        """主循环:从队列取页 → 提交线程池并发渲染。哨兵/取消时退出。

        cancel 时用 cancel_futures=True 关闭线程池，取消尚未开始的待处理
        任务，避免等待卡在阻塞 HTTP 调用的 in-flight 任务（配合有界 HTTP 超时）。
        """
        pool = ThreadPoolExecutor(max_workers=_THUMB_CONCURRENCY)
        try:
            while not self._cancelled:
                try:
                    # 带超时轮询,便于响应 cancel(避免无限阻塞在 get)
                    item = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is _STOP:
                    break
                pool.submit(self._render_one, item)  # type: ignore[arg-type]
        finally:
            # cancel_futures=True：取消尚未开始的待处理任务。
            # 已在运行的 in-flight 任务会因 HTTP 有界超时（_HTTP_TIMEOUT）最终返回。
            # 不使用 with 块（其 __exit__ 是 shutdown(wait=True) 无 cancel_futures）。
            pool.shutdown(wait=True, cancel_futures=True)
