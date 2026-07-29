from __future__ import annotations

from unittest.mock import MagicMock


def test_settings_runtime_persists_then_applies(monkeypatch) -> None:
    from vibeocr.classic.pyside import settings_runtime

    config = MagicMock()
    config.get_log_level.return_value = "WARNING"
    config.set_log_level.return_value = True
    monkeypatch.setattr(settings_runtime, "_config_manager", lambda: config)
    apply = MagicMock()
    monkeypatch.setattr(settings_runtime, "_apply_log_level", apply)

    assert settings_runtime.get_log_level() == "WARNING"
    assert settings_runtime.set_log_level("DEBUG") is True
    config.set_log_level.assert_called_once_with("DEBUG")
    apply.assert_called_once_with("DEBUG")


def test_settings_runtime_does_not_apply_when_persistence_fails(monkeypatch) -> None:
    from vibeocr.classic.pyside import settings_runtime

    config = MagicMock()
    config.set_log_level.return_value = False
    monkeypatch.setattr(settings_runtime, "_config_manager", lambda: config)
    apply = MagicMock()
    monkeypatch.setattr(settings_runtime, "_apply_log_level", apply)

    assert settings_runtime.set_log_level("INFO") is False
    apply.assert_not_called()
