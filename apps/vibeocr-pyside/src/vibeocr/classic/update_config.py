"""Classic-owned release URLs and portable updater paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeocr.classic.app_paths import get_install_root

if TYPE_CHECKING:
    from pathlib import Path

GITHUB_OWNER = "FelixJI"
GITHUB_REPO = "vibeocr-classic"
GITHUB_REPO_BASE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_BASE = f"{GITHUB_REPO_BASE}/releases"
GITHUB_DOWNLOAD_BASE = f"{GITHUB_RELEASES_BASE}/download"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_PROXY_PREFIXES = ("https://gh-proxy.com/", "https://ghfast.top/")

# The existing Gitee mirror remains a source-code link only. It is never used
# for update discovery or downloads.
GITEE_REPO_BASE = "https://gitee.com/felixjii/vibeocr"


def _ordered_download_prefixes(network_type: str) -> tuple[str, ...]:
    if network_type == "domestic":
        return (*GITHUB_PROXY_PREFIXES, GITHUB_DOWNLOAD_BASE)
    return (GITHUB_DOWNLOAD_BASE, *GITHUB_PROXY_PREFIXES)


def _asset_url(prefix: str, version: str, asset_name: str) -> str:
    github_url = f"{GITHUB_DOWNLOAD_BASE}/v{version}/{asset_name}"
    if prefix in GITHUB_PROXY_PREFIXES:
        return prefix + github_url
    return github_url


def build_github_asset_urls(
    network_type: str,
    version: str,
    asset_name: str,
) -> list[str]:
    return [
        _asset_url(prefix, version, asset_name)
        for prefix in _ordered_download_prefixes(network_type)
    ]


def build_asset_url_pairs(
    network_type: str,
    version: str,
    zip_name: str,
    sha_name: str,
) -> list[tuple[str, str]]:
    return [
        (
            _asset_url(prefix, version, zip_name),
            _asset_url(prefix, version, sha_name),
        )
        for prefix in _ordered_download_prefixes(network_type)
    ]


def get_data_dir() -> Path:
    return get_install_root() / "data"


def get_update_cache_dir() -> Path:
    path = get_data_dir() / "cache" / "update"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_update_settings_path() -> Path:
    directory = get_data_dir() / "settings"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "update_settings.json"


def get_update_progress_path() -> Path:
    return get_data_dir() / "cache" / "update" / "progress.json"


__all__ = [
    "GITEE_REPO_BASE",
    "GITHUB_API_LATEST",
    "GITHUB_DOWNLOAD_BASE",
    "GITHUB_OWNER",
    "GITHUB_PROXY_PREFIXES",
    "GITHUB_RELEASES_BASE",
    "GITHUB_REPO",
    "GITHUB_REPO_BASE",
    "build_asset_url_pairs",
    "build_github_asset_urls",
    "get_data_dir",
    "get_update_cache_dir",
    "get_update_progress_path",
    "get_update_settings_path",
]
