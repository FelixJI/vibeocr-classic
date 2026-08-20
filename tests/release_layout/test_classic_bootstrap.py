from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import classic_release_entry


def _bootstrap_event_environment(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    nonce: str = "a" * 32,
) -> Path:
    current = root / "current"
    state = root / "state"
    current.mkdir(parents=True)
    state.mkdir()
    monkeypatch.setattr(sys, "executable", str(current / "VibeOCR.exe"))
    monkeypatch.setattr(sys, "argv", [str(current / "VibeOCR.exe"), "--probe"])
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_MODE", "artifact-smoke")
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_NONCE", nonce)
    monkeypatch.setenv("VIBEOCR_SELF_TEST_RESULT", str(state / f"moved-result-{nonce}.json"))
    monkeypatch.setenv("VIBEOCR_SELF_TEST_VELOPACK_UPDATE", "1")
    monkeypatch.setenv("VIBEOCR_SELF_TEST_TARGET_VERSION", "1.2.3")
    monkeypatch.setenv("VIBEOCR_SELF_TEST_UPDATE_FEED", "secret-feed-value")
    return state / f"{nonce}-bootstrap-events.jsonl"


def test_authenticated_artifact_smoke_records_bounded_early_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_path = _bootstrap_event_environment(monkeypatch, tmp_path / "portable")

    classic_release_entry._record_early_bootstrap_event("before-velopack")
    classic_release_entry._record_early_bootstrap_event("after-velopack")

    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["phase"] for event in events] == [
        "before-velopack",
        "after-velopack",
    ]
    assert all(event["pid"] == classic_release_entry.os.getpid() for event in events)
    assert events[0]["argv"] == [str(tmp_path / "portable/current/VibeOCR.exe"), "--probe"]
    assert events[0]["environment"] == {
        "VIBEOCR_CLASSIC_TEST_MODE": True,
        "VIBEOCR_CLASSIC_TEST_NONCE": True,
        "VIBEOCR_SELF_TEST_RESULT": True,
        "VIBEOCR_SELF_TEST_TARGET_VERSION": True,
        "VIBEOCR_SELF_TEST_UPDATE_FEED": True,
        "VIBEOCR_SELF_TEST_VELOPACK_UPDATE": True,
    }
    assert "secret-feed-value" not in events_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "nonce"),
    [("production", "a" * 32), ("artifact-smoke", "invalid")],
)
def test_early_bootstrap_event_rejects_unauthenticated_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    nonce: str,
) -> None:
    events_path = _bootstrap_event_environment(monkeypatch, tmp_path / "portable")
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_MODE", mode)
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_NONCE", nonce)

    classic_release_entry._record_early_bootstrap_event("before-velopack")

    assert not events_path.exists()


def test_early_bootstrap_event_rejects_result_outside_portable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_path = _bootstrap_event_environment(monkeypatch, tmp_path / "portable")
    monkeypatch.setenv(
        "VIBEOCR_SELF_TEST_RESULT",
        str(tmp_path / "outside" / "result.json"),
    )

    classic_release_entry._record_early_bootstrap_event("before-velopack")

    assert not events_path.exists()


@pytest.mark.parametrize(
    "existing",
    [b"{}\n" * 16, b"x" * (16 * 1024)],
)
def test_early_bootstrap_event_respects_entry_and_size_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bytes,
) -> None:
    events_path = _bootstrap_event_environment(monkeypatch, tmp_path / "portable")
    events_path.write_bytes(existing)

    classic_release_entry._record_early_bootstrap_event("before-velopack")

    assert events_path.read_bytes() == existing


def test_bootstrap_logs_import_failure_before_app_modules_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "data" / "logs" / "vibeocr-bootstrap.log"
    monkeypatch.setenv("VIBEOCR_BOOTSTRAP_LOG", str(log_path))

    def fail_during_import() -> int:
        raise RuntimeError("synthetic early import failure")

    with pytest.raises(RuntimeError, match="synthetic early import failure"):
        classic_release_entry._run_with_bootstrap(fail_during_import)

    content = log_path.read_text(encoding="utf-8")
    assert "synthetic early import failure" in content
    assert "Traceback" in content


def test_bootstrap_captures_nonzero_startup_exit_and_console_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "data" / "logs" / "vibeocr-bootstrap.log"
    monkeypatch.setenv("VIBEOCR_BOOTSTRAP_LOG", str(log_path))

    def fail_runtime_install() -> int:
        print("Runtime Installer 准备失败: path too long")
        return 1

    assert classic_release_entry._run_with_bootstrap(fail_runtime_install) == 1

    content = log_path.read_text(encoding="utf-8")
    assert "Runtime Installer 准备失败: path too long" in content
    assert "application exited before ready: code=1" in content
    assert sys.stdout is not None
