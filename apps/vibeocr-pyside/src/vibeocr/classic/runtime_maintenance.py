"""UI-neutral Runtime request, projection and product-maintenance seam."""

from __future__ import annotations

import os
import json
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeocr.classic.runtime_installation import RuntimeMaintenanceUpdate


class RuntimeInstallerClientError(RuntimeError):
    """The installer could not complete or returned an invalid envelope."""

    def __init__(
        self,
        message: str,
        *,
        canonical_code: str | None = None,
        category: str | None = None,
        retryable: bool = False,
        retry_after: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.canonical_code = canonical_code
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class InstallationRecord:
    """Last user-confirmed operation; service readiness never overwrites it."""

    operation_id: str
    sequence: int
    state: str
    plan_id: str | None = None
    requested_component_ids: tuple[str, ...] | None = None
    reason_code: str = ""
    next_action: str = ""

    @staticmethod
    def path(product_root: Path) -> Path:
        return product_root / "config" / "installation-operation.json"

    @classmethod
    def read(cls, product_root: Path) -> InstallationRecord | None:
        path = cls.path(product_root)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RuntimeInstallerClientError(
                "上次安装记录无法读取，请检查诊断；未开始新安装"
            ) from exc
        try:
            UUID(value["operation_id"])
            if type(value["sequence"]) is not int or value["sequence"] < 0:
                raise ValueError("invalid sequence")
            if value["state"] not in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
            }:
                raise ValueError("invalid state")
            scope = value.get("requested_component_ids")
            if scope is not None and (
                not isinstance(scope, list)
                or any(not isinstance(v, str) for v in scope)
            ):
                raise ValueError("invalid scope")
            for key in ("reason_code", "next_action"):
                if not isinstance(value.get(key, ""), str):
                    raise ValueError("invalid guidance")
            plan_id = value.get("plan_id")
            if plan_id is not None and not isinstance(plan_id, str):
                raise ValueError("invalid plan")
            return cls(
                value["operation_id"],
                value["sequence"],
                value["state"],
                plan_id,
                None if scope is None else tuple(scope),
                value.get("reason_code", ""),
                value.get("next_action", ""),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RuntimeInstallerClientError("上次安装记录无效；未开始新安装") from exc

    def save(self, product_root: Path) -> None:
        path = self.path(product_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        value = {
            "operation_id": self.operation_id,
            "sequence": self.sequence,
            "state": self.state,
            "plan_id": self.plan_id,
            "requested_component_ids": self.requested_component_ids,
            "reason_code": self.reason_code,
            "next_action": self.next_action,
        }
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def summary(self) -> str:
        labels = {
            "queued": "等待确认执行结果",
            "running": "进行中或等待恢复确认",
            "succeeded": "运行环境已验证；模型仍需准备或验证",
            "failed": "失败",
            "cancelled": "已取消",
        }
        return " · ".join(
            filter(
                None,
                (f"本次安装：{labels[self.state]}", self.reason_code, self.next_action),
            )
        )


class RuntimeInstallerCancelled(RuntimeInstallerClientError):
    """The caller cancelled an in-progress installer operation."""


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceRequestBuilder:
    """Build the shared ensure/retry selection fragment and enforce gates."""

    negotiated_capabilities: tuple[str, ...]

    def selection_fields(
        self,
        *,
        operation: str,
        install_component_ids: tuple[str, ...] | None,
        download_source_ids: tuple[str, ...] | None,
    ) -> dict[str, list[str]]:
        if install_component_ids is None and download_source_ids is None:
            return {}
        capabilities = set(self.negotiated_capabilities)
        if "runtime.maintenance.v2" not in capabilities:
            raise RuntimeInstallerClientError(
                "Runtime selection requires runtime.maintenance.v2"
            )
        if operation not in {"ensure", "retry"}:
            raise RuntimeInstallerClientError(
                "install_component_ids/download_source_ids 仅用于 ensure/retry"
            )
        if download_source_ids is not None and not download_source_ids:
            raise RuntimeInstallerClientError(
                "download_source_ids 不能发送空列表；空选择应省略字段"
            )
        if (
            install_component_ids is not None
            and "runtime.component-selection.v1" not in capabilities
        ):
            raise RuntimeInstallerClientError("Runtime 不支持组件手动选择")
        if (
            download_source_ids is not None
            and "runtime.download-sources.v1" not in capabilities
        ):
            raise RuntimeInstallerClientError("Runtime 不支持下载源选择")
        fields: dict[str, list[str]] = {}
        if install_component_ids is not None:
            fields["install_component_ids"] = list(install_component_ids)
        if download_source_ids is not None:
            fields["download_source_ids"] = list(download_source_ids)
        return fields


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceViewModel:
    """UI-neutral projection of requested/effective maintenance truth."""

    requested_component_ids: tuple[str, ...]
    effective_component_ids: tuple[str, ...]
    requested_source_ids: tuple[str, ...]
    effective_source_ids: tuple[str, ...]

    @classmethod
    def from_update(
        cls, update: RuntimeMaintenanceUpdate
    ) -> RuntimeMaintenanceViewModel:
        return cls(
            requested_component_ids=tuple(update.requested_component_ids),
            effective_component_ids=tuple(update.effective_component_ids),
            requested_source_ids=tuple(update.requested_download_source_ids),
            effective_source_ids=tuple(update.effective_download_source_ids),
        )

    @property
    def source_summary(self) -> str:
        if not self.requested_source_ids and not self.effective_source_ids:
            return ""
        requested = "、".join(self.requested_source_ids) or "Backend 默认源"
        effective = "、".join(self.effective_source_ids) or "Backend 未回显"
        return f"请求源：{requested}；实际源：{effective}"

    @property
    def next_operation_note(self) -> str:
        return "运行中的源已快照；设置修改仅影响下一次操作"


class ProductMaintenanceOwner(str, Enum):
    IDLE = "Idle"
    RUNTIME = "RuntimeMaintenance"
    UPDATE = "AppUpdate"


class ProductMaintenanceBusy(RuntimeError):
    """A second maintenance owner could not be started safely."""


@dataclass(slots=True)
class _RuntimeControl:
    cancel: Callable[[], None]
    wait_terminal: Callable[[float], bool]


class ProductMaintenanceLease:
    def __init__(self, coordinator: ProductMaintenanceCoordinator, token: UUID) -> None:
        self._coordinator = coordinator
        self._token = token
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._coordinator._release(self._token)
            self._released = True

    def __enter__(self) -> ProductMaintenanceLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class ProductMaintenanceCoordinator:
    """Serialize Runtime maintenance and app update behind one interface."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._condition = threading.Condition()
        self._owner = ProductMaintenanceOwner.IDLE
        self._token: UUID | None = None
        self._runtime_control: _RuntimeControl | None = None
        self._file: BinaryIO | None = None

    @property
    def owner(self) -> ProductMaintenanceOwner:
        with self._condition:
            return self._owner

    def begin_runtime_maintenance(
        self,
        *,
        cancel: Callable[[], None],
        wait_terminal: Callable[[float], bool],
    ) -> ProductMaintenanceLease:
        with self._condition:
            if self._owner is not ProductMaintenanceOwner.IDLE:
                raise ProductMaintenanceBusy(
                    f"product maintenance is owned by {self._owner.value}"
                )
            return self._acquire(
                ProductMaintenanceOwner.RUNTIME,
                _RuntimeControl(cancel, wait_terminal),
            )

    def begin_app_update(
        self, *, cancel_runtime: bool, timeout: float
    ) -> ProductMaintenanceLease:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            if self._owner is ProductMaintenanceOwner.UPDATE:
                raise ProductMaintenanceBusy(
                    "product maintenance is owned by AppUpdate"
                )
            control = self._runtime_control
            if self._owner is ProductMaintenanceOwner.RUNTIME and not cancel_runtime:
                raise ProductMaintenanceBusy(
                    "product maintenance is owned by RuntimeMaintenance"
                )
        if control is not None:
            control.cancel()
            remaining = max(0.0, deadline - time.monotonic())
            if not control.wait_terminal(remaining):
                raise ProductMaintenanceBusy(
                    "Runtime installer did not reach terminal state before update"
                )
        with self._condition:
            while self._owner is not ProductMaintenanceOwner.IDLE:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(remaining):
                    raise ProductMaintenanceBusy(
                        "Runtime installer did not release terminal state before update"
                    )
            return self._acquire(ProductMaintenanceOwner.UPDATE, None)

    def _acquire(
        self,
        owner: ProductMaintenanceOwner,
        runtime_control: _RuntimeControl | None,
    ) -> ProductMaintenanceLease:
        stream = self._open_and_lock_file()
        token = uuid4()
        self._file = stream
        self._owner = owner
        self._token = token
        self._runtime_control = runtime_control
        return ProductMaintenanceLease(self, token)

    def _release(self, token: UUID) -> None:
        with self._condition:
            if token != self._token:
                return
            self._unlock_file()
            self._owner = ProductMaintenanceOwner.IDLE
            self._token = None
            self._runtime_control = None
            self._condition.notify_all()

    def _open_and_lock_file(self) -> BinaryIO:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self._lock_path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise ProductMaintenanceBusy(
                "product-maintenance.lock is owned by another process"
            ) from exc
        return stream

    def _unlock_file(self) -> None:
        stream = self._file
        self._file = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


_coordinator_lock = threading.Lock()
_coordinators: dict[Path, ProductMaintenanceCoordinator] = {}


def get_product_maintenance_coordinator(
    lock_path: Path | None = None,
) -> ProductMaintenanceCoordinator:
    if lock_path is None:
        from vibeocr.classic.app_paths import get_active_app_paths

        lock_path = get_active_app_paths().locks_root / "product-maintenance.lock"
    path = lock_path.resolve()
    with _coordinator_lock:
        coordinator = _coordinators.get(path)
        if coordinator is None:
            coordinator = ProductMaintenanceCoordinator(path)
            _coordinators[path] = coordinator
        return coordinator


__all__ = [
    "ProductMaintenanceBusy",
    "ProductMaintenanceCoordinator",
    "ProductMaintenanceLease",
    "ProductMaintenanceOwner",
    "RuntimeInstallerCancelled",
    "RuntimeInstallerClientError",
    "RuntimeMaintenanceRequestBuilder",
    "RuntimeMaintenanceViewModel",
    "get_product_maintenance_coordinator",
]
