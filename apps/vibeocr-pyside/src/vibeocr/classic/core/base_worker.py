"""Worker 基类

提供统一的 Worker 抽象，支持取消操作和错误处理。
"""

import logging
from abc import abstractmethod
from typing import Any, TypeVar

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseWorker[T](QThread):
    """Worker 基类

    提供通用的 Worker 功能：
    - 取消操作
    - 进度报告
    - 错误处理
    - 结果收集

    使用方法:
        class MyWorker(BaseWorker[MyResult]):
            progress = Signal(int, int, str)  # current, total, message
            file_completed = Signal(str, str, dict)  # file, status, result
            finished = Signal(dict)  # all results
            error = Signal(str)  # error message

            def _process_item(self, item: Any, index: int) -> Any:
                # 处理单个项目
                return result

            def _get_items(self) -> list:
                # 返回要处理的项目列表
                return self._items
    """

    # 信号定义（子类应该覆盖这些）
    progress = Signal(int, int, str)  # completed, total, message
    file_completed = Signal(str, str, object)  # file_path, status, result
    finished = Signal(object)  # all results
    error = Signal(str)  # error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False
        self._results: dict[str, Any] = {}

    def run(self):
        """执行 Worker 任务

        子类可以覆盖此方法，但建议覆盖 _get_items 和 _process_item。
        """
        try:
            items = self._get_items()
            total = len(items)

            if total == 0:
                self.finished.emit(self._results)
                return

            for index, item in enumerate(items):
                if self._cancelled:
                    break

                # 获取项目标识
                item_id = self._get_item_id(item, index)

                # 发送进度
                self.progress.emit(index, total, item_id)

                try:
                    # 处理项目
                    result = self._process_item(item, index)

                    # 存储结果
                    self._results[item_id] = {
                        "item": item,
                        "result": result,
                        "status": "completed",
                    }

                    # 发送完成信号
                    self.file_completed.emit(item_id, "completed", result)

                except Exception as e:
                    logger.error(f"处理失败 {item_id}: {e}")
                    self._results[item_id] = {
                        "item": item,
                        "error": str(e),
                        "status": "failed",
                    }
                    self.file_completed.emit(item_id, "failed", {"error": str(e)})

            self.finished.emit(self._results)

        except Exception as e:
            logger.exception("Worker 执行失败")
            self.error.emit(str(e))

    def cancel(self):
        """取消任务

        设置取消标志，任务将在下一个检查点停止。
        """
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """检查是否已取消"""
        return self._cancelled

    @abstractmethod
    def _get_items(self) -> list:
        """获取要处理的项目列表

        Returns:
            项目列表
        """

    @abstractmethod
    def _get_item_id(self, item: Any, index: int) -> str:
        """获取项目标识

        Args:
            item: 项目数据
            index: 项目索引

        Returns:
            项目唯一标识
        """

    @abstractmethod
    def _process_item(self, item: Any, index: int) -> Any:
        """处理单个项目

        Args:
            item: 项目数据
            index: 项目索引

        Returns:
            处理结果
        """


class BatchWorker(BaseWorker[T]):
    """批量处理 Worker 基类

    专门用于处理文件列表的 Worker。
    """

    def __init__(self, files: list, parent=None):
        """初始化

        Args:
            files: 文件信息列表，每项为 dict 包含 path, name 等
            parent: 父对象
        """
        super().__init__(parent)
        self._files = files

    def _get_items(self) -> list:
        """获取文件列表"""
        return self._files

    def _get_item_id(self, item: dict, index: int) -> str:
        """获取文件路径作为标识"""
        return item.get("path", f"file_{index}")

    def _get_file_name(self, item: dict) -> str:
        """获取文件名"""
        from pathlib import Path

        return item.get("name", Path(item.get("path", "")).name)
