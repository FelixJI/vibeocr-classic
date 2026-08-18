from __future__ import annotations

import threading
from pathlib import Path

import pytest

from vibeocr.classic.runtime_maintenance import (
    ProductMaintenanceBusy,
    ProductMaintenanceCoordinator,
    RuntimeMaintenanceRequestBuilder,
    RuntimeMaintenanceViewModel,
)
from vibeocr.classic.runtime_installation import (
    RuntimeInstallerCancelled,
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
    RuntimeMaintenanceUpdate,
)


def _runtime_client(tmp_path: Path) -> RuntimeInstallerClient:
    backend = tmp_path / "backend"
    backend.mkdir(parents=True)
    (backend / "runtime-manifest.json").write_text(
        """{"capabilities":["runtime.maintenance.v2",
        "runtime.component-selection.v1","runtime.download-sources.v1"]}""",
        encoding="utf-8",
    )
    return RuntimeInstallerClient(tmp_path, command=("installer.exe",))


def _launch_envelope() -> dict[str, object]:
    return {
        "launch": {
            "python_executable": "python.exe",
            "supervisor_module": "vibeocr.backend.supervisor.main",
            "working_directory": ".",
            "model_root": "models",
            "environment": {},
        }
    }


def test_selection_request_requires_maintenance_v2_even_with_selection_capability() -> None:
    builder = RuntimeMaintenanceRequestBuilder(
        negotiated_capabilities=("runtime.component-selection.v1",)
    )

    with pytest.raises(RuntimeInstallerClientError, match="runtime.maintenance.v2"):
        builder.selection_fields(
            operation="ensure",
            install_component_ids=(),
            download_source_ids=None,
        )


def test_ensure_and_retry_share_selection_request_contract() -> None:
    builder = RuntimeMaintenanceRequestBuilder(
        negotiated_capabilities=(
            "runtime.maintenance.v2",
            "runtime.component-selection.v1",
            "runtime.download-sources.v1",
        )
    )
    expected = {
        "install_component_ids": [],
        "download_source_ids": ["tuna-pypi"],
    }

    assert builder.selection_fields(
        operation="ensure",
        install_component_ids=(),
        download_source_ids=("tuna-pypi",),
    ) == expected
    assert builder.selection_fields(
        operation="retry",
        install_component_ids=(),
        download_source_ids=("tuna-pypi",),
    ) == expected


def test_runtime_view_model_exposes_requested_and_effective_source_truth() -> None:
    update = RuntimeMaintenanceUpdate(
        event_type="snapshot",
        operation_id="op-1",
        sequence=1,
        operation="ensure",
        operation_state="running",
        phase="install_profile",
        profile_id="win-x64-cpu",
        updated_at="2026-08-18T00:00:00Z",
        requested_download_source_ids=("tuna-pypi",),
        effective_download_source_ids=("official-pypi",),
    )

    view = RuntimeMaintenanceViewModel.from_update(update)

    assert view.requested_source_ids == ("tuna-pypi",)
    assert view.effective_source_ids == ("official-pypi",)
    assert view.source_summary == "请求源：tuna-pypi；实际源：official-pypi"
    assert view.next_operation_note == "运行中的源已快照；设置修改仅影响下一次操作"


def test_product_maintenance_allows_exactly_one_owner(tmp_path: Path) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    runtime = coordinator.begin_runtime_maintenance(
        cancel=lambda: None,
        wait_terminal=lambda _timeout: False,
    )
    try:
        with pytest.raises(ProductMaintenanceBusy, match="RuntimeMaintenance"):
            coordinator.begin_runtime_maintenance(
                cancel=lambda: None,
                wait_terminal=lambda _timeout: False,
            )
    finally:
        runtime.release()


def test_product_maintenance_file_lock_blocks_a_second_coordinator(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "state/locks/product-maintenance.lock"
    first = ProductMaintenanceCoordinator(lock)
    second = ProductMaintenanceCoordinator(lock)
    update = first.begin_app_update(cancel_runtime=True, timeout=0)
    try:
        with pytest.raises(ProductMaintenanceBusy, match="another process"):
            second.begin_runtime_maintenance(
                cancel=lambda: None,
                wait_terminal=lambda _timeout: True,
            )
    finally:
        update.release()


def test_update_cancels_runtime_and_waits_for_terminal_before_ownership(
    tmp_path: Path,
) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    cancelled = threading.Event()
    terminal = threading.Event()
    runtime = coordinator.begin_runtime_maintenance(
        cancel=cancelled.set,
        wait_terminal=terminal.wait,
    )

    def finish_runtime() -> None:
        assert cancelled.wait(2)
        runtime.release()
        terminal.set()

    thread = threading.Thread(target=finish_runtime)
    thread.start()
    update = coordinator.begin_app_update(cancel_runtime=True, timeout=2)
    thread.join(timeout=2)
    try:
        assert cancelled.is_set()
        assert coordinator.owner.value == "AppUpdate"
    finally:
        update.release()


def test_update_fails_closed_when_installer_never_reaches_terminal(
    tmp_path: Path,
) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    runtime = coordinator.begin_runtime_maintenance(
        cancel=lambda: None,
        wait_terminal=lambda _timeout: False,
    )
    try:
        with pytest.raises(ProductMaintenanceBusy, match="terminal"):
            coordinator.begin_app_update(cancel_runtime=True, timeout=0.01)
        assert coordinator.owner.value == "RuntimeMaintenance"
    finally:
        runtime.release()


@pytest.mark.parametrize("operation", ["ensure", "repair", "retry"])
def test_real_runtime_client_rejects_every_mutation_while_update_owns_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    monkeypatch.setattr(
        "vibeocr.classic.runtime_installation.get_product_maintenance_coordinator",
        lambda *_args: coordinator,
    )
    client = _runtime_client(tmp_path)
    update = coordinator.begin_app_update(cancel_runtime=True, timeout=0)
    try:
        with pytest.raises(RuntimeInstallerClientError, match="AppUpdate"):
            if operation == "ensure":
                client.ensure()
            elif operation == "repair":
                client.repair()
            else:
                client.retry("op-1")
    finally:
        update.release()


def test_update_waits_for_real_runtime_client_cancel_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    monkeypatch.setattr(
        "vibeocr.classic.runtime_installation.get_product_maintenance_coordinator",
        lambda *_args: coordinator,
    )
    client = _runtime_client(tmp_path)
    outcome: list[str] = []

    def invoke(_operation: str, **kwargs: object) -> dict[str, object]:
        cancel_event = kwargs["cancel_event"]
        assert isinstance(cancel_event, threading.Event)
        assert cancel_event.wait(2)
        raise RuntimeInstallerCancelled("cancelled")

    monkeypatch.setattr(client, "_invoke", invoke)

    def install() -> None:
        try:
            client.ensure()
        except RuntimeInstallerCancelled:
            outcome.append("cancelled")

    thread = threading.Thread(target=install)
    thread.start()
    for _ in range(100):
        if coordinator.owner.value == "RuntimeMaintenance":
            break
        threading.Event().wait(0.01)
    update = coordinator.begin_app_update(cancel_runtime=True, timeout=2)
    thread.join(timeout=2)
    try:
        assert outcome == ["cancelled"]
        assert not thread.is_alive()
        assert coordinator.owner.value == "AppUpdate"
    finally:
        update.release()


def test_real_runtime_client_releases_owner_on_success_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = ProductMaintenanceCoordinator(
        tmp_path / "state/locks/product-maintenance.lock"
    )
    monkeypatch.setattr(
        "vibeocr.classic.runtime_installation.get_product_maintenance_coordinator",
        lambda *_args: coordinator,
    )
    client = _runtime_client(tmp_path)
    monkeypatch.setattr(client, "_invoke", lambda *_args, **_kwargs: _launch_envelope())

    client.ensure()
    assert coordinator.owner.value == "Idle"

    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("installer crashed")

    monkeypatch.setattr(client, "_invoke", fail)
    with pytest.raises(RuntimeError, match="installer crashed"):
        client.repair()
    assert coordinator.owner.value == "Idle"
