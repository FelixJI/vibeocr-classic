from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vibeocr.classic.services import log_service
from vibeocr.classic.services.log_service import setup_logging


def test_application_log_is_written_under_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    monkeypatch.setattr(log_service, "get_install_root", lambda: tmp_path)

    try:
        setup_logging()
        logging.getLogger("vibeocr.test").warning("durable startup evidence")
        for handler in root_logger.handlers:
            handler.flush()

        log_path = tmp_path / "data" / "logs" / "vibeocr.log"
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
