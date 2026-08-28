"""Update source routing for Velopack feeds and package downloads."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import threading
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from vibeocr.classic.update_config import GITHUB_API_LATEST, GITHUB_PROXY_PREFIXES

GITHUB_LATEST_DOWNLOAD_BASE = (
    "https://github.com/FelixJI/vibeocr-classic/releases/latest/download/"
)


@dataclass(frozen=True, slots=True)
class UpdateSourceCandidate:
    kind: str
    base_url: str


@dataclass(slots=True)
class MaterializedUpdateSource:
    base_url: str
    cache_dir: Path
    candidate: UpdateSourceCandidate
    version: str
    filename: str
    expected_sha256: str
    expected_size: int
    _server: ThreadingHTTPServer
    _available_bytes: Callable[[Path], int]

    async def download(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Stream the release-bound package unchanged into the local source."""
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        marker = "/latest/download/"
        package_url = (
            self.candidate.base_url.replace(marker, f"/download/v{self.version}/", 1)
            + self.filename
        )
        target = self.cache_dir / self.filename
        partial = self.cache_dir / f".{self.filename}.partial"
        digest = hashlib.sha256()
        downloaded = 0
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, trust_env=True
            ) as client:
                async with client.stream("GET", package_url) as response:
                    response.raise_for_status()
                    raw_length = response.headers.get("content-length")
                    content_length = int(raw_length) if raw_length is not None else None
                    required = content_length or self.expected_size
                    if self._available_bytes(self.cache_dir) < required:
                        raise ValueError(f"目标卷空间不足：需要至少 {required} 字节")
                    if (
                        content_length is not None
                        and content_length != self.expected_size
                    ):
                        raise ValueError("materialized full package size mismatch")
                    with partial.open("wb") as stream:
                        async for chunk in response.aiter_raw():
                            if cancel_event is not None and cancel_event.is_set():
                                raise asyncio.CancelledError
                            stream.write(chunk)
                            digest.update(chunk)
                            downloaded += len(chunk)
                            if progress is not None:
                                progress(min(100, max(1, downloaded * 100 // required)))
            if downloaded != self.expected_size:
                raise ValueError("materialized full package size mismatch")
            if digest.hexdigest().casefold() != self.expected_sha256.casefold():
                raise ValueError("materialized full package SHA-256 mismatch")
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class HttpxFeedMaterializer:
    """Fallback for SDK transports that do not honor standard forward proxies."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        available_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._available_bytes = available_bytes or (
            lambda path: shutil.disk_usage(path).free
        )

    async def materialize(
        self, candidates: tuple[UpdateSourceCandidate, ...]
    ) -> MaterializedUpdateSource:
        failures: list[str] = []
        async with httpx.AsyncClient(follow_redirects=True, trust_env=True) as client:
            for candidate in candidates:
                try:
                    return await self._materialize_candidate(client, candidate)
                except (OSError, ValueError, httpx.HTTPError) as error:
                    failures.append(f"{candidate.kind}: {error}")
        raise RuntimeError("; ".join(failures) or "无法物化 Velopack feed")

    async def _materialize_candidate(
        self, client: httpx.AsyncClient, candidate: UpdateSourceCandidate
    ) -> MaterializedUpdateSource:
        response = await client.get(candidate.base_url + "releases.win.json")
        response.raise_for_status()
        try:
            feed = response.json()
        except ValueError as error:
            raise ValueError("Velopack feed is invalid JSON") from error
        assets = feed.get("Assets") if isinstance(feed, dict) else None
        full: list[tuple[tuple[int, int, int], dict[str, object]]] = []
        for candidate_asset in assets or ():
            if (
                not isinstance(candidate_asset, dict)
                or candidate_asset.get("PackageId") != "VibeOCRClassic"
                or candidate_asset.get("Type") != "Full"
            ):
                continue
            raw_version = candidate_asset.get("Version")
            parts = raw_version.split(".") if isinstance(raw_version, str) else []
            if len(parts) != 3 or not all(part.isdigit() for part in parts):
                raise ValueError("Velopack full package version is invalid")
            full.append((tuple(int(part) for part in parts), candidate_asset))
        if not full:
            raise ValueError("Velopack feed does not contain a full package")
        latest = max(version for version, _asset in full)
        selected = [
            candidate_asset for version, candidate_asset in full if version == latest
        ]
        if len(selected) != 1:
            raise ValueError("Velopack feed has an ambiguous latest full package")
        asset = selected[0]
        version = asset.get("Version")
        filename = asset.get("FileName")
        expected_hash = asset.get("SHA256")
        expected_size = asset.get("Size")
        if (
            not isinstance(version, str)
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(expected_hash, str)
            or not isinstance(expected_size, int)
        ):
            raise ValueError("Velopack full package metadata is invalid")
        marker = "/latest/download/"
        if marker not in candidate.base_url:
            raise ValueError("update candidate is not a latest release URL")
        staging = self._cache_dir.with_name(f".{self._cache_dir.name}.materializing")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        # 该 fallback 只预取当前 full。把本地 feed 同步收窄为 full-only，
        # 避免启用 delta 后本地 UpdateManager 选择一个缓存中不存在的 delta。
        local_feed = dict(feed)
        local_feed["Assets"] = [asset]
        (staging / "releases.win.json").write_text(
            json.dumps(local_feed, separators=(",", ":")),
            encoding="utf-8",
        )
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
        os.replace(staging, self._cache_dir)
        handler = functools.partial(
            SimpleHTTPRequestHandler, directory=str(self._cache_dir)
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return MaterializedUpdateSource(
            f"http://127.0.0.1:{server.server_port}/",
            self._cache_dir,
            candidate,
            version,
            filename,
            expected_hash,
            expected_size,
            server,
            self._available_bytes,
        )


def build_update_source_candidates(
    network_type: str,
) -> tuple[UpdateSourceCandidate, ...]:
    """Return direct/URL-prefix candidates in the existing user-facing order.

    Standard forward proxies are deliberately not rewritten into this list.
    Velopack/HTTP clients consume ``HTTP_PROXY``, ``HTTPS_PROXY`` and
    ``NO_PROXY`` from their process environment for the selected candidate.
    """
    direct = UpdateSourceCandidate("direct", GITHUB_LATEST_DOWNLOAD_BASE)
    prefixed = tuple(
        UpdateSourceCandidate("url-prefix", prefix + GITHUB_LATEST_DOWNLOAD_BASE)
        for prefix in GITHUB_PROXY_PREFIXES
    )
    if network_type == "domestic":
        return (*prefixed, direct)
    return (direct, *prefixed)


async def _github_reachable(timeout: float = 3.0) -> bool:
    """Use GitHub API reachability only to order the complete candidate list."""

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.head(
                GITHUB_API_LATEST,
                headers={"Accept": "application/vnd.github+json"},
            )
        return response.status_code < 500
    except Exception:
        return False


async def resolve_update_source_candidates() -> tuple[UpdateSourceCandidate, ...]:
    """Resolve the established domestic/overseas ordering once per check."""
    network_type = "overseas" if await _github_reachable() else "domestic"
    return build_update_source_candidates(network_type)


__all__ = [
    "GITHUB_LATEST_DOWNLOAD_BASE",
    "HttpxFeedMaterializer",
    "MaterializedUpdateSource",
    "UpdateSourceCandidate",
    "build_update_source_candidates",
    "resolve_update_source_candidates",
]
