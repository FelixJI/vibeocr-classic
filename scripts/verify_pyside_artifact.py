"""Verify the PySide Classic Velopack input and exact backend binding（融合形态）."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path


def _load_policy_summary(policy_path: Path) -> tuple[set[str], str, str]:
    """读取 component-policy.json 的绑定要素（capabilities/加速/协议版本）。"""

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    capabilities = policy.get("required_capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) and capability for capability in capabilities
    ):
        raise RuntimeError("component policy required_capabilities is invalid")
    backend = policy.get("backend", {})
    accelerator = backend.get("accelerator") if isinstance(backend, dict) else None
    if not isinstance(accelerator, str) or not accelerator:
        raise RuntimeError("component policy backend accelerator is invalid")
    protocol = policy.get("protocol", {})
    protocol_version = (
        protocol.get("version") if isinstance(protocol, dict) else None
    )
    if not isinstance(protocol_version, str) or not protocol_version:
        raise RuntimeError("component policy protocol version is invalid")
    return set(capabilities), accelerator, protocol_version


def verify_component_policy_binding(
    component_lock_path: Path,
    policy_path: Path,
) -> None:
    """Require the embedded component lock to retain the product policy closure."""

    lock = json.loads(component_lock_path.read_text(encoding="utf-8"))
    required_capabilities = lock.get("required_capabilities")
    if not isinstance(required_capabilities, list) or not all(
        isinstance(capability, str) and capability
        for capability in required_capabilities
    ):
        raise RuntimeError("component lock capabilities are invalid")
    backend = lock.get("backend")
    if (
        not isinstance(backend, dict)
        or not isinstance(backend.get("accelerator"), str)
        or not backend["accelerator"]
    ):
        raise RuntimeError("component lock backend accelerator is invalid")
    protocol = lock.get("protocol")
    protocol_version = protocol.get("version") if isinstance(protocol, dict) else None
    protocol_match = (
        re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", protocol_version)
        if isinstance(protocol_version, str)
        else None
    )
    if protocol_match is None:
        raise RuntimeError("component lock Protocol version is invalid")
    policy_capabilities, policy_accelerator, policy_protocol = _load_policy_summary(
        policy_path
    )
    if set(required_capabilities) != policy_capabilities:
        raise RuntimeError(
            "Classic component lock capability set differs from component policy"
        )
    if backend["accelerator"] != policy_accelerator:
        raise RuntimeError(
            "Classic component lock accelerator differs from component policy"
        )
    if int(protocol_match.group(1)) != int(policy_protocol.split(".", 1)[0]):
        raise RuntimeError(
            "Classic component lock Protocol major differs from component policy"
        )


def _authorize_smoke_data_root(environment: dict[str, str], root: Path) -> Path:
    """Install the test-only cross-process data-root override with a nonce."""

    nonce = secrets.token_hex(16)
    data_root = root / f".smoke-data-{nonce}"
    environment["VIBEOCR_CLASSIC_DATA_ROOT"] = str(data_root)
    environment["VIBEOCR_CLASSIC_TEST_MODE"] = "artifact-smoke"
    environment["VIBEOCR_CLASSIC_TEST_NONCE"] = nonce
    return data_root


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
            or "quick3d" in relative
            or "/qmltooling/" in f"/{relative}/"
        ):
            prohibited.append(relative)
    if prohibited:
        raise RuntimeError(f"prohibited reduced-layout files present: {prohibited}")


def _prepare_smoke_python(root: Path) -> tuple[Path, Path]:
    """把产品内绑定 wheel 解到隔离 import 根，供 Supervisor smoke 使用。

    融合形态：产品只带 ``backend/vibeocr_backend-*.whl``（引擎环境安装与
    smoke 共用）；Protocol contracts 由当前构建环境提供。
    """
    backend_wheels = sorted((root / "backend").glob("vibeocr_backend-*.whl"))
    if len(backend_wheels) != 1:
        raise RuntimeError("frozen smoke requires exactly one backend wheel")
    smoke_root = root / ".smoke-runtime"
    site_packages = smoke_root / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backend_wheels[0]) as archive:
        archive.extractall(site_packages)
    return Path(sys.executable), site_packages


def _startup_log_tail(path: Path, *, limit: int = 4096) -> str:
    if not path.is_file():
        return "<missing>"
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content[-limit:] if content else "<empty>"


def _verify_frozen_startup(
    root: Path,
    timeout_seconds: float = 45.0,
) -> None:
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
    smoke_data = _authorize_smoke_data_root(env, root)
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
            raise RuntimeError(
                f"frozen PySide startup smoke exited with {process_result.returncode}: "
                f"stdout={_startup_log_tail(stdout_log)}, "
                f"stderr={_startup_log_tail(stderr_log)}"
            )
        if not trace.is_file():
            raise RuntimeError(
                "frozen PySide startup smoke produced no trace: "
                f"stdout={_startup_log_tail(stdout_log)}, "
                f"stderr={_startup_log_tail(stderr_log)}"
            )
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
                "frozen PySide startup smoke produced no result evidence: "
                f"stdout={_startup_log_tail(stdout_log)}, "
                f"stderr={_startup_log_tail(stderr_log)}"
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
        raise RuntimeError(
            f"frozen PySide startup smoke timed out after {timeout_seconds:.0f}s: "
            f"stdout={_startup_log_tail(stdout_log)}, "
            f"stderr={_startup_log_tail(stderr_log)}"
        ) from error
    finally:
        trace.unlink(missing_ok=True)
        result_file.unlink(missing_ok=True)
        stdout_log.unlink(missing_ok=True)
        stderr_log.unlink(missing_ok=True)
        shutil.rmtree(smoke_root, ignore_errors=True)
        shutil.rmtree(smoke_data, ignore_errors=True)


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
    smoke_data = _authorize_smoke_data_root(env, root)
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
        shutil.rmtree(smoke_data, ignore_errors=True)


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
    smoke_data = _authorize_smoke_data_root(environment, root)
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
        shutil.rmtree(smoke_data, ignore_errors=True)


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
            ".smoke-data*",
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


_PROXY_BLACKHOLE = "http://127.0.0.1:9"
_PROXY_VARIABLES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_root", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
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
            root / "bin" / "uv.exe",
        ]
        missing = [
            str(path.relative_to(root)) for path in required if not path.is_file()
        ]
        if missing:
            raise RuntimeError(f"required PySide files missing: {missing}")
        if not sorted((root / "backend").glob("vibeocr_backend-*.whl")):
            raise RuntimeError("required PySide files missing: ['backend/*.whl']")
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
        verify_component_policy_binding(lock_path, args.policy)

        wheel = sorted((root / "backend").glob("vibeocr_backend-*.whl"))[0]
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if actual != backend.get("artifact_sha256"):
            raise RuntimeError("bound backend wheel hash mismatch")
        with zipfile.ZipFile(wheel) as wheel_archive:
            members = set(wheel_archive.namelist())
        if "vibeocr/backend/supervisor/main.py" not in members:
            raise RuntimeError("backend wheel has no Supervisor entry")
        if "vibeocr/backend/runtime-profiles/win-x64-base/requirements.in" not in members:
            raise RuntimeError("backend wheel has no engine environment manifests")

        if os.name == "nt":
            _verify_frozen_startup(root)
            _verify_frozen_pdf(root)
            _verify_frozen_webengine(root)
            _verify_portable_state_smoke(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
