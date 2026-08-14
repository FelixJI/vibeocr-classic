"""Persistent user choices for the Velopack update prompt."""

from __future__ import annotations

import json
import time
from pathlib import Path

from vibeocr.classic.json_storage import write_json_atomic

REMIND_LATER_SECONDS = 86400


def _read(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    try:
        value = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def load_skip_version(settings_path: Path) -> str:
    value = _read(settings_path).get("skip_version", "")
    return value if isinstance(value, str) else ""


def save_skip_version(version: str, settings_path: Path) -> None:
    data = _read(settings_path)
    data["skip_version"] = version
    write_json_atomic(settings_path, data)


def should_skip_version(version: str, settings_path: Path) -> bool:
    return load_skip_version(settings_path) == version


def load_remind_later(settings_path: Path) -> float:
    value = _read(settings_path).get("remind_later_until", 0.0)
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0


def save_remind_later(until_ts: float, settings_path: Path) -> None:
    data = _read(settings_path)
    data["remind_later_until"] = until_ts
    write_json_atomic(settings_path, data)


def is_remind_later_active(settings_path: Path, *, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return load_remind_later(settings_path) > current


__all__ = [
    "REMIND_LATER_SECONDS",
    "is_remind_later_active",
    "load_remind_later",
    "load_skip_version",
    "save_remind_later",
    "save_skip_version",
    "should_skip_version",
]
