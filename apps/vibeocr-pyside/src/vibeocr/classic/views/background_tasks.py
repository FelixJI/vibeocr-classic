"""设置与启动流程共用的轻量后台任务。"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class FunctionTaskSignals(QObject):
    """把 QRunnable 的结果安全送回其创建线程。"""

    finished = Signal(object)
    error = Signal(str)


class FunctionTask(QRunnable):
    """执行一个同步 callable；调用方负责持有任务引用。"""

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self._operation = operation
        self.signals = FunctionTaskSignals()
        self._done = threading.Event()

    def is_drained(self) -> bool:
        """Return whether the native thread-pool invocation has returned."""
        return self._done.is_set()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self._operation())
        except Exception as exc:
            logger.exception("后台任务执行失败")
            self.signals.error.emit(str(exc))
        finally:
            self._done.set()


class DependencyUpdateCheckTask(QObject):
    """兼容的组件更新检查 single-flight。

    Runtime 版本由产品 ``component-lock.json`` 固定，用户机器不再解析或逐包
    升级 Backend 依赖。因此该任务只保留异步信号协议并返回空更新集；产品更新
    服务负责整体替换前端与组件绑定。
    """

    started = Signal(str)
    completed = Signal(str, object)
    failed = Signal(str, str)

    def __init__(
        self,
        project_root: Path,
        parent: QObject | None = None,
        *,
        operation: Callable[[], dict] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._running = False
        self._closing = False
        self._generation = 0
        self._source = ""
        self._task: FunctionTask | None = None
        self._operation = operation or (dict)

    @property
    def is_running(self) -> bool:
        return self._running

    def request(self, source: str) -> bool:
        """请求检查；返回是否启动了新的 worker。"""
        if self._closing:
            return False

        if self._running:
            return False

        self._source = source
        self._running = True
        self._generation += 1
        generation = self._generation
        self.started.emit(source)

        task = FunctionTask(self._operation)
        self._task = task
        task.signals.finished.connect(
            lambda result: self._finish(generation, result)
        )
        task.signals.error.connect(lambda error: self._fail(generation, error))
        QThreadPool.globalInstance().start(task)
        return True

    def close(self) -> None:
        """使所有迟到结果失效；底层同步探测由线程池自然收尾。"""
        self._closing = True
        self._generation += 1
        self._source = ""

    def is_drained(self) -> bool:
        task = self._task
        return task is None or task.is_drained()

    def _finish(self, generation: int, result: object) -> None:
        if generation != self._generation or self._closing:
            if self._closing:
                self._running = False
                self._task = None
            return
        source = self._source
        self._running = False
        self._task = None
        self.completed.emit(source, result)

    def _fail(self, generation: int, error: str) -> None:
        if generation != self._generation or self._closing:
            if self._closing:
                self._running = False
                self._task = None
            return
        source = self._source
        self._running = False
        self._task = None
        self.failed.emit(source, error)
