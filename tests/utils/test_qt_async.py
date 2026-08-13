"""测试 qt_async 模块的 run_coroutine / AsyncTaskRunner 超时与任务管理"""

import asyncio

import pytest

from vibeocr.classic.utils.qt_async import (
    AsyncTaskRunner,
    get_async_runner,
    run_coroutine,
)


@pytest.fixture(autouse=True)
def _clear_closed_event_loop_after_test():
    """避免本模块关闭的 loop 污染后续 GUI 测试的线程当前 loop。"""
    yield
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_closed():
        asyncio.set_event_loop(None)


def _run_loop_until_complete(coro, timeout=2.0):
    """在新事件循环上同步运行协程,带整体超时保护(测试辅助)"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


class TestAsyncTaskRunner:
    """AsyncTaskRunner 行为测试"""

    def test_run_with_result(self):
        """无超时:正常完成返回结果,触发 on_complete"""

        async def coro():
            await asyncio.sleep(0.01)
            return 42

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()
            captured = []

            async def driver():
                task = runner.run(coro(), on_complete=lambda r: captured.append(r))
                await task

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
            assert captured == [42]
            assert runner.active_count == 0
        finally:
            loop.close()

    def test_run_with_timeout_raises(self):
        """有超时:协程慢于 timeout 时抛 TimeoutError,触发 on_error"""

        async def slow_coro():
            await asyncio.sleep(10)
            return "done"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()
            errors = []

            async def driver():
                task = runner.run(
                    slow_coro(),
                    on_error=lambda e: errors.append(e),
                    timeout=0.05,
                )
                with pytest.raises(TimeoutError):
                    await task

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
            assert len(errors) == 1
            assert isinstance(errors[0], TimeoutError)
        finally:
            loop.close()

    def test_cancel_all_clears_tasks(self):
        """cancel_all 取消所有运行中任务"""

        async def long_coro():
            await asyncio.sleep(10)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()

            async def driver():
                t1 = runner.run(long_coro())
                t2 = runner.run(long_coro())
                assert runner.active_count == 2
                runner.cancel_all()
                # cancel 是异步的,await 让取消真正生效(会抛 CancelledError,忽略)
                import contextlib

                for t in (t1, t2):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t
                assert all(t.done() for t in (t1, t2))

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
        finally:
            loop.close()


class TestRunCoroutine:
    """run_coroutine 函数测试"""

    def test_run_coroutine_accepts_timeout_param(self):
        """run_coroutine 接受 timeout 关键字参数(委托 AsyncTaskRunner)"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # run_coroutine 不阻塞,仅调度;在循环上跑一下让它完成
            ran = []

            async def quick():
                ran.append(True)
                return "ok"

            async def pump():
                run_coroutine(quick(), timeout=1.0)
                await asyncio.sleep(0.05)

            loop.run_until_complete(asyncio.wait_for(pump(), timeout=2.0))
            assert ran == [True]
        finally:
            loop.close()

    def test_run_coroutine_timeout_triggers(self):
        """run_coroutine 的 timeout 真的能让慢协程超时"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:

            async def slow():
                await asyncio.sleep(10)

            # 注入一个 on_error 不便(签名限制),改为直接观察全局 runner 的任务
            runner = get_async_runner()

            async def pump():
                run_coroutine(slow(), timeout=0.05)
                # 等足够久让 timeout 触发
                await asyncio.sleep(0.2)
                # 等所有 pending 任务完成(它们应因 timeout 而完成)
                for t in list(runner._tasks):
                    with contextlib_suppress():
                        await t

            # 由于 run_coroutine 默认无 on_error,超时会 log + raise(在 task 上下文)
            # 我们只验证任务最终 done 且 active_count 归零
            loop.run_until_complete(asyncio.wait_for(pump(), timeout=2.0))
            # 任务应已完成(无论成功或异常)
            for t in runner._tasks:
                assert t.done()
        finally:
            loop.close()


def contextlib_suppress():
    """返回 contextlib.suppress(Exception),避免顶部 import 污染"""
    import contextlib

    return contextlib.suppress(Exception)


class TestQasyncFailFast:
    """qasync 缺失时 fail-fast，不返回不可用的标准 asyncio loop。

    根因：qasync 是必选依赖，但 create_qasync_event_loop 在 ImportError 时
    返回标准 asyncio loop，main.py 仍 run_forever，标准 loop 不泵 Qt 事件，
    形成"窗口显示但无响应"的假启动。
    """

    def test_missing_qasync_raises_runtime_error(self):
        """qasync 导入失败时应抛 RuntimeError，而非返回标准 loop"""
        from unittest.mock import MagicMock, patch

        from vibeocr.classic.utils.qt_async import create_qasync_event_loop

        mock_app = MagicMock()
        # 模拟 qasync 不可导入
        with patch.dict("sys.modules", {"qasync": None}):
            with pytest.raises(RuntimeError, match="qasync"):
                create_qasync_event_loop(mock_app)

    def test_qasync_available_returns_qasync_loop(self):
        """qasync 可用时正常返回 loop（不抛异常）"""
        from unittest.mock import MagicMock

        from vibeocr.classic.utils.qt_async import create_qasync_event_loop

        # qasync 在测试环境中应已安装（pyproject 必选依赖）
        mock_app = MagicMock()
        loop = create_qasync_event_loop(mock_app)
        assert loop is not None
        # 清理：qasync QEventLoop 绑定了 app，关闭它
        try:
            loop.close()
        except Exception:
            pass


class TestAsyncTaskDrainAndError:
    """AsyncTaskRunner drain 与 on_error 错误观测。"""

    def test_cancel_all_async_drains_tasks(self):
        """cancel_all_async 取消后任务真正完成（drained）"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()

            async def driver():
                async def long_coro():
                    await asyncio.sleep(10)

                t1 = runner.run(long_coro())
                t2 = runner.run(long_coro())
                assert runner.active_count == 2
                # cancel_all_async 应 drain（await 所有任务完成）
                await runner.cancel_all_async()
                assert all(t.done() for t in (t1, t2))
                assert runner.active_count == 0

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
        finally:
            loop.close()

    def test_run_coroutine_forwards_on_error(self):
        """run_coroutine 透传 on_error 回调"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            errors = []

            async def failing():
                raise ValueError("test error")

            async def pump():
                run_coroutine(
                    failing(), on_error=lambda e: errors.append(e), timeout=0.5
                )
                await asyncio.sleep(0.3)
                runner = get_async_runner()
                for t in list(runner._tasks):
                    with contextlib_suppress():
                        await t

            loop.run_until_complete(asyncio.wait_for(pump(), timeout=2.0))
            assert len(errors) == 1
            assert isinstance(errors[0], ValueError)
        finally:
            loop.close()

    def test_run_coroutine_rejects_nonrunning_loop_without_leaking(self):
        """仅 set 但未运行的 loop 必须 fail-fast，并关闭原协程。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def pending():
            await asyncio.sleep(1)

        coroutine = pending()
        try:
            with pytest.raises(RuntimeError, match="running qasync"):
                run_coroutine(coroutine)
            assert coroutine.cr_frame is None
        finally:
            loop.close()


class TestAwaitDialogFutureGuard:
    """await_dialog Future 防护：取消后迟到 finished 不 set_result。"""

    def test_cancelled_future_ignores_late_finished(self):
        """Future 被取消后，对话框 finished 不再 set_result（不抛 InvalidStateError）"""
        from unittest.mock import MagicMock

        from vibeocr.classic.pyside.update import await_dialog

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:

            async def test():
                dialog = MagicMock()
                # 模拟 finished 信号的 connect/disconnect
                connected_cb = []

                def connect(cb):
                    connected_cb.append(cb)

                dialog.finished.connect = connect
                dialog.finished.disconnect = lambda *a: None
                dialog.show = MagicMock()

                task = loop.create_task(await_dialog(dialog))
                await asyncio.sleep(0.05)
                # 取消任务（模拟超时/外部取消）
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                # 迟到的 finished 不应抛 InvalidStateError
                # （await_dialog 的 _on_finished 应检查 fut.done()）
                if connected_cb:
                    try:
                        connected_cb[0](1)
                    except Exception as e:
                        pytest.fail(f"迟到 finished 不应抛异常: {e}")

            loop.run_until_complete(asyncio.wait_for(test(), timeout=2.0))
        finally:
            loop.close()
