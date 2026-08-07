"""Classic 更新下载源路由契约。"""

import asyncio
from pathlib import Path

import pytest

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
