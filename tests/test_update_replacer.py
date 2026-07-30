from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import update_replacer


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "VibeOCR.exe").write_bytes(b"old executable")
    (app_dir / "version.json").write_text('{"version":"0.7.1"}', encoding="utf-8")
    data = app_dir / "data"
    data.mkdir()
    (data / "user.txt").write_text("preserved", encoding="utf-8")

    new_files = tmp_path / "new"
    new_files.mkdir()
    (new_files / "VibeOCR.exe").write_bytes(b"new executable")
    (new_files / "version.json").write_text(
        '{"version":"0.7.2"}', encoding="utf-8"
    )
    return app_dir, new_files


def test_backup_failure_preserves_original_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir, new_files = _layout(tmp_path)

    def fail_backup(_source: Path, _destination: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(update_replacer, "_busy_copy2", fail_backup)

    assert not update_replacer.replace_app_files(
        new_files, app_dir, self_exe_names=("VibeOCR.exe",)
    )
    assert (app_dir / "VibeOCR.exe").read_bytes() == b"old executable"
    assert not (app_dir / "VibeOCR.exe.old").exists()
    assert (app_dir / "data" / "user.txt").read_text(encoding="utf-8") == "preserved"


def test_copy_failure_restores_original_entry_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir, new_files = _layout(tmp_path)
    monkeypatch.setattr(update_replacer, "_busy_copy_file", lambda *_args: False)

    assert not update_replacer.replace_app_files(
        new_files, app_dir, self_exe_names=("VibeOCR.exe",)
    )
    assert (app_dir / "VibeOCR.exe").read_bytes() == b"old executable"
    assert not (app_dir / "VibeOCR.exe.old").exists()
    assert (app_dir / "version.json").read_text(encoding="utf-8") == (
        '{"version":"0.7.1"}'
    )
    assert (app_dir / "data" / "user.txt").read_text(encoding="utf-8") == "preserved"


def test_ready_signal_failure_is_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(_self: Path, *_args: object, **_kwargs: object) -> int:
        raise OSError("read only")

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(OSError, match="read only"):
        update_replacer.signal_ready(tmp_path, "updater.ready")


def test_launch_failure_rolls_back_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir, new_files = _layout(tmp_path)
    update_zip = tmp_path / "update.zip"
    update_zip.write_bytes(b"placeholder")
    monkeypatch.setattr(update_replacer, "verify_sha256", lambda _path: True)
    monkeypatch.setattr(
        update_replacer, "verify_update_payload", lambda _path, _entry: True
    )
    monkeypatch.setattr(update_replacer, "signal_ready", lambda *_args: None)
    monkeypatch.setattr(update_replacer, "extract_zip", lambda *_args: new_files)
    monkeypatch.setattr(update_replacer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        update_replacer,
        "launch_app",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("startup failed")
        ),
    )

    result = update_replacer.run_replacement(
        update_zip,
        app_dir,
        self_exe_names=(),
        launch_entry="VibeOCR.exe",
        launch_health_file=tmp_path / "startup.health",
    )

    assert result == 1
    assert (app_dir / "VibeOCR.exe").read_bytes() == b"old executable"
    assert (app_dir / "version.json").read_text(encoding="utf-8") == (
        '{"version":"0.7.1"}'
    )
    assert (app_dir / "data" / "user.txt").read_text(encoding="utf-8") == "preserved"


@pytest.mark.skipif(os.name != "nt", reason="Windows updater executable semantics")
def test_updater_cli_replaces_product_and_preserves_data(tmp_path: Path) -> None:
    app_dir, _new_files = _layout(tmp_path)
    command_exe = Path(os.environ["ComSpec"])
    new_executable = command_exe.read_bytes()
    version = b'{"version":"0.7.2"}'
    records = {
        "VibeOCR.exe": {
            "sha256": hashlib.sha256(new_executable).hexdigest(),
            "size": len(new_executable),
        },
        "version.json": {
            "sha256": hashlib.sha256(version).hexdigest(),
            "size": len(version),
        },
    }
    manifest = json.dumps(
        {"frontend": "classic", "files": records},
        sort_keys=True,
    ).encode()
    update_zip = tmp_path / "VibeOCR-Classic-v0.7.2-win64.zip"
    with zipfile.ZipFile(update_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VibeOCR/VibeOCR.exe", new_executable)
        archive.writestr("VibeOCR/version.json", version)
        archive.writestr("VibeOCR/product-release-manifest.json", manifest)
    Path(f"{update_zip}.sha256").write_text(
        hashlib.sha256(update_zip.read_bytes()).hexdigest(),
        encoding="utf-8",
    )
    health_file = app_dir / "data" / "cache" / "update" / "startup.health"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/updater_main.py",
            "--update",
            str(update_zip),
            "--app-dir",
            str(app_dir),
            "--entry",
            "VibeOCR.exe",
            "--entry-arg",
            "/d",
            "--entry-arg",
            "/c",
            "--entry-arg",
            "echo ready>%VIBEOCR_UPDATE_HEALTH_FILE%",
            "--health-file",
            str(health_file),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (app_dir / "VibeOCR.exe").read_bytes() == new_executable
    assert json.loads((app_dir / "version.json").read_text(encoding="utf-8")) == {
        "version": "0.7.2"
    }
    assert (app_dir / "data" / "user.txt").read_text(encoding="utf-8") == "preserved"
    assert not (app_dir / "data" / "cache" / "update" / "_backup").exists()
