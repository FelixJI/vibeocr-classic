"""Run a real two-version Velopack Portable apply/restart/move E2E on Windows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_STATE_MARKERS = ("config", "logs", "cache", "models", "runtime")
_PORTABLE_LAUNCHER = "VibeOCR.exe"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def _portable_root(extracted: Path) -> Path:
    markers = list(extracted.rglob(".portable"))
    if len(markers) != 1 or not markers[0].is_file():
        raise RuntimeError(
            f"Portable archive must contain exactly one .portable marker: {markers}"
        )
    root = markers[0].parent
    _portable_launcher(root)
    return root


def _portable_launcher(root: Path) -> Path:
    """Validate the canonical Velopack layout and return its stable stub."""
    required = (
        root / "Update.exe",
        root / _PORTABLE_LAUNCHER,
        root / "current" / "VibeOCR.exe",
        root / "current" / "sq.version",
    )
    missing = [path.relative_to(root).as_posix() for path in required if not path.is_file()]
    root_executables = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() == ".exe"
    )
    expected_executables = frozenset({"update.exe", _PORTABLE_LAUNCHER.casefold()})
    actual_executables = frozenset(path.name.casefold() for path in root_executables)
    if missing or actual_executables != expected_executables:
        raise RuntimeError(
            "Portable root does not match the canonical Velopack layout: "
            f"missing={missing}; root_executables="
            f"{[path.name for path in root_executables]}"
        )
    return root / _PORTABLE_LAUNCHER


def _write_state_markers(root: Path, nonce: str) -> dict[str, bytes]:
    expected: dict[str, bytes] = {}
    for name in _STATE_MARKERS:
        directory = root / "state" / name
        directory.mkdir(parents=True, exist_ok=True)
        payload = f"{name}:{nonce}".encode()
        marker = directory / f"velopack-e2e-{nonce}.marker"
        marker.write_bytes(payload)
        expected[marker.relative_to(root).as_posix()] = payload
    return expected


def _assert_state_markers(root: Path, expected: dict[str, bytes]) -> None:
    for relative, payload in expected.items():
        marker = root / relative
        if not marker.is_file() or marker.read_bytes() != payload:
            raise RuntimeError(f"Velopack apply lost stable state marker: {relative}")


def _wait_for_result(result: Path, timeout: float, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.is_file():
            return json.loads(result.read_text(encoding="utf-8"))
        if process.poll() not in {None, 0}:
            raise RuntimeError(
                "Portable process exited before writing result "
                f"{result}: returncode={process.returncode}"
            )
        time.sleep(0.25)
    returncode = process.poll()
    raise RuntimeError(
        f"Portable update/restart timed out after {timeout:.0f}s; "
        f"expected={result}; returncode={returncode}; "
        f"state_evidence={_state_evidence(result.parent)}"
    )


def _wait_for_evidence_writer_exit(evidence: dict, *, timeout: float) -> None:
    """Wait for the restarted app which durably wrote the E2E evidence."""
    process_id = evidence.get("process_id")
    if (
        isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id <= 0
    ):
        raise RuntimeError(
            f"Portable E2E evidence has invalid process_id: {process_id!r}"
        )
    _wait_for_pid_exit(process_id, timeout=timeout)


def _wait_for_pid_exit(process_id: int, *, timeout: float) -> None:
    """Wait on a PID with SYNCHRONIZE rights only; never terminate that process."""
    if os.name != "nt":
        raise RuntimeError("Portable E2E writer wait is Windows-only")
    if timeout <= 0:
        raise ValueError("Portable E2E writer timeout must be positive")

    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    wait_failed = 0xFFFFFFFF
    error_invalid_parameter = 87
    maximum_finite_wait = 0xFFFFFFFE

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(synchronize, False, process_id)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return
        raise OSError(error, f"OpenProcess failed for evidence writer {process_id}")

    milliseconds = min(maximum_finite_wait, max(1, int(timeout * 1000)))
    try:
        wait_result = wait_for_single_object(handle, milliseconds)
        if wait_result == wait_object_0:
            return
        if wait_result == wait_timeout:
            raise RuntimeError(
                "Portable E2E evidence writer did not exit naturally within "
                f"{timeout:.0f}s: process_id={process_id}"
            )
        if wait_result == wait_failed:
            error = ctypes.get_last_error()
            raise OSError(
                error,
                f"WaitForSingleObject failed for evidence writer {process_id}",
            )
        raise RuntimeError(
            "Portable E2E evidence writer returned unexpected wait status: "
            f"process_id={process_id}; status={wait_result}"
        )
    finally:
        close_handle(handle)


def _state_evidence(state_root: Path, *, limit: int = 24) -> list[str]:
    """List bounded, shallow state names for CI diagnosis without reading contents."""
    evidence: list[str] = []
    try:
        for path in sorted(state_root.iterdir(), key=lambda item: item.name.casefold()):
            if _is_reparse_point(path):
                evidence.append(f"{path.name}/<reparse>")
                if len(evidence) >= limit:
                    break
                continue
            evidence.append(f"{path.name}/" if path.is_dir() else path.name)
            if len(evidence) >= limit:
                break
            if not path.is_dir():
                continue
            for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
                relative = f"{path.name}/{child.name}"
                if _is_reparse_point(child):
                    evidence.append(f"{relative}/<reparse>")
                else:
                    evidence.append(f"{relative}/" if child.is_dir() else relative)
                if len(evidence) >= limit:
                    break
            if len(evidence) >= limit:
                break
    except OSError as exc:
        evidence.append(f"<unavailable: {type(exc).__name__}>")
    return evidence


def _is_reparse_point(path: Path) -> bool:
    """Use lstat so diagnostics never traverse a symlink or Windows junction."""
    try:
        metadata = path.lstat()
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)


def _launch(root: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 - verified packaged executable
        [str(_portable_launcher(root))],
        cwd=root,
        env=env,
    )


def _wait_for_path(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.1)
    return path.is_file()


def _bounded_file_tail(path: Path, *, limit: int) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


def _owned_bootstrap_log_tail(root: Path, *, limit: int = 8192) -> str | None:
    """Read only the bounded Portable-owned bootstrap log, never a reparse path."""
    state = root / "state"
    logs = state / "logs"
    log = logs / "vibeocr-bootstrap.log"
    for label, path in (
        ("state", state),
        ("logs", logs),
        ("vibeocr-bootstrap.log", log),
    ):
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"<unavailable: {label}: {type(exc).__name__}>"
        if _is_reparse_point(path):
            return f"<reparse: {label}>"
    return _bounded_file_tail(log, limit=limit)


def _diagnose_moved_start(
    root: Path,
    env: dict[str, str],
    result: Path,
    *,
    process_timeout: float = 5.0,
    result_timeout: float = 3.0,
    log_limit: int = 4096,
) -> dict[str, object]:
    """Probe Update.exe after a launcher failure without changing its outcome."""
    diagnostic_log = root / "state" / "velopack-start-diagnostic.log"
    command = [
        str(root / "Update.exe"),
        "--rootDir",
        str(root),
        "--log",
        str(diagnostic_log),
        "start",
        "VibeOCR.exe",
    ]
    process: subprocess.Popen | None = None
    returncode: int | None = None
    timed_out = False
    launch_error: str | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - canonical packaged updater
            command,
            cwd=root,
            env=env,
        )
        try:
            returncode = process.wait(timeout=process_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop_process(process, timeout=2.0)
            returncode = process.returncode
    except Exception as exc:  # diagnostic must preserve the original E2E failure
        launch_error = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None and process.poll() is None:
            _stop_process(process, timeout=2.0)

    result_created = _wait_for_path(result, result_timeout)
    nonce = env.get("VIBEOCR_CLASSIC_TEST_NONCE", "")
    bootstrap_events = root / "state" / f"{nonce}-bootstrap-events.jsonl"
    return {
        "command": command,
        "returncode": returncode,
        "timed_out": timed_out,
        "launch_error": launch_error,
        "result_created": result_created,
        "log_tail": _bounded_file_tail(diagnostic_log, limit=log_limit),
        "bootstrap_events_tail": _bounded_file_tail(
            bootstrap_events,
            limit=8192,
        ),
        "bootstrap_log_tail": _owned_bootstrap_log_tail(root),
    }


def _wait_for_moved_result(
    result: Path,
    process: subprocess.Popen,
    root: Path,
    env: dict[str, str],
) -> dict:
    try:
        return _wait_for_result(result, 45.0, process)
    except RuntimeError as exc:
        diagnostic = _diagnose_moved_start(root, env, result)
        raise RuntimeError(f"{exc}; update_start_diagnostic={diagnostic!r}") from exc


def _stop_process(process: subprocess.Popen, *, timeout: float = 15.0) -> None:
    """Stop one packaged app without leaking it across E2E failure paths."""
    if process.poll() is not None:
        process.wait(timeout=timeout)
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _outside_product_writes(*directories: Path) -> dict[str, list[str]]:
    writes: dict[str, list[str]] = {}
    for directory in directories:
        product_files = []
        for path in directory.rglob("*"):
            relative = path.relative_to(directory)
            if not path.is_file():
                continue
            if (
                directory.name == "Temp"
                and relative.as_posix().casefold()
                == "velopack_vibeocrclassic.log".casefold()
            ):
                continue
            if any("vibeocr" in part.casefold() for part in relative.parts):
                product_files.append(relative.as_posix())
        if product_files:
            writes[directory.name] = sorted(product_files)
    return writes


def verify_portable_e2e(
    old_portable: Path,
    new_feed: Path,
    target_version: str,
    work_dir: Path,
    *,
    timeout: float = 180.0,
) -> None:
    if os.name != "nt":
        raise RuntimeError("Velopack Portable E2E is Windows-only")
    if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", target_version) is None:
        raise RuntimeError(f"invalid target version: {target_version}")
    if work_dir.exists():
        raise RuntimeError("Portable E2E work directory must not already exist")
    extracted = work_dir / "installed-old"
    extracted.mkdir(parents=True)
    with zipfile.ZipFile(old_portable) as archive:
        for member in archive.infolist():
            relative = Path(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe Portable archive member: {member.filename}")
        archive.extractall(extracted)
    root = _portable_root(extracted)
    nonce = uuid.uuid4().hex
    expected = _write_state_markers(root, nonce)
    replaced_marker = root / "current" / f"old-content-{nonce}.marker"
    replaced_marker.write_text("must be replaced", encoding="utf-8")

    isolated = work_dir / "outside-portable"
    local = isolated / "LocalAppData"
    roaming = isolated / "AppData"
    temp = isolated / "Temp"
    profile = isolated / "Profile"
    for directory in (local, roaming, temp, profile):
        directory.mkdir(parents=True)
    result = root / "state" / f"velopack-e2e-result-{nonce}.json"
    env = os.environ.copy()
    env.update(
        {
            "VIBEOCR_CLASSIC_TEST_MODE": "artifact-smoke",
            "VIBEOCR_CLASSIC_TEST_NONCE": nonce,
            "VIBEOCR_SELF_TEST_VELOPACK_UPDATE": "1",
            "VIBEOCR_SELF_TEST_TARGET_VERSION": target_version,
            "VIBEOCR_SELF_TEST_RESULT": str(result),
            "LOCALAPPDATA": str(local),
            "APPDATA": str(roaming),
            "USERPROFILE": str(profile),
            "TEMP": str(temp),
            "TMP": str(temp),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)

    handler = partial(_QuietHandler, directory=str(new_feed))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env["VIBEOCR_SELF_TEST_UPDATE_FEED"] = (
        f"http://127.0.0.1:{server.server_address[1]}/"
    )
    process: subprocess.Popen | None = None
    try:
        process = _launch(root, env)
        evidence = _wait_for_result(result, timeout, process)
        _wait_for_evidence_writer_exit(evidence, timeout=15.0)
    finally:
        try:
            if process is not None:
                _stop_process(process)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    if evidence.get("installed_version") != target_version:
        raise RuntimeError(f"restarted app reported wrong version: {evidence}")
    if replaced_marker.exists():
        raise RuntimeError("Velopack apply did not replace the old current directory")
    _assert_state_markers(root, expected)

    moved = work_dir / "moved after update 便携"
    deadline = time.monotonic() + 20.0
    while True:
        try:
            shutil.move(str(root), moved)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    moved_result = moved / "state" / f"moved-result-{nonce}.json"
    env["VIBEOCR_SELF_TEST_RESULT"] = str(moved_result)
    env["VIBEOCR_SELF_TEST_UPDATE_FEED"] = "http://127.0.0.1:9/"
    moved_process = _launch(moved, env)
    try:
        moved_evidence = _wait_for_moved_result(
            moved_result,
            moved_process,
            moved,
            env,
        )
        _wait_for_evidence_writer_exit(moved_evidence, timeout=15.0)
    finally:
        _stop_process(moved_process)
    if moved_evidence.get("installed_version") != target_version:
        raise RuntimeError(f"moved app reported wrong version: {moved_evidence}")
    if Path(moved_evidence["install_root"]) != moved.resolve():
        raise RuntimeError("moved app retained its previous Portable root")
    _assert_state_markers(moved, expected)
    outside_writes = _outside_product_writes(local, roaming, profile, temp)
    if outside_writes:
        raise RuntimeError(
            "Portable E2E wrote product state outside the Portable root: "
            f"{outside_writes}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-portable", type=Path, required=True)
    parser.add_argument("--new-feed", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    verify_portable_e2e(
        args.old_portable.resolve(strict=True),
        args.new_feed.resolve(strict=True),
        args.target_version,
        args.work_dir.resolve(),
        timeout=args.timeout,
    )
    print("Velopack Portable two-version E2E passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
