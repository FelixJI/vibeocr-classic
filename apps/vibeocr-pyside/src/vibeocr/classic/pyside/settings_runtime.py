"""Settings-page runtime bridge for the PySide shell.

UI modules depend on this platform boundary instead of importing manager and
service implementations directly.
"""

from __future__ import annotations


def _config_manager():
    """Resolve the config singleton lazily to keep the shell import lightweight."""
    from vibeocr.classic.managers.config_manager import ConfigManager

    return ConfigManager.instance()


def _apply_log_level(level: str) -> None:
    """Resolve the logging service only when the user changes the setting."""
    from vibeocr.classic.services.log_service import apply_log_level

    apply_log_level(level)


def get_log_level() -> str:
    """Return the persisted application log level."""
    try:
        return _config_manager().get_log_level()
    except RuntimeError:
        # Isolated settings-page construction (tests/embedding) can happen
        # before MainWindow initializes the application configuration root.
        return "INFO"


def get_default_ocr_engine() -> str | None:
    """全局默认 OCR 引擎；未知旧值/未初始化返回 None（请求省略 engine）。"""

    try:
        engine_id, _requires_selection = _config_manager().get_ocr_engine_selection()
    except RuntimeError:
        return None
    return engine_id


def set_log_level(level: str) -> bool:
    """Persist and immediately apply an application log level."""
    try:
        config = _config_manager()
    except RuntimeError:
        return False
    if not config.set_log_level(level):
        return False
    _apply_log_level(level)
    return True


__all__ = ["get_log_level", "set_log_level"]
