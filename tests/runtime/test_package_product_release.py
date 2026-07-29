from __future__ import annotations

import hashlib
import json
import zipfile
from typing import TYPE_CHECKING

from scripts.bind_component_releases import bind_product_releases
from scripts.package_product_release import package_product_release

if TYPE_CHECKING:
    from pathlib import Path


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _releases(tmp_path: Path) -> tuple[Path, Path]:
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    protocol_wheel = protocol / "vibeocr_runtime_contracts-2.0.0-py3-none-any.whl"
    protocol_wheel.write_bytes(b"protocol")
    protocol_manifest = protocol / "release-manifest.json"
    protocol_manifest.write_text(
        json.dumps(
            {
                "protocol_version": "2.0.0",
                "artifacts": {
                    protocol_wheel.name: {
                        "sha256": _sha(b"protocol"),
                        "size": len(b"protocol"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = tmp_path / "backend-release"
    backend.mkdir()
    backend_wheel = backend / "vibeocr_backend-0.7.0-py3-none-any.whl"
    backend_wheel.write_bytes(b"backend")
    copied_protocol_wheel = backend / protocol_wheel.name
    copied_protocol_wheel.write_bytes(protocol_wheel.read_bytes())
    copied_manifest = backend / "protocol-release-manifest.json"
    copied_manifest.write_bytes(protocol_manifest.read_bytes())
    python = backend / "python.tar.gz"
    python.write_bytes(b"python")
    lock = backend / "cpu.lock"
    lock.write_bytes(b"cpu")
    installer = backend / "installer.zip"
    with zipfile.ZipFile(installer, "w") as archive:
        archive.writestr("runtime-installer/installer.exe", b"installer")
    runtime_manifest = backend / "runtime-manifest.json"
    runtime_manifest.write_text(
        json.dumps(
            {
                "backend_version": "0.7.0",
                "backend_wheel": backend_wheel.name,
                "backend_sha256": _sha(b"backend"),
                "protocol_manifest": copied_manifest.name,
                "protocol_manifest_sha256": _sha(copied_manifest.read_bytes()),
                "protocol_wheel": copied_protocol_wheel.name,
                "protocol_sha256": _sha(copied_protocol_wheel.read_bytes()),
                "python": {"archive": python.name, "sha256": _sha(b"python")},
                "installer": {
                    "archive": installer.name,
                    "sha256": _sha(installer.read_bytes()),
                    "executable_path": "runtime-installer/installer.exe",
                    "executable_sha256": _sha(b"installer"),
                },
                "profiles": {
                    "win-x64-cpu": {"lock": lock.name, "sha256": _sha(b"cpu")}
                },
                "capabilities": ["ocr.recognition.v2"],
            }
        ),
        encoding="utf-8",
    )
    checksums = backend / "SHA256SUMS"
    checksums.write_text(
        "".join(
            f"{_sha(path.read_bytes())}  {path.name}\n"
            for path in sorted(backend.iterdir(), key=lambda item: item.name)
            if path.is_file() and path != checksums
        ),
        encoding="utf-8",
    )
    return protocol, backend


def test_product_package_is_deterministic_and_binds_runtime(tmp_path: Path) -> None:
    protocol, backend = _releases(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        profile="win-x64-cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    outputs = []
    for name in ("first", "second"):
        product = tmp_path / name / "VibeOCR"
        product.mkdir(parents=True)
        (product / "VibeOCR.exe").write_bytes(b"app")
        output = tmp_path / f"{name}.zip"
        outputs.append(
            package_product_release(
                product_root=product,
                frontend="classic",
                frontend_version="0.7.0",
                source_commit="a" * 40,
                component_lock=component_lock,
                protocol_release_dir=protocol,
                backend_release_dir=backend,
                output=output,
            )
        )
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with zipfile.ZipFile(outputs[0]) as archive:
        members = set(archive.namelist())
    assert "VibeOCR/component-lock.json" in members
    assert "VibeOCR/runtime-installer/vibeocr-runtime-installer.exe" in members
    assert "VibeOCR/backend/runtime-manifest.json" in members
