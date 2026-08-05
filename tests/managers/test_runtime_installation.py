from __future__ import annotations

import hashlib
import json
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from vibeocr.classic.runtime_installation import (
    RuntimeInstallerCancelled,
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
    RuntimeMaintenancePage,
    RuntimeMaintenanceUpdate,
)


def _bound_client(tmp_path: Path, *, executable_name: str = "renamed.exe"):
    executable = tmp_path / executable_name
    executable.write_bytes(b"installer")
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "backend_version": "0.7.0",
                "python": {"version": "3.13.12"},
                "profiles": {"win-x64-cpu": {}},
                "installer": {
                    "executable_sha256": hashlib.sha256(b"installer").hexdigest()
                },
            }
        ),
        encoding="utf-8",
    )
    lock = tmp_path / "component-lock.json"
    lock.write_text(
        json.dumps(
            {
                "protocol": {"version": "2.1.0"},
                "backend": {
                    "accelerator": "cpu",
                    "runtime_manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    return RuntimeInstallerClient(
        tmp_path,
        component_lock=lock,
        runtime_manifest=manifest,
        command=(str(executable),),
    )


def test_renamed_installer_still_requires_full_binding(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client._verify_installer_executable()
    Path(client.command[0]).write_bytes(b"tampered")
    with pytest.raises(RuntimeInstallerClientError, match="SHA-256"):
        client._verify_installer_executable()


def test_manifest_tamper_is_rejected_before_executable(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client.runtime_manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeInstallerClientError, match="无法验证"):
        client._verify_installer_executable()


def test_explicit_layout_environment_is_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "portable-layout.json"
    marker.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("VIBEOCR_PORTABLE_LAYOUT", str(marker))
    client = RuntimeInstallerClient(
        tmp_path / "classic",
        command=("python", "-m", "vibeocr.backend.runtime_installer"),
    )
    arguments = client._arguments("inspect")
    request = json.loads(arguments[arguments.index("--request-json") + 1])
    assert request["layout_manifest"] == str(marker.resolve())
    assert request["product_id"] == "classic"


def test_local_layout_does_not_forward_product_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEOCR_PORTABLE_LAYOUT", raising=False)
    client = RuntimeInstallerClient(
        tmp_path / "classic",
        command=("python", "-m", "vibeocr.backend.runtime_installer"),
    )

    arguments = client._arguments("inspect")

    request = json.loads(arguments[arguments.index("--request-json") + 1])
    assert "layout_manifest" not in request
    assert "product_id" not in request


def test_product_release_manifest_is_default_portable_layout(tmp_path: Path) -> None:
    product = tmp_path / "classic"
    product.mkdir()
    marker = product / "product-release-manifest.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_root": "data",
                "products": {
                    "classic": {
                        "root": ".",
                        "component_lock": "component-lock.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    client = RuntimeInstallerClient(
        product,
        command=("python", "-m", "vibeocr.backend.runtime_installer"),
    )
    arguments = client._arguments("inspect")

    request = json.loads(arguments[arguments.index("--request-json") + 1])
    assert request["layout_manifest"] == str(marker.resolve())
    assert request["product_id"] == "classic"


def test_frozen_installer_is_materialized_from_bound_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    installer_archive = backend / "installer.zip"
    executable_bytes = b"installer executable"
    with zipfile.ZipFile(installer_archive, "w") as archive:
        archive.writestr("runtime-installer/installer.exe", executable_bytes)
    manifest = backend / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "backend_version": "0.7.0",
                "installer": {
                    "archive": installer_archive.name,
                    "sha256": hashlib.sha256(
                        installer_archive.read_bytes()
                    ).hexdigest(),
                    "executable_path": "runtime-installer/installer.exe",
                    "executable_sha256": hashlib.sha256(executable_bytes).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    lock = tmp_path / "component-lock.json"
    lock.write_text(
        json.dumps(
            {
                "backend": {
                    "accelerator": "cpu",
                    "runtime_manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    client = RuntimeInstallerClient(tmp_path)
    client._verify_installer_executable()

    executable = Path(client.command[0])
    assert executable.read_bytes() == executable_bytes
    assert executable.parent == tmp_path / "data" / "cache" / "runtime-installer"

    executable.write_bytes(b"stale installer")
    client._verify_installer_executable()
    assert executable.read_bytes() == executable_bytes


def test_frozen_t6_inspect_does_not_spawn_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _bound_client(tmp_path)
    smoke_python = tmp_path / "python.exe"
    smoke_python.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")
    monkeypatch.setenv("VIBEOCR_SELF_TEST_PYTHON", str(smoke_python))
    monkeypatch.setattr(
        "vibeocr.classic.runtime_installation.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("T6 inspect must not spawn installer"),
    )

    inspection = client.inspect()

    assert inspection.ready
    assert inspection.accelerator == "cpu"
    assert inspection.profile == "win-x64-cpu"
    assert inspection.python_version == "3.13.12"
    assert inspection.protocol_version == "2.1.0"


def test_installer_output_larger_than_pipe_buffer_does_not_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = """
import json
import sys

sys.stdout.write("x" * 1_000_000 + "\\n")
print(json.dumps({
    "protocol_version": 2,
    "ok": True,
    "operation": "ensure",
    "state": {
        "status": "ready",
        "runtime_root": "runtime",
        "accelerator": "cpu",
        "manifest_sha256": "0" * 64,
        "backend_version": "0.7.0",
        "integrity": "verified",
    },
    "launch": {
        "python_executable": "python.exe",
        "supervisor_module": "vibeocr.backend.supervisor.main",
        "working_directory": ".",
        "model_root": "models",
        "environment": {},
    },
}))
"""
    client = RuntimeInstallerClient(
        tmp_path,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)

    value = client._invoke("ensure", timeout=3)

    assert value["state"]["accelerator"] == "cpu"


def test_maintenance_capability_opts_into_ndjson(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v1"]}),
        encoding="utf-8",
    )
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", "pass"),
    )

    arguments = client._arguments("ensure")
    request = json.loads(arguments[arguments.index("--request-json") + 1])

    assert request["accepted_event_streams"] == ["ndjson.v1"]


def test_v2_maintenance_binds_operation_scope_and_negotiation(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "capabilities": [
                    "runtime.maintenance.v1",
                    "runtime.maintenance.v2",
                    "runtime.component-repair.v1",
                    "runtime.capability-metadata.v1",
                ]
            }
        ),
        encoding="utf-8",
    )
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", "pass"),
    )

    arguments = client._arguments(
        "repair",
        operation_id="op-1",
        component_ids=("ocr_engine",),
        required_capabilities=("runtime.component-repair.v1",),
    )
    request = json.loads(arguments[arguments.index("--request-json") + 1])

    assert request["accepted_event_streams"] == ["ndjson.v2"]
    assert request["operation_id"] == "op-1"
    assert request["component_ids"] == ["ocr_engine"]
    assert request["required_capabilities"] == ["runtime.component-repair.v1"]


def test_v2_omits_ungated_capability_metadata_and_rejects_scoped_fields(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v2"]}),
        encoding="utf-8",
    )
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", "pass"),
    )

    arguments = client._arguments("ensure", operation_id="op-1")
    request = json.loads(arguments[arguments.index("--request-json") + 1])

    assert "required_capabilities" not in request
    with pytest.raises(RuntimeInstallerClientError, match="按组件 repair"):
        client._arguments("repair", component_ids=("ocr_engine",))
    with pytest.raises(RuntimeInstallerClientError, match="negotiation metadata"):
        client._arguments("ensure", required_capabilities=("runtime.maintenance.v2",))


def test_v2_cancel_command_failure_does_not_fake_cancel_or_kill_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v2"]}),
        encoding="utf-8",
    )
    script = "import time; time.sleep(0.1)"
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)

    def cancel(*_args, **_kwargs):
        raise RuntimeInstallerClientError("cancel command failed")

    monkeypatch.setattr(client, "cancel", cancel)
    terminated: list[object] = []
    monkeypatch.setattr(
        "vibeocr.classic.runtime_installation._terminate_process_tree",
        terminated.append,
    )
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(RuntimeInstallerClientError, match="cancel command failed"):
        client._invoke("ensure", cancel_event=cancel_event, timeout=1)

    assert terminated == []


def test_ndjson_maintenance_event_is_delivered_before_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v1"]}),
        encoding="utf-8",
    )
    script = r"""
import json
print(json.dumps({
    "protocol_version": 2,
    "event_version": 1,
    "event_type": "progress",
    "operation": "ensure",
    "snapshot": {
        "operation_id": "op-1",
        "sequence": 3,
        "operation": "ensure",
        "operation_state": "running",
        "phase": "install_profile",
        "profile_id": "win-x64-cpu",
        "component_id": "ocr_engine",
        "updated_at": "2026-08-05T00:00:00Z",
        "progress": {"unit": "steps", "current": 2, "total": 7},
    },
    "message_code": "runtime.installing",
}), flush=True)
print(json.dumps({
    "protocol_version": 2,
    "ok": True,
    "operation": "ensure",
    "state": {},
    "launch": {},
}), flush=True)
"""
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)
    updates: list[RuntimeMaintenanceUpdate] = []

    client._invoke("ensure", progress=updates.append, timeout=3)

    assert len(updates) == 1
    assert updates[0].component_id == "ocr_engine"
    assert updates[0].progress_current == 2
    assert updates[0].progress_total == 7


def test_v2_sequence_gap_is_replayed_and_duplicate_event_is_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v2"]}),
        encoding="utf-8",
    )
    script = r"""
import json
snapshot = {
    "operation_id": "op-1", "sequence": 3, "operation": "ensure",
    "operation_state": "running", "phase": "install_profile",
    "profile_id": "win-x64-cpu", "updated_at": "2026-08-05T00:00:01Z",
}
event = {
    "schema_version": 2, "protocol_version": 2, "event_version": 1,
    "event_type": "progress", "sequence": 3, "operation": "ensure",
    "snapshot": snapshot, "message_code": "runtime.installing",
}
print(json.dumps(event), flush=True)
print(json.dumps(event), flush=True)
print(json.dumps({
    "protocol_version": 2, "ok": True, "operation": "ensure",
    "state": {}, "launch": {},
}), flush=True)
"""
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)
    replay = RuntimeMaintenanceUpdate(
        event_type="progress",
        operation_id="op-1",
        sequence=1,
        operation="ensure",
        operation_state="running",
        phase="prepare_runtime",
        profile_id="win-x64-cpu",
        updated_at="2026-08-05T00:00:00Z",
    )
    replay_second = RuntimeMaintenanceUpdate(
        event_type="progress",
        operation_id="op-1",
        sequence=2,
        operation="ensure",
        operation_state="running",
        phase="install_profile",
        profile_id="win-x64-cpu",
        updated_at="2026-08-05T00:00:00.500Z",
    )
    observe_calls: list[tuple[str, int]] = []

    def observe(operation_id: str, *, after_sequence: int = 0, limit: int = 128):
        del limit
        observe_calls.append((operation_id, after_sequence))
        event = replay if after_sequence == 0 else replay_second
        return RuntimeMaintenancePage(
            operation_id=operation_id,
            events=(event,),
            oldest_sequence=1,
            through_sequence=event.sequence,
            more=after_sequence == 0,
        )

    monkeypatch.setattr(client, "observe", observe)
    updates: list[RuntimeMaintenanceUpdate] = []

    client._invoke("ensure", progress=updates.append, timeout=3, operation_id="op-1")

    assert [update.sequence for update in updates] == [1, 2, 3]
    assert observe_calls == [("op-1", 0), ("op-1", 1)]


@pytest.mark.parametrize("sequence", [True, "2", 2.5])
def test_observe_rejects_weak_event_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sequence: object,
) -> None:
    client = RuntimeInstallerClient(tmp_path, command=(sys.executable, "-c", "pass"))
    snapshot = {
        "operation_id": "op-1",
        "sequence": sequence,
        "operation": "ensure",
        "operation_state": "running",
        "phase": "install_profile",
        "profile_id": "win-x64-cpu",
        "updated_at": "2026-08-05T00:00:00Z",
    }
    monkeypatch.setattr(
        client,
        "_invoke_control",
        lambda *_args, **_kwargs: {
            "operation_id": "op-1",
            "snapshot": snapshot,
            "events": [
                {
                    "protocol_version": 2,
                    "event_version": 1,
                    "event_type": "progress",
                    "sequence": sequence,
                    "operation": "ensure",
                    "snapshot": snapshot,
                    "message_code": "runtime.installing",
                }
            ],
            "oldest_sequence": 1,
            "through_sequence": 1,
            "more": False,
        },
    )

    with pytest.raises(RuntimeInstallerClientError, match="sequence"):
        client.observe("op-1")


def test_observe_rejects_page_that_skips_requested_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = RuntimeInstallerClient(tmp_path, command=(sys.executable, "-c", "pass"))
    snapshot = {
        "operation_id": "op-1",
        "sequence": 3,
        "operation": "ensure",
        "operation_state": "running",
        "phase": "install_profile",
        "profile_id": "win-x64-cpu",
        "updated_at": "2026-08-05T00:00:00Z",
    }
    monkeypatch.setattr(
        client,
        "_invoke_control",
        lambda *_args, **_kwargs: {
            "operation_id": "op-1",
            "snapshot": snapshot,
            "events": [
                {
                    "protocol_version": 2,
                    "event_version": 1,
                    "event_type": "progress",
                    "sequence": 3,
                    "operation": "ensure",
                    "snapshot": snapshot,
                    "message_code": "runtime.installing",
                }
            ],
            "oldest_sequence": 1,
            "through_sequence": 3,
            "more": False,
        },
    )

    with pytest.raises(RuntimeInstallerClientError, match="cursor"):
        client.observe("op-1", after_sequence=1)


def test_cancel_waits_until_terminal_snapshot_page_is_fully_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v2"]}),
        encoding="utf-8",
    )
    script = 'print(\'{"protocol_version":2,"ok":true,"operation":"ensure","state":{},"launch":{}}\')'
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)
    monkeypatch.setattr(client, "cancel", lambda *_args, **_kwargs: None)
    running_1 = RuntimeMaintenanceUpdate(
        event_type="progress",
        operation_id="op-1",
        sequence=1,
        operation="ensure",
        operation_state="running",
        phase="prepare_runtime",
        profile_id="win-x64-cpu",
        updated_at="2026-08-05T00:00:00Z",
    )
    running_2 = RuntimeMaintenanceUpdate(
        event_type="progress",
        operation_id="op-1",
        sequence=2,
        operation="ensure",
        operation_state="running",
        phase="install_profile",
        profile_id="win-x64-cpu",
        updated_at="2026-08-05T00:00:01Z",
    )
    cancelled = RuntimeMaintenanceUpdate(
        event_type="snapshot",
        operation_id="op-1",
        sequence=3,
        operation="ensure",
        operation_state="cancelled",
        phase="install_profile",
        profile_id="win-x64-cpu",
        updated_at="2026-08-05T00:00:02Z",
    )
    observe_calls: list[int] = []

    def observe(_operation_id: str, *, after_sequence: int = 0, limit: int = 128):
        del limit
        observe_calls.append(after_sequence)
        if after_sequence == 0:
            return RuntimeMaintenancePage(
                operation_id="op-1",
                events=(running_1,),
                oldest_sequence=1,
                through_sequence=1,
                more=True,
                snapshot=cancelled,
            )
        return RuntimeMaintenancePage(
            operation_id="op-1",
            events=(running_2, cancelled),
            oldest_sequence=1,
            through_sequence=3,
            more=False,
            snapshot=cancelled,
        )

    monkeypatch.setattr(client, "observe", observe)
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(RuntimeInstallerCancelled):
        client._invoke(
            "ensure",
            cancel_event=cancel_event,
            timeout=3,
            operation_id="op-1",
        )

    assert observe_calls == [0, 1]


def test_capability_negotiation_is_fail_closed_and_exposes_deprecation() -> None:
    client = RuntimeInstallerClient(
        ".",
        command=(sys.executable, "-c", "pass"),
    )
    descriptor = {
        "name": "runtime.maintenance.v2",
        "lifecycle": "deprecated",
        "introduced_in": "2.3.0",
        "deprecated_in": "2.9.0",
        "sunset_at": "2027-01-01T00:00:00Z",
        "replacement": "runtime.maintenance.v3",
    }

    with pytest.raises(RuntimeInstallerClientError, match="未协商必需"):
        client._record_negotiation(
            {
                "negotiated_capabilities": [],
                "capability_descriptors": [descriptor],
            },
            ("runtime.maintenance.v2",),
        )

    client._record_negotiation(
        {
            "negotiated_capabilities": ["runtime.maintenance.v2"],
            "capability_descriptors": [descriptor],
        },
        ("runtime.maintenance.v2",),
    )

    assert client.negotiated_capabilities == ("runtime.maintenance.v2",)
    assert client.capability_descriptors[0].lifecycle == "deprecated"
    assert client.capability_descriptors[0].replacement == "runtime.maintenance.v3"


def test_cancel_and_retry_use_idempotent_v2_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = RuntimeInstallerClient(
        tmp_path,
        command=(sys.executable, "-c", "pass"),
    )
    requests: list[dict] = []
    monkeypatch.setattr(
        client,
        "_invoke_control",
        lambda request, **_kwargs: requests.append(request) or {"ok": True},
    )

    client.cancel("op-1", command_id="cancel-command-1", expected_sequence=8)
    client.retry("op-1", command_id="retry-command-1", new_operation_id="op-2")

    assert requests[0]["command"] == "cancel"
    assert requests[0]["expected_sequence"] == 8
    assert requests[1]["command"] == "retry"
    assert requests[1]["new_operation_id"] == "op-2"
    assert requests[0]["command_id"] == "cancel-command-1"
    assert requests[1]["command_id"] == "retry-command-1"


def test_runtime_error_preserves_canonical_retry_metadata() -> None:
    error = RuntimeInstallerClient._error_from_wire(
        {
            "message": "busy",
            "canonical_code": "RUNTIME_BUSY",
            "category": "transient",
            "retryable": True,
            "retry_after": 2,
            "detail": {"owner": "other"},
        },
        fallback="fallback",
    )

    assert error.canonical_code == "RUNTIME_BUSY"
    assert error.category == "transient"
    assert error.retryable is True
    assert error.retry_after == 2
    assert error.detail == {"owner": "other"}
