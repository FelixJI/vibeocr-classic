"""ThumbnailIpcWorker 并发渲染测试。

验证:
- 多页并发渲染均回传 PNG bytes(顺序不保证,但页索引正确)
- generation 校验:invalidate 后的旧 gen 结果在 worker 内仍带原 gen,
  由调用方(_on_thumbnail_ready)按 gen 丢弃(本测试验证 worker 透传 gen)
- cancel() 能让 worker 主循环退出并等待线程池收尾
"""

from __future__ import annotations

import io
import threading
import time
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PySide6.QtCore import Qt

from vibeocr.classic.workers.pdf_render_thumb_ipc_worker import ThumbnailIpcWorker


def _png_bytes(color=(255, 0, 0)) -> bytes:
    """生成一张纯色 PNG(用于 mock render_thumbnail 返回)。"""
    img = Image.new("RGB", (60, 80), color=color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _make_mock_client(delay: float = 0.0) -> MagicMock:
    """mock PdfBackendClient:render_thumbnail 返回固定 PNG,可选延迟(模拟耗时)。

    线程安全:并发调用时各自返回独立 bytes(无共享可变状态)。
    """
    client = MagicMock()
    call_count = {"n": 0}
    call_lock = threading.Lock()

    def _render(session_id, page, size=160):
        if delay:
            time.sleep(delay)
        with call_lock:
            call_count["n"] += 1
        return _png_bytes(color=(page % 256, 0, 0))

    client.render_thumbnail = MagicMock(side_effect=_render)
    client.call_count = call_count  # 暴露给测试断言
    return client


@pytest.fixture
def worker(qapp):
    """构造 worker 但不 start(部分测试直接调 _render_one)。"""
    client = _make_mock_client()
    w = ThumbnailIpcWorker(client=client, session_id="test-sid", size=160)
    yield w
    if not w.isFinished():
        w.cancel()
        w.wait(3000)


class TestThumbnailIpcWorkerConcurrent:
    def test_concurrent_render_returns_all_pages(self, worker, qapp):
        """多页提交后,启动 worker,所有页都应回传 thumbnail_ready。"""
        results: dict[int, object] = {}
        done = threading.Event()

        def _on_ready(page_index, data, gen):
            results[page_index] = data
            if len(results) >= 8:
                done.set()

        # DirectConnection:槽在 emit 的线程直接执行,无需主线程事件循环
        # (生产代码用默认 AutoConnection,槽排队到主线程;测试线程在
        #  done.wait() 阻塞无法处理事件,故测试用 DirectConnection 绕过)
        worker.thumbnail_ready.connect(_on_ready, Qt.ConnectionType.DirectConnection)
        worker.start()
        try:
            for i in range(8):
                worker.request(i, gen=0)
            # 等待全部回传
            assert done.wait(timeout=10.0), f"只收到 {len(results)}/8 页"
        finally:
            worker.cancel()
            worker.wait(5000)

        assert len(results) == 8
        for i in range(8):
            data = results[i]
            assert isinstance(data, (bytes, bytearray))
            assert len(data) > 0

    def test_gen_passthrough(self, worker, qapp):
        """worker 应透传 request 时的 gen,由调用方校验丢弃。"""
        received_gens: list[int] = []
        done = threading.Event()

        def _on_ready(page_index, data, gen):
            received_gens.append(gen)
            done.set()

        # DirectConnection:槽在 emit 的线程直接执行,无需主线程事件循环
        # (生产代码用默认 AutoConnection,槽排队到主线程;测试线程在
        #  done.wait() 阻塞无法处理事件,故测试用 DirectConnection 绕过)
        worker.thumbnail_ready.connect(_on_ready, Qt.ConnectionType.DirectConnection)
        worker.start()
        try:
            worker.request(0, gen=42)
            assert done.wait(timeout=10.0)
        finally:
            worker.cancel()
            worker.wait(5000)

        assert received_gens == [42]

    def test_cancel_exits_cleanly(self, qapp):
        """cancel 应让 worker 退出;即便队列里还有未处理的页。"""
        # 用较长延迟确保 cancel 时有 in-flight 任务
        client = _make_mock_client(delay=0.3)
        w = ThumbnailIpcWorker(client=client, session_id="test-sid", size=160)
        w.start()
        # 投一批请求,然后立即 cancel
        for i in range(20):
            w.request(i, gen=0)
        time.sleep(0.1)  # 让部分任务进入 in-flight
        w.cancel()
        # 应在合理时间内退出(in-flight 的 0.3s 任务 + 收尾)
        assert w.wait(10000), "cancel 后 worker 未在 10s 内退出"
        assert w.isFinished()

    def test_dedup_pending(self, worker, qapp):
        """同页重复 request 在飞行中不应重新入队(_pending 去重)。"""
        results: list[int] = []
        done = threading.Event()

        def _on_ready(page_index, data, gen):
            results.append(page_index)
            done.set()

        # DirectConnection:槽在 emit 的线程直接执行,无需主线程事件循环
        # (生产代码用默认 AutoConnection,槽排队到主线程;测试线程在
        #  done.wait() 阻塞无法处理事件,故测试用 DirectConnection 绕过)
        worker.thumbnail_ready.connect(_on_ready, Qt.ConnectionType.DirectConnection)
        worker.start()
        try:
            worker.request(0, gen=0)
            worker.request(0, gen=0)  # 重复,应被 _pending 去重
            assert done.wait(timeout=10.0)
            time.sleep(0.3)  # 确保没有第二次回传
        finally:
            worker.cancel()
            worker.wait(5000)

        # 页 0 只应回传一次
        assert results.count(0) == 1

    def test_cancel_with_blocked_http_exits_without_terminate(self, qapp):
        """cancel 后即使 HTTP 请求阻塞，worker 也应退出而不依赖 terminate()。

        复现 bug：阻塞的 HTTP 请求使 ThreadPoolExecutor 无法 shutdown(wait=True)，
        关闭路径不得用 worker.terminate() 强杀。cancel() 应使用
        cancel_futures=True 让 executor 快速收尾并自然退出。
        """

        # 用 threading.Event 阻塞 render_thumbnail，模拟后端卡死
        block_event = threading.Event()

        def _blocking_render(session_id, page, size=160):
            block_event.wait(timeout=5.0)  # 阻塞直到测试放行或超时
            return _png_bytes()

        client = MagicMock()
        client.render_thumbnail = MagicMock(side_effect=_blocking_render)

        w = ThumbnailIpcWorker(client=client, session_id="test-sid", size=160)
        w.start()
        # 投几个请求，让 executor 开始处理（阻塞中）
        for i in range(3):
            w.request(i, gen=0)
        time.sleep(0.2)  # 确保任务进入 in-flight
        w.cancel()
        # 放行阻塞的 HTTP（模拟后端最终响应）
        block_event.set()
        # 应在合理时间内退出（cancel_futures 让 executor 不等未开始的任务）
        assert w.wait(10000), "cancel 后 worker 未在 10s 内退出"
        assert w.isFinished()
