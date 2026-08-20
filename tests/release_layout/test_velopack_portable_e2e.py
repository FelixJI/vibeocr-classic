from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.verify_velopack_portable_e2e as portable_e2e
from scripts.verify_velopack_portable_e2e import (
    _diagnose_moved_start,
    _launch,
    _owned_bootstrap_log_tail,
    _portable_root,
    _stop_process,
    _wait_for_moved_result,
    _wait_for_result,
)


class _Process:
    def __init__(
        self,
        *,
        running: bool,
        terminate_times_out: bool = False,
        initial_wait_times_out: bool = False,
    ) -> None:
        self.running = running
        self.terminate_times_out = terminate_times_out
        self.initial_wait_times_out = initial_wait_times_out
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.returncode: int | None = None if running else 0

    def poll(self) -> int | None:
        return None if self.running else self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False
        self.returncode = -9

    def wait(self, timeout: float) -> int:
        del timeout
        self.waits += 1
        if self.initial_wait_times_out and not self.terminated:
            raise subprocess.TimeoutExpired("Update.exe", 5)
        if self.terminate_times_out and self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("VibeOCR.exe", 15)
        self.running = False
        self.returncode = 0
        return self.returncode


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


def test_moved_start_diagnostic_uses_explicit_root_and_owned_log(
    tmp_path: Path, monkeypatch
) -> None:
    _write_portable_layout(tmp_path)
    result = tmp_path / "state" / "moved-result.json"
    result.parent.mkdir()
    captured: dict[str, object] = {}
    process = _Process(running=False)

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(portable_e2e.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(portable_e2e, "_wait_for_path", lambda *args: False)

    evidence = _diagnose_moved_start(
        tmp_path,
        {"KEY": "value"},
        result,
    )

    diagnostic_log = tmp_path / "state" / "velopack-start-diagnostic.log"
    assert captured["command"] == [
        str(tmp_path / "Update.exe"),
        "--rootDir",
        str(tmp_path),
        "--log",
        str(diagnostic_log),
        "start",
        "VibeOCR.exe",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["env"] == {"KEY": "value"}
    assert evidence["returncode"] == 0
    assert evidence["result_created"] is False


def test_moved_start_diagnostic_keeps_bounded_owned_log_tail(
    tmp_path: Path, monkeypatch
) -> None:
    _write_portable_layout(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    log = state / "velopack-start-diagnostic.log"
    log.write_text("discarded-prefix\n" + "useful-tail", encoding="utf-8")
    process = _Process(running=False)
    monkeypatch.setattr(portable_e2e.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(portable_e2e, "_wait_for_path", lambda *args: False)

    evidence = _diagnose_moved_start(
        tmp_path,
        {},
        state / "moved-result.json",
        log_limit=11,
    )

    assert evidence["log_tail"] == "useful-tail"


def test_moved_start_diagnostic_includes_owned_early_bootstrap_events(
    tmp_path: Path, monkeypatch
) -> None:
    _write_portable_layout(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    nonce = "a" * 32
    events = state / f"{nonce}-bootstrap-events.jsonl"
    events.write_text('{"phase":"before-velopack","pid":4312}\n', encoding="utf-8")
    bootstrap_log = state / "logs" / "vibeocr-bootstrap.log"
    bootstrap_log.parent.mkdir()
    bootstrap_log.write_text("runtime probe failed", encoding="utf-8")
    process = _Process(running=False)
    monkeypatch.setattr(portable_e2e.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(portable_e2e, "_wait_for_path", lambda *args: False)

    evidence = _diagnose_moved_start(
        tmp_path,
        {"VIBEOCR_CLASSIC_TEST_NONCE": nonce},
        state / "moved-result.json",
    )

    assert '"phase":"before-velopack"' in evidence["bootstrap_events_tail"]
    assert evidence["bootstrap_log_tail"] == "runtime probe failed"


def test_owned_bootstrap_log_tail_is_bounded_to_portable_state(tmp_path: Path) -> None:
    log = tmp_path / "state" / "logs" / "vibeocr-bootstrap.log"
    log.parent.mkdir(parents=True)
    log.write_text("discarded-prefix\n" + "useful-tail", encoding="utf-8")

    assert _owned_bootstrap_log_tail(tmp_path, limit=11) == "useful-tail"


@pytest.mark.parametrize("reparse_name", ["state", "logs", "vibeocr-bootstrap.log"])
def test_owned_bootstrap_log_tail_refuses_reparse_path(
    tmp_path: Path, monkeypatch, reparse_name: str
) -> None:
    log = tmp_path / "state" / "logs" / "vibeocr-bootstrap.log"
    log.parent.mkdir(parents=True)
    log.write_text("must-not-be-read", encoding="utf-8")
    guarded = {
        "state": tmp_path / "state",
        "logs": tmp_path / "state" / "logs",
        "vibeocr-bootstrap.log": log,
    }[reparse_name]
    monkeypatch.setattr(
        portable_e2e,
        "_is_reparse_point",
        lambda path: path == guarded,
    )
    monkeypatch.setattr(
        portable_e2e,
        "_bounded_file_tail",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("reparse log was read")
        ),
    )

    assert _owned_bootstrap_log_tail(tmp_path) == f"<reparse: {reparse_name}>"


def test_moved_start_diagnostic_stops_its_own_timed_out_updater(
    tmp_path: Path, monkeypatch
) -> None:
    _write_portable_layout(tmp_path)
    process = _Process(running=True, initial_wait_times_out=True)
    monkeypatch.setattr(portable_e2e.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(portable_e2e, "_wait_for_path", lambda *args: False)

    evidence = _diagnose_moved_start(
        tmp_path,
        {},
        tmp_path / "state" / "moved-result.json",
    )

    assert evidence["timed_out"] is True
    assert process.terminated is True
    assert process.killed is False


def test_moved_start_diagnostic_never_swallows_original_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    original = RuntimeError("original launcher timeout")
    monkeypatch.setattr(portable_e2e, "_wait_for_result", lambda *args: (_ for _ in ()).throw(original))
    monkeypatch.setattr(
        portable_e2e,
        "_diagnose_moved_start",
        lambda *args, **kwargs: {"returncode": 0, "result_created": True},
    )

    with pytest.raises(RuntimeError, match="original launcher timeout") as captured:
        _wait_for_moved_result(
            tmp_path / "state" / "moved-result.json",
            _Process(running=False),  # type: ignore[arg-type]
            tmp_path,
            {},
        )

    assert captured.value is not original
    assert captured.value.__cause__ is original
    assert "result_created': True" in str(captured.value)


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


def test_outside_write_audit_ignores_tools_but_detects_product_state(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "Profile"
    temp = tmp_path / "Temp"
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "AppData"
    (profile / ".rustup").mkdir(parents=True)
    (profile / ".rustup/settings.toml").write_text("version = 12", encoding="utf-8")
    temp.mkdir()
    (temp / "velopack_VibeOCRClassic.log").write_text("updater log", encoding="utf-8")
    (local / "VibeOCRClassic/config").mkdir(parents=True)
    (local / "VibeOCRClassic/config/settings.json").write_text("{}", encoding="utf-8")
    roaming.mkdir()

    assert portable_e2e._outside_product_writes(local, roaming, profile, temp) == {
        "LocalAppData": ["VibeOCRClassic/config/settings.json"]
    }
