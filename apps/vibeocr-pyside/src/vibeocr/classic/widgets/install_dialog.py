"""安装进度对话框"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    RuntimeComponentDescriptor,
    RuntimeInstallerCancelled,
    RuntimeInstallerClient,
    RuntimeMaintenanceUpdate,
    RuntimeProfileDescriptor,
)
from vibeocr.classic.runtime_maintenance import RuntimeMaintenanceViewModel
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

_MAINTENANCE_MIN_EMIT_INTERVAL_SECONDS = 0.5


def component_state_label(
    component: RuntimeComponentDescriptor, *, completed: bool = False
) -> str:
    """Project the Runtime descriptor truth without implying every row downloads."""

    if component.desired_state == "not_required":
        return "不需要"
    if completed or component.actual_state == "ready":
        return "已就绪"
    if component.included_in_base:
        return "随包提供"
    return {
        "missing": "缺失",
        "drifted": "需修复",
        "unknown": "未知",
    }.get(component.actual_state, "等待中")


def _format_byte_count(value: int) -> str:
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{value} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


@dataclass(frozen=True, slots=True)
class MaintenanceProgressDetail:
    """由 RuntimeMaintenanceUpdate 派生的进度条区间与阶段文案渲染结果。"""

    detail: str
    phase_label: str
    state_label: str
    determinate: bool = False
    progress_value: int = 0
    progress_maximum: int = 0


class MaintenanceActivityClock:
    """按维护事件签名跟踪当前阶段已用时（秒），签名变化时重置起点。"""

    def __init__(self) -> None:
        self._signature: tuple[str, str, str, str] | None = None
        self._started_at = 0.0

    def elapsed_seconds(self, update: RuntimeMaintenanceUpdate) -> int:
        signature = (
            update.operation_id,
            update.phase,
            update.component_id or "",
            update.message_code or "",
        )
        now = time.monotonic()
        if signature != self._signature:
            self._signature = signature
            self._started_at = now
        return max(0, int(now - self._started_at))


def build_maintenance_detail(
    update: RuntimeMaintenanceUpdate,
    clock: MaintenanceActivityClock | None = None,
) -> MaintenanceProgressDetail:
    """把维护事件统一渲染为进度条区间与阶段文案。

    首启 BackendChoiceDialog 与设置页 InstallDialog 共用，避免百分比、
    字节与步数格式化逻辑出现两份逐渐分歧的实现。仅在渲染不确定进度的
    running 事件时才读取 clock（保持阶段计时的调用语义不变）。
    """
    phase = _PHASE_LABELS.get(update.phase, update.phase)
    state = _STATE_LABELS.get(update.operation_state, update.operation_state)
    scope_note = _component_scope_note(update)
    view = RuntimeMaintenanceViewModel.from_update(update)
    source_note = view.source_summary
    if source_note:
        source_note += f"；{view.next_operation_note}"
    if update.has_determinate_progress:
        assert update.progress_total is not None
        assert update.progress_current is not None
        percent = update.progress_current * 100 / update.progress_total
        percent_text = f"{percent:.1f}".rstrip("0").rstrip(".")
        if update.progress_unit == "bytes":
            detail = (
                f"{phase} · {percent_text}% · "
                f"{_format_byte_count(update.progress_current)} / "
                f"{_format_byte_count(update.progress_total)}"
            )
        else:
            detail = (
                f"{phase} · {percent_text}% · "
                f"{update.progress_current}/{update.progress_total} 项"
            )
        if update.estimated_remaining_seconds is not None:
            detail += f" · 预计剩余 {update.estimated_remaining_seconds} 秒"
        if scope_note:
            detail += f" · {scope_note}"
        if source_note:
            detail += f" · {source_note}"
        return MaintenanceProgressDetail(
            detail=detail,
            phase_label=phase,
            state_label=state,
            determinate=True,
            progress_value=update.progress_current,
            progress_maximum=update.progress_total,
        )
    detail = phase
    if (
        update.progress_unit == "steps"
        and update.progress_current is not None
        and update.progress_total is not None
    ):
        detail = f"{phase} · {update.progress_current}/{update.progress_total} 步"
    if update.operation_state == "running" and clock is not None:
        detail += f" · 已用时 {clock.elapsed_seconds(update)} 秒"
    if scope_note:
        detail += f" · {scope_note}"
    if source_note:
        detail += f" · {source_note}"
    return MaintenanceProgressDetail(
        detail=detail, phase_label=phase, state_label=state
    )


def _component_scope_note(update: RuntimeMaintenanceUpdate) -> str:
    """requested/effective 组件回显差异说明；闭包扩大时不伪称只装勾选项。"""

    effective = tuple(update.effective_component_ids)
    requested = tuple(update.requested_component_ids)
    if not effective and not requested:
        return ""
    if effective == requested:
        return ""
    if not requested:
        return f"Backend 实际安装：{'、'.join(effective)}"
    return (
        f"实际安装 {'、'.join(effective)}（请求：{'、'.join(requested)}）"
        if effective
        else f"请求：{'、'.join(requested)}"
    )


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
        install_component_ids: tuple[str, ...] | None = None,
        download_source_ids: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
        self._single_pkg = single_pkg
        self._packages = packages
        self._install_component_ids = install_component_ids
        self._download_source_ids = download_source_ids
        self._cancel_event = threading.Event()
        self._maintenance_signature: tuple[str, str, str, str, str] | None = None
        self._maintenance_logged_signature: tuple[str, str, str, str, str] | None = None
        self._maintenance_progress_bucket: int | None = None
        self._maintenance_emitted_at = 0.0

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
            repair = (
                self._reinstall_python
                or self._missing_only
                or self._single_pkg is not None
                or self._packages is not None
            )
            self.profile.emit(
                client.profile_descriptor(
                    install_component_ids=(
                        None if repair else self._install_component_ids
                    )
                )
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
                    install_component_ids=self._install_component_ids,
                    download_source_ids=self._download_source_ids,
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
        if not self._should_emit_maintenance(update):
            return
        phase = _PHASE_LABELS.get(update.phase, update.phase)
        component = update.component_id or "runtime"
        signature = self._maintenance_update_signature(update)
        if signature != self._maintenance_logged_signature:
            log = logger.debug if update.event_type == "heartbeat" else logger.info
            log(
                "[Runtime Installer] %s component=%s state=%s sequence=%s code=%s",
                phase,
                component,
                update.operation_state,
                update.sequence,
                update.message_code,
            )
            self._maintenance_logged_signature = signature
        self.maintenance.emit(update)

    def _should_emit_maintenance(self, update: RuntimeMaintenanceUpdate) -> bool:
        signature = self._maintenance_update_signature(update)
        progress_bucket = self._progress_bucket(update)
        now = time.monotonic()
        signature_changed = signature != self._maintenance_signature
        progress_changed = (
            progress_bucket is not None
            and progress_bucket != self._maintenance_progress_bucket
        )
        interval_elapsed = (
            now - self._maintenance_emitted_at >= _MAINTENANCE_MIN_EMIT_INTERVAL_SECONDS
        )
        if not (signature_changed or progress_changed or interval_elapsed):
            return False
        self._maintenance_signature = signature
        if progress_bucket is not None:
            self._maintenance_progress_bucket = progress_bucket
        self._maintenance_emitted_at = now
        return True

    @staticmethod
    def _maintenance_update_signature(
        update: RuntimeMaintenanceUpdate,
    ) -> tuple[str, str, str, str, str]:
        return (
            update.operation_id,
            update.phase,
            update.component_id or "",
            update.operation_state,
            update.message_code or "",
        )

    @staticmethod
    def _progress_bucket(update: RuntimeMaintenanceUpdate) -> int | None:
        current = update.progress_current
        total = update.progress_total
        if current is None or total is None or total <= 0:
            return None
        return min(100, max(0, current * 100 // total))


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
        install_component_ids: tuple[str, ...] | None = None,
        download_source_ids: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._missing_only = missing_only
        self._force_backend = force_backend
        self._single_pkg = single_pkg
        self._packages = packages
        self._maintenance_callback = maintenance_callback
        self._install_component_ids = install_component_ids
        self._download_source_ids = download_source_ids
        self._component_items: dict[str, QTreeWidgetItem] = {}
        self._component_descriptors: dict[str, RuntimeComponentDescriptor] = {}
        self._last_maintenance_summary: str | None = None
        self._activity_clock = MaintenanceActivityClock()
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
            install_component_ids=self._install_component_ids,
            download_source_ids=self._download_source_ids,
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
        self._component_descriptors.clear()
        for component in profile.components:
            item = QTreeWidgetItem(
                [
                    component.display_name,
                    component_state_label(component),
                    component.actual_version or component.version or "—",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, component.component_id)
            self._components_tree.addTopLevelItem(item)
            self._component_items[component.component_id] = item
            self._component_descriptors[component.component_id] = component

    @Slot(object)
    def _on_maintenance(self, update: RuntimeMaintenanceUpdate) -> None:
        rendered = build_maintenance_detail(update, clock=self._activity_clock)
        if rendered.determinate:
            self._progress_bar.setRange(0, rendered.progress_maximum)
            self._progress_bar.setValue(rendered.progress_value)
        else:
            self._progress_bar.setRange(0, 0)
        self._stage_label.setText(f"{rendered.detail} · {rendered.state_label}")

        if update.component_id:
            item = self._component_items.get(update.component_id)
            if item is not None:
                item.setText(1, rendered.state_label)
                self._components_tree.scrollToItem(item)

        summary = f"Runtime {rendered.phase_label}：{rendered.state_label}"
        if update.component_id:
            item = self._component_items.get(update.component_id)
            component_name = item.text(0) if item is not None else update.component_id
            summary = (
                f"Runtime {rendered.phase_label}：{component_name} · "
                f"{rendered.state_label}"
            )
        if summary != self._last_maintenance_summary:
            if update.event_type != "heartbeat":
                self._log(summary)
            if self._maintenance_callback is not None:
                self._maintenance_callback(summary)
            self._last_maintenance_summary = summary

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        """安装完成"""
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)

        if success:
            for component_id, item in self._component_items.items():
                component = self._component_descriptors[component_id]
                item.setText(1, component_state_label(component, completed=True))
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
