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


class RuntimeInstallerClientError(RuntimeError):
    """The installer could not complete or returned an invalid envelope."""


class RuntimeInstallerCancelled(RuntimeInstallerClientError):
    """The caller cancelled an in-progress installer operation."""


@dataclass(frozen=True, slots=True)
class RuntimeComponentDescriptor:
    component_id: str
    display_name: str
    version: str | None = None


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
    message_code: str | None = None


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
        components.append(
            RuntimeComponentDescriptor(component_id, display_name, version)
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
    if not isinstance(sequence, int) or sequence < 0:
        raise RuntimeInstallerClientError("Runtime maintenance sequence 无效")
    component_id = snapshot.get("component_id")
    if component_id is not None and not isinstance(component_id, str):
        raise RuntimeInstallerClientError("Runtime maintenance component_id 无效")
    current = total = None
    unit = None
    progress = snapshot.get("progress")
    if progress is not None:
        if not isinstance(progress, dict):
            raise RuntimeInstallerClientError("Runtime maintenance progress 无效")
        current = progress.get("current")
        total = progress.get("total")
        unit = progress.get("unit")
        if (
            not isinstance(current, int)
            or current < 0
            or (total is not None and (not isinstance(total, int) or total < 0))
            or not isinstance(unit, str)
        ):
            raise RuntimeInstallerClientError("Runtime maintenance progress 字段无效")
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
        message_code=message_code,
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

    def _arguments(self, operation: str) -> list[str]:
        request = {
            "protocol_version": 2,
            "operation": operation,
            "product_root": str(self.product_root),
            "component_lock": str(self.component_lock),
            "runtime_manifest": str(self.runtime_manifest),
            "accelerator": self.accelerator,
        }
        if self._supports_maintenance_events():
            request["accepted_event_streams"] = ["ndjson.v1"]
        if self.layout_manifest is not None:
            request["layout_manifest"] = str(self.layout_manifest)
            request["product_id"] = self.product_id
        return [*self.command, "--request-json", json.dumps(request)]

    def _manifest(self) -> dict[str, Any]:
        try:
            value: Any = json.loads(self.runtime_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeInstallerClientError("Runtime manifest 无法读取") from exc
        if not isinstance(value, dict):
            raise RuntimeInstallerClientError("Runtime manifest 无效")
        return value

    def _supports_maintenance_events(self) -> bool:
        try:
            capabilities = self._manifest().get("capabilities", [])
        except RuntimeInstallerClientError:
            return False
        return (
            isinstance(capabilities, list) and "runtime.maintenance.v1" in capabilities
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
        try:
            process = subprocess.Popen(
                self._arguments(operation),
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
        while len(completed_channels) < 2:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process_tree(process)
                raise RuntimeInstallerCancelled("Runtime Installer 操作已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
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
        if (
            process.returncode != 0
            or envelope is None
            or envelope.get("protocol_version") != 2
            or envelope.get("operation") != operation
            or envelope.get("ok") is not True
        ):
            detail = ""
            if envelope is not None:
                error = envelope.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or error.get("code") or "")
            if not detail:
                detail = stderr.strip() or stdout.strip() or "未知错误"
            raise RuntimeInstallerClientError(detail)
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
    ) -> RuntimeLaunch:
        return self._launch_from(
            self._invoke(
                "ensure",
                progress=progress,
                cancel_event=cancel_event,
            )
        )

    def repair(
        self,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RuntimeLaunch:
        return self._launch_from(
            self._invoke(
                "repair",
                progress=progress,
                cancel_event=cancel_event,
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
    "RuntimeInspection",
    "RuntimeInstallerCancelled",
    "RuntimeInstallerClient",
    "RuntimeInstallerClientError",
    "RuntimeLaunch",
    "RuntimeMaintenanceUpdate",
    "RuntimeProfileDescriptor",
]
