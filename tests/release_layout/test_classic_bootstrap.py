from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import classic_release_entry


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
