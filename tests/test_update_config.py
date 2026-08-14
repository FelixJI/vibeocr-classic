from types import SimpleNamespace

import pytest

from vibeocr.classic import update_config
from vibeocr.classic.update_config import (
    GITHUB_PROXY_PREFIXES,
    get_data_dir,
    get_update_cache_dir,
    get_update_settings_path,
)


@pytest.fixture(autouse=True)
def _stub_app_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(
        update_config,
        "get_active_app_paths",
        lambda: SimpleNamespace(data_root=tmp_path / "data"),
    )


def test_github_proxy_prefixes_order() -> None:
    assert GITHUB_PROXY_PREFIXES == (
        "https://gh-proxy.com/",
        "https://ghfast.top/",
    )


def test_update_paths_use_stable_data_root(tmp_path) -> None:
    assert get_data_dir() == tmp_path / "data"
    assert get_update_cache_dir() == tmp_path / "data" / "cache" / "update"
    assert get_update_settings_path() == (
        tmp_path / "data" / "settings" / "update_settings.json"
    )
    assert get_update_cache_dir().is_dir()
    assert get_update_settings_path().parent.is_dir()


def test_module_exports_only_current_update_contract() -> None:
    assert set(update_config.__all__) == {
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
    }
