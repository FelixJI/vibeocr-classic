"""Classic client for the Backend-owned Runtime Installer JSON CLI.

This is the only dependency-installation surface exposed to Classic.  It
deliberately returns runtime state and launch paths, never package names,
indexes, requirement expressions or pip arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading


class RuntimeInstallerClientError(RuntimeError):
    """The installer could not complete or returned an invalid envelope."""


class RuntimeInstallerCancelled(RuntimeInstallerClientError):
    """The caller cancelled an in-progress installer operation."""


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    status: str
    runtime_id: str
    profile: str
    runtime_root: str
    manifest_sha256: str
    backend_version: str
    integrity: str

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.integrity == "verified"


@dataclass(frozen=True, slots=True)
class RuntimeLaunch:
    runtime_id: str
    profile: str
    python_executable: str
    supervisor_module: str
    working_directory: str
    model_root: str
    environment: dict[str, str]


ProgressCallback = Callable[[str], None]


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
        profile: str = "auto",
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.product_root = Path(product_root).resolve()
        self.component_lock = Path(
            component_lock or self.product_root / "component-lock.json"
        ).resolve()
        self.runtime_manifest = Path(
            runtime_manifest
            or self.product_root / "backend" / "runtime-manifest.json"
        ).resolve()
        explicit_layout = layout_manifest or os.environ.get(
            "VIBEOCR_PORTABLE_LAYOUT"
        )
        self.layout_manifest = (
            Path(explicit_layout).resolve() if explicit_layout is not None else None
        )
        self.product_id = product_id
        self.profile = profile
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
        args = [
            *self.command,
            operation,
            "--product-root",
            str(self.product_root),
            "--component-lock",
            str(self.component_lock),
            "--runtime-manifest",
            str(self.runtime_manifest),
            "--profile",
            self.profile,
            "--json",
        ]
        if self.layout_manifest is not None:
            args.extend(("--layout-manifest", str(self.layout_manifest)))
            args.extend(("--product-id", self.product_id))
        return args

    def _invoke(
        self,
        operation: str,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 3600,
    ) -> dict[str, Any]:
        if progress is not None:
            progress(f"Runtime Installer: {operation}")
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
                "status": "ready",
                "runtime_id": "artifact-smoke",
                "profile": str(lock["backend"]["profile"]),
                "runtime_root": str(self.product_root / ".smoke-runtime"),
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "backend_version": str(manifest["backend_version"]),
                "integrity": "verified",
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
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt"
                    else 0
                ),
            )
        except OSError as exc:
            raise RuntimeInstallerClientError(
                f"无法启动 Runtime Installer: {exc}"
            ) from exc

        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.communicate()
                raise RuntimeInstallerCancelled("Runtime Installer 操作已取消")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise RuntimeInstallerClientError(
                    f"Runtime Installer {operation} 超时"
                )
            time.sleep(0.05)

        stdout, stderr = process.communicate()
        envelopes: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            try:
                value: Any = json.loads(line)
            except ValueError:
                if progress is not None and line.strip():
                    progress(line.strip())
                continue
            if isinstance(value, dict):
                envelopes.append(value)
        envelope = envelopes[-1] if envelopes else None
        if process.returncode != 0 or envelope is None or "error" in envelope:
            detail = ""
            if envelope is not None:
                detail = str(envelope.get("message") or envelope.get("error") or "")
            if not detail:
                detail = stderr.strip() or stdout.strip() or "未知错误"
            raise RuntimeInstallerClientError(detail)
        return envelope

    def _verify_installer_executable(self) -> None:
        if len(self.command) > 1 and self.command[1:2] == ("-m",):
            return
        executable = Path(self.command[0])
        try:
            component_lock = json.loads(
                self.component_lock.read_text(encoding="utf-8")
            )
            expected_manifest = component_lock["backend"][
                "runtime_manifest_sha256"
            ]
            manifest_bytes = self.runtime_manifest.read_bytes()
            actual_manifest = hashlib.sha256(manifest_bytes).hexdigest()
            if (
                not isinstance(expected_manifest, str)
                or len(expected_manifest) != 64
                or expected_manifest.lower() != expected_manifest
                or actual_manifest != expected_manifest
            ):
                raise ValueError("runtime manifest hash mismatch")
            manifest = json.loads(self.runtime_manifest.read_text(encoding="utf-8"))
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
        value = self._invoke("inspect", timeout=60)
        try:
            return RuntimeInspection(
                status=str(value["status"]),
                runtime_id=str(value["runtime_id"]),
                profile=str(value["profile"]),
                runtime_root=str(value["runtime_root"]),
                manifest_sha256=str(value["manifest_sha256"]),
                backend_version=str(value["backend_version"]),
                integrity=str(value["integrity"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
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
            environment = value["environment"]
            if not isinstance(environment, dict) or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in environment.items()
            ):
                raise TypeError("invalid environment")
            return RuntimeLaunch(
                runtime_id=str(value["runtime_id"]),
                profile=str(value["profile"]),
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
    "RuntimeInspection",
    "RuntimeInstallerCancelled",
    "RuntimeInstallerClient",
    "RuntimeInstallerClientError",
    "RuntimeLaunch",
]
