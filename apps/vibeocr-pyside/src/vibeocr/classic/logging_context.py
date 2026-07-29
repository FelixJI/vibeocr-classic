"""Stable JSONL logging primitives shared by the UI and WorkerHost."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

_BASE_FIELDS = {
    "timestamp",
    "level",
    "logger",
    "process",
    "thread",
    "event",
    "frontend",
    "profile",
    "message",
    "exception",
}
LOG_CONTEXT_FIELDS = ("request_id", "task_id", "pipeline", "page", "batch")

_NOISY_LOGGERS = (
    "fontTools",
    "PIL",
    "paddle",
    "paddlex",
    "paddleocr",
    "urllib3",
    "matplotlib",
    "huggingface_hub",
    "filelock",
    "asyncio",
    "qasync",
    "httpcore",
    "httpx",
)


def _timestamp(record: logging.LogRecord) -> str:
    value = datetime.fromtimestamp(record.created, UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonLogFormatter(logging.Formatter):
    """Render one valid JSON object per log record."""

    def __init__(self, *, frontend: str, profile: str) -> None:
        super().__init__()
        self._frontend = frontend
        self._profile = profile

    def format(self, record: logging.LogRecord) -> str:
        worker_exception = getattr(record, "worker_exception", None)
        if worker_exception is not None:
            exception = worker_exception
        elif record.exc_info:
            exception = self.formatException(record.exc_info)
        else:
            exception = record.exc_text

        document: dict[str, Any] = {
            "timestamp": getattr(record, "worker_timestamp", None)
            or _timestamp(record),
            "level": record.levelname,
            "logger": getattr(record, "worker_logger", None) or record.name,
            "process": getattr(record, "worker_process", None) or record.process,
            "thread": getattr(record, "worker_thread", None) or record.threadName,
            "event": getattr(record, "event", "log"),
            "frontend": getattr(record, "worker_frontend", None)
            or getattr(record, "frontend", self._frontend),
            "profile": getattr(record, "worker_profile", None)
            or getattr(record, "profile", self._profile),
            "message": record.getMessage(),
            "exception": exception,
        }
        for field in LOG_CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                document[field] = value
        context = getattr(record, "worker_context", None)
        if context:
            document["context"] = context
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_worker_stderr_logging(
    *,
    frontend: str,
    profile: str,
    stream: TextIO | None = None,
    level: int | str | None = None,
) -> logging.Handler:
    """Configure serving-mode logs as JSONL on stderr only."""
    effective_level = _coerce_level(
        level if level is not None else os.environ.get("VIBEOCR_LOG_LEVEL"),
        logging.INFO,
    )
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonLogFormatter(frontend=frontend, profile=profile))
    handler.setLevel(effective_level)

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.setLevel(effective_level)
    root.addHandler(handler)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(effective_level, logging.WARNING))
    return handler


def _coerce_level(value: object, fallback: int) -> int:
    if isinstance(value, int):
        return value if value > 0 else fallback
    if isinstance(value, str):
        candidate = logging.getLevelNamesMapping().get(value.upper())
        if isinstance(candidate, int):
            return candidate
    return fallback


def forward_worker_output_line(
    logger: logging.Logger,
    line: str,
    *,
    fallback_level: int,
    stream_name: str,
) -> bool:
    """Forward a worker JSONL record at its original severity.

    Returns ``True`` for recognized structured records and ``False`` for the
    safe raw-line fallback.
    """
    text = line.rstrip("\r\n")
    if not text:
        return False
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        document = None

    if not isinstance(document, dict) or "message" not in document:
        logger.log(
            fallback_level,
            "WorkerHost %s: %s",
            stream_name,
            text,
            extra={"event": "worker.output", "worker_stream": stream_name},
        )
        return False

    level = _coerce_level(document.get("level"), fallback_level)
    extra: dict[str, Any] = {
        "event": document.get("event") or "worker.log",
        "worker_timestamp": document.get("timestamp"),
        "worker_logger": document.get("logger"),
        "worker_process": document.get("process"),
        "worker_thread": document.get("thread"),
        "worker_frontend": document.get("frontend"),
        "worker_profile": document.get("profile"),
        "worker_exception": document.get("exception"),
        "worker_stream": stream_name,
    }
    for field in LOG_CONTEXT_FIELDS:
        if document.get(field) is not None:
            extra[field] = document[field]
    context = {
        key: value
        for key, value in document.items()
        if key not in _BASE_FIELDS and key not in LOG_CONTEXT_FIELDS
    }
    if context:
        extra["worker_context"] = context
    logger.log(level, "%s", document["message"], extra=extra)
    return True


def ui_status_extra(**context: Any) -> dict[str, Any]:
    """Build explicit logging ``extra`` values for a Qt status-bar record."""
    return {"ui_status": True, "event": "ui.status", **context}
