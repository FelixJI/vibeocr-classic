"""Qt-asyncio 集成工具

使用 qasync 库实现 Qt 事件循环与 asyncio 事件循环的集成。
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import threading
import warnings
import weakref
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# 存储异步任务引用，防止垃圾回收
_async_tasks: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()

# ``asyncio.to_thread`` 的 asyncio Future 被取消时会立即进入 done，但其原生
# executor callable 仍可能运行。应用关闭若只探测 Task，会过早销毁 callable
# 捕获的 Qt owner。这里保留底层 concurrent Future，直到原生调用真正返回。
_native_executor: concurrent.futures.ThreadPoolExecutor | None = None
_native_futures: set[concurrent.futures.Future[Any]] = set()
_native_futures_lock = threading.Lock()


def _get_native_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _native_executor
    with _native_futures_lock:
        if _native_executor is None:
            _native_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="vibeocr-async-native",
            )
        return _native_executor


async def tracked_to_thread(func: Callable[..., Any], /, *args, **kwargs) -> Any:
    """Run a sync callable off-loop while retaining a native shutdown probe."""
    context = contextvars.copy_context()
    call = functools.partial(context.run, func, *args, **kwargs)
    future = _get_native_executor().submit(call)
    with _native_futures_lock:
        _native_futures.add(future)

    def release(completed: concurrent.futures.Future[Any]) -> None:
        with _native_futures_lock:
            _native_futures.discard(completed)

    future.add_done_callback(release)
    return await asyncio.wrap_future(future)


def are_tracked_native_jobs_drained() -> bool:
    """Thread-safe, zero-wait native completion probe used by MainWindow."""
    with _native_futures_lock:
        return not _native_futures


def _get_running_or_set_loop() -> asyncio.AbstractEventLoop:
    """获取当前线程的事件循环，必要时创建并设为当前循环。

    Python 3.13 起，无参 ``asyncio.get_event_loop()`` 在当前线程没有已设置
    的事件循环时会抛出 ``RuntimeError``（3.10+ 已对其 DeprecationWarning，
    3.12+ 在无 running loop 且无 set 过 loop 时直接报错）。

    生产环境由 ``create_qasync_event_loop`` 提前 ``set_event_loop``，这里仅在
    单元测试等未经过 main.py 初始化的场景兜底：若无当前循环则新建一个，避免
    ``run_coroutine`` / ``AsyncTaskRunner`` 在这些环境下崩溃。
    """
    # 复用当前线程已设置的事件循环（生产环境的 qasync 循环通过
    # ``set_event_loop`` 注册）。3.13 起无循环时 ``get_event_loop()`` 既发
    # DeprecationWarning 又抛 RuntimeError；用 catch_warnings 屏蔽该 warning，
    # 再在 RuntimeError 时新建循环，避免在未经 ``create_qasync_event_loop``
    # 初始化的场景（如单元测试）下崩溃或留下噪音日志。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop


def create_qasync_event_loop(app) -> asyncio.AbstractEventLoop:
    """创建 qasync 事件循环

    qasync 是 VibeOCR 的必选依赖（pyproject.toml）。缺失时 fail-fast，
    抛 RuntimeError 并显示明确依赖错误，而非返回不可用的标准 asyncio loop
    （标准 loop 不泵 Qt 事件，会形成"窗口显示但无响应"的假启动）。

    Args:
        app: QApplication 实例

    Returns:
        QEventLoop 事件循环

    Raises:
        RuntimeError: qasync 未安装时
    """
    try:
        import qasync  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "qasync 是 VibeOCR 的必选依赖，缺失时 GUI 事件循环无法与 asyncio 集成。"
            "请安装 qasync (pip install qasync) 后重试。"
        ) from e

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    logger.debug("qasync 事件循环已创建")
    return loop


def run_coroutine(
    coro: Coroutine,
    callback: Callable | None = None,
    timeout: float | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """在 Qt 环境中运行协程

    将协程添加到事件循环中执行，可选提供完成回调和超时。
    内部委托给全局 ``AsyncTaskRunner``，复用其任务管理与 ``asyncio.wait_for``
    超时保护能力。

    Args:
        coro: 要执行的协程
        callback: 可选的完成回调函数，接收协程返回值作为参数
        timeout: 可选超时时间（秒）。None 时无超时（依赖协程底层自管）。
            建议为可能长时间阻塞的协程传入兜底超时，避免 UI 协程永久挂起。
        on_error: 可选的错误回调函数，接收异常作为参数。
    """
    logger.debug(
        f"[run_coroutine] 开始执行协程 (timeout={timeout})..."
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError as error:
        # A merely "set" loop does not execute tasks. Silently scheduling on
        # it leaks both the wrapper and the caller-created coroutine forever.
        # Production calls originate from Qt slots while qasync is running.
        coro.close()
        raise RuntimeError("run_coroutine requires a running qasync event loop") from error
    runner = get_async_runner()
    runner.run(coro, on_complete=callback, on_error=on_error, timeout=timeout)


def async_slot(*types):
    """将异步函数转换为 Qt 槽的装饰器

    使用示例:
        @async_slot()
        async def on_button_clicked(self):
            result = await some_async_operation()
            self.label.setText(result)

    Args:
        *types: 可选的槽参数类型（与 PySide6.Slot 相同）

    Returns:
        装饰后的函数，可作为 Qt 槽使用
    """

    def decorator(async_func: Callable[..., Coroutine]) -> Callable:
        @functools.wraps(async_func)
        def wrapper(*args, **kwargs):
            coro = async_func(*args, **kwargs)
            task = asyncio.ensure_future(coro)
            # 存储引用以防止垃圾回收
            _async_tasks.add(task)
            task.add_done_callback(_async_tasks.discard)

            # 错误观测：记录异常，避免 "Task exception was never retrieved"
            def _log_exception(t):
                if t.cancelled():
                    return
                exc = t.exception()
                if exc is not None:
                    logger.error("async_slot 协程异常: %s", exc, exc_info=exc)

            task.add_done_callback(_log_exception)

        # 添加 Qt 槽信息（用于 PySide6 元对象系统）
        wrapper.__signature__ = getattr(async_func, "__signature__", None)  # type: ignore[attr-defined]
        wrapper.__annotations__ = getattr(async_func, "__annotations__", {})

        return wrapper

    return decorator


class AsyncTaskRunner:
    """异步任务运行器

    提供便捷的方式来管理和执行异步任务，支持取消和超时。

    使用示例:
        runner = AsyncTaskRunner()
        runner.run(some_async_func(), on_complete=handle_result)
        runner.cancel_all()  # 取消所有运行中的任务
    """

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    def run(
        self,
        coro: Coroutine,
        on_complete: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        timeout: float | None = None,
    ) -> asyncio.Task:
        """运行异步任务

        Args:
            coro: 要执行的协程
            on_complete: 成功完成回调
            on_error: 错误回调
            timeout: 可选超时时间（秒）

        Returns:
            asyncio.Task 对象
        """

        started = False

        async def wrapped():
            nonlocal started
            started = True
            try:
                if timeout:
                    result = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    result = await coro

                if on_complete:
                    on_complete(result)
                return result

            except TimeoutError as e:
                logger.error(f"任务超时: {timeout}s")
                if on_error:
                    on_error(e)
                raise

            except asyncio.CancelledError:
                logger.debug("任务已取消")
                raise

            except Exception as e:
                logger.error(f"任务失败: {e}")
                if on_error:
                    on_error(e)
                raise

            finally:
                # 从任务列表中移除（防御性：task 可能已被 cancel_all 清空）。
                # current_task() 在 loop 非 running 时（如单元测试用 set-only
                # loop 推进）会抛 RuntimeError，这里容忍：清理是 best-effort，
                # 不应掩盖协程本身的异常。生产环境 loop running 时正常返回 task。
                try:
                    task = asyncio.current_task()
                except RuntimeError:
                    task = None
                if task is not None:
                    try:
                        self._tasks.remove(task)
                    except ValueError:
                        pass

        loop = _get_running_or_set_loop()
        wrapped_coro = wrapped()
        try:
            task = loop.create_task(wrapped_coro)
        except Exception:
            wrapped_coro.close()
            if not started:
                coro.close()
            raise

        def _close_unstarted_coroutine(completed: asyncio.Task) -> None:
            # A task can be cancelled before wrapped() receives its first tick.
            # In that case the caller-created coroutine was never awaited and
            # must be closed explicitly to avoid leaking resources/warnings.
            if completed.cancelled() and not started:
                coro.close()

        task.add_done_callback(_close_unstarted_coroutine)
        self._tasks.append(task)
        return task

    def cancel_all(self) -> None:
        """取消所有运行中的任务（同步，不 drain）。

        注意：此方法只 cancel 不 await，取消的任务可能仍 pending。
        在 async 上下文中应使用 cancel_all_async() 确保 drain。
        """
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

    async def cancel_all_async(self) -> None:
        """取消所有运行中的任务并等待其完成（drain）。

        与 cancel_all 的区别：此方法 await 所有被取消的任务，确保它们
        真正完成（CancelledError 被处理），避免残留 pending 任务。
        """
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def active_count(self) -> int:
        """获取活动任务数量"""
        return sum(1 for t in self._tasks if not t.done())


class DelayedAsyncTask:
    """Own a delayed coroutine from timer creation through coroutine terminal state.

    ``request_shutdown`` cancels the not-yet-fired timer and invokes a cooperative
    cancellation hook for work already in flight. The global ``AsyncTaskRunner``
    owns/cancels the asyncio Task; native thread calls must use ``tracked_to_thread``
    so shutdown can retain captured Qt owners after asyncio cancellation.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        delay_seconds: float,
        coroutine_factory: Callable[[], Coroutine[Any, Any, Any]],
        *,
        should_start: Callable[[], bool] | None = None,
        request_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._loop = loop
        self._coroutine_factory = coroutine_factory
        self._should_start = should_start
        self._request_cancel = request_cancel
        self._closing = False
        self._task: asyncio.Task | None = None
        self._handle: asyncio.TimerHandle | None = loop.call_later(
            delay_seconds, self._start
        )

    def _start(self) -> None:
        self._handle = None
        if self._closing or (
            self._should_start is not None and not self._should_start()
        ):
            return
        self._task = get_async_runner().run(self._coroutine_factory())

    def request_shutdown(self) -> None:
        self._closing = True
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.cancel()
        if self._task is not None and not self._task.done() and self._request_cancel:
            self._request_cancel()

    def is_drained(self) -> bool:
        handle = self._handle
        timer_drained = handle is None or handle.cancelled()
        task = self._task
        return timer_drained and (task is None or task.done())


# 全局任务运行器实例
_global_runner: AsyncTaskRunner | None = None


def get_async_runner() -> AsyncTaskRunner:
    """获取全局异步任务运行器"""
    global _global_runner
    if _global_runner is None:
        _global_runner = AsyncTaskRunner()
    return _global_runner
