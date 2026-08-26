from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

import vibeocr.classic.runtime_installation as runtime_installation
from vibeocr.classic.runtime_maintenance import ProductMaintenanceCoordinator
from vibeocr.classic.runtime_installation import (
    RuntimeComponentDescriptor,
    RuntimeInspection,
    RuntimeInstallerCancelled,
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
    RuntimeMaintenancePage,
    RuntimeMaintenanceUpdate,
)


@pytest.mark.parametrize(
    "status,integrity", [("failed", "verified"), ("ready", "failed")]
)
def test_base_ready_never_bypasses_failed_runtime_inspection(
    status: str, integrity: str
) -> None:
    inspection = RuntimeInspection(
        status=status,
        runtime_root="C:/runtime",
        accelerator="cpu",
        profile="base",
        python_version="3.13",
        protocol_version="2.8.0",
        manifest_sha256="manifest",
        backend_version="0.13.4",
        integrity=integrity,
        components=(
            RuntimeComponentDescriptor(
                "ocr_engine",
                "RapidOCR",
                actual_state="ready",
                included_in_base=True,
            ),
        ),
    )

    assert not inspection.base_ready


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
        maintenance_coordinator=ProductMaintenanceCoordinator(
            tmp_path / "state/locks/product-maintenance.lock"
        ),
    )


def test_renamed_installer_still_requires_full_binding(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client._verify_installer_executable()
    Path(client.command[0]).write_bytes(b"tampered")
    with pytest.raises(RuntimeInstallerClientError, match="SHA-256"):
        client._verify_installer_executable()


def test_required_capabilities_come_from_product_component_lock(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    lock = json.loads(client.component_lock.read_text(encoding="utf-8"))
    lock["required_capabilities"] = [
        "ocr.recognition.v2",
        "pdf.edit.v2",
    ]
    client.component_lock.write_text(json.dumps(lock), encoding="utf-8")

    assert client.required_capabilities() == (
        "ocr.recognition.v2",
        "pdf.edit.v2",
    )


def test_profile_descriptor_projects_base_only_scope(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    manifest = json.loads(client.runtime_manifest.read_text(encoding="utf-8"))
    base_components = [
        {"component_id": "ocr_engine", "display_name": "RapidOCR"},
        {"component_id": "pdf_document_tools", "display_name": "PDF tools"},
    ]
    manifest["profiles"] = {
        "win-x64-base": {"components": base_components},
        "win-x64-cpu": {
            "components": [
                *base_components,
                {
                    "component_id": "document_parsing",
                    "display_name": "Document parsing",
                },
            ]
        },
    }
    client.runtime_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    profile = client.profile_descriptor(install_component_ids=())
    by_id = {component.component_id: component for component in profile.components}

    assert by_id["ocr_engine"].included_in_base is True
    assert by_id["ocr_engine"].desired_state == "ready"
    assert by_id["pdf_document_tools"].included_in_base is True
    assert by_id["document_parsing"].included_in_base is False
    assert by_id["document_parsing"].desired_state == "not_required"


@pytest.mark.parametrize(
    "value",
    [None, ["ocr.recognition.v2", "ocr.recognition.v2"], [""]],
)
def test_required_capabilities_reject_invalid_lock_values(
    tmp_path: Path,
    value: object,
) -> None:
    client = _bound_client(tmp_path)
    lock = json.loads(client.component_lock.read_text(encoding="utf-8"))
    lock["required_capabilities"] = value
    client.component_lock.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(RuntimeInstallerClientError, match="required_capabilities"):
        client.required_capabilities()


def test_manifest_tamper_is_rejected_before_executable(tmp_path: Path) -> None:
    client = _bound_client(tmp_path)
    client.runtime_manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeInstallerClientError, match="无法验证"):
        client._verify_installer_executable()


def test_runtime_host_response_requires_protocol_schema(
    tmp_path: Path, monkeypatch
) -> None:
    script = 'print(\'{"protocol_version":2,"ok":true,"operation":"ensure"}\')'
    client = RuntimeInstallerClient(
        tmp_path,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)

    with pytest.raises(RuntimeInstallerClientError):
        client._invoke("ensure", timeout=3)


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


def test_external_state_root_keeps_component_authority_in_product_content(
    tmp_path: Path,
) -> None:
    content = tmp_path / "current"
    state = tmp_path / "VibeOCRClassicData"
    (content / "backend").mkdir(parents=True)
    (content / "component-lock.json").write_text("{}", encoding="utf-8")
    (content / "backend" / "runtime-manifest.json").write_text("{}", encoding="utf-8")
    client = RuntimeInstallerClient(
        state,
        content_root=content,
        command=("python", "-m", "vibeocr.backend.runtime_installer"),
    )

    arguments = client._arguments("inspect")
    request = json.loads(arguments[arguments.index("--request-json") + 1])

    # Runtime Host 的 product_root 必须是 layout 注册的产品根（content_root）；
    # state root 只属于 Classic 自己，不进入绑定请求。
    assert request["product_root"] == str(content.resolve())
    assert request["component_lock"] == str((content / "component-lock.json").resolve())
    assert request["runtime_manifest"] == str(
        (content / "backend" / "runtime-manifest.json").resolve()
    )


def test_product_release_manifest_is_default_portable_layout(tmp_path: Path) -> None:
    product = tmp_path / "classic"
    product.mkdir()
    marker = product / "product-release-manifest.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_root": "state",
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
    assert executable.parent == tmp_path / "cache" / "runtime-installer"

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


def test_control_process_forces_utf8_and_preserves_parent_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _bound_client(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setenv("VIBEOCR_TEST_PARENT_ENV", "preserved")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setattr(
        runtime_installation,
        "_parse_runtime_host_response_line",
        lambda _line: {"ok": True},
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "wire\n", "")

    monkeypatch.setattr(runtime_installation.subprocess, "run", fake_run)

    assert client._invoke_control({"request_kind": "inspect"})["ok"] is True
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["VIBEOCR_TEST_PARENT_ENV"] == "preserved"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"


def test_installer_process_forces_utf8_and_preserves_parent_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = """
import json

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
    monkeypatch.setenv("VIBEOCR_TEST_PARENT_ENV", "preserved")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    real_popen = runtime_installation.subprocess.Popen
    captured: dict[str, object] = {}

    def capture_popen(*args: object, **kwargs: object):
        captured.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(runtime_installation.subprocess, "Popen", capture_popen)

    assert client._invoke("ensure", timeout=3)["ok"] is True
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["VIBEOCR_TEST_PARENT_ENV"] == "preserved"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"


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


def test_v2_cancel_rejected_after_commit_waits_for_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"capabilities": ["runtime.maintenance.v2"]}),
        encoding="utf-8",
    )
    script = (
        "import json,time; time.sleep(0.1); "
        "print(json.dumps({"
        "'protocol_version':2,'ok':True,'operation':'ensure',"
        "'state':{'status':'ready','runtime_root':'C:/runtime',"
        "'accelerator':'cpu','integrity':'verified',"
        "'manifest_sha256':'a'*64,'backend_version':'0.11.1'}}))"
    )
    client = RuntimeInstallerClient(
        tmp_path,
        runtime_manifest=manifest,
        command=(sys.executable, "-c", script),
    )
    monkeypatch.setattr(client, "_verify_installer_executable", lambda: None)
    cancel_calls: list[str] = []

    def cancel(operation_id: str, **_kwargs):
        cancel_calls.append(operation_id)
        raise RuntimeInstallerClientError(
            "operation cannot be cancelled during commit",
            canonical_code="RUNTIME_OPERATION_NOT_CANCELLABLE",
        )

    monkeypatch.setattr(client, "cancel", cancel)
    cancel_event = threading.Event()
    cancel_event.set()

    result = client._invoke("ensure", cancel_event=cancel_event, timeout=1)

    assert result["ok"] is True
    assert len(cancel_calls) == 1


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
state = {
    "status": "ready", "runtime_root": "C:/runtime", "accelerator": "cpu",
    "integrity": "verified", "manifest_sha256": "a" * 64,
    "backend_version": "0.11.1",
}
print(json.dumps({
    "protocol_version": 2,
    "ok": True,
    "operation": "ensure",
    "state": state,
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
state = {
    "status": "ready", "runtime_root": "C:/runtime", "accelerator": "cpu",
    "integrity": "verified", "manifest_sha256": "a" * 64,
    "backend_version": "0.11.1",
}
print(json.dumps(event), flush=True)
print(json.dumps(event), flush=True)
print(json.dumps({
    "protocol_version": 2, "ok": True, "operation": "ensure",
    "state": state,
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
        maintenance_coordinator=ProductMaintenanceCoordinator(
            tmp_path / "state/locks/product-maintenance.lock"
        ),
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


def test_cancel_sequence_race_retries_same_operation_without_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = RuntimeInstallerClient(
        tmp_path,
        command=(sys.executable, "-c", "pass"),
    )
    expected_sequences: list[int | None] = []

    def cancel(
        _operation_id: str,
        *,
        command_id: str | None = None,
        expected_sequence: int | None = None,
    ) -> None:
        del command_id
        expected_sequences.append(expected_sequence)
        if expected_sequence is not None:
            raise RuntimeInstallerClientError("expected_sequence mismatch")

    monkeypatch.setattr(client, "cancel", cancel)

    client._request_cancel("op-1", expected_sequence=8)

    assert expected_sequences == [8, None]


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


def _selection_capable_client(
    tmp_path: Path, *, capabilities: list[str] | None = None
) -> RuntimeInstallerClient:
    client = _bound_client(tmp_path)
    manifest = json.loads(client.runtime_manifest.read_text(encoding="utf-8"))
    manifest["capabilities"] = (
        capabilities
        if capabilities is not None
        else [
            "runtime.maintenance.v2",
            "runtime.capability-metadata.v1",
            "runtime.component-repair.v1",
            "runtime.component-selection.v1",
            "runtime.download-sources.v1",
        ]
    )
    client.runtime_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return client


def _request_of(client: RuntimeInstallerClient, *args, **kwargs) -> dict:
    arguments = client._arguments(*args, **kwargs)
    return json.loads(arguments[arguments.index("--request-json") + 1])


def test_ensure_install_scope_distinguishes_omission_and_base_only(
    tmp_path: Path,
) -> None:
    client = _selection_capable_client(tmp_path)

    assert "install_component_ids" not in _request_of(client, "ensure")
    assert (
        _request_of(client, "ensure", install_component_ids=())["install_component_ids"]
        == []
    )
    assert _request_of(
        client, "ensure", install_component_ids=("win-x64-cpu-document-parsing",)
    )["install_component_ids"] == ["win-x64-cpu-document-parsing"]


def test_ensure_download_source_ids_reject_empty_list(tmp_path: Path) -> None:
    client = _selection_capable_client(tmp_path)

    with pytest.raises(RuntimeInstallerClientError, match="download_source_ids"):
        client._arguments("ensure", download_source_ids=())

    assert _request_of(client, "ensure", download_source_ids=("tuna-pypi",))[
        "download_source_ids"
    ] == ["tuna-pypi"]


def test_install_intent_is_isolated_from_repair_component_ids(tmp_path: Path) -> None:
    client = _selection_capable_client(tmp_path)

    with pytest.raises(RuntimeInstallerClientError, match="仅用于 ensure"):
        client._arguments("repair", install_component_ids=())
    with pytest.raises(RuntimeInstallerClientError, match="仅用于 ensure"):
        client._arguments("repair", download_source_ids=("tuna-pypi",))

    repair_request = _request_of(client, "repair", component_ids=("some-component",))
    assert repair_request["component_ids"] == ["some-component"]
    assert "install_component_ids" not in repair_request
    assert "download_source_ids" not in repair_request


def test_install_intent_requires_selection_capabilities(tmp_path: Path) -> None:
    client = _selection_capable_client(
        tmp_path,
        capabilities=["runtime.maintenance.v2", "runtime.capability-metadata.v1"],
    )

    with pytest.raises(RuntimeInstallerClientError, match="组件手动选择"):
        client._arguments("ensure", install_component_ids=())
    with pytest.raises(RuntimeInstallerClientError, match="下载源选择"):
        client._arguments("ensure", download_source_ids=("tuna-pypi",))


def test_negotiated_capabilities_override_manifest_for_intent_gating(
    tmp_path: Path,
) -> None:
    # manifest 可能落后于运行时：信封 capability_descriptors（available 集）
    # 必须优先于 manifest 声明，否则运行中的合格 Backend 无法接收选择意图。
    # negotiated_capabilities 只是 required 回显子集，不能作为可用集——
    # 带八项 required 的 inspect 之后回显不含 maintenance.v2，ensure 仍须
    # 正常构造 v2 事件流与选择意图。
    from vibeocr.classic.runtime_installation import RuntimeCapabilityDescriptor

    client = _selection_capable_client(
        tmp_path, capabilities=["runtime.maintenance.v2"]
    )
    client._capability_descriptors = tuple(
        RuntimeCapabilityDescriptor(
            name=name, lifecycle="active", introduced_in="2.7.0"
        )
        for name in (
            "runtime.maintenance.v2",
            "runtime.component-selection.v1",
            "runtime.download-sources.v1",
        )
    )
    client._negotiated_capabilities = (
        "ocr.engine-selection.v1",
        "runtime.component-selection.v1",
        "runtime.download-sources.v1",
    )

    request = _request_of(
        client,
        "ensure",
        install_component_ids=("win-x64-cu126-gpu-runtime",),
        download_source_ids=("tuna-pypi",),
    )
    assert request["accepted_event_streams"] == ["ndjson.v2"]
    assert request["install_component_ids"] == ["win-x64-cu126-gpu-runtime"]
    assert request["download_source_ids"] == ["tuna-pypi"]


def test_retry_command_carries_install_intent_with_capability_gate(
    tmp_path: Path,
) -> None:
    client = _selection_capable_client(tmp_path)
    captured: dict[str, object] = {}

    def _fake_invoke_control(request, *, timeout=30):
        captured.update(request)
        return {"protocol_version": 2, "ok": True}

    client._invoke_control = _fake_invoke_control  # type: ignore[method-assign]

    client.retry(
        "op-1",
        install_component_ids=(),
        download_source_ids=("tuna-pypi",),
    )

    assert captured["command"] == "retry"
    assert captured["install_component_ids"] == []
    assert captured["download_source_ids"] == ["tuna-pypi"]

    with pytest.raises(RuntimeInstallerClientError, match="download_source_ids"):
        client.retry("op-1", download_source_ids=())


def test_maintenance_update_parses_download_source_scope() -> None:
    from vibeocr.classic.runtime_installation import _maintenance_update

    wire = {
        "protocol_version": 2,
        "event_version": 1,
        "operation": "ensure",
        "event_type": "snapshot",
        "message_code": "runtime.ensure.snapshot",
        "snapshot": {
            "operation_id": "op-42",
            "sequence": 1,
            "operation": "ensure",
            "operation_state": "running",
            "phase": "install_profile",
            "profile_id": "win-x64-cpu",
            "updated_at": "2026-08-17T00:00:00+00:00",
            "requested_component_ids": ["win-x64-cpu-document-parsing"],
            "effective_component_ids": ["win-x64-cpu-document-parsing"],
            "requested_download_source_ids": ["tuna-pypi"],
            "effective_download_source_ids": ["tuna-pypi"],
        },
    }

    parsed = _maintenance_update(wire, expected_operation="ensure")
    assert parsed.requested_download_source_ids == ("tuna-pypi",)
    assert parsed.effective_download_source_ids == ("tuna-pypi",)

    invalid = {
        **wire,
        "snapshot": {
            **wire["snapshot"],
            "effective_download_source_ids": ["tuna-pypi", ""],
        },
    }
    with pytest.raises(RuntimeInstallerClientError, match="scope"):
        _maintenance_update(invalid, expected_operation="ensure")
