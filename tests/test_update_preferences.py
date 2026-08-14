from __future__ import annotations

from pathlib import Path

import pytest

from vibeocr.classic.services import update_preferences


def test_skip_version_and_remind_later_share_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "update_settings.json"

    update_preferences.save_skip_version("0.7.2", settings_path)
    update_preferences.save_remind_later(2000.0, settings_path)

    assert update_preferences.should_skip_version("0.7.2", settings_path)
    assert not update_preferences.should_skip_version("0.7.3", settings_path)
    assert update_preferences.is_remind_later_active(settings_path, now=1999.0)
    assert not update_preferences.is_remind_later_active(settings_path, now=2000.0)

    update_preferences.save_skip_version("0.7.3", settings_path)
    assert update_preferences.load_remind_later(settings_path) == 2000.0
    update_preferences.save_remind_later(3000.0, settings_path)
    assert update_preferences.load_skip_version(settings_path) == "0.7.3"


def test_update_settings_preserve_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_path = tmp_path / "update_settings.json"
    original = '{"remind_later_until": 2000.0}'
    settings_path.write_text(original, encoding="utf-8")

    def fail_replace(_temporary: Path, _target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        update_preferences.save_skip_version("0.7.2", settings_path)

    assert settings_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".update_settings.json.*.tmp"))
