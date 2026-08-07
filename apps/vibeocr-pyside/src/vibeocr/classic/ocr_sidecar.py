"""Classic-owned OCR resume state for incrementally persisted PDF pages.

Version 1 sidecars remain under
``<product_root>/data/backend/ocr_sessions/<path-slug>.json`` so existing
portable installations can resume without moving or rewriting local state.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from vibeocr.classic.app_paths import get_install_root

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
_SIDECAR_SUBDIR = "ocr_sessions"


def compute_fingerprint(file_path: str) -> str:
    """Return the diagnostic ``size:mtime_ns`` fingerprint without reading content."""
    stat = Path(file_path).stat()
    return f"{stat.st_size}:{int(stat.st_mtime_ns)}"


def _sessions_dir() -> Path:
    """Keep the version 1 storage directory compatible with existing installs."""
    return get_install_root().resolve() / "data" / "backend" / _SIDECAR_SUBDIR


def _path_slug(file_path: str) -> str:
    """Use the normalized absolute path as the stable session identity."""
    absolute_path = str(Path(file_path).resolve())
    return hashlib.md5(absolute_path.encode("utf-8")).hexdigest()


def sidecar_path(file_path: str) -> Path:
    return _sessions_dir() / f"{_path_slug(file_path)}.json"


def _growth_ok(data: dict, file_path: str) -> bool:
    """Accept incremental growth and reject replacement, shrink, or time rollback."""
    original_size = data.get("original_size")
    original_mtime = data.get("original_mtime_ns")
    if original_size is None or original_mtime is None:
        return False
    try:
        stat = Path(file_path).stat()
    except OSError:
        return False
    return stat.st_size >= int(original_size) and int(stat.st_mtime_ns) >= int(
        original_mtime
    )


def load_sidecar(file_path: str) -> dict | None:
    """Return a valid incomplete/completed version 1 sidecar, otherwise ``None``."""
    try:
        path = sidecar_path(file_path)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != SIDECAR_VERSION:
            return None
        if not _growth_ok(data, file_path):
            return None
        return data
    except Exception as error:
        logger.debug("sidecar 读取失败（忽略）: %s", error)
        return None


def save_sidecar(file_path: str, data: dict) -> bool:
    """Atomically persist one sidecar with a sibling temporary file."""
    path = sidecar_path(file_path)
    temporary = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return True
    except Exception as error:
        logger.warning("sidecar 写入失败（忽略，不阻断 OCR）: %s", error)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _new_sidecar(file_path: str) -> dict:
    path = Path(file_path).resolve()
    stat = path.stat()
    return {
        "version": SIDECAR_VERSION,
        "file_path": str(path),
        "fingerprint": compute_fingerprint(file_path),
        "original_size": stat.st_size,
        "original_mtime_ns": int(stat.st_mtime_ns),
        "completed": False,
        "pages": {},
    }


def mark_pages_saved(
    file_path: str, page_indices: list[int], angles: dict[int, int]
) -> bool:
    """Merge newly persisted pages while retaining the original growth baseline."""
    data = load_sidecar(file_path) or _new_sidecar(file_path)
    for index in page_indices:
        data["pages"][str(index)] = {
            "has_text_layer": True,
            "ocr_preproc_angle": int(angles.get(index, 0)),
        }
    data["completed"] = False
    return save_sidecar(file_path, data)


def mark_completed(file_path: str) -> bool:
    """Mark completion without losing pages when growth validation has failed."""
    data = load_sidecar(file_path)
    if data is None:
        path = sidecar_path(file_path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") != SIDECAR_VERSION:
                    data = _new_sidecar(file_path)
            except Exception as error:
                logger.debug("mark_completed 原始读取失败，回退新建: %s", error)
                data = _new_sidecar(file_path)
        else:
            data = _new_sidecar(file_path)
    data["completed"] = True
    return save_sidecar(file_path, data)


def refresh_baseline(file_path: str) -> bool:
    """Refresh the growth baseline after a full PDF rewrite or compression."""
    path = sidecar_path(file_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stat = Path(file_path).stat()
        data["original_size"] = stat.st_size
        data["original_mtime_ns"] = int(stat.st_mtime_ns)
        data["fingerprint"] = compute_fingerprint(file_path)
        return save_sidecar(file_path, data)
    except Exception as error:
        logger.debug("sidecar refresh_baseline 失败（忽略）: %s", error)
        return False


def restore_pending_pages(file_path: str) -> dict[int, int] | None:
    """Return persisted page angles for an incomplete valid session."""
    data = load_sidecar(file_path)
    if data is None or data.get("completed"):
        return None
    return {
        int(index): value.get("ocr_preproc_angle", 0)
        for index, value in data.get("pages", {}).items()
    }


__all__ = [
    "SIDECAR_VERSION",
    "compute_fingerprint",
    "load_sidecar",
    "mark_completed",
    "mark_pages_saved",
    "refresh_baseline",
    "restore_pending_pages",
    "save_sidecar",
    "sidecar_path",
]
