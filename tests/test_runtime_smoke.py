from __future__ import annotations

from pathlib import Path

import pytest

from vibeocr.classic.runtime_installation import RuntimeLaunch
from vibeocr.classic.runtime_smoke import probe_runtime_launch


class _LaunchReached(RuntimeError):
    pass


def test_offline_probe_uses_protocol_27_engine_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Protocol 2.7.0 exposes OcrEngine from dtos, not the package root."""
    import vibeocr.runtime_contracts as contracts
    from vibeocr.runtime_client.process import SupervisorProcess

    monkeypatch.delattr(contracts, "OcrEngine", raising=False)

    def stop_after_protocol_imports(**_kwargs: object) -> None:
        raise _LaunchReached

    monkeypatch.setattr(SupervisorProcess, "launch", stop_after_protocol_imports)
    launch = RuntimeLaunch(
        python_executable="python.exe",
        supervisor_module="vibeocr.backend.supervisor.app",
        working_directory=str(tmp_path),
        model_root=str(tmp_path / "models"),
        environment={},
    )

    with pytest.raises(_LaunchReached):
        probe_runtime_launch(launch, tmp_path / "state")


def test_artifact_smoke_accepts_uppercase_hex_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.classic_release_entry import _run_velopack_update_smoke

    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_MODE", "artifact-smoke")
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_NONCE", "A" * 32)
    monkeypatch.delenv("VIBEOCR_SELF_TEST_UPDATE_FEED", raising=False)

    with pytest.raises(KeyError, match="VIBEOCR_SELF_TEST_UPDATE_FEED"):
        _run_velopack_update_smoke()
