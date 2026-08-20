from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from scripts.bind_component_releases import bind_product_releases, bind_protocol_release
from scripts.finalize_product_release import finalize_product_release

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
    advanced_lock = backend / "cpu-advanced.lock"
    advanced_lock.write_bytes(b"advanced")
    cuda_lock = backend / "cuda.lock"
    cuda_lock.write_bytes(b"cuda")
    runtime_pack = backend / "foundation-runtime.chunk01.zip"
    runtime_pack.write_bytes(b"pack")
    advanced_packs = {
        "advanced-win-x64-base-disguise.zip": b"cpu-profile",
        "cuda-profile-pack.zip": b"cuda-profile",
        "paddlex-profile-pack.zip": b"paddlex-profile",
        "mineru-profile-pack.zip": b"mineru-profile",
    }
    for name, content in advanced_packs.items():
        (backend / name).write_bytes(content)
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
                    "win-x64-base": {
                        "lock": lock.name,
                        "sha256": _sha(b"cpu"),
                        "runtime_pack": [runtime_pack.name],
                    },
                    "win-x64-cpu": {
                        "lock": lock.name,
                        "sha256": _sha(b"cpu"),
                        "install_scopes": [
                            {
                                "scope_id": "cpu-runtime",
                                "lock": advanced_lock.name,
                                "runtime_pack": ["advanced-win-x64-base-disguise.zip"],
                            },
                            {
                                "scope_id": "paddlex-runtime",
                                "lock": advanced_lock.name,
                                "runtime_pack": ["paddlex-profile-pack.zip"],
                            },
                            {
                                "scope_id": "mineru-runtime",
                                "lock": advanced_lock.name,
                                "runtime_pack": ["mineru-profile-pack.zip"],
                            },
                        ],
                    },
                    "win-x64-cu126": {
                        "lock": cuda_lock.name,
                        "sha256": _sha(b"cuda"),
                        "install_scopes": [
                            {
                                "scope_id": "gpu-runtime",
                                "lock": cuda_lock.name,
                                "runtime_pack": ["cuda-profile-pack.zip"],
                            }
                        ],
                    },
                },
                "capabilities": ["ocr.recognition.v2"],
            }
        ),
        encoding="utf-8",
    )
    (backend / "SBOM.spdx.json").write_text("{}", encoding="utf-8")
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


def _frontend_release(tmp_path: Path) -> tuple[Path, Path]:
    release = tmp_path / "protocol-sdk"
    release.mkdir()
    artifacts: dict[str, dict[str, object]] = {}
    for distribution in ("vibeocr_runtime_contracts", "vibeocr_runtime_client"):
        wheel = release / f"{distribution}-2.4.0-py3-none-any.whl"
        wheel.write_bytes(distribution.encode())
        artifacts[wheel.name] = {
            "sha256": _sha(wheel.read_bytes()),
            "size": wheel.stat().st_size,
        }
    (release / "release-manifest.json").write_text(
        json.dumps({"protocol_version": "2.4.0", "artifacts": artifacts}),
        encoding="utf-8",
    )
    lock = tmp_path / "frontend-protocol-lock.json"
    bind_protocol_release(
        release_dir=release,
        repository="FelixJI/vibeocr-protocol",
        version="2.4.0",
        output=lock,
    )
    return release, lock


def test_product_finalizer_is_deterministic_and_binds_runtime(tmp_path: Path) -> None:
    protocol, backend = _releases(tmp_path)
    frontend_protocol, frontend_lock = _frontend_release(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        accelerator="cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    manifests = []
    for name in ("first", "second"):
        product = tmp_path / name / "VibeOCR"
        product.mkdir(parents=True)
        (product / "VibeOCR.exe").write_bytes(b"app")
        manifests.append(
            finalize_product_release(
                product_root=product,
                frontend="classic",
                frontend_version="0.7.0",
                source_commit="a" * 40,
                component_lock=component_lock,
                frontend_protocol_lock=frontend_lock,
                frontend_protocol_release_dir=frontend_protocol,
                protocol_release_dir=protocol,
                backend_release_dir=backend,
            )
        )
    assert manifests[0].read_bytes() == manifests[1].read_bytes()
    product = manifests[0].parent
    members = {
        path.relative_to(product).as_posix()
        for path in product.rglob("*")
        if path.is_file()
    }
    version = json.loads((product / "version.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert "component-lock.json" in members
    assert "frontend-protocol-lock.json" in members
    assert "runtime-installer/vibeocr-runtime-installer.exe" not in members
    assert "backend/installer.zip" in members
    assert "backend/python.tar.gz" in members
    assert "backend/runtime-manifest.json" in members
    assert "backend/foundation-runtime.chunk01.zip" in members
    assert "backend/cpu-advanced.lock" in members
    assert "backend/cuda.lock" in members
    assert (
        not {
            "backend/advanced-win-x64-base-disguise.zip",
            "backend/cuda-profile-pack.zip",
            "backend/paddlex-profile-pack.zip",
            "backend/mineru-profile-pack.zip",
        }
        & members
    )
    assert "backend/SHA256SUMS" not in members
    assert "backend/SBOM.spdx.json" not in members
    assert version == {"version": "0.7.0"}
    assert manifest["shared_root"] == "state"
    assert manifest["products"] == {
        "classic": {
            "component_lock": "component-lock.json",
            "frontend_protocol_lock": "frontend-protocol-lock.json",
            "root": ".",
        }
    }
    assert manifest["frontend_protocol_lock_sha256"] == _sha(frontend_lock.read_bytes())


def test_product_finalizer_rejects_reparse_runtime_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, backend = _releases(tmp_path)
    runtime_pack = backend / "foundation-runtime.chunk01.zip"
    frontend_protocol, frontend_lock = _frontend_release(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        accelerator="cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    product = tmp_path / "product" / "VibeOCR"
    product.mkdir(parents=True)
    (product / "VibeOCR.exe").write_bytes(b"app")
    path_type = type(runtime_pack)
    original_lstat = path_type.lstat

    def _lstat_with_reparse_point(path: Path) -> object:
        metadata = original_lstat(path)
        if path == runtime_pack:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
        return metadata

    monkeypatch.setattr(path_type, "lstat", _lstat_with_reparse_point)

    with pytest.raises(ValueError, match="regular non-reparse file"):
        finalize_product_release(
            product_root=product,
            frontend="classic",
            frontend_version="0.7.0",
            source_commit="a" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=frontend_lock,
            frontend_protocol_release_dir=frontend_protocol,
            protocol_release_dir=protocol,
            backend_release_dir=backend,
        )


def test_product_finalizer_rejects_unexpected_top_level_items(tmp_path: Path) -> None:
    protocol, backend = _releases(tmp_path)
    frontend_protocol, frontend_lock = _frontend_release(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        accelerator="cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    product = tmp_path / "product" / "VibeOCR"
    product.mkdir(parents=True)
    (product / "VibeOCR.exe").write_bytes(b"app")
    (product / "debug-notes.txt").write_text("not a product file", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected product root items"):
        finalize_product_release(
            product_root=product,
            frontend="classic",
            frontend_version="0.7.0",
            source_commit="a" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=frontend_lock,
            frontend_protocol_release_dir=frontend_protocol,
            protocol_release_dir=protocol,
            backend_release_dir=backend,
        )


def test_product_finalizer_accepts_equivalent_crlf_component_lock(
    tmp_path: Path,
) -> None:
    protocol, backend = _releases(tmp_path)
    frontend_protocol, frontend_lock = _frontend_release(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        accelerator="cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    component_lock.write_bytes(
        component_lock.read_text(encoding="utf-8").replace("\n", "\r\n").encode()
    )
    product = tmp_path / "product" / "VibeOCR"
    product.mkdir(parents=True)
    (product / "VibeOCR.exe").write_bytes(b"app")

    output = finalize_product_release(
        product_root=product,
        frontend="classic",
        frontend_version="0.7.0",
        source_commit="a" * 40,
        component_lock=component_lock,
        frontend_protocol_lock=frontend_lock,
        frontend_protocol_release_dir=frontend_protocol,
        protocol_release_dir=protocol,
        backend_release_dir=backend,
    )

    assert output.is_file()


def test_product_finalizer_rejects_interchanged_frontend_lock(tmp_path: Path) -> None:
    protocol, backend = _releases(tmp_path)
    frontend_protocol, _frontend_lock = _frontend_release(tmp_path)
    component_lock = tmp_path / "component-lock.json"
    bind_product_releases(
        protocol_release_dir=protocol,
        backend_release_dir=backend,
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        backend_version="0.7.0",
        accelerator="cpu",
        required_capabilities=("ocr.recognition.v2",),
        output=component_lock,
    )
    product = tmp_path / "product" / "VibeOCR"
    product.mkdir(parents=True)
    (product / "VibeOCR.exe").write_bytes(b"app")

    with pytest.raises(ValueError, match="frontend Protocol lock is incomplete"):
        finalize_product_release(
            product_root=product,
            frontend="classic",
            frontend_version="0.7.0",
            source_commit="a" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=component_lock,
            frontend_protocol_release_dir=frontend_protocol,
            protocol_release_dir=protocol,
            backend_release_dir=backend,
        )


def test_runtime_asset_names_accepts_pack_string_and_part_list() -> None:
    from scripts.finalize_product_release import _runtime_asset_names

    def _manifest(runtime_pack: object) -> dict[str, object]:
        return {
            "backend_wheel": "backend.whl",
            "protocol_manifest": "release-manifest.json",
            "protocol_wheel": "protocol.whl",
            "python": {"archive": "python.tar.gz"},
            "installer": {"archive": "installer.zip"},
            "profiles": {
                "win-x64-base": {"lock": "base.lock", "runtime_pack": runtime_pack}
            },
        }

    base_pack = "arbitrary-foundation-pack.zip"
    assert base_pack in _runtime_asset_names(_manifest(base_pack))
    base_parts = [
        "foundation.part01.zip",
        "foundation.part02.zip",
    ]
    assert _runtime_asset_names(_manifest(base_parts)) >= {
        *base_parts,
    }
    with pytest.raises(ValueError, match="base Runtime Pack"):
        _runtime_asset_names(_manifest(None))

    with pytest.raises(ValueError, match="runtime_pack"):
        _runtime_asset_names(_manifest(["pack.zip", 42]))
    with pytest.raises(ValueError, match="runtime_pack"):
        _runtime_asset_names(_manifest({"name": "pack.zip"}))


def test_runtime_asset_names_includes_install_scope_locks() -> None:
    from scripts.finalize_product_release import _runtime_asset_names

    manifest = {
        "backend_wheel": "backend.whl",
        "protocol_manifest": "release-manifest.json",
        "protocol_wheel": "protocol.whl",
        "python": {"archive": "python.tar.gz"},
        "installer": {"archive": "installer.zip"},
        "profiles": {
            "win-x64-base": {
                "lock": "requirements-win-x64-base.lock",
                "runtime_pack": "arbitrary-foundation-pack.zip",
            },
            "win-x64-cpu": {
                "lock": "requirements-win-x64-cpu.lock",
            },
            "win-x64-cu126": {
                "lock": "requirements-win-x64-cu126.lock",
                "runtime_pack": None,
                "install_scopes": [
                    {
                        "scope_id": "gpu-runtime",
                        "component_ids": ["gpu_runtime"],
                        "lock": "requirements-win-x64-cu126-gpu.lock",
                        "runtime_pack": ["gpu-pack.part01.zip"],
                    }
                ],
            },
        },
    }

    names = _runtime_asset_names(manifest)

    assert "requirements-win-x64-cu126.lock" in names
    assert "requirements-win-x64-cu126-gpu.lock" in names
    assert "gpu-pack.part01.zip" not in names

    broken = {
        **manifest,
        "profiles": {
            "win-x64-cu126": {
                "lock": "requirements-win-x64-cu126.lock",
                "install_scopes": {"lock": "not-a-list"},
            }
        },
    }
    with pytest.raises(ValueError, match="install_scopes"):
        _runtime_asset_names(broken)

    empty_pack_list = json.loads(json.dumps(manifest))
    empty_pack_list["profiles"]["win-x64-cu126"]["install_scopes"][0][
        "runtime_pack"
    ] = []
    with pytest.raises(ValueError, match="runtime_pack"):
        _runtime_asset_names(empty_pack_list)


@pytest.mark.parametrize(
    "field",
    [
        "backend_wheel",
        "protocol_manifest",
        "protocol_wheel",
        "python.archive",
        "installer.archive",
        "profile.lock",
        "profile.runtime_pack",
        "scope.lock",
        "scope.runtime_pack",
    ],
)
def test_runtime_asset_names_rejects_release_path_escape(field: str) -> None:
    from scripts.finalize_product_release import _runtime_asset_names

    manifest: dict[str, object] = {
        "backend_wheel": "backend.whl",
        "protocol_manifest": "release-manifest.json",
        "protocol_wheel": "protocol.whl",
        "python": {"archive": "python.tar.gz"},
        "installer": {"archive": "installer.zip"},
        "profiles": {
            "win-x64-base": {
                "lock": "base.lock",
                "runtime_pack": ["base-pack.zip"],
            },
            "win-x64-cpu": {
                "lock": "cpu.lock",
                "install_scopes": [
                    {
                        "lock": "scope.lock",
                        "runtime_pack": ["advanced-pack.zip"],
                    }
                ],
            },
        },
    }
    if "." in field:
        owner, name = field.split(".", 1)
        if owner in {"python", "installer"}:
            cast(dict[str, object], manifest[owner])[name] = "../outside.zip"
        elif owner == "profile":
            profiles = cast(dict[str, object], manifest["profiles"])
            cast(dict[str, object], profiles["win-x64-base"])[name] = "../outside.zip"
        else:
            profiles = cast(dict[str, object], manifest["profiles"])
            cpu_profile = cast(dict[str, object], profiles["win-x64-cpu"])
            scopes = cast(list[object], cpu_profile["install_scopes"])
            cast(dict[str, object], scopes[0])[name] = "../outside.zip"
    else:
        manifest[field] = "../outside.zip"

    with pytest.raises(ValueError, match="release file name"):
        _runtime_asset_names(manifest)


@pytest.mark.parametrize(
    "name",
    ["pack.zip:stream", "CON", "pack.zip.", "nested\\pack.zip", "pack?.zip"],
)
def test_runtime_asset_names_rejects_nonportable_windows_file_name(name: str) -> None:
    from scripts.finalize_product_release import _runtime_asset_names

    manifest = {
        "backend_wheel": "backend.whl",
        "protocol_manifest": "release-manifest.json",
        "protocol_wheel": "protocol.whl",
        "python": {"archive": "python.tar.gz"},
        "installer": {"archive": "installer.zip"},
        "profiles": {
            "win-x64-base": {"lock": "base.lock", "runtime_pack": [name]},
        },
    }

    with pytest.raises(ValueError, match="release file name"):
        _runtime_asset_names(manifest)
