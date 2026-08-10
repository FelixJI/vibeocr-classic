"""update_config 模块测试（Classic 发布 URL 构建 + 更新路径）。

覆盖成功路径、失败路径与边界条件。重点验证：
- build_github_asset_urls：overseas [direct, proxy1, proxy2]、domestic [proxy1, proxy2, direct]；
- build_asset_url_pairs：成对 tuple、各 prefix 对应 zip+sha；
- _asset_url：proxy 拼接 vs direct 不加前缀；
- _ordered_download_prefixes：两种网络类型顺序；
- get_update_cache_dir / get_update_settings_path：mkdir 幂等；
- get_update_progress_path：不创建目录。

隔离约定：patch get_install_root 返回 tmp_path，避免污染真实安装根。
"""

import pytest

from vibeocr.classic import update_config
from vibeocr.classic.update_config import (
    GITHUB_DOWNLOAD_BASE,
    GITHUB_PROXY_PREFIXES,
    _asset_url,
    _ordered_download_prefixes,
    build_asset_url_pairs,
    build_github_asset_urls,
    get_data_dir,
    get_update_cache_dir,
    get_update_progress_path,
    get_update_settings_path,
)


@pytest.fixture(autouse=True)
def _stub_install_root(monkeypatch, tmp_path):
    """patch get_install_root 返回 tmp_path，隔离真实安装根。"""
    monkeypatch.setattr(update_config, "get_install_root", lambda: tmp_path)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------


def test_github_proxy_prefixes_order():
    """代理前缀顺序固定（影响 domestic 候选顺序）。"""
    assert GITHUB_PROXY_PREFIXES == (
        "https://gh-proxy.com/",
        "https://ghfast.top/",
    )


def test_github_download_base_value():
    """GITHUB_DOWNLOAD_BASE 形式正确。"""
    assert GITHUB_DOWNLOAD_BASE.endswith("/download")


# ---------------------------------------------------------------------------
# _ordered_download_prefixes
# ---------------------------------------------------------------------------


def test_ordered_prefixes_overseas():
    """overseas 直连优先，代理仍作为失败回退。"""
    prefixes = _ordered_download_prefixes("overseas")
    assert prefixes == (
        GITHUB_DOWNLOAD_BASE,
        "https://gh-proxy.com/",
        "https://ghfast.top/",
    )


def test_ordered_prefixes_domestic():
    """domestic 返回 [proxy1, proxy2, direct]。"""
    prefixes = _ordered_download_prefixes("domestic")
    assert prefixes == (
        "https://gh-proxy.com/",
        "https://ghfast.top/",
        GITHUB_DOWNLOAD_BASE,
    )


def test_ordered_prefixes_domestic_has_proxies_first():
    """domestic 模式代理在前，直连兜底在后。"""
    prefixes = _ordered_download_prefixes("domestic")
    assert prefixes[-1] == GITHUB_DOWNLOAD_BASE  # 直连在最后
    assert len(prefixes) == 3


# ---------------------------------------------------------------------------
# _asset_url
# ---------------------------------------------------------------------------


def test_asset_url_direct_no_prefix():
    """direct prefix 不加前缀。"""
    url = _asset_url(GITHUB_DOWNLOAD_BASE, "1.0.0", "asset.zip")
    assert url == f"{GITHUB_DOWNLOAD_BASE}/v1.0.0/asset.zip"


def test_asset_url_proxy_gh_proxy():
    """gh-proxy 前缀拼接完整 GitHub URL。"""
    url = _asset_url("https://gh-proxy.com/", "1.0.0", "asset.zip")
    assert url == f"https://gh-proxy.com/{GITHUB_DOWNLOAD_BASE}/v1.0.0/asset.zip"


def test_asset_url_proxy_ghfast():
    """ghfast 前缀拼接完整 GitHub URL。"""
    url = _asset_url("https://ghfast.top/", "2.3.4", "pkg.sha256")
    assert url == f"https://ghfast.top/{GITHUB_DOWNLOAD_BASE}/v2.3.4/pkg.sha256"


def test_asset_url_version_prefix_v():
    """URL 中版本号带 v 前缀。"""
    url = _asset_url(GITHUB_DOWNLOAD_BASE, "0.8.0", "x.zip")
    assert "/v0.8.0/" in url


# ---------------------------------------------------------------------------
# build_github_asset_urls
# ---------------------------------------------------------------------------


def test_build_github_asset_urls_overseas():
    """overseas 返回直连优先、代理回退列表。"""
    urls = build_github_asset_urls("overseas", "1.0.0", "asset.zip")
    assert len(urls) == 3
    assert urls[0] == f"{GITHUB_DOWNLOAD_BASE}/v1.0.0/asset.zip"
    assert urls[1].startswith("https://gh-proxy.com/")
    assert urls[2].startswith("https://ghfast.top/")


def test_build_github_asset_urls_domestic():
    """domestic 返回 [proxy1, proxy2, direct] 三元素。"""
    urls = build_github_asset_urls("domestic", "1.0.0", "asset.zip")
    assert len(urls) == 3
    # 代理 URL 以代理前缀开头
    assert urls[0].startswith("https://gh-proxy.com/")
    assert urls[1].startswith("https://ghfast.top/")
    # 直连不含代理前缀
    assert urls[2] == f"{GITHUB_DOWNLOAD_BASE}/v1.0.0/asset.zip"


def test_build_github_asset_urls_returns_list():
    """返回 list 类型。"""
    urls = build_github_asset_urls("overseas", "1.0.0", "x")
    assert isinstance(urls, list)


def test_build_github_asset_urls_domestic_all_distinct():
    """domestic 三个候选 URL 互不相同。"""
    urls = build_github_asset_urls("domestic", "1.0.0", "asset.zip")
    assert len(set(urls)) == 3


# ---------------------------------------------------------------------------
# build_asset_url_pairs
# ---------------------------------------------------------------------------


def test_build_asset_url_pairs_overseas():
    """overseas 返回直连优先、代理回退的三对 URL。"""
    pairs = build_asset_url_pairs("overseas", "1.0.0", "pkg.zip", "pkg.zip.sha256")
    assert len(pairs) == 3
    zip_url, sha_url = pairs[0]
    assert zip_url.endswith("/v1.0.0/pkg.zip")
    assert sha_url.endswith("/v1.0.0/pkg.zip.sha256")


def test_build_asset_url_pairs_domestic():
    """domestic 返回 3 对，每对的 zip 与 sha 共享同一 prefix。"""
    pairs = build_asset_url_pairs("domestic", "1.0.0", "pkg.zip", "pkg.zip.sha256")
    assert len(pairs) == 3
    for zip_url, sha_url in pairs:
        # 同一 prefix 下，zip 与 sha 仅 asset name 不同
        assert zip_url.replace("pkg.zip", "pkg.zip.sha256") == sha_url


def test_build_asset_url_pairs_returns_tuples():
    """返回 tuple 列表。"""
    pairs = build_asset_url_pairs("overseas", "1.0.0", "a", "b")
    assert all(isinstance(p, tuple) and len(p) == 2 for p in pairs)


def test_build_asset_url_pairs_domestic_proxy_pairs():
    """domestic 各对分别对应 proxy1/proxy2/direct。"""
    pairs = build_asset_url_pairs("domestic", "1.0.0", "pkg.zip", "pkg.zip.sha256")
    # 第 1 对走 gh-proxy
    assert pairs[0][0].startswith("https://gh-proxy.com/")
    # 第 2 对走 ghfast
    assert pairs[1][0].startswith("https://ghfast.top/")
    # 第 3 对直连
    assert not pairs[2][0].startswith("https://gh")


# ---------------------------------------------------------------------------
# 路径 helper（patch get_install_root 后落到 tmp_path）
# ---------------------------------------------------------------------------


def test_get_data_dir(tmp_path):
    """get_data_dir 在 install_root/data。"""
    assert get_data_dir() == tmp_path / "data"


def test_get_update_cache_dir_creates_directory(tmp_path):
    """get_update_cache_dir 创建目录并返回路径。"""
    result = get_update_cache_dir()
    assert result == tmp_path / "data" / "cache" / "update"
    assert result.exists()


def test_get_update_cache_dir_idempotent(tmp_path):
    """多次调用安全（mkdir 幂等）。"""
    p1 = get_update_cache_dir()
    p2 = get_update_cache_dir()
    assert p1 == p2
    assert p1.exists()


def test_get_update_settings_path_creates_parent(tmp_path):
    """get_update_settings_path 创建父目录并返回文件路径。"""
    result = get_update_settings_path()
    assert result == tmp_path / "data" / "settings" / "update_settings.json"
    assert result.parent.exists()


def test_get_update_settings_path_idempotent(tmp_path):
    """多次调用安全。"""
    p1 = get_update_settings_path()
    p2 = get_update_settings_path()
    assert p1 == p2


def test_get_update_progress_path_no_directory_creation(tmp_path):
    """get_update_progress_path 不创建目录（仅拼路径）。"""
    result = get_update_progress_path()
    assert result == tmp_path / "data" / "cache" / "update" / "progress.json"
    # 不创建目录（与 cache/settings 不同）
    assert not result.parent.exists()


def test_get_update_progress_path_value():
    """progress 路径与 cache 同级（都在 data/cache/update）。"""
    cache_dir = get_update_cache_dir()
    progress = get_update_progress_path()
    assert progress.parent == cache_dir


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


def test_module_all_exports():
    """__all__ 导出预期符号。"""
    expected = {
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
    }
    assert set(update_config.__all__) == expected
