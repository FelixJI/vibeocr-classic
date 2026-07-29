"""依赖管理器

提供依赖检查和安装管理功能。
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from vibeocr.classic.app_paths import get_install_root
from vibeocr.classic.runtime_installation import (
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
)

logger = logging.getLogger(__name__)


class DependencyCheckSignals(QObject):
    """依赖检查信号"""

    finished = Signal(bool, list)  # (是否就绪, 缺失依赖列表)


class DependencyCheckTask(QRunnable):
    """依赖检查任务（在后台线程执行）"""

    def __init__(
        self,
        project_root: Path,
        client: RuntimeInstallerClient | None = None,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._client = client or RuntimeInstallerClient(project_root)
        self.signals = DependencyCheckSignals()

    def run(self) -> None:
        """通过 Installer inspect 检查完整 Runtime 的绑定与完整性。"""
        try:
            inspection = self._client.inspect()
        except RuntimeInstallerClientError as exc:
            logging.warning("[Runtime 检查] %s", exc)
            self.signals.finished.emit(False, [str(exc)])
            return
        if inspection.ready:
            logging.debug("[Runtime 检查] %s 已验证", inspection.runtime_id)
            self.signals.finished.emit(True, [])
        else:
            reason = f"{inspection.profile}: {inspection.integrity}"
            logging.warning("[Runtime 检查] 未就绪: %s", reason)
            self.signals.finished.emit(False, [reason])


class DependencyManager(QObject):
    """依赖管理器

    管理 OCR 依赖的检查和安装状态。
    通过信号与 UI 通信，不直接操作 UI。

    Signals:
        check_completed(bool, list): 依赖检查完成，参数为(是否就绪, 缺失依赖列表)
        check_started(): 依赖检查开始
    """

    check_completed = Signal(bool, list)  # (是否就绪, 缺失依赖列表)
    check_started = Signal()

    def __init__(self, project_root: Path | None = None, parent=None):
        """初始化依赖管理器

        Args:
            project_root: 项目根目录，默认为自动检测
            parent: 父对象
        """
        super().__init__(parent)
        self._project_root = project_root or get_install_root()
        self._client = RuntimeInstallerClient(self._project_root)
        self._thread_pool = QThreadPool()
        self._is_checking = False
        self._pending_check = False
        self._closing = False
        self._generation = 0
        self._tasks: set[DependencyCheckTask] = set()
        self._is_ready = False
        self._missing_dependencies: list = []

    def check_dependencies(self) -> None:
        """异步检查依赖

        在后台线程中执行依赖检查，通过信号返回结果。
        """
        if self._closing:
            return
        if self._is_checking:
            logger.debug("依赖检查已在进行中，完成后将重新检查")
            self._pending_check = True
            return

        self._is_checking = True
        self._generation += 1
        generation = self._generation
        self.check_started.emit()

        task = DependencyCheckTask(self._project_root, self._client)
        self._tasks.add(task)
        task.signals.finished.connect(
            lambda ready, missing, current=task: self._on_task_finished(
                current, generation, ready, missing
            )
        )
        self._thread_pool.start(task)

    def _on_task_finished(
        self,
        task: DependencyCheckTask,
        generation: int,
        ready: bool,
        missing: list,
    ) -> None:
        self._tasks.discard(task)
        if self._closing or generation != self._generation:
            self._is_checking = False
            logger.debug("忽略已重置依赖检查的迟到结果")
            return
        self._on_check_finished(ready, missing)

    def _on_check_finished(self, ready: bool, missing: list) -> None:
        """依赖检查完成回调"""
        self._is_checking = False
        self._is_ready = ready
        self._missing_dependencies = missing
        self.check_completed.emit(ready, missing)
        if self._pending_check:
            self._pending_check = False
            self.check_dependencies()

    def is_checking(self) -> bool:
        """检查是否正在检查依赖"""
        return self._is_checking

    def is_ready(self) -> bool:
        """检查依赖是否就绪"""
        return self._is_ready

    def get_missing_dependencies(self) -> list:
        """获取缺失的依赖列表"""
        return self._missing_dependencies.copy()

    def reset(self) -> None:
        """重置状态"""
        self._generation += 1
        self._is_checking = False
        self._pending_check = False
        self._is_ready = False
        self._missing_dependencies = []

    def request_shutdown(self) -> None:
        """Freeze new checks; the running QRunnable is allowed to return naturally."""
        self._closing = True
        self._generation += 1
        self._pending_check = False

    def is_drained(self) -> bool:
        """Non-blocking native thread-pool probe for GUI shutdown polling."""
        return self._thread_pool.activeThreadCount() == 0 and not self._tasks
