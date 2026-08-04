from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from vibeocr.classic.runtime_installation import (
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
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
