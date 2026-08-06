"""安装进度对话框"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from vibeocr.classic.runtime_installation import (
    RuntimeInstallerCancelled,
    RuntimeInstallerClient,
    RuntimeMaintenanceUpdate,
    RuntimeProfileDescriptor,
)
from vibeocr.classic.utils.dialog_workers import track_dialog_worker

logger = logging.getLogger(__name__)

_PHASE_LABELS = {
    "validate_binding": "验证组件绑定",
    "wait_for_lock": "等待运行时锁",
    "prepare_runtime": "准备 Python 运行时",
    "install_profile": "安装运行时依赖",
    "install_backend": "安装 Backend 服务",
    "verify_runtime": "验证运行时",
    "commit_runtime": "提交运行时",
}

_STATE_LABELS = {
    "queued": "等待中",
    "running": "进行中",
    "succeeded": "已就绪",
    "failed": "失败",
    "cancelled": "已取消",
}


class InstallWorker(QThread):
    """通过唯一 Runtime Installer API 安装或修复完整运行时。"""

    progress = Signal(str, str)  # (stage, message)
    profile = Signal(object)  # RuntimeProfileDescriptor
    maintenance = Signal(object)  # RuntimeMaintenanceUpdate
    completed = Signal(bool, str)  # (success, message)

    def __init__(
        self,
        project_root: Path,
        force_backend: str | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
        self._single_pkg = single_pkg
        self._packages = packages
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """协作式取消；客户端负责终止它拥有的 Installer 子进程。"""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _emit_progress(self, stage: str, message: str) -> None:
        """发送进度信号并同步写入 logger（确保 UI 进度落盘到 vibeocr.log）。

        无论是否连接 InstallDialog，进度都落盘，便于无界面场景（如测试/后台）排查。
        """
        logger.info("[%s] %s", stage, message)
        self.progress.emit(stage, message)

    def run(self) -> None:
        """确保或修复整个内容寻址 Runtime；不提供逐包变更入口。"""
        try:
            self._emit_progress("运行时维护", "正在断开 OCR 运行时...")
            try:
                from vibeocr.classic.client import shutdown_backend_client

                shutdown_backend_client()
            except Exception as exc:
                logger.warning("关闭旧 WorkerHost 失败，将继续安装: %s", exc)
            accelerator = {
                "cpu": "cpu",
                "gpu": "nvidia_cuda",
            }.get(self._force_backend)
            client = RuntimeInstallerClient(
                self._project_root,
                accelerator=accelerator,
            )
            self.profile.emit(client.profile_descriptor())
            repair = (
                self._reinstall_python
                or self._single_pkg is not None
                or self._packages is not None
            )
            if repair:
                self._emit_progress(
                    "运行时修复",
                    "逐包重装已停用，正在校验并修复完整 Runtime profile...",
                )
                client.repair(
                    progress=self._emit_maintenance,
                    cancel_event=self._cancel_event,
                )
            else:
                self._emit_progress(
                    "运行时安装",
                    "正在确保绑定的 Runtime profile 可用...",
                )
                client.ensure(
                    progress=self._emit_maintenance,
                    cancel_event=self._cancel_event,
                )
            self.completed.emit(
                True,
                f"Runtime {accelerator or '当前加速方案'} 已验证",
            )
        except RuntimeInstallerCancelled as exc:
            logger.info("安装取消: %s", exc)
            self.completed.emit(False, str(exc))
        except Exception as exc:
            logger.exception("Runtime Installer 异常")
            self.completed.emit(False, f"安装异常: {exc}")

    def _emit_maintenance(self, update: RuntimeMaintenanceUpdate) -> None:
        phase = _PHASE_LABELS.get(update.phase, update.phase)
        component = update.component_id or "runtime"
        logger.info(
            "[Runtime Installer] %s component=%s state=%s sequence=%s code=%s",
            phase,
            component,
            update.operation_state,
            update.sequence,
            update.message_code,
        )
        self.maintenance.emit(update)


class InstallDialog(QDialog):
    """安装进度对话框"""

    install_succeeded = Signal()

    def __init__(
        self,
        project_root: Path,
        parent=None,
        missing_only: bool = False,
        force_backend: str | None = None,
        single_pkg: str | None = None,
        packages: list[str] | None = None,
        maintenance_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._missing_only = missing_only
        self._force_backend = force_backend
        self._single_pkg = single_pkg
        self._packages = packages
        self._maintenance_callback = maintenance_callback
        self._component_items: dict[str, QTreeWidgetItem] = {}
        self._setup_ui()
        self._worker: InstallWorker | None = None

    def _setup_ui(self) -> None:
        """设置UI"""
        if self._single_pkg:
            self.setWindowTitle(f"重装依赖：{self._single_pkg}")
            self._title_text = f"正在重装 {self._single_pkg}..."
        elif self._packages is not None:
            n = len(self._packages)
            self.setWindowTitle(f"批量重装 {n} 个依赖包")
            self._title_text = f"正在批量重装 {n} 个依赖包..."
        else:
            self.setWindowTitle("安装OCR依赖")
            self._title_text = "正在安装OCR依赖..."
        self.setMinimumSize(620, 520)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # 标题
        self._title_label = QLabel(self._title_text)
        layout.addWidget(self._title_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self._progress_bar)

        # 当前阶段
        self._stage_label = QLabel("准备中...")
        layout.addWidget(self._stage_label)

        self._components_tree = QTreeWidget()
        self._components_tree.setObjectName("runtimeComponentsTree")
        self._components_tree.setHeaderLabels(["Backend 组件", "状态", "版本"])
        self._components_tree.setRootIsDecorated(False)
        self._components_tree.setAlternatingRowColors(True)
        self._components_tree.setMinimumHeight(150)
        header = self._components_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._components_tree)

        # 日志输出
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        # 取消按钮（安装进行中显示，触发协作式取消）
        self._cancel_button = QPushButton("取消安装")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.setVisible(False)
        layout.addWidget(self._cancel_button)

        # 关闭按钮（初始隐藏）
        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(False)
        layout.addWidget(self._close_button)

    def showEvent(self, event) -> None:
        """显示事件 - 开始安装"""
        super().showEvent(event)
        if not self._worker:
            self._start_install()

    def _start_install(self) -> None:
        """开始安装"""
        if self._single_pkg:
            self._log(f"开始重装 {self._single_pkg}...")
        elif self._packages is not None:
            self._log(f"开始批量重装 {len(self._packages)} 个依赖包...")
        else:
            self._log("开始安装OCR依赖...")

        self._worker = InstallWorker(
            self._project_root,
            missing_only=self._missing_only,
            force_backend=self._force_backend,
            single_pkg=self._single_pkg,
            packages=self._packages,
        )
        track_dialog_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        self._worker.profile.connect(self._on_profile)
        self._worker.maintenance.connect(self._on_maintenance)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()
        # 安装开始后显示取消按钮
        self._cancel_button.setVisible(True)

    def _on_cancel_clicked(self) -> None:
        """取消按钮：确认后协作式取消安装（不杀线程，只 kill 子进程 + 设标志）。"""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "取消安装",
            "确定要取消安装吗？\n已下载的内容会保留，下次可继续补装缺失依赖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._worker and self._worker.isRunning():
            self._cancel_button.setEnabled(False)
            self._cancel_button.setText("正在取消...")
            self._log("用户取消安装，正在停止当前任务（可能需要数秒）...")
            self._worker.request_cancel()
            # 不阻塞 UI 事件循环：让 worker 自然结束，finished 信号会驱动后续 UI。
        else:
            self._cancel_button.setVisible(False)

    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        """进度更新（日志已在 InstallWorker._emit_progress 落盘，此处仅更新 UI）"""
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")

    @Slot(object)
    def _on_profile(self, profile: RuntimeProfileDescriptor) -> None:
        self._components_tree.clear()
        self._component_items.clear()
        for component in profile.components:
            item = QTreeWidgetItem(
                [component.display_name, "等待中", component.version or "—"]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, component.component_id)
            self._components_tree.addTopLevelItem(item)
            self._component_items[component.component_id] = item

    @Slot(object)
    def _on_maintenance(self, update: RuntimeMaintenanceUpdate) -> None:
        phase = _PHASE_LABELS.get(update.phase, update.phase)
        state = _STATE_LABELS.get(update.operation_state, update.operation_state)
        detail = phase
        if update.has_determinate_progress:
            assert update.progress_total is not None
            assert update.progress_current is not None
            self._progress_bar.setRange(0, update.progress_total)
            self._progress_bar.setValue(update.progress_current)
            detail = f"{phase} · {update.progress_current}/{update.progress_total}"
            if update.progress_unit == "bytes":
                detail += " bytes"
            if update.estimated_remaining_seconds is not None:
                detail += f" · 预计剩余 {update.estimated_remaining_seconds} 秒"
        else:
            self._progress_bar.setRange(0, 0)
            if (
                update.progress_unit == "steps"
                and update.progress_current is not None
                and update.progress_total is not None
            ):
                detail = (
                    f"{phase} · {update.progress_current}/{update.progress_total} 步"
                )
        self._stage_label.setText(f"{detail} · {state}")

        if update.component_id:
            item = self._component_items.get(update.component_id)
            if item is not None:
                item.setText(1, state)
                self._components_tree.scrollToItem(item)

        summary = f"Runtime {phase}：{state}"
        if update.component_id:
            item = self._component_items.get(update.component_id)
            component_name = item.text(0) if item is not None else update.component_id
            summary = f"Runtime {phase}：{component_name} · {state}"
        if update.event_type != "heartbeat":
            self._log(summary)
        if self._maintenance_callback is not None:
            self._maintenance_callback(summary)

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        """安装完成"""
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)

        if success:
            for item in self._component_items.values():
                item.setText(1, "已就绪")
            self._title_label.setText("安装成功!")
            # 单包/批量重装时 message 是具体结果（如"scipy 安装成功"/"已重装 3 个依赖包"），
            # 优先用它，避免笼统的"OCR依赖安装完成"（用户报告"单包却提示全部安装完毕"）。
            self._stage_label.setText(message or "OCR依赖安装完成")
            self._log(f"\n安装成功: {message}")
            self._close_button.setVisible(True)
            # 设置结果为成功
            self.install_succeeded.emit()
            self.done(1)
        else:
            self._title_label.setText("安装失败")
            self._stage_label.setText("安装过程中出现错误")
            self._log(f"\n安装失败: {message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            # 设置结果为失败
            self.done(0)

    def _log(self, message: str) -> None:
        """添加日志"""
        self._log_text.append(message)
        # 滚动到底部
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        """Request cancellation and return immediately; registry owns the worker."""
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        event.accept()

    def request_shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
        self.close()
