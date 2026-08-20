"""Run a real two-version Velopack Portable apply/restart/move E2E on Windows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_STATE_MARKERS = ("config", "logs", "cache", "models", "runtime")


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
    if not (root / "current" / "VibeOCR.exe").is_file():
        raise RuntimeError("Portable archive current root has no VibeOCR.exe")
    _portable_launcher(root)
    return root


def _portable_launcher(root: Path) -> Path:
    """Return Velopack's stable root execution stub, failing on ambiguity."""
    launchers = sorted(
        path
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.casefold() == ".exe"
        and path.name.casefold() != "update.exe"
    )
    if len(launchers) != 1:
        raise RuntimeError(
            "Portable root must contain exactly one execution stub: "
            f"{[path.name for path in launchers]}"
        )
    return launchers[0]


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


def _state_evidence(state_root: Path, *, limit: int = 24) -> list[str]:
    """List bounded, shallow state names for CI diagnosis without reading contents."""
    evidence: list[str] = []
    try:
        for path in sorted(state_root.iterdir(), key=lambda item: item.name.casefold()):
            evidence.append(f"{path.name}/" if path.is_dir() else path.name)
            if len(evidence) >= limit:
                break
            if not path.is_dir():
                continue
            for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
                evidence.append(
                    f"{path.name}/{child.name}/" if child.is_dir() else f"{path.name}/{child.name}"
                )
                if len(evidence) >= limit:
                    break
            if len(evidence) >= limit:
                break
    except OSError as exc:
        evidence.append(f"<unavailable: {type(exc).__name__}>")
    return evidence


def _launch(root: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 - verified packaged executable
        [str(_portable_launcher(root))],
        cwd=root,
        env=env,
    )


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
        moved_evidence = _wait_for_result(moved_result, 45.0, moved_process)
    finally:
        _stop_process(moved_process)
    if moved_evidence.get("installed_version") != target_version:
        raise RuntimeError(f"moved app reported wrong version: {moved_evidence}")
    if Path(moved_evidence["install_root"]) != moved.resolve():
        raise RuntimeError("moved app retained its previous Portable root")
    _assert_state_markers(moved, expected)
    outside_writes = {
        directory.name: [str(path.relative_to(directory)) for path in directory.rglob("*")]
        for directory in (local, roaming, profile, temp)
        if any(directory.rglob("*"))
    }
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
