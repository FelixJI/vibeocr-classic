from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vibeocr.classic.app_paths import AppPaths
from vibeocr.classic.services import log_service
from vibeocr.classic.services.log_service import setup_logging


def _portable_paths(state_root: Path) -> AppPaths:
    return AppPaths(
        install_root=state_root.parent,
        state_root=state_root,
        data_root=state_root / "data",
        runtime_root=state_root / "runtime",
        model_cache_root=state_root / "models",
        output_root=state_root / "output",
        config_file=state_root / "config" / "app_settings.json",
    )


def test_application_log_is_written_under_state_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    state_root = tmp_path / "state"
    monkeypatch.setattr(
        log_service,
        "get_active_app_paths",
        lambda: _portable_paths(state_root),
    )

    try:
        setup_logging()
        logging.getLogger("vibeocr.test").warning("durable startup evidence")
        for handler in root_logger.handlers:
            handler.flush()

        log_path = state_root / "logs" / "vibeocr.log"
        assert "durable startup evidence" in log_path.read_text(encoding="utf-8")
        assert not (tmp_path / "logs").exists()
    finally:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            if handler not in original_handlers:
                handler.close()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)
