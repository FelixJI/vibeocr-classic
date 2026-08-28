from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from vibeocr.classic.services.update_coordinator import (
    UpdateApplyStatus,
    UpdateCheckStatus,
)
from vibeocr.classic.services.velopack_update import (
    VelopackUpdateCoordinator,
    _default_manager_factory,
)
from vibeocr.classic.services.update_transport import (
    build_update_source_candidates,
    resolve_update_source_candidates,
)
from vibeocr.classic.runtime_maintenance import ProductMaintenanceCoordinator


@dataclass
class _Asset:
    Version: str = "0.11.0"
    NotesMarkdown: str = "- stable data root"


@dataclass
class _Info:
    TargetFullRelease: _Asset


class _Manager:
    def __init__(self, *, portable: bool = False) -> None:
        self.portable = portable
        self.downloaded = False
        self.apply_started = False

    def get_is_portable(self) -> bool:
        return self.portable

    def get_current_version(self) -> str:
        return "0.10.4"

    def check_for_updates(self):
        return _Info(_Asset())

    def download_updates(self, _info, progress_callback=None) -> None:
        if progress_callback is not None:
            progress_callback(25)
            progress_callback(100)
        self.downloaded = True

    def wait_exit_then_apply_updates(
        self, _info, *, silent=False, restart=True, restart_args=None
    ) -> None:
        assert silent is False
        assert restart is True
        assert restart_args is None
        self.apply_started = True


def _simulate_process_exit(coordinator: VelopackUpdateCoordinator) -> None:
    lease = coordinator._apply_lease
    assert lease is not None
    lease.release()
    coordinator._apply_lease = None


def test_default_manager_enables_only_one_delta(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOptions:
        def __init__(self, allow_downgrade, maximum_deltas, channel) -> None:
            captured["options"] = (allow_downgrade, maximum_deltas, channel)

    class FakeModule:
        UpdateOptions = FakeOptions

        @staticmethod
        def HttpSource(source):
            return source

        @staticmethod
        def UpdateManager(source, options):
            captured["manager"] = (source, options)
            return object()

    monkeypatch.setattr(
        "vibeocr.classic.services.velopack_update.importlib.import_module",
        lambda _name: FakeModule,
    )

    _default_manager_factory("https://updates.invalid/")

    assert captured["options"] == (False, 1, "win")


def test_installed_version_reads_local_velopack_state_without_checking_feed():
    manager = _Manager(portable=True)
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("http://127.0.0.1:9/",),
        manager_factory=lambda _source: manager,
    )

    assert asyncio.run(coordinator.installed_version()) == "0.10.4"
    assert manager.downloaded is False
    assert manager.apply_started is False


def test_coordinator_exposes_available_update_and_starts_apply(tmp_path):
    manager = _Manager()
    observed: list[int] = []
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
        maintenance_coordinator=ProductMaintenanceCoordinator(
            tmp_path / "state/locks/product-maintenance.lock"
        ),
    )

    checked = asyncio.run(coordinator.check())
    applied = asyncio.run(coordinator.download_and_apply(observed.append))

    assert checked.status is UpdateCheckStatus.AVAILABLE
    assert checked.current_version == "0.10.4"
    assert checked.version == "0.11.0"
    assert checked.release_notes == "- stable data root"
    assert applied.status is UpdateApplyStatus.APPLY_STARTED
    assert manager.downloaded is True
    assert manager.apply_started is True
    assert observed == [25, 100]
    _simulate_process_exit(coordinator)


def test_portable_coordinator_shares_velopack_update_flow(tmp_path):
    """Portable 不再硬拒绝：check/download/apply 与安装模式共用 Velopack 流程。"""
    manager = _Manager(portable=True)
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
        maintenance_coordinator=ProductMaintenanceCoordinator(
            tmp_path / "state/locks/product-maintenance.lock"
        ),
    )

    checked = asyncio.run(coordinator.check())
    applied = asyncio.run(coordinator.download_and_apply())

    assert checked.status is UpdateCheckStatus.AVAILABLE
    assert applied.status is UpdateApplyStatus.APPLY_STARTED
    assert manager.downloaded is True
    assert manager.apply_started is True
    _simulate_process_exit(coordinator)


def test_installed_forward_proxy_falls_back_to_materialized_local_feed(
    monkeypatch,
    tmp_path,
):
    class FailingRemoteManager(_Manager):
        def check_for_updates(self):
            raise RuntimeError("SDK transport ignored HTTPS_PROXY")

    class LocalSource:
        base_url = "http://127.0.0.1:43123/"
        downloaded = False

        async def download(self, progress=None, cancel_event=None):
            self.downloaded = True
            if progress is not None:
                progress(75)

        def close(self):
            pass

    local_source = LocalSource()

    class Materializer:
        candidates = ()

        async def materialize(self, candidates):
            self.candidates = candidates
            return local_source

    materializer = Materializer()
    local_manager = _Manager()

    def manager_factory(source: str):
        if source.startswith("http://127.0.0.1:"):
            return local_manager
        return FailingRemoteManager()

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=manager_factory,
        materializer=materializer,
        maintenance_coordinator=ProductMaintenanceCoordinator(
            tmp_path / "state/locks/product-maintenance.lock"
        ),
    )

    checked = asyncio.run(coordinator.check())
    observed: list[int] = []
    applied = asyncio.run(coordinator.download_and_apply(observed.append))

    assert checked.status is UpdateCheckStatus.AVAILABLE
    assert materializer.candidates[0].base_url == "https://updates.invalid/"
    assert applied.status is UpdateApplyStatus.APPLY_STARTED
    assert local_source.downloaded is True
    assert 75 in observed
    assert local_manager.downloaded is True
    assert local_manager.apply_started is True
    _simulate_process_exit(coordinator)


def test_materialized_fallback_cancellation_does_not_call_velopack_download(
    monkeypatch,
):
    class FailingRemoteManager(_Manager):
        def check_for_updates(self):
            raise RuntimeError("SDK transport ignored HTTPS_PROXY")

    class LocalSource:
        base_url = "http://127.0.0.1:43123/"

        async def download(self, progress=None, cancel_event=None):
            raise asyncio.CancelledError

        def close(self):
            pass

    class Materializer:
        async def materialize(self, _candidates):
            return LocalSource()

    local_manager = _Manager()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda source: (
            local_manager
            if source.startswith("http://127.0.0.1:")
            else FailingRemoteManager()
        ),
        materializer=Materializer(),
    )
    asyncio.run(coordinator.check())

    applied = asyncio.run(coordinator.download_and_apply())

    assert applied.status is UpdateApplyStatus.CANCELLED
    assert local_manager.downloaded is False
    assert local_manager.apply_started is False


def test_coordinator_cancellation_does_not_start_apply():
    manager = _Manager()
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
    )
    asyncio.run(coordinator.check())
    cancelled = asyncio.Event()
    cancelled.set()

    result = asyncio.run(coordinator.download_and_apply(cancel_event=cancelled))

    assert result.status is UpdateApplyStatus.CANCELLED
    assert manager.downloaded is False
    assert manager.apply_started is False


def test_velopack_apply_cancels_runtime_and_waits_for_terminal(tmp_path):
    manager = _Manager()
    maintenance = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    cancelled = threading.Event()
    terminal = threading.Event()
    runtime = maintenance.begin_runtime_maintenance(
        cancel=cancelled.set,
        wait_terminal=terminal.wait,
    )

    def finish_runtime():
        assert cancelled.wait(2)
        runtime.release()
        terminal.set()

    thread = threading.Thread(target=finish_runtime)
    thread.start()
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
        maintenance_coordinator=maintenance,
    )
    asyncio.run(coordinator.check())

    result = asyncio.run(coordinator.download_and_apply())
    thread.join(timeout=2)

    assert result.status is UpdateApplyStatus.APPLY_STARTED
    assert cancelled.is_set()
    assert manager.apply_started is True
    assert maintenance.owner.value == "AppUpdate"
    _simulate_process_exit(coordinator)
    assert maintenance.owner.value == "Idle"


def test_velopack_apply_fails_before_download_when_installer_never_terminates(
    tmp_path,
):
    manager = _Manager()
    maintenance = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    runtime = maintenance.begin_runtime_maintenance(
        cancel=lambda: None,
        wait_terminal=lambda _timeout: False,
    )
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
        maintenance_coordinator=maintenance,
    )
    asyncio.run(coordinator.check())
    try:
        result = asyncio.run(coordinator.download_and_apply())
    finally:
        runtime.release()

    assert result.status is UpdateApplyStatus.FAILED
    assert "terminal" in result.detail
    assert manager.downloaded is False
    assert manager.apply_started is False


def test_velopack_apply_releases_update_owner_when_download_raises(tmp_path):
    class FailingManager(_Manager):
        def download_updates(self, _info, progress_callback=None) -> None:
            raise RuntimeError("download failed")

    manager = FailingManager()
    maintenance = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    coordinator = VelopackUpdateCoordinator(
        source_candidates=("https://updates.invalid/",),
        manager_factory=lambda _source: manager,
        maintenance_coordinator=maintenance,
    )
    asyncio.run(coordinator.check())

    result = asyncio.run(coordinator.download_and_apply())

    assert result.status is UpdateApplyStatus.FAILED
    assert "download failed" in result.detail
    assert maintenance.owner.value == "Idle"
    retry = maintenance.begin_runtime_maintenance(
        cancel=lambda: None,
        wait_terminal=lambda _timeout: True,
    )
    retry.release()


def test_feed_candidate_order_preserves_network_semantics():
    overseas = build_update_source_candidates("overseas")
    domestic = build_update_source_candidates("domestic")

    assert overseas[0].kind == "direct"
    assert [candidate.kind for candidate in domestic] == [
        "url-prefix",
        "url-prefix",
        "direct",
    ]
    assert overseas[0].base_url.endswith("/releases/latest/download/")


def test_authoritative_reachability_selects_domestic_or_overseas_order(monkeypatch):
    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(
        "vibeocr.classic.services.update_transport._github_reachable", unreachable
    )
    domestic = asyncio.run(resolve_update_source_candidates())
    assert [candidate.kind for candidate in domestic] == [
        "url-prefix",
        "url-prefix",
        "direct",
    ]

    async def reachable() -> bool:
        return True

    monkeypatch.setattr(
        "vibeocr.classic.services.update_transport._github_reachable", reachable
    )
    overseas = asyncio.run(resolve_update_source_candidates())
    assert overseas[0].kind == "direct"
