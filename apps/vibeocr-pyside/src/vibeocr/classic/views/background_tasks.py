"""设置与启动流程共用的轻量后台任务。"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, Signal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


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
