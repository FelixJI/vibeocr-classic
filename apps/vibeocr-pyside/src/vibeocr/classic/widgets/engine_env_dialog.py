"""引擎环境安装对话框：平实文案 + 阶段提示 + 实时安装日志。

展示将安装/移除的识别引擎（PaddleOCR / MinerU，CPU 或 GPU 版），用户确认后
逐个执行 ``uv`` 安装；下载与安装过程由 uv 输出逐行转发到日志区，让用户
始终看得到进展，而不是只面对一根转圈的进度条。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.engine_envs import (
    EngineEnvError,
    EngineEnvManager,
    get_engine_env_spec,
)
from vibeocr.classic.utils.dialog_workers import track_dialog_worker

logger = logging.getLogger(__name__)


class EngineEnvWorker(QThread):
    """在后台线程依次安装/移除引擎环境。"""

    phase = Signal(str)
    log_line = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(
        self,
        manager: EngineEnvManager,
        install_ids: list[str],
        remove_ids: list[str],
        parent: QWidget | None = None,
        *,
        package_index_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._install_ids = list(install_ids)
        self._remove_ids = list(remove_ids)
        self._package_index_id = package_index_id
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:  # pragma: no cover - 线程体
        try:
            for spec_id in self._remove_ids:
                spec = get_engine_env_spec(spec_id)
                self.phase.emit(f"正在移除 {spec.display_name}")
                self._manager.remove(spec_id)
            for spec_id in self._install_ids:
                spec = get_engine_env_spec(spec_id)
                self._manager.ensure(
                    spec_id,
                    on_phase=lambda message, _s=spec.display_name: self.phase.emit(
                        f"{_s}：{message}"
                    ),
                    on_log=self.log_line.emit,
                    cancel_event=self._cancel_event,
                    package_index_id=self._package_index_id,
                )
        except EngineEnvError as exc:
            self.failed.emit(str(exc))
            return
        except Exception:
            logger.exception("[EngineEnvDialog] 安装失败")
            self.failed.emit("安装过程中出现未预期的错误，详见日志文件。")
            return
        self.succeeded.emit()


class EngineEnvDialog(QDialog):
    """安装/调整识别引擎的对话框。"""

    def __init__(
        self,
        manager: EngineEnvManager,
        install_ids: list[str],
        remove_ids: list[str],
        *,
        before_start: Callable[[], bool] | None = None,
        on_finished: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._install_ids = list(install_ids)
        self._remove_ids = list(remove_ids)
        self._before_start = before_start
        self._on_finished = on_finished
        self._worker: EngineEnvWorker | None = None
        self._done = False

        self.setWindowTitle("安装识别引擎")
        self.setModal(False)
        self.setMinimumSize(640, 520)

        layout = QVBoxLayout(self)

        self._header_label = QLabel("以下识别引擎将被安装或移除：")
        self._header_label.setWordWrap(True)
        layout.addWidget(self._header_label)

        self._plan_tree = QTreeWidget()
        self._plan_tree.setHeaderLabels(["项目", "说明"])
        self._plan_tree.setRootIsDecorated(False)
        layout.addWidget(self._plan_tree, 2)

        self._build_source_selectors(layout)

        self._phase_label = QLabel("确认后开始安装。")
        self._phase_label.setWordWrap(True)
        layout.addWidget(self._phase_label)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setPlaceholderText("安装日志会实时显示在这里。")
        self._log_view.setMaximumBlockCount(2000)
        layout.addWidget(self._log_view, 3)

        self._confirm_button = QPushButton("开始安装")
        self._confirm_button.clicked.connect(self._on_confirm)
        layout.addWidget(self._confirm_button, 0, Qt.AlignmentFlag.AlignRight)

        self._populate_plan()

    def _build_source_selectors(self, layout: QVBoxLayout) -> None:
        """安装时可临时选择下载来源；选择会保存为之后的默认。"""
        from PySide6.QtWidgets import QComboBox, QFormLayout

        from vibeocr.classic.download_sources import (
            MODEL_SOURCES,
            PACKAGE_INDEXES,
            DownloadSourceStore,
        )

        store = DownloadSourceStore(self._manager.state_root)
        saved = store.load()

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 4)

        self._package_index_combo = QComboBox()
        for index in PACKAGE_INDEXES:
            self._package_index_combo.addItem(index.label, index.id)
        self._package_index_combo.setCurrentIndex(
            max(0, self._package_index_combo.findData(saved["package_index_id"]))
        )
        form.addRow("依赖下载来源：", self._package_index_combo)

        self._model_source_combo = QComboBox()
        for source in MODEL_SOURCES:
            self._model_source_combo.addItem(source.label, source.id)
        self._model_source_combo.setCurrentIndex(
            max(0, self._model_source_combo.findData(saved["model_source_id"]))
        )
        form.addRow("模型下载来源：", self._model_source_combo)

        layout.addLayout(form)

    def _populate_plan(self) -> None:
        for spec_id in self._install_ids:
            spec = get_engine_env_spec(spec_id)
            item = QTreeWidgetItem([f"安装：{spec.display_name}", spec.description])
            self._plan_tree.addTopLevelItem(item)
        for spec_id in self._remove_ids:
            spec = get_engine_env_spec(spec_id)
            item = QTreeWidgetItem([f"移除：{spec.display_name}", "不再使用时释放磁盘空间"])
            self._plan_tree.addTopLevelItem(item)
        if not self._remove_ids:
            note = QTreeWidgetItem(
                ["提示", "下载量较大时会看到具体进度；安装完成后即可使用。"]
            )
            self._plan_tree.addTopLevelItem(note)
        self._plan_tree.resizeColumnToContents(0)

    def _on_confirm(self) -> None:
        if self._worker is not None:
            return
        if self._before_start is not None and not self._before_start():
            self._append_log("等待识别服务停止超时，未开始安装。可稍后重试。")
            return

        # 安装时选择的下载来源保存为之后的默认；模型来源的环境变量
        # 立即生效（安装完成后重启的识别服务与引擎子进程会继承）。
        from vibeocr.classic.download_sources import DownloadSourceStore

        package_index_id = self._package_index_combo.currentData()
        model_source_id = self._model_source_combo.currentData()
        store = DownloadSourceStore(self._manager.state_root)
        try:
            store.save(
                package_index_id=package_index_id,
                model_source_id=model_source_id,
            )
            store.apply_model_source_environment()
        except Exception:
            logger.exception("[EngineEnvDialog] 保存下载来源失败，按现有来源安装")

        self._confirm_button.setEnabled(False)
        self._confirm_button.setText("正在安装…")
        self._phase_label.setText("正在安装，请保持网络可用。详细进展见下方日志。")

        self._worker = EngineEnvWorker(
            self._manager,
            self._install_ids,
            self._remove_ids,
            self,
            package_index_id=package_index_id,
        )
        self._worker.phase.connect(self._phase_label.setText)
        self._worker.log_line.connect(self._append_log)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        track_dialog_worker(self._worker)
        self._worker.start()

    def _append_log(self, line: str) -> None:
        self._log_view.append(line)

    def _finish(self, success: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        self._phase_label.setText(message)
        self._confirm_button.setText("关闭")
        self._confirm_button.setEnabled(True)
        self._confirm_button.clicked.disconnect(self._on_confirm)
        self._confirm_button.clicked.connect(self.accept)
        if self._on_finished is not None:
            try:
                self._on_finished(success)
            except Exception:  # pragma: no cover - 回调异常不影响对话框
                logger.exception("[EngineEnvDialog] 完成回调异常")

    def _on_succeeded(self) -> None:
        self._manager.apply_runtime_env()
        self._finish(True, "安装完成。识别服务将自动启动。")

    def _on_failed(self, message: str) -> None:
        self._append_log(message)
        self._finish(False, f"安装未完成：{message}")

    def closeEvent(self, event) -> None:  # pragma: no cover - Qt 事件
        if self._worker is not None and self._worker.isRunning():
            # 安装进行中不允许直接关窗，先取消。
            self._phase_label.setText("正在取消安装，请稍候…")
            self._worker.request_cancel()
            event.ignore()
            return
        self._finish(False, "窗口已关闭。")
        super().closeEvent(event)


__all__ = ["EngineEnvDialog", "EngineEnvWorker"]
