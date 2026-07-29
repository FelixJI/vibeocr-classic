"""Qt-owned workers drained 后使用的非 GUI 资源关闭任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThread

if TYPE_CHECKING:
    from collections.abc import Callable


class ExternalShutdownJob(QThread):
    """顺序运行普通 Python 资源清理；绝不接收 QWidget/QObject owner。"""

    def __init__(
        self,
        operations: tuple[tuple[str, Callable[[], object]], ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._operations = operations
        self.errors: tuple[tuple[str, str], ...] = ()

    def run(self) -> None:
        errors: list[tuple[str, str]] = []
        for name, operation in self._operations:
            try:
                operation()
            except Exception as exc:
                errors.append((name, str(exc)))
        self.errors = tuple(errors)


__all__ = ["ExternalShutdownJob"]
