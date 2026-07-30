"""Verify the PySide Classic ZIP and exact backend-wheel binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_CLASSIC_ARCHIVE_BYTES = 300_000_000


def _verify_archive_size(
    artifact: Path, *, max_bytes: int = MAX_CLASSIC_ARCHIVE_BYTES
) -> None:
    actual = artifact.stat().st_size
    if actual > max_bytes:
        raise RuntimeError(
            f"Classic archive exceeds size budget: {actual} > {max_bytes} bytes"
        )


def _prepare_smoke_python(root: Path) -> tuple[Path, Path]:
    """把产品内绑定 wheel 解到隔离 import 根，供 Supervisor smoke 使用。"""
    runtime_manifest = json.loads(
        (root / "backend" / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    backend_wheel = root / "backend" / str(runtime_manifest["backend_wheel"])
    protocol_wheels = sorted((root / "backend").glob("vibeocr_runtime_contracts-*.whl"))
    if len(protocol_wheels) != 1:
        raise RuntimeError("frozen smoke requires exactly one contracts wheel")
    smoke_root = root / ".smoke-runtime"
    site_packages = smoke_root / "site-packages"
    site_packages.mkdir(parents=True)
    for wheel in (protocol_wheels[0], backend_wheel):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site_packages)
    return Path(sys.executable), site_packages


def _verify_frozen_startup(root: Path, timeout_seconds: float = 45.0) -> None:
    """真实启动冻结入口并要求它完成 Supervisor 就绪握手。"""
    exe = root / "VibeOCR.exe"
    trace = root / ".startup-smoke.jsonl"
    result_file = root / ".startup-smoke-result.json"
    stdout_log = root / ".startup-smoke.stdout.log"
    stderr_log = root / ".startup-smoke.stderr.log"
    trace.unlink(missing_ok=True)
    result_file.unlink(missing_ok=True)
    stdout_log.unlink(missing_ok=True)
    stderr_log.unlink(missing_ok=True)
    smoke_root = root / ".smoke-runtime"
    smoke_python, smoke_import_root = _prepare_smoke_python(root)
    env = os.environ.copy()
    env["VIBEOCR_SELF_TEST_SMOKE"] = "t6"
    env["VIBEOCR_STARTUP_TRACE"] = str(trace)
    env["VIBEOCR_SELF_TEST_RESULT"] = str(result_file)
    env["VIBEOCR_SELF_TEST_PYTHON"] = str(smoke_python)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for variable in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "VIBEOCR_REPOSITORY_ROOT",
    ):
        env.pop(variable, None)
    env["PYTHONPATH"] = str(smoke_import_root)
    try:
        # 不使用 PIPE：启动阶段的后台清理子进程可能继承 stdout/stderr，
        # 即使主进程已 os._exit，communicate() 仍会等待继承的管道关闭并误报超时。
        with (
            stdout_log.open("wb") as stdout_handle,
            stderr_log.open("wb") as stderr_handle,
        ):
            process_result = subprocess.run(
                [str(exe)],
                cwd=root,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout_seconds,
                check=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        if process_result.returncode != 0:
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(
                f"frozen PySide startup smoke exited with {process_result.returncode}: "
                f"{stderr}"
            )
        if not trace.is_file():
            raise RuntimeError("frozen PySide startup smoke produced no trace")
        records = [
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        required_events = {f"T{index}" for index in range(7)}
        if not records or not required_events.issubset(records[-1]):
            raise RuntimeError(
                f"frozen PySide startup smoke did not reach T6: "
                f"{records[-1:] or 'empty'}"
            )
        if not result_file.is_file():
            raise RuntimeError(
                "frozen PySide startup smoke produced no result evidence"
            )
        smoke_result = json.loads(result_file.read_text(encoding="utf-8"))
        if smoke_result.get("supervisor_ready") is not True:
            raise RuntimeError(
                "frozen PySide startup smoke did not prove Supervisor ready"
            )
        module_file = Path(str(smoke_result.get("module_file", ""))).resolve()
        try:
            module_file.relative_to(root.resolve())
        except ValueError as error:
            raise RuntimeError(
                f"Supervisor module loaded outside extracted artifact: {module_file}"
            ) from error
        if not module_file.is_file():
            raise RuntimeError(
                f"Supervisor module evidence does not exist in artifact: {module_file}"
            )
    except subprocess.TimeoutExpired as error:
        stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(
            f"frozen PySide startup smoke timed out after {timeout_seconds:.0f}s: "
            f"{stderr}"
        ) from error
    finally:
        trace.unlink(missing_ok=True)
        result_file.unlink(missing_ok=True)
        stdout_log.unlink(missing_ok=True)
        stderr_log.unlink(missing_ok=True)
        shutil.rmtree(smoke_root, ignore_errors=True)


def _verify_frozen_webengine(root: Path, timeout_seconds: float = 30.0) -> None:
    """Launch the frozen entry and require a real QWebEngineView HTML load."""
    exe = root / "VibeOCR.exe"
    result_file = root / ".webengine-smoke-result.json"
    stdout_log = root / ".webengine-smoke.stdout.log"
    stderr_log = root / ".webengine-smoke.stderr.log"
    result_file.unlink(missing_ok=True)
    stdout_log.unlink(missing_ok=True)
    stderr_log.unlink(missing_ok=True)
    env = os.environ.copy()
    env["VIBEOCR_SELF_TEST_WEBENGINE"] = "1"
    env["VIBEOCR_SELF_TEST_RESULT"] = str(result_file)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_OPENGL"] = "software"
    env["QT_QUICK_BACKEND"] = "software"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(variable, None)
    try:
        with (
            stdout_log.open("wb") as stdout_handle,
            stderr_log.open("wb") as stderr_handle,
        ):
            process_result = subprocess.run(
                [str(exe)],
                cwd=root,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout_seconds,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if process_result.returncode != 0:
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(
                "frozen WebEngine smoke exited with "
                f"{process_result.returncode}: {stderr}"
            )
        if not result_file.is_file():
            raise RuntimeError("frozen WebEngine smoke produced no result")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("load_finished") is not True:
            raise RuntimeError("frozen WebEngine smoke did not finish an HTML load")
        if result.get("webchannel_round_trip") is not True:
            raise RuntimeError(
                "frozen WebEngine smoke did not finish a WebChannel round trip"
            )
    except subprocess.TimeoutExpired as error:
        stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(
            f"frozen WebEngine smoke timed out after {timeout_seconds:.0f}s: {stderr}"
        ) from error
    finally:
        result_file.unlink(missing_ok=True)
        stdout_log.unlink(missing_ok=True)
        stderr_log.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    _verify_archive_size(args.artifact)
    with tempfile.TemporaryDirectory(prefix="vibeocr-pyside-verify-") as temp:
        with zipfile.ZipFile(args.artifact) as archive:
            archive.extractall(temp)
        roots = list(Path(temp).iterdir())
        root = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(temp)
        required = [
            root / "VibeOCR.exe",
            root / "updater.exe",
            root / "component-lock.json",
            root / "product-release-manifest.json",
            root / "backend" / "runtime-manifest.json",
            root / "runtime-installer" / "vibeocr-runtime-installer.exe",
        ]
        missing = [
            str(path.relative_to(root)) for path in required if not path.is_file()
        ]
        if missing:
            raise RuntimeError(f"required PySide files missing: {missing}")
        prohibited = [
            path.name
            for path in root.iterdir()
            if path.name.casefold() in {"vibeocr.winui.exe", "vibeocr.bootstrapper.exe"}
        ]
        if prohibited:
            raise RuntimeError(
                f"Next executable present in Classic artifact: {prohibited}"
            )
        if (root / "updater.exe").stat().st_size == 0:
            raise RuntimeError("Classic updater is empty")
        manifest = json.loads(
            (root / "product-release-manifest.json").read_text(encoding="utf-8")
        )
        if manifest.get("frontend") != "classic":
            raise RuntimeError("product release manifest frontend is not classic")
        records = manifest.get("files")
        if not isinstance(records, dict) or not records:
            raise RuntimeError("product release manifest has no file closure")
        for relative, record in records.items():
            bound = root / str(relative)
            if not bound.is_file():
                raise RuntimeError(f"bound product file is missing: {relative}")
            if hashlib.sha256(bound.read_bytes()).hexdigest() != record.get("sha256"):
                raise RuntimeError(f"bound product file hash mismatch: {relative}")
            if bound.stat().st_size != record.get("size"):
                raise RuntimeError(f"bound product file size mismatch: {relative}")

        lock_path = root / "component-lock.json"
        lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        if lock_hash != manifest.get("component_lock_sha256"):
            raise RuntimeError("embedded component lock hash mismatch")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        backend = lock.get("backend", {})
        required_capabilities = set(lock.get("required_capabilities", []))
        expected_capabilities = {
            "export.document.v1",
            "ocr.recognition.v2",
            "pdf.edit.v2",
            "qrcode.v2",
            "runtime.settings.v2",
        }
        if required_capabilities != expected_capabilities:
            raise RuntimeError("Classic component lock capability set is incomplete")

        runtime_manifest_path = root / "backend" / "runtime-manifest.json"
        runtime_manifest_hash = hashlib.sha256(
            runtime_manifest_path.read_bytes()
        ).hexdigest()
        if runtime_manifest_hash != backend.get("runtime_manifest_sha256"):
            raise RuntimeError("bound runtime manifest hash mismatch")
        runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
        wheel = root / "backend" / str(runtime_manifest.get("backend_wheel", ""))
        if not wheel.is_file():
            raise RuntimeError("bound backend wheel is missing")
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if actual != backend.get("artifact_sha256"):
            raise RuntimeError("bound backend wheel hash mismatch")
        if actual != runtime_manifest.get("backend_sha256"):
            raise RuntimeError("runtime manifest backend wheel hash mismatch")
        with zipfile.ZipFile(wheel) as wheel_archive:
            members = set(wheel_archive.namelist())
        if "vibeocr/backend/supervisor/main.py" not in members:
            raise RuntimeError("backend wheel has no Supervisor entry")

        installer = runtime_manifest.get("installer", {})
        installer_exe = root / "runtime-installer" / "vibeocr-runtime-installer.exe"
        if hashlib.sha256(installer_exe.read_bytes()).hexdigest() != installer.get(
            "executable_sha256"
        ):
            raise RuntimeError("extracted Runtime Installer hash mismatch")
        if os.name == "nt":
            _verify_frozen_startup(root)
            _verify_frozen_webengine(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
