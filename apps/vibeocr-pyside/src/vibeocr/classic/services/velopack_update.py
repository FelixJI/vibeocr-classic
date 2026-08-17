"""Velopack implementation of the UI-free UpdateCoordinator interface."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from vibeocr.classic.services.update_coordinator import (
    UpdateApplyResult,
    UpdateApplyStatus,
    UpdateCheckResult,
    UpdateCheckStatus,
)
from vibeocr.classic.services.update_transport import UpdateSourceCandidate
from vibeocr.classic.services.update_transport import (
    HttpxFeedMaterializer,
    MaterializedUpdateSource,
)


class _VelopackAsset(Protocol):
    Version: str
    NotesMarkdown: str


class _VelopackUpdateInfo(Protocol):
    TargetFullRelease: _VelopackAsset


class _VelopackManager(Protocol):
    def get_is_portable(self) -> bool: ...

    def get_current_version(self) -> str: ...

    def check_for_updates(self) -> _VelopackUpdateInfo | None: ...

    def download_updates(
        self,
        update_info: _VelopackUpdateInfo,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None: ...

    def wait_exit_then_apply_updates(
        self,
        update: _VelopackUpdateInfo,
        *,
        silent: bool = False,
        restart: bool = True,
        restart_args: list[str] | None = None,
    ) -> None: ...


ManagerFactory = Callable[[str], _VelopackManager]
SourceResolver = Callable[[], Awaitable[tuple[UpdateSourceCandidate, ...]]]


class _FeedMaterializer(Protocol):
    async def materialize(
        self, candidates: tuple[UpdateSourceCandidate, ...]
    ) -> MaterializedUpdateSource: ...


def _default_manager_factory(source: str) -> _VelopackManager:
    velopack = importlib.import_module("velopack")

    options = velopack.UpdateOptions(False, -1, "win")
    return velopack.UpdateManager(velopack.HttpSource(source), options)


class _DownloadCancelled(RuntimeError):
    pass


class VelopackUpdateCoordinator:
    """Own Velopack version selection, verification, staging and apply."""

    def __init__(
        self,
        *,
        source_candidates: Iterable[UpdateSourceCandidate | str] = (),
        manager_factory: ManagerFactory = _default_manager_factory,
        source_resolver: SourceResolver | None = None,
        materializer: _FeedMaterializer | None = None,
    ) -> None:
        self._source_candidates = tuple(
            candidate
            if isinstance(candidate, UpdateSourceCandidate)
            else UpdateSourceCandidate("direct", candidate)
            for candidate in source_candidates
        )
        self._manager_factory = manager_factory
        self._source_resolver = source_resolver
        self._materializer = materializer
        self._materialized_source: MaterializedUpdateSource | None = None
        self._manager: _VelopackManager | None = None
        self._update: _VelopackUpdateInfo | None = None

    async def check(self) -> UpdateCheckResult:
        if self._materialized_source is not None:
            self._materialized_source.close()
            self._materialized_source = None
        self._manager = None
        self._update = None
        if self._source_resolver is not None:
            self._source_candidates = tuple(await self._source_resolver())
        failures: list[str] = []
        installed_runtime_seen = False
        for candidate in self._source_candidates:
            source = candidate.base_url
            try:
                manager = self._manager_factory(source)
                # Portable 与安装模式共用 Velopack check/download/apply：
                # NUPKG/feed 是机器更新资产，Portable 根布局（state 位于
                # 产品根下）由 Velopack apply 保留，不再硬拒绝。
                current = await asyncio.to_thread(manager.get_current_version)
            except Exception as exc:
                failures.append(f"{source}: {exc}")
                continue
            installed_runtime_seen = True
            try:
                update = await asyncio.to_thread(manager.check_for_updates)
            except Exception as exc:
                failures.append(f"{source}: {exc}")
                continue
            self._manager = manager
            self._update = update
            if update is None:
                return UpdateCheckResult(
                    UpdateCheckStatus.LATEST,
                    current_version=current,
                )
            asset = update.TargetFullRelease
            return UpdateCheckResult(
                UpdateCheckStatus.AVAILABLE,
                current_version=current,
                version=asset.Version,
                release_notes=asset.NotesMarkdown or "",
            )
        if installed_runtime_seen and (
            os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        ):
            fallback = await self._check_materialized_source(failures)
            if fallback is not None:
                return fallback
        return UpdateCheckResult(
            UpdateCheckStatus.FETCH_FAILED,
            detail="; ".join(failures) or "没有可用的更新源",
        )

    async def _check_materialized_source(
        self, failures: list[str]
    ) -> UpdateCheckResult | None:
        if self._materializer is None:
            from vibeocr.classic.update_config import get_update_cache_dir

            self._materializer = HttpxFeedMaterializer(
                get_update_cache_dir() / "velopack-source"
            )
        try:
            source = await self._materializer.materialize(self._source_candidates)
            manager = self._manager_factory(source.base_url)
            current = await asyncio.to_thread(manager.get_current_version)
            update = await asyncio.to_thread(manager.check_for_updates)
        except Exception as exc:
            failures.append(f"forward-proxy fallback: {exc}")
            return None
        self._materialized_source = source
        self._manager = manager
        self._update = update
        if update is None:
            return UpdateCheckResult(UpdateCheckStatus.LATEST, current_version=current)
        asset = update.TargetFullRelease
        return UpdateCheckResult(
            UpdateCheckStatus.AVAILABLE,
            current_version=current,
            version=asset.Version,
            release_notes=asset.NotesMarkdown or "",
        )

    async def download_and_apply(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> UpdateApplyResult:
        manager = self._manager
        update = self._update
        if manager is None or update is None:
            return UpdateApplyResult(
                UpdateApplyStatus.FAILED,
                "没有已检查的 Velopack 更新",
            )
        if cancel_event is not None and cancel_event.is_set():
            return UpdateApplyResult(UpdateApplyStatus.CANCELLED)

        def report(value: int) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise _DownloadCancelled("用户取消了更新下载")
            if progress is not None:
                progress(value)

        try:
            if self._materialized_source is not None:
                await self._materialized_source.download(progress, cancel_event)
            await asyncio.to_thread(manager.download_updates, update, report)
            if cancel_event is not None and cancel_event.is_set():
                return UpdateApplyResult(UpdateApplyStatus.CANCELLED)
            await asyncio.to_thread(
                manager.wait_exit_then_apply_updates,
                update,
                silent=False,
                restart=True,
                restart_args=None,
            )
        except (_DownloadCancelled, asyncio.CancelledError):
            return UpdateApplyResult(UpdateApplyStatus.CANCELLED)
        except Exception as exc:
            return UpdateApplyResult(UpdateApplyStatus.FAILED, str(exc))
        return UpdateApplyResult(UpdateApplyStatus.APPLY_STARTED)


__all__ = ["VelopackUpdateCoordinator"]
