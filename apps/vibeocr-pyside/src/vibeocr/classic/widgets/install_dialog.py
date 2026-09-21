"""安装进度对话框"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from vibeocr.runtime_contracts.dtos import RuntimeInstallPlan

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
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
    RuntimeInstallerClientError,
    RuntimeMaintenanceUpdate,
    RuntimeProfileDescriptor,
)
from vibeocr.classic.runtime_maintenance import (
    InstallationRecord,
    RuntimeMaintenanceViewModel,
)
from vibeocr.classic.utils.dialog_workers import track_dialog_worker

logger = logging.getLogger(__name__)

_PHASE_LABELS = {
    "validate_binding": "验证组件绑定",
    "wait_for_lock": "等待运行时锁",
    "prepare_runtime": "准备 Python 运行时",
    "install_profile": "安装运行时依赖",
    "resolve_packages": "解析依赖",
    "download_artifacts": "下载依赖",
    "install_wheels": "安装依赖包",
    "probe_environment": "验证环境",
    "install_backend": "安装 Backend 服务",
    "verify_runtime": "验证运行时",
    "commit_runtime": "提交运行时",
}

_STATE_LABELS = {
    "queued": "等待中",
    "running": "进行中",
    "succeeded": "运行环境已验证",
    "failed": "失败",
    "cancelled": "已取消",
}

_MAINTENANCE_MIN_EMIT_INTERVAL_SECONDS = 0.5


def component_state_label(
    component: RuntimeComponentDescriptor,
    *,
    completed: bool = False,
    requested_component_ids: frozenset[str] = frozenset(),
    effective_component_ids: frozenset[str] = frozenset(),
) -> str:
    """Project the Runtime descriptor truth without implying every row downloads.

    ``requested_component_ids`` 是用户本次勾选的组件；未勾选的可选组件是否
    被安装由 Backend 解析的依赖闭包决定。Classic 不预猜闭包，
    在 maintenance 事件回报 ``effective_component_ids`` 前先标“未选择”，
    回报后如实标注“随闭包一并安装”。无显式勾选的 profile 操作
    （切换推理后端、修复 Runtime）同样按 ``effective_component_ids`` 标注，
    避免 Backend 回报闭包前停留在含义模糊的“等待中”。
    """

    if component.desired_state == "not_required":
        return "不需要"
    if completed or component.actual_state == "ready":
        return "已就绪"
    if component.included_in_base:
        return "随包提供"
    if (
        requested_component_ids
        and component.component_id not in requested_component_ids
    ):
        if component.component_id in effective_component_ids:
            return "随闭包一并安装"
        return "未选择"
    if (
        not requested_component_ids
        and component.component_id in effective_component_ids
    ):
        return "随闭包一并安装"
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
    detail_note: str | None = None,
) -> MaintenanceProgressDetail:
    """把维护事件统一渲染为进度条区间与阶段文案。

    首启 BackendChoiceDialog 与设置页 InstallDialog 共用，避免百分比、
    字节与步数格式化逻辑出现两份逐渐分歧的实现。仅在渲染不确定进度的
    running 事件时才读取 clock（保持阶段计时的调用语义不变）。
    ``detail_note`` 是调用方维护的“当前在做什么”明细（如 Installer 事件
    回报的 fallback_message），为空时不影响既有文案。
    """
    phase = _PHASE_LABELS.get(update.phase, update.phase)
    details = []
    for key, label in (
        ("package", "依赖包"),
        ("step", "活动"),
        ("elapsed_seconds", "总耗时（秒）"),
        ("last_activity_seconds", "距上次活动（秒）"),
        ("reason_code", "原因"),
        ("next_action", "下一步"),
    ):
        value = update.message_args.get(key)
        if value:
            details.append(f"{label}：{value}")
    if (
        update.progress_unit == "bytes"
        and update.progress_current is not None
        and update.progress_total is None
    ):
        details.append(
            f"已下载 {_format_byte_count(update.progress_current)}，总量未知"
        )
    if update.message_args.get("model_readiness") == "not_checked":
        details.append("依赖已安装，模型仍需首次准备或验证")
    if details:
        detail_note = " · ".join(filter(None, (detail_note, *details)))
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
        if detail_note:
            detail += f" · {detail_note}"
        if scope_note:
            detail += f" · {scope_note}"
        if source_note:
            detail += f" · {source_note}"
        return MaintenanceProgressDetail(
            detail=detail,
            phase_label=phase,
            state_label=state,
            determinate=True,
            progress_value=(
                update.progress_current
                if update.progress_total <= 2_147_483_647
                else min(1000, update.progress_current * 1000 // update.progress_total)
            ),
            progress_maximum=min(update.progress_total, 1000)
            if update.progress_total > 2_147_483_647
            else update.progress_total,
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
    if detail_note:
        detail += f" · {detail_note}"
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


def describe_install_plan(
    plan: RuntimeInstallPlan | None, component_names: Mapping[str, str] | None = None
) -> str:
    """Render Backend's actions without constructing a client dependency graph."""
    if plan is None:
        return "将检查当前已安装组件；仅在损坏时修复已有闭包，不补装标为“不需要”的引擎。无损坏时不会重建。确认后停止服务并执行。"
    actions = {"install": "安装", "retain": "保留", "replace": "替换", "remove": "移除"}
    reasons = {
        "requested": "用户选择",
        "required_dependency": "必需依赖",
        "removed_by_selection": "本次选择不再需要",
    }
    names = component_names or {}
    device = "NVIDIA GPU" if plan.accelerator.value == "nvidia_cuda" else "CPU"
    lines = [f"目标推理设备：{device}"]
    for component in plan.components:
        action = component.action.value
        reason = "、".join(reasons.get(code, code) for code in component.reason_codes)
        lines.append(
            f"{actions.get(action, action)} {names.get(component.component_id, component.component_id)}（{reason}）"
        )
    lines.append(f"依赖包来源：{'、'.join(plan.effective_download_source_ids)}")
    cost = plan.cost
    lines.append(
        "下载量："
        + (
            _format_byte_count(cost.download_bytes)
            if cost.download_bytes is not None
            else "尚未知，解析依赖后显示真实流量"
        )
    )
    lines.append(
        "新增磁盘占用："
        + (
            _format_byte_count(cost.additional_disk_bytes)
            if cost.additional_disk_bytes is not None
            else "尚未知"
        )
    )
    lines.append("模型由引擎原生来源在首次准备时获取；安装依赖不表示已可识别。")
    for blocker in plan.blockers:
        lines.append(f"无法安装：{blocker.code} · {blocker.next_action}")
    return "\n".join(lines)


class InstallWorker(QThread):
    """通过唯一 Runtime Installer API 安装或修复完整运行时。"""

    operation_recovered = (
        Signal()
    )  # A previous operation reached a known terminal state.
    plan_ready = Signal(object)  # RuntimeInstallPlan or repair scope
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
        self._confirmation = threading.Event()
        self._operation_id = str(uuid4())
        self._record: InstallationRecord | None = None
        self._maintenance_signature: tuple[str, str, str, str, str] | None = None
        self._maintenance_logged_signature: tuple[str, str, str, str, str] | None = None
        self._maintenance_progress_bucket: int | None = None
        self._maintenance_emitted_at = 0.0

    def confirm_install(self) -> None:
        self._confirmation.set()

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
            accelerator = {
                "cpu": "cpu",
                "gpu": "nvidia_cuda",
            }.get(self._force_backend)
            client = RuntimeInstallerClient(
                self._project_root,
                accelerator=accelerator,
            )
            previous = InstallationRecord.read(self._project_root)
            if previous is not None and previous.state in {"queued", "running"}:
                self._record = previous
                cursor = previous.sequence
                while True:
                    try:
                        page = client.observe(
                            previous.operation_id, after_sequence=cursor
                        )
                    except RuntimeInstallerClientError as exc:
                        oldest = exc.detail.get("oldest_sequence")
                        if (
                            exc.canonical_code != "RUNTIME_CURSOR_EXPIRED"
                            or type(oldest) is not int
                            or oldest <= cursor + 1
                        ):
                            raise
                        cursor = oldest - 1
                        self._emit_progress(
                            "恢复安装状态",
                            "部分历史进度已过期，正在读取 Backend 保留的事件。",
                        )
                        if self._cancel_event.is_set():
                            raise RuntimeInstallerCancelled(
                                "已停止观察；原操作仍由 Backend 管理"
                            ) from exc
                        continue

                    for update in page.events:
                        self._emit_maintenance(update)
                    cursor = page.through_sequence
                    if page.more:
                        if self._cancel_event.is_set():
                            raise RuntimeInstallerCancelled(
                                "已停止观察；原操作仍由 Backend 管理"
                            )
                        continue
                    if (
                        page.snapshot is not None
                        and page.snapshot.sequence > self._record.sequence
                    ):
                        self._emit_maintenance(page.snapshot)
                    if self._record.state in {"succeeded", "failed", "cancelled"}:
                        break
                    if self._cancel_event.wait(0.5):
                        raise RuntimeInstallerCancelled(
                            "已停止观察；原操作仍由 Backend 管理"
                        )
                record = self._record
                self.operation_recovered.emit()
                self.completed.emit(
                    record.state == "succeeded",
                    f"已恢复上次操作：{record.summary}；未启动新安装",
                )
                return
            repair = (
                self._reinstall_python
                or self._missing_only
                or self._single_pkg is not None
                or self._packages is not None
            )
            if repair:
                inspection = client.inspect()
                client.accelerator = inspection.accelerator
                self.profile.emit(
                    RuntimeProfileDescriptor(
                        inspection.profile,
                        inspection.accelerator,
                        inspection.components,
                    )
                )
            else:
                self.profile.emit(
                    client.profile_descriptor(
                        install_component_ids=self._install_component_ids
                    )
                )
            plan = (
                None
                if repair
                else client.preview_install_plan(
                    install_component_ids=self._install_component_ids,
                    download_source_ids=self._download_source_ids,
                )
            )
            self.plan_ready.emit(plan)
            if plan is not None and plan.blockers:
                raise RuntimeInstallerClientError(
                    "安装计划存在阻碍，请查看预览并处理后重试"
                )
            while not self._confirmation.wait(0.1):
                if self._cancel_event.is_set():
                    raise RuntimeInstallerCancelled("已取消，安装尚未开始")
            if self._cancel_event.is_set():
                raise RuntimeInstallerCancelled("已取消，安装尚未开始")
            self._record = InstallationRecord(
                self._operation_id,
                0,
                "queued",
                plan.plan_id if plan is not None else None,
                self._install_component_ids,
            )
            self._record.save(self._project_root)
            self._emit_progress("运行时维护", "正在断开 OCR 运行时...")
            try:
                from vibeocr.classic.client import shutdown_backend_client

                shutdown_backend_client()
            except Exception as exc:
                logger.warning("关闭旧 WorkerHost 失败，将继续安装: %s", exc)
            if repair:
                self._emit_progress(
                    "运行时修复",
                    "正在检查已安装组件；仅修复损坏闭包，不补装其他引擎...",
                )
                client.repair(
                    operation_id=self._operation_id,
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
                    operation_id=self._operation_id,
                    plan_id=plan.plan_id,
                )
            if self._record is not None and self._record.state not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                self._record = replace(self._record, state="succeeded")
                self._record.save(self._project_root)
            self.completed.emit(True, "运行环境已验证；模型可能仍需首次准备")
        except RuntimeInstallerCancelled as exc:
            logger.info("安装取消: %s", exc)
            self.completed.emit(False, str(exc))
        except RuntimeInstallerClientError as exc:
            if self._record is not None and exc.canonical_code in {
                "RUNTIME_INSTALL_PLAN_STALE",
                "RUNTIME_INSTALL_PLAN_BLOCKED",
                "RUNTIME_OPERATION_ID_CONFLICT",
                "RUNTIME_OPERATION_NOT_FOUND",
            }:
                self._record = replace(
                    self._record,
                    state="failed",
                    reason_code=exc.canonical_code,
                    next_action="refresh_install_plan",
                )
                self._record.save(self._project_root)
                self.completed.emit(
                    False, f"{exc}\n请关闭窗口，重新读取安装计划并确认。"
                )
            else:
                self.completed.emit(False, str(exc))
        except Exception as exc:
            logger.exception("Runtime Installer 异常")
            self.completed.emit(False, f"安装异常: {exc}")

    def _emit_maintenance(self, update: RuntimeMaintenanceUpdate) -> None:
        if not self._should_emit_maintenance(update):
            return
        record = self._record
        if (
            record is not None
            and update.operation_id == record.operation_id
            and update.sequence > record.sequence
        ):
            self._record = replace(
                record,
                sequence=update.sequence,
                state=update.operation_state,
                reason_code=update.message_args.get("reason_code", ""),
                next_action=update.message_args.get("next_action", ""),
            )
            self._record.save(self._project_root)
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
    install_completed = Signal(bool, str)
    operation_recovered = Signal()

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
        before_install: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._close_requested = False
        self._before_install = before_install
        self._terminal_success = False
        self._missing_only = missing_only
        self._force_backend = force_backend
        self._single_pkg = single_pkg
        self._packages = packages
        self._maintenance_callback = maintenance_callback
        self._install_component_ids = install_component_ids
        self._download_source_ids = download_source_ids
        # 用户本次勾选的可选组件与 Backend 回报的实际安装闭包；用于区分
        # “待安装”与“未选择/随闭包一并安装”两类行文案。
        self._requested_install_ids = frozenset(install_component_ids or ())
        self._effective_install_ids: frozenset[str] = frozenset()
        self._component_items: dict[str, QTreeWidgetItem] = {}
        self._component_descriptors: dict[str, RuntimeComponentDescriptor] = {}
        self._last_maintenance_summary: str | None = None
        self._activity_clock = MaintenanceActivityClock()
        # 事件是离散到达的；已用时走字与明细展示由 1 秒 QTimer 基于最后一个
        # 事件本地重渲染，避免事件间隔大（如在线安装 5 秒心跳）时界面停更。
        self._last_maintenance_update: RuntimeMaintenanceUpdate | None = None
        self._sticky_detail: tuple[str, str] | None = None
        self._stage_refresh_timer = QTimer(self)
        self._stage_refresh_timer.setInterval(1000)
        self._stage_refresh_timer.timeout.connect(self._refresh_stage_label)
        self._setup_ui()
        self._worker: InstallWorker | None = None

    def _setup_ui(self) -> None:
        """设置UI"""
        repairing = (
            self._missing_only
            or self._single_pkg is not None
            or self._packages is not None
        )
        self.setWindowTitle("修复运行环境" if repairing else "安装运行环境")
        self._title_text = "正在读取安装范围，尚未开始安装"
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

        self._confirm_button = QPushButton("确认并安装")
        self._confirm_button.setVisible(False)
        self._confirm_button.clicked.connect(self._confirm_install)
        layout.addWidget(self._confirm_button)

        # 取消按钮（安装进行中显示，触发协作式取消）
        self._cancel_button = QPushButton("取消安装")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.setVisible(False)
        layout.addWidget(self._cancel_button)

        # 关闭按钮（初始隐藏）
        self._close_button = QPushButton("关闭")
        self._close_button.clicked.connect(
            lambda: self.done(1 if self._terminal_success else 0)
        )
        self._close_button.setVisible(False)
        layout.addWidget(self._close_button)

    def showEvent(self, event) -> None:
        """显示事件 - 开始安装"""
        super().showEvent(event)
        if not self._worker:
            self._start_install()

    def _start_install(self) -> None:
        """开始安装"""
        self._log("正在读取运行环境范围；确认后执行安装或修复。")

        self._worker = InstallWorker(
            self._project_root,
            missing_only=self._missing_only,
            force_backend=self._force_backend,
            single_pkg=self._single_pkg,
            packages=self._packages,
            install_component_ids=self._install_component_ids,
            download_source_ids=self._download_source_ids,
        )
        self._worker.finished.connect(self._on_worker_stopped)
        track_dialog_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        if hasattr(self._worker, "operation_recovered"):
            self._worker.operation_recovered.connect(self.operation_recovered.emit)
        self._worker.plan_ready.connect(self._on_plan_ready)
        self._worker.profile.connect(self._on_profile)
        self._worker.maintenance.connect(self._on_maintenance)
        self._worker.completed.connect(self._on_finished)
        self._worker.start()
        self._stage_refresh_timer.start()
        # 安装开始后显示取消按钮
        self._cancel_button.setVisible(True)

    def _on_worker_stopped(self) -> None:
        # The tracker deletes finished QThreads; retained failure UI must release it.
        self._worker = None
        if self._close_requested:
            self.done(1 if self._terminal_success else 0)

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

    @Slot(object)
    def _on_plan_ready(self, plan: RuntimeInstallPlan | None) -> None:
        # Reuse operations may omit scope from every event; retain Backend truth.
        self._effective_install_ids = (
            frozenset(plan.effective_component_ids)
            if plan is not None
            else frozenset(
                key
                for key, component in self._component_descriptors.items()
                if component.desired_state == "ready"
            )
        )
        self._title_label.setText("请确认运行环境变更")
        self._stage_label.setText("预览已就绪；确认前不会停止服务或安装")
        self._log(
            describe_install_plan(
                plan,
                {
                    key: value.display_name
                    for key, value in self._component_descriptors.items()
                },
            )
        )
        self._confirm_button.setVisible(True)
        self._confirm_button.setEnabled(plan is None or not plan.blockers)

    def can_start_installation(self) -> bool:
        return (
            self._worker is not None
            and self._worker.isRunning()
            and not self._worker.is_cancelled()
        )

    def _confirm_install(self) -> None:
        worker = self._worker
        if (
            worker is None
            or not worker.isRunning()
            or not self._confirm_button.isEnabled()
        ):
            return
        self._confirm_button.setEnabled(False)
        if self._before_install is None:
            worker.confirm_install()
        else:
            self._before_install(worker.confirm_install)

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
                    component_state_label(
                        component,
                        requested_component_ids=self._requested_install_ids,
                        effective_component_ids=self._effective_install_ids,
                    ),
                    component.actual_version or component.version or "—",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, component.component_id)
            self._components_tree.addTopLevelItem(item)
            self._component_items[component.component_id] = item
            self._component_descriptors[component.component_id] = component

    @Slot(object)
    def _on_maintenance(self, update: RuntimeMaintenanceUpdate) -> None:
        previous = self._last_maintenance_update
        if (
            previous is not None
            and previous.operation_id == update.operation_id
            and update.sequence <= previous.sequence
        ):
            return
        self._last_maintenance_update = update
        self._cancel_button.setEnabled(
            update.operation_state in {"queued", "running"}
            and update.phase != "commit_runtime"
        )
        rendered = self._render_maintenance_stage(update)
        self._sync_closure_rows(update)

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

    def _render_maintenance_stage(
        self, update: RuntimeMaintenanceUpdate
    ) -> MaintenanceProgressDetail:
        """按最后一个事件渲染阶段行与进度条；可被 QTimer 重复调用。"""

        rendered = build_maintenance_detail(
            update,
            clock=self._activity_clock,
            detail_note=self._current_detail_note(update),
        )
        if rendered.determinate:
            self._progress_bar.setRange(0, rendered.progress_maximum)
            self._progress_bar.setValue(rendered.progress_value)
        else:
            self._progress_bar.setRange(0, 0)
        self._stage_label.setText(f"{rendered.detail} · {rendered.state_label}")
        return rendered

    def _refresh_stage_label(self) -> None:
        """QTimer 槽：事件静默期让已用时/明细继续走字，不产生新日志。"""

        if self._last_maintenance_update is None:
            return
        self._render_maintenance_stage(self._last_maintenance_update)

    def _current_detail_note(self, update: RuntimeMaintenanceUpdate) -> str | None:
        """解析事件的“当前在做什么”明细，并在同阶段内保持粘性。

        Installer 只在明细变化时携带 ``fallback_message``；后续心跳事件不带
        该字段，若不粘性保留，界面会在两次明细事件之间闪回无明细文案。
        """

        note = update.fallback_message
        if note:
            self._sticky_detail = (update.phase, note)
            return note
        cached = self._sticky_detail
        if cached is not None and cached[0] == update.phase:
            return cached[1]
        return None

    def _sync_closure_rows(self, update: RuntimeMaintenanceUpdate) -> None:
        """按 Backend 回报的 effective 闭包更新未勾选可选组件的行文案。

        只刷新“未选择/随闭包一并安装”类行；已勾选组件的行文案由各组件
        自身的 maintenance 事件驱动，不能被整体刷新覆盖。无显式勾选的全量
        profile 操作（切换后端/修复）同样消费 ``effective_component_ids``，
        否则 MinerU/PaddleOCR 会整场停留在“等待中”直到最终成功。
        """

        effective = frozenset(update.effective_component_ids)
        if not effective or effective == self._effective_install_ids:
            return
        self._effective_install_ids = effective
        for component_id, component in self._component_descriptors.items():
            if (
                component_id in self._requested_install_ids
                or component.included_in_base
            ):
                continue
            item = self._component_items.get(component_id)
            if item is not None:
                item.setText(
                    1,
                    component_state_label(
                        component,
                        requested_component_ids=self._requested_install_ids,
                        effective_component_ids=effective,
                    ),
                )

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        """安装完成"""
        self._terminal_success = success
        self.install_completed.emit(success, message)
        if self._close_requested:
            if success:
                self.install_succeeded.emit()
            return
        self._confirm_button.setVisible(False)
        self._stage_refresh_timer.stop()
        self._last_maintenance_update = None
        self._sticky_detail = None
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)

        if success:
            for component_id, item in self._component_items.items():
                component = self._component_descriptors[component_id]
                # Missing scope is not evidence that every component was installed.
                completed = component_id in self._effective_install_ids
                item.setText(
                    1,
                    component_state_label(
                        component,
                        completed=completed,
                        requested_component_ids=self._requested_install_ids,
                        effective_component_ids=self._effective_install_ids,
                    ),
                )
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
            self._title_label.setText(
                "已取消"
                if self._worker is not None and self._worker.is_cancelled()
                else "安装未完成"
            )
            self._stage_label.setText(message or "安装未完成，请检查详情")
            self._log(f"\n安装失败: {message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            # 设置结果为失败
            # Keep the failure visible while the controller restores the effective runtime.
            if self._maintenance_callback is not None:
                self._maintenance_callback(f"本次安装未完成：{message}")

    def _log(self, message: str) -> None:
        """添加日志"""
        self._log_text.append(message)
        # 滚动到底部
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def reject(self) -> None:
        """Keep ownership until cancellation or a late commit reports its result."""
        self._close_requested = True
        self._stage_refresh_timer.stop()
        self._last_maintenance_update = None
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.request_cancel()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        self.reject()
        if self._worker is not None:
            event.ignore()
        else:
            event.accept()

    def request_shutdown(self) -> None:
        self.reject()
