"""Verify the PySide Classic Velopack input and exact backend-wheel binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path


def _verify_embedded_app_icon(executable: Path, icon: Path) -> None:
    """Require every source ICO frame to be embedded in the final PE."""
    try:
        icon_bytes = icon.read_bytes()
        executable_bytes = executable.read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", icon_bytes)
        if reserved != 0 or image_type != 1 or count == 0:
            raise ValueError("invalid ICO header")
        for index in range(count):
            entry_offset = 6 + index * 16
            size, payload_offset = struct.unpack_from(
                "<II", icon_bytes, entry_offset + 8
            )
            payload = icon_bytes[payload_offset : payload_offset + size]
            if len(payload) != size or payload not in executable_bytes:
                raise ValueError(f"ICO frame {index} is not embedded")
    except (OSError, struct.error, ValueError) as error:
        raise RuntimeError("VibeOCR.exe has no embedded custom app icon") from error


def _verify_product_file_closure(root: Path, records: dict[str, object]) -> None:
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "product-release-manifest.json"
    }
    expected = {str(relative) for relative in records}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"product file closure mismatch: missing={missing}, extra={extra}"
        )


def _verify_frontend_protocol_lock(
    root: Path,
    product_manifest: dict[str, object],
    component_lock: dict[str, object],
) -> dict[str, object]:
    path = root / "frontend-protocol-lock.json"
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != product_manifest.get("frontend_protocol_lock_sha256"):
        raise RuntimeError("embedded frontend Protocol lock hash mismatch")
    frontend_lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(frontend_lock, dict):
        raise RuntimeError("embedded frontend Protocol lock is invalid")
    frontend_version = frontend_lock.get("version")
    runtime = component_lock.get("protocol")
    runtime_version = runtime.get("version") if isinstance(runtime, dict) else None
    if (
        not isinstance(frontend_version, str)
        or not isinstance(runtime_version, str)
        or frontend_version.split(".", 1)[0] != runtime_version.split(".", 1)[0]
    ):
        raise RuntimeError("frontend and Runtime Protocol majors differ")
    return frontend_lock


def _verify_reduced_layout(root: Path) -> None:
    prohibited = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().casefold()
        if (
            relative.startswith("runtime-installer/")
            or "/pymupdf" in f"/{relative}"
            or "/fitz" in f"/{relative}"
            or "/lxml" in f"/{relative}"
            or "quick3d" in relative
            or "/qmltooling/" in f"/{relative}/"
        ):
            prohibited.append(relative)
    if prohibited:
        raise RuntimeError(f"prohibited reduced-layout files present: {prohibited}")


def _verify_bound_python_archive(
    root: Path, runtime_manifest: dict[str, object]
) -> None:
    python = runtime_manifest.get("python")
    if not isinstance(python, dict):
        raise RuntimeError("runtime manifest has no bound Python archive")
    archive = root / "backend" / str(python.get("archive", ""))
    if not archive.is_file():
        raise RuntimeError("bound Python archive is missing")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != python.get("sha256"):
        raise RuntimeError("bound Python archive hash mismatch")


def _verify_bound_installer_archive(
    root: Path, runtime_manifest: dict[str, object]
) -> bytes:
    installer = runtime_manifest.get("installer")
    if not isinstance(installer, dict):
        raise RuntimeError("runtime manifest has no bound Runtime Installer")
    archive_path = root / "backend" / str(installer.get("archive", ""))
    if not archive_path.is_file():
        raise RuntimeError("bound Runtime Installer archive is missing")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != installer.get("sha256"):
        raise RuntimeError("bound Runtime Installer archive hash mismatch")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            executable = archive.read(str(installer.get("executable_path", "")))
    except (KeyError, zipfile.BadZipFile) as error:
        raise RuntimeError("bound Runtime Installer executable is missing") from error
    if hashlib.sha256(executable).hexdigest() != installer.get("executable_sha256"):
        raise RuntimeError("bound Runtime Installer executable hash mismatch")
    return executable


def _verify_bound_installer_inspect(
    root: Path,
    runtime_manifest: dict[str, object],
    executable: bytes,
    accelerator: str,
    timeout_seconds: float = 60.0,
) -> None:
    """不走 Classic T6 bypass，直接验证本地 layout 的真实 Installer inspect。"""
    executable_path = (
        root / "data" / "cache" / "runtime-installer" / "vibeocr-runtime-installer.exe"
    )
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    executable_path.write_bytes(executable)
    request = {
        "protocol_version": 2,
        "operation": "inspect",
        "product_root": str(root),
        "component_lock": str(root / "component-lock.json"),
        "runtime_manifest": str(root / "backend" / "runtime-manifest.json"),
        "accelerator": accelerator,
        "layout_manifest": str(root / "product-release-manifest.json"),
        "product_id": "classic",
    }
    try:
        result = subprocess.run(
            [str(executable_path), "--request-json", json.dumps(request)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "bound Runtime Installer inspect timed out after "
            f"{timeout_seconds:.0f} seconds"
        ) from error
    envelopes = []
    for line in result.stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            envelopes.append(value)
    envelope = envelopes[-1] if envelopes else {}
    state = envelope.get("state")
    if (
        result.returncode != 0
        or envelope.get("protocol_version") != 2
        or envelope.get("ok") is not True
        or envelope.get("operation") != "inspect"
        or not isinstance(state, dict)
        or state.get("status") != "missing"
    ):
        raise RuntimeError(
            "bound Runtime Installer inspect failed: "
            f"exit={result.returncode}, stdout={result.stdout}, stderr={result.stderr}"
        )
    if state.get("integrity") != "not-installed":
        raise RuntimeError("bound Runtime Installer inspect returned invalid integrity")
    _verify_runtime_layout(state, root, accelerator)


def _verify_runtime_layout(
    envelope: dict[str, object],
    root: Path,
    accelerator: str,
) -> None:
    """Require Backend's single fixed Runtime layout."""
    if envelope.get("accelerator") != accelerator:
        raise RuntimeError("bound Runtime Installer returned an invalid accelerator")
    runtime_root = envelope.get("runtime_root")
    if not isinstance(runtime_root, str):
        raise RuntimeError("bound Runtime Installer returned no runtime_root")
    expected = (root / "data" / "runtime").resolve()
    if Path(runtime_root).resolve() != expected:
        raise RuntimeError("bound Runtime Installer escaped the data runtime layout")


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
    env["VIBEOCR_CLASSIC_DATA_ROOT"] = str(root / ".smoke-data")
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
    env["VIBEOCR_CLASSIC_DATA_ROOT"] = str(root / ".smoke-data")
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


def _verify_frozen_pdf(root: Path, timeout_seconds: float = 30.0) -> None:
    """真实启动冻结入口并要求 QtPdf 在无 PyMuPDF 时可构造。"""
    exe = root / "VibeOCR.exe"
    result_file = root / ".pdf-smoke-result.json"
    stdout_log = root / ".pdf-smoke.stdout.log"
    stderr_log = root / ".pdf-smoke.stderr.log"
    for path in (result_file, stdout_log, stderr_log):
        path.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["VIBEOCR_SELF_TEST_PDF"] = "1"
    environment["VIBEOCR_SELF_TEST_RESULT"] = str(result_file)
    environment["VIBEOCR_CLASSIC_DATA_ROOT"] = str(root / ".smoke-data")
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        with (
            stdout_log.open("w", encoding="utf-8") as stdout_handle,
            stderr_log.open("w", encoding="utf-8") as stderr_handle,
        ):
            process_result = subprocess.run(
                [str(exe)],
                cwd=root,
                env=environment,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout_seconds,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if process_result.returncode != 0:
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(
                f"frozen QtPdf smoke exited with {process_result.returncode}: {stderr}"
            )
        if not result_file.is_file():
            raise RuntimeError("frozen QtPdf smoke produced no result")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("qt_pdf_created") is not True:
            raise RuntimeError("frozen QtPdf smoke did not create QPdfDocument")
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"frozen QtPdf smoke timed out after {timeout_seconds:.0f}s"
        ) from error
    finally:
        result_file.unlink(missing_ok=True)
        stdout_log.unlink(missing_ok=True)
        stderr_log.unlink(missing_ok=True)


def _verify_portable_state_smoke(root: Path, timeout_seconds: float = 45.0) -> None:
    """完全便携状态根 smoke：含空格/中文/长段的便携根内运行真实冻结入口。

    断言：
    - fail closed：``state`` 被同名文件占据（等效不可创建/不可写）时，
      入口以退出码 2 结束并把明确原因写入结果文件（windowed 进程没有
      可用 stderr），不弹原生对话框、不在别处落盘。
    - 默认解析（无注入缝）：可写便携根下创建 ``<portable-root>/state``
      子树，bootstrap 日志位于 ``state/logs``；
    - 把 ``LOCALAPPDATA`` 指到监控目录后，不创建旧的
      ``VibeOCRClassicData`` 用户目录。
    """

    import tempfile

    # 副本放在短路径临时父目录（测试装置自有 scratch，非产品状态），
    # 目录名含空格 + 中文 + 较长段，不依赖系统长路径开关。
    smoke_parent = Path(tempfile.mkdtemp(prefix="vibeocr-portable-smoke-"))
    portable_root = smoke_parent / "VibeOCR 便携 Smoke 目录 2026 with spaces"
    blocked_result = smoke_parent / ".blocked-result.json"
    local_app_data = smoke_parent / ".smoke-localappdata"
    # 复制时排除前面 smoke 与 installer inspect 的瞬态产物
    shutil.copytree(
        root,
        portable_root,
        ignore=shutil.ignore_patterns(
            ".smoke-data",
            ".smoke-runtime",
            "data",
            "state",
            ".startup-smoke*",
            ".pdf-smoke*",
            ".webengine-smoke*",
        ),
    )
    # 必须运行副本内的 exe：便携根解析跟随 sys.executable，而不是 cwd
    exe = portable_root / "VibeOCR.exe"
    result_file = portable_root / ".pdf-smoke-result.json"
    state_root = portable_root / "state"
    local_app_data.mkdir()

    def _launch(extra_env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env.pop("VIBEOCR_CLASSIC_DATA_ROOT", None)
        env["LOCALAPPDATA"] = str(local_app_data)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.update(extra_env)
        for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(variable, None)
        return subprocess.run(
            [str(exe)],
            cwd=portable_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )

    try:
        # 1) fail closed：state 被同名文件占据（不请求管理员权限、不回退）
        state_root.write_text("blocked", encoding="utf-8")
        blocked = _launch(
            {
                "VIBEOCR_SILENT_PORTABLE_ERROR": "1",
                "VIBEOCR_SELF_TEST_PDF": "1",
                "VIBEOCR_SELF_TEST_RESULT": str(blocked_result),
            }
        )
        reason = ""
        if blocked_result.is_file():
            reason = str(
                json.loads(blocked_result.read_text(encoding="utf-8")).get(
                    "portable_state_error", ""
                )
            )
        if blocked.returncode != 2 or "状态目录不可用" not in reason:
            raise RuntimeError(
                "portable state fail-closed smoke did not exit(2) with a clear "
                f"reason: code={blocked.returncode} reason={reason[:500]}"
            )
        if (local_app_data / "VibeOCRClassicData").exists():
            raise RuntimeError(
                "portable state fail-closed smoke wrote the legacy LocalAppData root"
            )

        # 2) 默认便携解析：真实启动并断言 state 子树与 bootstrap 日志
        state_root.unlink()
        launched = _launch(
            {
                "VIBEOCR_SELF_TEST_PDF": "1",
                "VIBEOCR_SELF_TEST_RESULT": str(result_file),
            }
        )
        if launched.returncode != 0:
            stderr_text = launched.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"portable state smoke exited with {launched.returncode}: "
                f"{stderr_text[:500]}"
            )
        if not result_file.is_file():
            raise RuntimeError("portable state smoke produced no result evidence")
        for relative in (
            "config",
            "logs",
            "temp/clipboard",
            "web/qtwebengine/cache",
            "web/qtwebengine/persistent",
        ):
            if not (state_root / relative).is_dir():
                raise RuntimeError(f"portable state layout missing: state/{relative}")
        bootstrap_log = state_root / "logs" / "vibeocr-bootstrap.log"
        if not bootstrap_log.is_file():
            raise RuntimeError("portable state smoke has no state/logs bootstrap log")
        legacy_dir = local_app_data / "VibeOCRClassicData"
        if legacy_dir.exists():
            raise RuntimeError(
                f"portable state smoke wrote the legacy user directory: {legacy_dir}"
            )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"portable state smoke timed out after {timeout_seconds:.0f}s"
        ) from error
    finally:
        shutil.rmtree(smoke_parent, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_root", type=Path)
    args = parser.parse_args()
    root = args.product_root.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("PySide product root must be a directory")
    if root.is_dir():
        required = [
            root / "VibeOCR.exe",
            root / "component-lock.json",
            root / "frontend-protocol-lock.json",
            root / "product-release-manifest.json",
            root / "backend" / "runtime-manifest.json",
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
        manifest = json.loads(
            (root / "product-release-manifest.json").read_text(encoding="utf-8")
        )
        if manifest.get("frontend") != "classic":
            raise RuntimeError("product release manifest frontend is not classic")
        records = manifest.get("files")
        if not isinstance(records, dict) or not records:
            raise RuntimeError("product release manifest has no file closure")
        _verify_product_file_closure(root, records)
        _verify_reduced_layout(root)
        _verify_embedded_app_icon(
            root / "VibeOCR.exe",
            root / "_internal" / "resources" / "app_icon.ico",
        )
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
        _verify_frontend_protocol_lock(root, manifest, lock)
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
        _verify_bound_python_archive(root, runtime_manifest)
        installer_executable = _verify_bound_installer_archive(root, runtime_manifest)
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

        if os.name == "nt":
            _verify_bound_installer_inspect(
                root,
                runtime_manifest,
                installer_executable,
                str(backend.get("accelerator", "")),
            )
            _verify_frozen_startup(root)
            _verify_frozen_pdf(root)
            _verify_frozen_webengine(root)
            _verify_portable_state_smoke(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
