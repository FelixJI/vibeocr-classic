"""Velopack implementation of the UI-free UpdateCoordinator interface."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from vibeocr.classic.services.update_coordinator import (
    UpdateApplyMode,
    UpdateApplyResult,
    UpdateApplyStatus,
    UpdateCheckResult,
    UpdateCheckStatus,
)
from vibeocr.classic.services.setup_bridge import SetupBridge
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
        setup_bridge: SetupBridge | None = None,
        source_resolver: SourceResolver | None = None,
        migration_ready: Callable[[], bool] | None = None,
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
        self._migration_ready = migration_ready
        self._materializer = materializer
        self._materialized_source: MaterializedUpdateSource | None = None
        self._setup_bridge = setup_bridge
        self._apply_mode = UpdateApplyMode.VELOPACK
        self._manager: _VelopackManager | None = None
        self._update: _VelopackUpdateInfo | None = None

    async def check(self) -> UpdateCheckResult:
        if self._materialized_source is not None:
            self._materialized_source.close()
            self._materialized_source = None
        self._manager = None
        self._update = None
        self._apply_mode = UpdateApplyMode.VELOPACK
        if self._source_resolver is not None:
            self._source_candidates = tuple(await self._source_resolver())
        failures: list[str] = []
        installed_runtime_seen = False
        for candidate in self._source_candidates:
            source = candidate.base_url
            try:
                manager = self._manager_factory(source)
                if await asyncio.to_thread(manager.get_is_portable):
                    return await self._check_setup_bridge()
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
        if not installed_runtime_seen:
            return await self._check_setup_bridge(failures)
        if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
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

    async def _check_setup_bridge(
        self, prior_failures: list[str] | None = None
    ) -> UpdateCheckResult:
        if not self._is_migration_ready():
            return UpdateCheckResult(
                UpdateCheckStatus.FETCH_FAILED,
                detail="稳定数据根迁移尚未完成，已保留 portable 数据并跳过 Setup",
                apply_mode=UpdateApplyMode.SETUP_BRIDGE,
            )
        if self._setup_bridge is None:
            self._setup_bridge = self._build_setup_bridge()
        try:
            release = await self._setup_bridge.check()
        except Exception as exc:
            failures = [*(prior_failures or ()), f"setup: {exc}"]
            return UpdateCheckResult(
                UpdateCheckStatus.FETCH_FAILED,
                detail="; ".join(failures),
                apply_mode=UpdateApplyMode.SETUP_BRIDGE,
            )
        from vibeocr.classic import __version__

        self._apply_mode = UpdateApplyMode.SETUP_BRIDGE
        return UpdateCheckResult(
            UpdateCheckStatus.AVAILABLE,
            current_version=__version__,
            version=release.version,
            release_notes="安装 Velopack 版本以继续接收安全更新。",
            apply_mode=UpdateApplyMode.SETUP_BRIDGE,
        )

    def _build_setup_bridge(self) -> SetupBridge:
        from vibeocr.classic.update_config import get_update_cache_dir

        return SetupBridge(
            source_candidates=self._source_candidates,
            cache_dir=get_update_cache_dir(),
        )

    def _is_migration_ready(self) -> bool:
        if self._migration_ready is not None:
            return self._migration_ready()
        from vibeocr.classic.data_migration import is_stable_data_root_ready

        return is_stable_data_root_ready()

    async def download_and_apply(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> UpdateApplyResult:
        if self._apply_mode is UpdateApplyMode.SETUP_BRIDGE:
            if cancel_event is not None and cancel_event.is_set():
                return UpdateApplyResult(UpdateApplyStatus.CANCELLED)
            try:
                if self._setup_bridge is None:
                    raise RuntimeError("Setup bridge has not checked a release")
                await self._setup_bridge.download_and_launch(progress, cancel_event)
            except asyncio.CancelledError:
                return UpdateApplyResult(UpdateApplyStatus.CANCELLED)
            except Exception as exc:
                return UpdateApplyResult(UpdateApplyStatus.FAILED, str(exc))
            return UpdateApplyResult(UpdateApplyStatus.APPLY_STARTED)
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
