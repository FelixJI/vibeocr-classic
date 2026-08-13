"""Portable-to-installed bridge using release-bound Velopack Setup assets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from vibeocr.classic.services.update_transport import UpdateSourceCandidate

SETUP_NAME = "VibeOCRClassic-win-Setup.exe"
FEED_NAME = "releases.win.json"


@dataclass(frozen=True, slots=True)
class SetupBridgeRelease:
    version: str
    source: UpdateSourceCandidate


def _release_asset_url(
    candidate: UpdateSourceCandidate, version: str, name: str
) -> str:
    marker = "/latest/download/"
    if marker not in candidate.base_url:
        raise ValueError("update candidate must end in a latest/download release URL")
    release_base = candidate.base_url.replace(marker, f"/download/v{version}/", 1)
    return release_base + name


def _parse_release(feed: bytes, source: UpdateSourceCandidate) -> SetupBridgeRelease:
    try:
        document = json.loads(feed)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Velopack feed is invalid JSON") from error
    assets = document.get("Assets") if isinstance(document, dict) else None
    if not isinstance(assets, list):
        raise ValueError("Velopack feed has no Assets list")
    versions = {
        asset.get("Version")
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("PackageId") == "VibeOCRClassic"
        and asset.get("Type") == "Full"
        and isinstance(asset.get("Version"), str)
    }
    if len(versions) != 1:
        raise ValueError("Velopack feed must select one VibeOCRClassic full release")
    version = versions.pop()
    if not isinstance(version, str):
        raise ValueError("Velopack feed release version is invalid")
    return SetupBridgeRelease(version, source)


def _parse_checksum(payload: bytes) -> str:
    try:
        fields = payload.decode("utf-8").strip().split()
    except UnicodeDecodeError as error:
        raise ValueError("Setup checksum sidecar is not UTF-8") from error
    if len(fields) != 2 or fields[1].lstrip("*") != SETUP_NAME:
        raise ValueError("Setup checksum sidecar does not name the Setup asset")
    digest = fields[0].lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("Setup checksum sidecar has an invalid SHA-256")
    return digest


class SetupBridge:
    """Download, verify and launch Setup without mutating the portable source."""

    def __init__(
        self,
        *,
        source_candidates: Iterable[UpdateSourceCandidate],
        cache_dir: Path,
        launcher: Callable[[Path], None] | None = None,
    ) -> None:
        self._source_candidates = tuple(source_candidates)
        self._cache_dir = cache_dir
        self._launcher = launcher or self._launch
        self._release: SetupBridgeRelease | None = None

    @staticmethod
    def _launch(path: Path) -> None:
        subprocess.Popen([os.fspath(path)], close_fds=True)

    async def check(self) -> SetupBridgeRelease:
        failures: list[str] = []
        async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
            for source in self._source_candidates:
                try:
                    response = await client.get(source.base_url + FEED_NAME)
                    response.raise_for_status()
                    release = _parse_release(response.content, source)
                except (httpx.HTTPError, ValueError) as error:
                    failures.append(f"{source.kind}: {error}")
                    continue
                self._release = release
                return release
        raise RuntimeError("; ".join(failures) or "没有可用的 Setup 更新源")

    async def download_and_launch(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        release = self._release
        if release is None:
            raise RuntimeError("Setup bridge has not checked a release")
        setup_url = _release_asset_url(release.source, release.version, SETUP_NAME)
        checksum_url = setup_url + ".sha256"
        async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
            checksum_response = await client.get(checksum_url)
            checksum_response.raise_for_status()
            expected = _parse_checksum(checksum_response.content)
            async with client.stream("GET", setup_url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", "0"))
                if total <= 0:
                    raise ValueError("Setup response has no valid Content-Length")
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                target = self._cache_dir / SETUP_NAME
                partial = self._cache_dir / f".{SETUP_NAME}.partial"
                digest = hashlib.sha256()
                downloaded = 0
                try:
                    with partial.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            if cancel_event is not None and cancel_event.is_set():
                                raise asyncio.CancelledError
                            stream.write(chunk)
                            digest.update(chunk)
                            downloaded += len(chunk)
                            if progress is not None:
                                progress(
                                    min(100, downloaded * 100 // total) if total else 0
                                )
                    if digest.hexdigest() != expected:
                        raise ValueError("Setup SHA-256 mismatch")
                    if downloaded != total:
                        raise ValueError("Setup size does not match Content-Length")
                    os.replace(partial, target)
                finally:
                    partial.unlink(missing_ok=True)
        self._launcher(target)


__all__ = ["FEED_NAME", "SETUP_NAME", "SetupBridge", "SetupBridgeRelease"]
