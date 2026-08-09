"""Classic client for the Backend-owned Runtime Installer JSON CLI.

This is the only dependency-installation surface exposed to Classic.  It
deliberately returns runtime state and launch paths, never package names,
indexes, requirement expressions or pip arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4


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


class RuntimeInstallerCancelled(RuntimeInstallerClientError):
    """The caller cancelled an in-progress installer operation."""


@dataclass(frozen=True, slots=True)
class RuntimeComponentDescriptor:
    component_id: str
    display_name: str
    version: str | None = None
    desired_state: str | None = None
    desired_version: str | None = None
    actual_state: str | None = None
    actual_version: str | None = None
    drift_reason: str | None = None
    repairable: bool | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSourceIdentity:
    backend_version: str
    backend_source_sha: str
    runtime_manifest_sha256: str
    protocol_version: str
    protocol_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityDescriptor:
    name: str
    lifecycle: str
    introduced_in: str
    deprecated_in: str | None = None
    sunset_at: str | None = None
    replacement: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProfileDescriptor:
    profile_id: str
    accelerator: str
    components: tuple[RuntimeComponentDescriptor, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceUpdate:
    event_type: str
    operation_id: str
    sequence: int
    operation: str
    operation_state: str
    phase: str
    profile_id: str
    updated_at: str
    component_id: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    progress_unit: str | None = None
    estimated_remaining_seconds: float | None = None
    message_code: str | None = None
    requested_component_ids: tuple[str, ...] = ()
    effective_component_ids: tuple[str, ...] = ()
    source: RuntimeSourceIdentity | None = None

    @property
    def has_determinate_progress(self) -> bool:
        return (
            self.progress_unit in {"items", "bytes"}
            and self.progress_current is not None
            and self.progress_total is not None
            and self.progress_total > 0
        )


@dataclass(frozen=True, slots=True)
class RuntimeMaintenancePage:
    operation_id: str
    events: tuple[RuntimeMaintenanceUpdate, ...]
    oldest_sequence: int
    through_sequence: int
    more: bool
    replay_expires_at: str | None = None
    snapshot: RuntimeMaintenanceUpdate | None = None


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    status: str
    runtime_root: str
    accelerator: str
    profile: str
    python_version: str
    protocol_version: str
    manifest_sha256: str
    backend_version: str
    integrity: str
    components: tuple[RuntimeComponentDescriptor, ...] = ()
    source: RuntimeSourceIdentity | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.integrity == "verified"


@dataclass(frozen=True, slots=True)
class RuntimeLaunch:
    python_executable: str
    supervisor_module: str
    working_directory: str
    model_root: str
    environment: dict[str, str]


ProgressCallback = Callable[[RuntimeMaintenanceUpdate], None]


def _source_identity(value: object) -> RuntimeSourceIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeInstallerClientError("Runtime 来源身份无效")
    fields = (
        "backend_version",
        "backend_source_sha",
        "runtime_manifest_sha256",
        "protocol_version",
        "protocol_manifest_sha256",
    )
    if any(
        not isinstance(value.get(field), str) or not value[field] for field in fields
    ):
        raise RuntimeInstallerClientError("Runtime 来源身份字段无效")
    return RuntimeSourceIdentity(**{field: value[field] for field in fields})


def _profile_descriptor(value: object) -> RuntimeProfileDescriptor:
    if not isinstance(value, dict):
        raise RuntimeInstallerClientError("Runtime profile 响应不完整")
    wire = value
    components_value = wire.get("components")
    if not isinstance(components_value, list):
        raise RuntimeInstallerClientError("Runtime profile 组件列表无效")
    components: list[RuntimeComponentDescriptor] = []
    for item in components_value:
        if not isinstance(item, dict):
            raise RuntimeInstallerClientError("Runtime profile 组件无效")
        component_id = item.get("component_id")
        display_name = item.get("display_name")
        version = item.get("version")
        if (
            not isinstance(component_id, str)
            or not component_id
            or not isinstance(display_name, str)
            or not display_name
            or (version is not None and not isinstance(version, str))
        ):
            raise RuntimeInstallerClientError("Runtime profile 组件字段无效")
        repairable = item.get("repairable")
        if repairable is not None and not isinstance(repairable, bool):
            raise RuntimeInstallerClientError("Runtime profile repairable 字段无效")
        components.append(
            RuntimeComponentDescriptor(
                component_id,
                display_name,
                version,
                desired_state=item.get("desired_state"),
                desired_version=item.get("desired_version"),
                actual_state=item.get("actual_state"),
                actual_version=item.get("actual_version"),
                drift_reason=item.get("drift_reason"),
                repairable=repairable,
            )
        )
    profile_id = wire.get("profile_id")
    accelerator = wire.get("accelerator")
    if not isinstance(profile_id, str) or not isinstance(accelerator, str):
        raise RuntimeInstallerClientError("Runtime profile 响应不完整")
    return RuntimeProfileDescriptor(profile_id, accelerator, tuple(components))


def _maintenance_update(
    value: object,
    *,
    expected_operation: str,
) -> RuntimeMaintenanceUpdate:
    if not isinstance(value, dict):
        raise RuntimeInstallerClientError("Runtime maintenance 事件不是对象")
    wire = value
    if (
        wire.get("protocol_version") != 2
        or wire.get("event_version") != 1
        or wire.get("operation") != expected_operation
    ):
        raise RuntimeInstallerClientError("Runtime maintenance 事件版本不兼容")
    snapshot = wire.get("snapshot")
    if not isinstance(snapshot, dict):
        raise RuntimeInstallerClientError("Runtime maintenance 快照缺失")
    required_strings = (
        "operation_id",
        "operation",
        "operation_state",
        "phase",
        "profile_id",
        "updated_at",
    )
    if any(not isinstance(snapshot.get(field), str) for field in required_strings):
        raise RuntimeInstallerClientError("Runtime maintenance 快照字段无效")
    sequence = snapshot.get("sequence")
    event_sequence = wire.get("sequence", sequence)
    if (
        type(sequence) is not int
        or sequence < 1
        or type(event_sequence) is not int
        or event_sequence != sequence
    ):
        raise RuntimeInstallerClientError("Runtime maintenance sequence 无效")
    component_id = snapshot.get("component_id")
    if component_id is not None and not isinstance(component_id, str):
        raise RuntimeInstallerClientError("Runtime maintenance component_id 无效")
    current = total = estimated_remaining_seconds = None
    unit = None
    progress = snapshot.get("progress")
    if progress is not None:
        if not isinstance(progress, dict):
            raise RuntimeInstallerClientError("Runtime maintenance progress 无效")
        current = progress.get("current")
        total = progress.get("total")
        unit = progress.get("unit")
        estimated_remaining_seconds = progress.get("estimated_remaining_seconds")
        if (
            not isinstance(current, int)
            or current < 0
            or (total is not None and (not isinstance(total, int) or total < 0))
            or not isinstance(unit, str)
            or (
                estimated_remaining_seconds is not None
                and (
                    isinstance(estimated_remaining_seconds, bool)
                    or not isinstance(estimated_remaining_seconds, (int, float))
                    or estimated_remaining_seconds < 0
                    or unit not in {"items", "bytes"}
                    or total is None
                    or total <= 0
                )
            )
        ):
            raise RuntimeInstallerClientError("Runtime maintenance progress 字段无效")
    requested = snapshot.get("requested_component_ids", [])
    effective = snapshot.get("effective_component_ids", [])
    if any(
        not isinstance(items, list)
        or any(not isinstance(item, str) or not item for item in items)
        for items in (requested, effective)
    ):
        raise RuntimeInstallerClientError("Runtime maintenance component scope 无效")
    event_type = wire.get("event_type")
    message_code = wire.get("message_code")
    if not isinstance(event_type, str) or not isinstance(message_code, str):
        raise RuntimeInstallerClientError("Runtime maintenance 事件字段无效")
    return RuntimeMaintenanceUpdate(
        event_type=event_type,
        operation_id=str(snapshot["operation_id"]),
        sequence=sequence,
        operation=str(snapshot["operation"]),
        operation_state=str(snapshot["operation_state"]),
        phase=str(snapshot["phase"]),
        profile_id=str(snapshot["profile_id"]),
        updated_at=str(snapshot["updated_at"]),
        component_id=component_id,
        progress_current=current,
        progress_total=total,
        progress_unit=unit,
        estimated_remaining_seconds=(
            float(estimated_remaining_seconds)
            if estimated_remaining_seconds is not None
            else None
        ),
        message_code=message_code,
        requested_component_ids=tuple(requested),
        effective_component_ids=tuple(effective),
        source=_source_identity(snapshot.get("source")),
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class RuntimeInstallerClient:
    """Invoke one bound runtime installer for one portable product root."""

    def __init__(
        self,
        product_root: str | Path,
        *,
        component_lock: str | Path | None = None,
        runtime_manifest: str | Path | None = None,
        layout_manifest: str | Path | None = None,
        product_id: str = "classic",
        accelerator: str | None = None,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.product_root = Path(product_root).resolve()
        self.component_lock = Path(
            component_lock or self.product_root / "component-lock.json"
        ).resolve()
        self.runtime_manifest = Path(
            runtime_manifest or self.product_root / "backend" / "runtime-manifest.json"
        ).resolve()
        explicit_layout = layout_manifest or os.environ.get("VIBEOCR_PORTABLE_LAYOUT")
        if explicit_layout is None:
            product_manifest = self.product_root / "product-release-manifest.json"
            try:
                product_layout = json.loads(
                    product_manifest.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                product_layout = None
            if (
                isinstance(product_layout, dict)
                and product_layout.get("shared_root") == "data"
                and isinstance(product_layout.get("products"), dict)
                and product_id in product_layout["products"]
            ):
                explicit_layout = product_manifest
        self.layout_manifest = (
            Path(explicit_layout).resolve() if explicit_layout is not None else None
        )
        self.product_id = product_id
        self.accelerator = accelerator
        configured = os.environ.get("VIBEOCR_RUNTIME_INSTALLER")
        self._materialize_bound_installer = False
        if command is not None:
            self.command = command
        elif configured:
            self.command = (configured,)
        elif getattr(sys, "frozen", False):
            self._materialize_bound_installer = True
            self.command = (
                str(
                    self.product_root
                    / "data"
                    / "cache"
                    / "runtime-installer"
                    / "vibeocr-runtime-installer.exe"
                ),
            )
        else:
            self.command = (
                sys.executable,
                "-m",
                "vibeocr.backend.runtime_installer",
            )
        self._last_operation_id: str | None = None
        self._negotiated_capabilities: tuple[str, ...] = ()
        self._capability_descriptors: tuple[RuntimeCapabilityDescriptor, ...] = ()

    @property
    def last_operation_id(self) -> str | None:
        return self._last_operation_id

    @property
    def negotiated_capabilities(self) -> tuple[str, ...]:
        return self._negotiated_capabilities

    @property
    def capability_descriptors(self) -> tuple[RuntimeCapabilityDescriptor, ...]:
        return self._capability_descriptors

    def required_capabilities(self) -> tuple[str, ...]:
        """Return the product capabilities pinned by ``component-lock.json``."""
        try:
            value: Any = json.loads(self.component_lock.read_text(encoding="utf-8"))
            required = value["required_capabilities"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeInstallerClientError(
                "组件锁缺少 required_capabilities"
            ) from exc
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) or not item for item in required)
            or len(set(required)) != len(required)
        ):
            raise RuntimeInstallerClientError("组件锁 required_capabilities 无效")
        return tuple(required)

    def _record_negotiation(
        self,
        envelope: dict[str, Any],
        required_capabilities: tuple[str, ...],
    ) -> None:
        raw_negotiated = envelope.get("negotiated_capabilities", [])
        if not isinstance(raw_negotiated, list) or any(
            not isinstance(item, str) or not item for item in raw_negotiated
        ):
            raise RuntimeInstallerClientError("Runtime negotiated_capabilities 无效")
        negotiated = tuple(raw_negotiated)
        missing = sorted(set(required_capabilities).difference(negotiated))
        if missing:
            raise RuntimeInstallerClientError(
                "Runtime 未协商必需 capability: " + ", ".join(missing)
            )

        raw_descriptors = envelope.get("capability_descriptors", [])
        if not isinstance(raw_descriptors, list):
            raise RuntimeInstallerClientError("Runtime capability_descriptors 无效")
        descriptors: list[RuntimeCapabilityDescriptor] = []
        for item in raw_descriptors:
            nullable_fields = ("deprecated_in", "sunset_at", "replacement")
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or item.get("lifecycle") not in {"active", "deprecated"}
                or not isinstance(item.get("introduced_in"), str)
                or not item["introduced_in"]
                or any(
                    item.get(field) is not None and not isinstance(item.get(field), str)
                    for field in nullable_fields
                )
            ):
                raise RuntimeInstallerClientError(
                    "Runtime capability descriptor 字段无效"
                )
            descriptors.append(
                RuntimeCapabilityDescriptor(
                    name=item["name"],
                    lifecycle=item["lifecycle"],
                    introduced_in=item["introduced_in"],
                    deprecated_in=item.get("deprecated_in"),
                    sunset_at=item.get("sunset_at"),
                    replacement=item.get("replacement"),
                )
            )
        self._negotiated_capabilities = negotiated
        self._capability_descriptors = tuple(descriptors)

    def _binding_request(self) -> dict[str, Any]:
        request = {
            "protocol_version": 2,
            "product_root": str(self.product_root),
            "component_lock": str(self.component_lock),
            "runtime_manifest": str(self.runtime_manifest),
        }
        if self.layout_manifest is not None:
            request["layout_manifest"] = str(self.layout_manifest)
            request["product_id"] = self.product_id
        return request

    def _request_arguments(self, request: dict[str, Any]) -> list[str]:
        return [*self.command, "--request-json", json.dumps(request)]

    def _arguments(
        self,
        operation: str,
        *,
        operation_id: str | None = None,
        component_ids: tuple[str, ...] = (),
        required_capabilities: tuple[str, ...] = (),
    ) -> list[str]:
        request = {
            **self._binding_request(),
            "operation": operation,
            "accelerator": self.accelerator,
        }
        if self._supports_capability("runtime.maintenance.v2"):
            request["accepted_event_streams"] = ["ndjson.v2"]
            if operation_id is not None:
                request["operation_id"] = operation_id
            if component_ids:
                if not self._supports_capability("runtime.component-repair.v1"):
                    raise RuntimeInstallerClientError("Runtime 不支持按组件 repair")
                request["component_ids"] = list(component_ids)
            if required_capabilities:
                if not self._supports_capability("runtime.capability-metadata.v1"):
                    raise RuntimeInstallerClientError(
                        "Runtime 不支持 capability negotiation metadata"
                    )
                request["required_capabilities"] = list(required_capabilities)
        elif self._supports_maintenance_events():
            request["accepted_event_streams"] = ["ndjson.v1"]
        return self._request_arguments(request)

    def _manifest(self) -> dict[str, Any]:
        try:
            value: Any = json.loads(self.runtime_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeInstallerClientError("Runtime manifest 无法读取") from exc
        if not isinstance(value, dict):
            raise RuntimeInstallerClientError("Runtime manifest 无效")
        return value

    def _supports_maintenance_events(self) -> bool:
        return self._supports_capability("runtime.maintenance.v1")

    def _supports_capability(self, capability: str) -> bool:
        try:
            capabilities = self._manifest().get("capabilities", [])
        except RuntimeInstallerClientError:
            return False
        return isinstance(capabilities, list) and capability in capabilities

    def _invoke_control(
        self, request: dict[str, Any], *, timeout: float = 30
    ) -> dict[str, Any]:
        self._verify_installer_executable()
        try:
            result = subprocess.run(
                self._request_arguments({**self._binding_request(), **request}),
                cwd=self.product_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeInstallerClientError("Runtime 控制命令执行失败") from exc
        envelopes: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("event_version") != 1:
                envelopes.append(value)
        envelope = envelopes[-1] if envelopes else None
        if result.returncode != 0 or envelope is None or envelope.get("ok") is not True:
            error = envelope.get("error") if isinstance(envelope, dict) else None
            raise self._error_from_wire(
                error,
                fallback=result.stderr.strip() or "Runtime 控制命令失败",
            )
        return envelope

    @staticmethod
    def _error_from_wire(
        error: object, *, fallback: str
    ) -> RuntimeInstallerClientError:
        if not isinstance(error, dict):
            return RuntimeInstallerClientError(fallback)
        retry_after = error.get("retry_after")
        return RuntimeInstallerClientError(
            str(error.get("message") or error.get("code") or fallback),
            canonical_code=(
                str(error["canonical_code"])
                if isinstance(error.get("canonical_code"), str)
                else None
            ),
            category=(
                str(error["category"])
                if isinstance(error.get("category"), str)
                else None
            ),
            retryable=error.get("retryable") is True,
            retry_after=(
                retry_after
                if isinstance(retry_after, int) and retry_after >= 0
                else None
            ),
            detail=(
                error.get("detail") if isinstance(error.get("detail"), dict) else None
            ),
        )

    def observe(
        self, operation_id: str, *, after_sequence: int = 0, limit: int = 128
    ) -> RuntimeMaintenancePage:
        envelope = self._invoke_control(
            {
                "request_kind": "observe",
                "operation_id": operation_id,
                "after_sequence": after_sequence,
                "limit": limit,
            }
        )
        snapshot = envelope.get("snapshot")
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("operation"), str
        ):
            raise RuntimeInstallerClientError("Runtime observe 响应缺少 operation")
        events = envelope.get("events")
        if not isinstance(events, list):
            raise RuntimeInstallerClientError("Runtime observe 响应缺少 events")
        updates = tuple(
            _maintenance_update(event, expected_operation=snapshot["operation"])
            for event in events
        )
        snapshot_update = _maintenance_update(
            {
                "protocol_version": 2,
                "event_version": 1,
                "event_type": "snapshot",
                "operation": snapshot["operation"],
                "snapshot": snapshot,
                "message_code": "runtime.observe_snapshot",
            },
            expected_operation=snapshot["operation"],
        )
        oldest = envelope.get("oldest_sequence")
        through = envelope.get("through_sequence")
        more = envelope.get("more")
        replay_expires_at = envelope.get("replay_expires_at")
        if (
            type(oldest) is not int
            or oldest < 1
            or type(through) is not int
            or through < 0
            or type(more) is not bool
            or (
                replay_expires_at is not None and not isinstance(replay_expires_at, str)
            )
            or (updates and updates[-1].sequence != through)
            or (updates and updates[0].sequence != after_sequence + 1)
            or any(
                right.sequence != left.sequence + 1
                for left, right in zip(updates, updates[1:])
            )
            or (not updates and through > after_sequence)
            or (not updates and more)
            or any(update.operation_id != operation_id for update in updates)
            or snapshot_update.operation_id != operation_id
            or snapshot_update.sequence < through
        ):
            raise RuntimeInstallerClientError("Runtime observe cursor 响应无效")
        return RuntimeMaintenancePage(
            operation_id=operation_id,
            events=updates,
            oldest_sequence=oldest,
            through_sequence=through,
            more=more,
            replay_expires_at=replay_expires_at,
            snapshot=snapshot_update,
        )

    def cancel(
        self,
        operation_id: str,
        *,
        command_id: str | None = None,
        expected_sequence: int | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "request_kind": "command",
            "command_id": command_id or str(uuid4()),
            "command": "cancel",
            "target_operation_id": operation_id,
        }
        if expected_sequence is not None:
            request["expected_sequence"] = expected_sequence
        return self._invoke_control(request)

    def _request_cancel(
        self, operation_id: str, *, expected_sequence: int | None
    ) -> dict[str, Any]:
        if expected_sequence is None:
            return self.cancel(operation_id)
        try:
            return self.cancel(
                operation_id,
                expected_sequence=expected_sequence,
            )
        except RuntimeInstallerClientError as exc:
            if str(exc) != "expected_sequence mismatch":
                raise
        return self.cancel(operation_id)

    def retry(
        self,
        operation_id: str,
        *,
        command_id: str | None = None,
        new_operation_id: str | None = None,
    ) -> dict[str, Any]:
        return self._invoke_control(
            {
                "request_kind": "command",
                "command_id": command_id or str(uuid4()),
                "command": "retry",
                "target_operation_id": operation_id,
                "new_operation_id": new_operation_id or str(uuid4()),
            },
            timeout=3600,
        )

    def profile_descriptor(self) -> RuntimeProfileDescriptor:
        manifest = self._manifest()
        accelerator = self.accelerator
        if accelerator is None:
            try:
                lock = json.loads(self.component_lock.read_text(encoding="utf-8"))
                accelerator = str(lock["backend"]["accelerator"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeInstallerClientError("组件锁缺少加速方案") from exc
        profile_id = {
            "cpu": "win-x64-cpu",
            "nvidia_cuda": "win-x64-cu126",
        }.get(accelerator)
        if profile_id is None:
            raise RuntimeInstallerClientError("Runtime 加速方案不受支持")
        profiles = manifest.get("profiles")
        if not isinstance(profiles, dict):
            raise RuntimeInstallerClientError("Runtime manifest 缺少 profiles")
        record = profiles.get(profile_id)
        if not isinstance(record, dict):
            raise RuntimeInstallerClientError("Runtime profile 未绑定")
        components = record.get("components")
        if components is None:
            components = []
        return _profile_descriptor(
            {
                "profile_id": profile_id,
                "accelerator": accelerator,
                "components": components,
            }
        )

    def _invoke(
        self,
        operation: str,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 3600,
        operation_id: str | None = None,
        component_ids: tuple[str, ...] = (),
        required_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self._verify_installer_executable()
        smoke_python = os.environ.get("VIBEOCR_SELF_TEST_PYTHON")
        if (
            operation == "inspect"
            and getattr(sys, "frozen", False)
            and os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6"
            and smoke_python
            and Path(smoke_python).is_file()
        ):
            # 冻结 artifact verifier 已复验 component lock、manifest、installer，
            # 并从产品内绑定 wheel 启动 Supervisor。这里仅阻止设置页的后台 inspect
            # 额外拉起 installer 进程；生产环境永远不会命中此双门禁。
            lock = json.loads(self.component_lock.read_text(encoding="utf-8"))
            manifest_bytes = self.runtime_manifest.read_bytes()
            manifest = json.loads(manifest_bytes)
            return {
                "protocol_version": 2,
                "ok": True,
                "operation": "inspect",
                "state": {
                    "status": "ready",
                    "accelerator": str(lock["backend"]["accelerator"]),
                    "runtime_root": str(self.product_root / ".smoke-runtime"),
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "backend_version": str(manifest["backend_version"]),
                    "integrity": "verified",
                },
                "launch": None,
            }
        supports_v2 = self._supports_capability("runtime.maintenance.v2")
        if supports_v2:
            operation_id = operation_id or str(uuid4())
            self._last_operation_id = operation_id
        try:
            process = subprocess.Popen(
                self._arguments(
                    operation,
                    operation_id=operation_id,
                    component_ids=component_ids,
                    required_capabilities=required_capabilities,
                ),
                cwd=self.product_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except OSError as exc:
            raise RuntimeInstallerClientError(
                f"无法启动 Runtime Installer: {exc}"
            ) from exc

        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def drain(channel: str, stream: TextIO) -> None:
            try:
                for line in stream:
                    output_queue.put((channel, line.rstrip("\r\n")))
            finally:
                output_queue.put((channel, None))

        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(
                target=drain,
                args=("stdout", process.stdout),
                daemon=True,
            ),
            threading.Thread(
                target=drain,
                args=("stderr", process.stderr),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        completed_channels: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        envelopes: list[dict[str, Any]] = []
        last_sequences: dict[str, int] = {}
        latest_states: dict[str, str] = {}
        cancel_sent = False
        cancellation_deadline: float | None = None
        while len(completed_channels) < 2:
            if cancel_event is not None and cancel_event.is_set():
                if supports_v2 and operation_id is not None and not cancel_sent:
                    self._request_cancel(
                        operation_id,
                        expected_sequence=last_sequences.get(operation_id),
                    )
                    cancel_sent = True
                    cancellation_deadline = time.monotonic() + 15
                    deadline = min(deadline, cancellation_deadline)
                elif not supports_v2:
                    _terminate_process_tree(process)
                    raise RuntimeInstallerCancelled("Runtime Installer 操作已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if cancel_sent:
                    raise RuntimeInstallerClientError("Runtime 取消未由终态快照确认")
                _terminate_process_tree(process)
                raise RuntimeInstallerClientError(f"Runtime Installer {operation} 超时")
            try:
                channel, line = output_queue.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if line is None:
                completed_channels.add(channel)
                continue
            if channel == "stderr":
                stderr_lines.append(line)
                continue
            stdout_lines.append(line)
            try:
                value: Any = json.loads(line)
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            try:
                if value.get("event_version") == 1:
                    update = _maintenance_update(value, expected_operation=operation)
                    last_sequence = last_sequences.get(update.operation_id, 0)
                    if update.sequence <= last_sequence:
                        continue
                    if supports_v2 and update.sequence != last_sequence + 1:
                        while update.sequence > last_sequence + 1:
                            page = self.observe(
                                update.operation_id,
                                after_sequence=last_sequence,
                            )
                            for replay in page.events:
                                replay_cursor = last_sequences.get(
                                    replay.operation_id, 0
                                )
                                if replay.sequence <= replay_cursor:
                                    continue
                                if replay.sequence != replay_cursor + 1:
                                    raise RuntimeInstallerClientError(
                                        "Runtime maintenance replay sequence 不连续"
                                    )
                                last_sequences[replay.operation_id] = replay.sequence
                                if progress is not None:
                                    progress(replay)
                            next_sequence = last_sequences.get(update.operation_id, 0)
                            if next_sequence <= last_sequence:
                                raise RuntimeInstallerClientError(
                                    "Runtime maintenance replay cursor 未推进"
                                )
                            last_sequence = next_sequence
                            if not page.more:
                                break
                        if update.sequence <= last_sequences.get(
                            update.operation_id, 0
                        ):
                            continue
                        if (
                            update.sequence
                            != last_sequences.get(update.operation_id, 0) + 1
                        ):
                            raise RuntimeInstallerClientError(
                                "Runtime maintenance sequence 缺口未能重放"
                            )
                    last_sequences[update.operation_id] = update.sequence
                    latest_states[update.operation_id] = update.operation_state
                    if progress is not None:
                        progress(update)
                else:
                    envelopes.append(value)
            except Exception:
                _terminate_process_tree(process)
                raise
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            raise RuntimeInstallerClientError(
                f"Runtime Installer {operation} 超时"
            ) from exc
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)
        envelope = envelopes[-1] if envelopes else None
        if cancel_sent:
            assert operation_id is not None
            terminal_state = latest_states.get(operation_id)
            while terminal_state not in {"succeeded", "failed", "cancelled"}:
                if (
                    cancellation_deadline is None
                    or time.monotonic() >= cancellation_deadline
                ):
                    raise RuntimeInstallerClientError("Runtime 取消未由终态快照确认")
                page = self.observe(
                    operation_id,
                    after_sequence=last_sequences.get(operation_id, 0),
                )
                for replay in page.events:
                    if replay.sequence > last_sequences.get(operation_id, 0):
                        last_sequences[operation_id] = replay.sequence
                        latest_states[operation_id] = replay.operation_state
                        if progress is not None:
                            progress(replay)
                if (
                    not page.more
                    and page.snapshot is not None
                    and last_sequences.get(operation_id, 0) >= page.snapshot.sequence
                ):
                    terminal_state = page.snapshot.operation_state
                    latest_states[operation_id] = terminal_state
                if terminal_state not in {"succeeded", "failed", "cancelled"}:
                    time.sleep(0.05)
            if terminal_state == "cancelled":
                raise RuntimeInstallerCancelled("Runtime Installer 操作已取消")
        if (
            process.returncode != 0
            or envelope is None
            or envelope.get("protocol_version") != 2
            or envelope.get("operation") != operation
            or envelope.get("ok") is not True
        ):
            detail = ""
            error = None
            if envelope is not None:
                error = envelope.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or error.get("code") or "")
            if not detail:
                detail = stderr.strip() or stdout.strip() or "未知错误"
            raise self._error_from_wire(error, fallback=detail)
        if supports_v2:
            self._record_negotiation(envelope, required_capabilities)
        return envelope

    def _verify_installer_executable(self) -> None:
        if len(self.command) > 1 and self.command[1:2] == ("-m",):
            return
        executable = Path(self.command[0])
        try:
            component_lock = json.loads(self.component_lock.read_text(encoding="utf-8"))
            expected_manifest = component_lock["backend"]["runtime_manifest_sha256"]
            manifest_bytes = self.runtime_manifest.read_bytes()
            actual_manifest = hashlib.sha256(manifest_bytes).hexdigest()
            if (
                not isinstance(expected_manifest, str)
                or len(expected_manifest) != 64
                or expected_manifest.lower() != expected_manifest
                or actual_manifest != expected_manifest
            ):
                raise ValueError("runtime manifest hash mismatch")
            manifest = self._manifest()
            installer = manifest["installer"]
            expected = installer["executable_sha256"]
            cached_hash = (
                hashlib.sha256(executable.read_bytes()).hexdigest()
                if executable.is_file()
                else ""
            )
            if cached_hash != expected and self._materialize_bound_installer:
                archive_path = self.runtime_manifest.parent / installer["archive"]
                archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
                if archive_hash != installer["sha256"]:
                    raise ValueError("runtime installer archive hash mismatch")
                with zipfile.ZipFile(archive_path) as archive:
                    executable_bytes = archive.read(installer["executable_path"])
                if hashlib.sha256(executable_bytes).hexdigest() != expected:
                    raise ValueError("runtime installer executable hash mismatch")
                executable.parent.mkdir(parents=True, exist_ok=True)
                temporary = executable.with_suffix(".tmp")
                temporary.write_bytes(executable_bytes)
                os.replace(temporary, executable)
            actual = hashlib.sha256(executable.read_bytes()).hexdigest()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeInstallerClientError(
                "无法验证 Runtime Installer 可执行文件"
            ) from exc
        if actual != expected:
            raise RuntimeInstallerClientError(
                "Runtime Installer 可执行文件 SHA-256 不匹配"
            )

    def inspect(self) -> RuntimeInspection:
        envelope = self._invoke("inspect", timeout=60)
        try:
            value = envelope["state"]
            accelerator = str(value["accelerator"])
            manifest = json.loads(self.runtime_manifest.read_text(encoding="utf-8"))
            component_lock = json.loads(self.component_lock.read_text(encoding="utf-8"))
            profile = {
                "cpu": "win-x64-cpu",
                "nvidia_cuda": "win-x64-cu126",
            }[accelerator]
            profiles = manifest["profiles"]
            if not isinstance(profiles[profile], dict):
                raise TypeError("invalid runtime profile")
            descriptor_value = envelope.get("profile")
            descriptor = (
                _profile_descriptor(descriptor_value)
                if descriptor_value is not None
                else self.profile_descriptor()
            )
            return RuntimeInspection(
                status=str(value["status"]),
                runtime_root=str(value["runtime_root"]),
                accelerator=accelerator,
                profile=profile,
                python_version=str(manifest["python"]["version"]),
                protocol_version=str(component_lock["protocol"]["version"]),
                manifest_sha256=str(value["manifest_sha256"]),
                backend_version=str(value["backend_version"]),
                integrity=str(value["integrity"]),
                components=descriptor.components,
                source=_source_identity(value.get("source")),
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise RuntimeInstallerClientError(
                "Runtime Installer inspect 响应不完整"
            ) from exc

    def ensure(
        self,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        operation_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
    ) -> RuntimeLaunch:
        if not required_capabilities and self._supports_capability(
            "runtime.maintenance.v2"
        ):
            required_capabilities = ("runtime.maintenance.v2",)
        return self._launch_from(
            self._invoke(
                "ensure",
                progress=progress,
                cancel_event=cancel_event,
                operation_id=operation_id,
                required_capabilities=required_capabilities,
            )
        )

    def repair(
        self,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        operation_id: str | None = None,
        component_ids: tuple[str, ...] = (),
        required_capabilities: tuple[str, ...] = (),
    ) -> RuntimeLaunch:
        if not required_capabilities and self._supports_capability(
            "runtime.maintenance.v2"
        ):
            required_capabilities = (
                "runtime.maintenance.v2",
                *(("runtime.component-repair.v1",) if component_ids else ()),
            )
        return self._launch_from(
            self._invoke(
                "repair",
                progress=progress,
                cancel_event=cancel_event,
                operation_id=operation_id,
                component_ids=component_ids,
                required_capabilities=required_capabilities,
            )
        )

    @staticmethod
    def _launch_from(value: dict[str, Any]) -> RuntimeLaunch:
        try:
            value = value["launch"]
            if not isinstance(value, dict):
                raise TypeError("missing launch")
            environment = value["environment"]
            if not isinstance(environment, dict) or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in environment.items()
            ):
                raise TypeError("invalid environment")
            return RuntimeLaunch(
                python_executable=str(value["python_executable"]),
                supervisor_module=str(value["supervisor_module"]),
                working_directory=str(value["working_directory"]),
                model_root=str(value["model_root"]),
                environment=dict(environment),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeInstallerClientError(
                "Runtime Installer launch 响应不完整"
            ) from exc


__all__ = [
    "RuntimeComponentDescriptor",
    "RuntimeCapabilityDescriptor",
    "RuntimeInspection",
    "RuntimeInstallerCancelled",
    "RuntimeInstallerClient",
    "RuntimeInstallerClientError",
    "RuntimeLaunch",
    "RuntimeMaintenancePage",
    "RuntimeMaintenanceUpdate",
    "RuntimeProfileDescriptor",
    "RuntimeSourceIdentity",
]
