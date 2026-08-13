"""Classic 更新下载源路由契约。"""

import asyncio
from pathlib import Path

import pytest

from vibeocr.classic.update_config import GITHUB_DOWNLOAD_BASE
from vibeocr.classic.services import update_service


class _AsyncClientStub:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        return None


@pytest.mark.parametrize(
    ("github_reachable", "expected_network_type"),
    [(True, "international"), (False, "domestic")],
)
def test_download_source_order_uses_github_reachability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_reachable: bool,
    expected_network_type: str,
) -> None:
    """更新下载只依据自身的 GitHub 可达性选择源序。"""
    selected_network_types: list[str] = []

    async def probe() -> bool:
        return github_reachable

    def build_pairs(
        network_type: str,
        _version: str,
        _zip_filename: str,
        _sha_filename: str,
    ) -> list[tuple[str, str]]:
        selected_network_types.append(network_type)
        return [
            (
                "https://download.invalid/update.zip",
                "https://download.invalid/update.sha256",
            )
        ]

    async def download(
        *_args: object, **_kwargs: object
    ) -> update_service.SourceAttempt:
        return update_service.SourceAttempt(True, "")

    monkeypatch.setattr(update_service, "_probe_github_reachable", probe)
    monkeypatch.setattr(update_service, "build_asset_url_pairs", build_pairs)
    monkeypatch.setattr(update_service.httpx, "AsyncClient", _AsyncClientStub)
    monkeypatch.setattr(update_service, "_download_zip_with_sha", download)

    info = update_service.UpdateInfo(
        version="0.7.2",
        download_url="https://github.invalid/update.zip",
        sha256_url="https://github.invalid/update.sha256",
        changelog="",
        zip_filename="update.zip",
        sha256_filename="update.sha256",
    )

    path, reasons = asyncio.run(update_service.download_update(info, tmp_path))

    assert path == tmp_path / "update.zip"
    assert reasons == []
    assert selected_network_types == [expected_network_type]


def test_direct_asset_failure_falls_back_to_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 可达但 release asset 直连失败时仍尝试代理。"""
    attempted_urls: list[str] = []

    async def probe() -> bool:
        return True

    async def download(
        _client: object,
        url: str,
        *_args: object,
        **_kwargs: object,
    ) -> update_service.SourceAttempt:
        attempted_urls.append(url)
        if len(attempted_urls) == 1:
            return update_service.SourceAttempt(
                False, update_service.DOWNLOAD_REASON_HTTP_ERROR
            )
        return update_service.SourceAttempt(True, update_service.DOWNLOAD_REASON_OK)

    monkeypatch.setattr(update_service, "_probe_github_reachable", probe)
    monkeypatch.setattr(update_service.httpx, "AsyncClient", _AsyncClientStub)
    monkeypatch.setattr(update_service, "_download_zip_with_sha", download)

    info = update_service.UpdateInfo(
        version="0.7.2",
        download_url="https://github.invalid/update.zip",
        sha256_url="https://github.invalid/update.sha256",
        changelog="",
        zip_filename="update.zip",
        sha256_filename="update.sha256",
    )

    path, reasons = asyncio.run(update_service.download_update(info, tmp_path))

    direct_url = f"{GITHUB_DOWNLOAD_BASE}/v0.7.2/update.zip"
    assert path == tmp_path / "update.zip"
    assert reasons == []
    assert attempted_urls == [direct_url, f"https://gh-proxy.com/{direct_url}"]


def test_skip_version_and_remind_later_settings_are_effective(tmp_path: Path) -> None:
    """跳过版本与稍后提醒共享设置文件且各自按约定生效。"""
    settings_path = tmp_path / "update_settings.json"

    update_service.save_skip_version("0.7.2", settings_path)
    update_service.save_remind_later(2000.0, settings_path)

    assert update_service.should_skip_version("0.7.2", settings_path)
    assert not update_service.should_skip_version("0.7.3", settings_path)
    assert update_service.is_remind_later_active(settings_path, now=1999.0)
    assert not update_service.is_remind_later_active(settings_path, now=2000.0)

    update_service.save_skip_version("0.7.3", settings_path)
    assert update_service.load_remind_later(settings_path) == 2000.0

    update_service.save_remind_later(3000.0, settings_path)
    assert update_service.load_skip_version(settings_path) == "0.7.3"


def test_update_settings_preserve_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原子替换失败时保留原设置，并清理临时文件。"""
    settings_path = tmp_path / "update_settings.json"
    original = '{"remind_later_until": 2000.0}'
    settings_path.write_text(original, encoding="utf-8")

    def fail_replace(_temporary: Path, _target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        update_service.save_skip_version("0.7.2", settings_path)

    assert settings_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".update_settings.json.*.tmp"))
