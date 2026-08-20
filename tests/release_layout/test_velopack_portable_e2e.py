from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.verify_velopack_portable_e2e as portable_e2e
from scripts.verify_velopack_portable_e2e import (
    _launch,
    _portable_root,
    _stop_process,
    _wait_for_result,
)


class _Process:
    def __init__(self, *, running: bool, terminate_times_out: bool = False) -> None:
        self.running = running
        self.terminate_times_out = terminate_times_out
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def wait(self, timeout: float) -> int:
        del timeout
        self.waits += 1
        if self.terminate_times_out and self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("VibeOCR.exe", 15)
        self.running = False
        return 0


def _write_portable_layout(root: Path) -> None:
    current = root / "current"
    current.mkdir(parents=True)
    (root / ".portable").write_text("", encoding="utf-8")
    (root / "Update.exe").write_bytes(b"MZ")
    (root / "VibeOCR.exe").write_bytes(b"MZ")
    (current / "VibeOCR.exe").write_bytes(b"MZ")
    (current / "sq.version").write_text("{}", encoding="utf-8")


def test_portable_root_uses_velopack_current_layout(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted"
    root = extracted / "portable"
    _write_portable_layout(root)

    assert _portable_root(extracted) == root


@pytest.mark.parametrize(
    "missing",
    ["Update.exe", "VibeOCR.exe", "current/VibeOCR.exe", "current/sq.version"],
)
def test_portable_root_requires_canonical_velopack_markers(
    tmp_path: Path, missing: str
) -> None:
    extracted = tmp_path / "extracted"
    root = extracted / "portable"
    _write_portable_layout(root)
    (root / missing).unlink()

    with pytest.raises(RuntimeError, match="canonical"):
        _portable_root(extracted)


@pytest.mark.parametrize("extra", ["VibeOCRClassic.exe", "Surprise.exe"])
def test_portable_root_rejects_extra_execution_stub(
    tmp_path: Path, extra: str
) -> None:
    extracted = tmp_path / "extracted"
    root = extracted / "portable"
    _write_portable_layout(root)
    (root / extra).write_bytes(b"MZ")

    with pytest.raises(RuntimeError, match="canonical"):
        _portable_root(extracted)


def test_launch_uses_the_stable_velopack_execution_stub(
    tmp_path: Path, monkeypatch
) -> None:
    _write_portable_layout(tmp_path)
    captured: dict[str, object] = {}
    process = object()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(
        "scripts.verify_velopack_portable_e2e.subprocess.Popen", fake_popen
    )

    launched = _launch(tmp_path, {"KEY": "value"})

    assert launched is process
    assert captured["command"] == [str(tmp_path / "VibeOCR.exe")]
    assert captured["cwd"] == tmp_path
    assert captured["env"] == {"KEY": "value"}


def test_wait_timeout_reports_expected_result_process_and_state_evidence(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    config = state / "config"
    config.mkdir(parents=True)
    (config / "runtime-e2e.json").write_text("[]", encoding="utf-8")
    result = state / "missing-result.json"
    process = _Process(running=True)

    with pytest.raises(RuntimeError, match="timed out") as captured:
        _wait_for_result(result, 0.0, process)  # type: ignore[arg-type]

    message = str(captured.value)
    assert str(result) in message
    assert "returncode=None" in message
    assert "config/runtime-e2e.json" in message


def test_wait_for_evidence_writer_exit_uses_reported_pid(monkeypatch) -> None:
    waited: dict[str, object] = {}

    def fake_wait(pid: int, *, timeout: float) -> None:
        waited.update(pid=pid, timeout=timeout)

    monkeypatch.setattr(portable_e2e, "_wait_for_pid_exit", fake_wait, raising=False)

    portable_e2e._wait_for_evidence_writer_exit(
        {"process_id": 4312},
        timeout=15.0,
    )

    assert waited == {"pid": 4312, "timeout": 15.0}


@pytest.mark.parametrize("process_id", [None, True, 0, -1, "4312"])
def test_wait_for_evidence_writer_exit_rejects_invalid_pid(process_id: object) -> None:
    with pytest.raises(RuntimeError, match="process_id"):
        portable_e2e._wait_for_evidence_writer_exit(
            {"process_id": process_id},
            timeout=15.0,
        )


def test_state_evidence_does_not_enter_reparse_directory(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    reparse = state / "linked-runtime"
    reparse.mkdir(parents=True)
    real_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == reparse:
            raise AssertionError("diagnostics followed a reparse directory")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    monkeypatch.setattr(
        portable_e2e,
        "_is_reparse_point",
        lambda path: path == reparse,
        raising=False,
    )

    evidence = portable_e2e._state_evidence(state)

    assert "linked-runtime/<reparse>" in evidence


def test_stop_process_terminates_a_running_packaged_app() -> None:
    process = _Process(running=True)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is False
    assert process.waits == 1


def test_stop_process_kills_after_terminate_timeout() -> None:
    process = _Process(running=True, terminate_times_out=True)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is True
    assert process.waits == 2


def test_stop_process_only_reaps_an_exited_app() -> None:
    process = _Process(running=False)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is False
    assert process.killed is False
    assert process.waits == 1
