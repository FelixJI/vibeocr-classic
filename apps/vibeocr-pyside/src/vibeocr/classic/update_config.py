"""Classic-owned release URLs and Velopack update state paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeocr.classic.app_paths import get_active_app_paths

if TYPE_CHECKING:
    from pathlib import Path

GITHUB_OWNER = "FelixJI"
GITHUB_REPO = "vibeocr-classic"
GITHUB_REPO_BASE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_BASE = f"{GITHUB_REPO_BASE}/releases"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_PROXY_PREFIXES = ("https://gh-proxy.com/", "https://ghfast.top/")
GITEE_REPO_BASE = "https://gitee.com/felixjii/vibeocr"


def get_data_dir() -> Path:
    return get_active_app_paths().data_root


def get_update_cache_dir() -> Path:
    path = get_data_dir() / "cache" / "update"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_update_settings_path() -> Path:
    directory = get_data_dir() / "settings"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "update_settings.json"


__all__ = [
    "GITEE_REPO_BASE",
    "GITHUB_API_LATEST",
    "GITHUB_OWNER",
    "GITHUB_PROXY_PREFIXES",
    "GITHUB_RELEASES_BASE",
    "GITHUB_REPO",
    "GITHUB_REPO_BASE",
    "get_data_dir",
    "get_update_cache_dir",
    "get_update_settings_path",
]
