"""批量文件列表组件

显示待处理的文件列表，支持添加、删除、勾选操作。
"""

import os
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class BatchFileListWidget(QWidget):
    """批量文件列表组件

    提供：
    - 选择文件按钮
    - 清空列表按钮
    - 文件列表（带状态显示）
    - 已选择文件数量显示
    """

    # 文件列表变更信号
    files_changed = Signal(list)  # List[dict]
    # 选中文件变更信号
    selection_changed = Signal(str)  # file_path
    _ROW_CHUNK_SIZE = 96
    _SYNC_ROW_LIMIT = 128
    _ROW_FRAME_INTERVAL_MS = 1

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._files: list[dict] = []
        self._path_keys: set[str] = set()
        self._file_index_by_key: dict[str, int] = {}
        self._status_counts = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }
        self._pending_rows: deque[tuple[int, dict]] = deque()
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.timeout.connect(self._drain_row_chunk)

        self._setup_ui()
        self._connect_signals()

        self.setAcceptDrops(True)

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # 按钮行
        button_layout = QHBoxLayout()

        self._select_btn = QPushButton("选择文件")
        self._clear_btn = QPushButton("清空")

        button_layout.addWidget(self._select_btn)
        button_layout.addWidget(self._clear_btn)
        button_layout.addStretch()

        layout.addLayout(button_layout)

        # 文件列表
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["状态", "文件名", ""])

        # 设置列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(2, 40)

        # 选择行为
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        layout.addWidget(self._table)

        # 状态行
        status_layout = QHBoxLayout()
        self._status_label = QLabel("已选择: 0 个文件")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()

        layout.addLayout(status_layout)

        self.setLayout(layout)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                paths.append(path)
        if paths:
            self.add_files(paths)
            event.acceptProposedAction()

    def _connect_signals(self):
        """连接信号"""
        self._select_btn.clicked.connect(self._on_select_files)
        self._clear_btn.clicked.connect(self._on_clear)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_select_files(self):
        """选择文件"""
        from vibeocr.backend.utils.mime_types import FILE_FILTER_ALL

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文件",
            "",
            f"{FILE_FILTER_ALL};;所有文件 (*)",
        )

        if files:
            self.add_files(files)

    def _on_clear(self):
        """清空列表"""
        self._row_timer.stop()
        self._pending_rows.clear()
        self._files.clear()
        self._path_keys.clear()
        self._file_index_by_key.clear()
        for status in self._status_counts:
            self._status_counts[status] = 0
        self._table.setRowCount(0)
        self._update_status()
        self.files_changed.emit([])

    def _on_selection_changed(self):
        """选择变更"""
        selected = self._table.selectedItems()
        if selected:
            row = selected[0].row()
            if row < len(self._files):
                file_path = self._files[row]["path"]
                self.selection_changed.emit(file_path)

    def add_files(self, file_paths: list[str]):
        """添加文件；数据立即可见，大批表格行分帧创建以保持事件循环响应。"""
        rows: list[tuple[int, dict]] = []
        for path in file_paths:
            key = self._normalize_path(path)
            if key in self._path_keys:
                continue

            file_info = {
                "path": path,
                "name": Path(path).name,
                "status": "pending",
            }
            row = len(self._files)
            self._files.append(file_info)
            self._path_keys.add(key)
            self._file_index_by_key[key] = row
            self._status_counts["pending"] += 1
            rows.append((row, file_info))

        if not rows:
            self._update_status()
            return

        self._update_status()
        # 数据模型已经完整更新；管道锁定/Start 语义不能等待表格分帧物化。
        # 每次实际数据变更只在这里立即发一次，row timer 不再重复发射。
        self.files_changed.emit(self._files)
        if not self._pending_rows and len(rows) <= self._SYNC_ROW_LIMIT:
            self._append_table_rows(rows)
            return
        self._pending_rows.extend(rows)
        if not self._row_timer.isActive():
            self._row_timer.start(self._ROW_FRAME_INTERVAL_MS)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """纯字符串生成去重键；禁止 resolve/stat 触碰网络盘或文件系统。"""
        normalized = os.path.normpath(path)
        absolute = os.path.abspath(normalized)  # noqa: PTH100 - intentionally no resolve()
        return os.path.normcase(absolute)

    @staticmethod
    def _status_icon(status: str) -> str:
        return {
            "pending": "...",
            "processing": "...",
            "completed": "[OK]",
            "failed": "[X]",
        }.get(status, "...")

    def _drain_row_chunk(self) -> None:
        rows: list[tuple[int, dict]] = []
        while self._pending_rows and len(rows) < self._ROW_CHUNK_SIZE:
            rows.append(self._pending_rows.popleft())
        self._append_table_rows(rows)
        if self._pending_rows:
            # A repeating zero-delay timer can starve timers posted by toolbar
            # drag/paint/input on Windows.  Yield at least one millisecond between
            # chunks so other event sources are guaranteed a scheduling turn.
            self._row_timer.start(self._ROW_FRAME_INTERVAL_MS)

    def _append_table_rows(self, rows: list[tuple[int, dict]]) -> None:
        if not rows:
            return
        table = self._table
        old_blocked = table.blockSignals(True)
        table.setUpdatesEnabled(False)
        try:
            required_rows = rows[-1][0] + 1
            if table.rowCount() < required_rows:
                table.setRowCount(required_rows)
            for row, file_info in rows:
                status = file_info["status"]
                status_item = QTableWidgetItem(self._status_icon(status))
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, 0, status_item)

                name_item = QTableWidgetItem(file_info["name"])
                if status == "failed":
                    name_item.setForeground(QColor("red"))
                table.setItem(row, 1, name_item)
                table.setItem(row, 2, QTableWidgetItem(""))
        finally:
            table.setUpdatesEnabled(True)
            table.blockSignals(old_blocked)
        table.viewport().update()

    def update_file_status(self, file_path: str, status: str, result=None):
        """更新文件状态

        Args:
            file_path: 文件路径
            status: 状态 (pending, processing, completed, failed)
            result: 识别结果（可选）
        """
        index = self._file_index_by_key.get(self._normalize_path(file_path))
        if index is not None:
            file_info = self._files[index]
            old_status = file_info["status"]
            if old_status != status:
                self._status_counts[old_status] -= 1
                self._status_counts[status] = self._status_counts.get(status, 0) + 1
            file_info["status"] = status
            file_info["result"] = result

            status_item = self._table.item(index, 0)
            if status_item:
                status_item.setText(self._status_icon(status))
            name_item = self._table.item(index, 1)
            if name_item:
                name_item.setForeground(
                    QColor("red" if status == "failed" else "black")
                )

        self._update_status()

    def get_selected_files(self) -> list[dict]:
        """获取所有待处理的文件"""
        return [f for f in self._files if f["status"] == "pending"]

    def get_file_count(self) -> int:
        """获取文件总数"""
        return len(self._files)

    def get_pending_count(self) -> int:
        """获取待处理数量"""
        return self._status_counts["pending"]

    def _update_status(self):
        """更新状态显示"""
        total = len(self._files)
        pending = self.get_pending_count()
        completed = self._status_counts["completed"]
        failed = self._status_counts["failed"]

        status_text = (
            f"共: {total} | 待处理: {pending} | 完成: {completed} | 失败: {failed}"
        )
        self._status_label.setText(status_text)

    def clear_results(self):
        """清除所有结果（重置状态）"""
        old_blocked = self._table.blockSignals(True)
        self._table.setUpdatesEnabled(False)
        try:
            for i, f in enumerate(self._files):
                f["status"] = "pending"
                f["result"] = None

                status_item = self._table.item(i, 0)
                if status_item:
                    status_item.setText("...")

                name_item = self._table.item(i, 1)
                if name_item:
                    name_item.setForeground(QColor("black"))
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(old_blocked)
        for status in self._status_counts:
            self._status_counts[status] = 0
        self._status_counts["pending"] = len(self._files)
        self._table.viewport().update()

        self._update_status()
