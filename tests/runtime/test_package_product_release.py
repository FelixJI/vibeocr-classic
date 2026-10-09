"""finalize_product_release（融合形态）测试：发布绑定与产品清单。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.finalize_product_release import finalize_product_release
from scripts.generate_workspace_locks import generate


@pytest.fixture()
def product_root(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "keep.bin").write_bytes(b"payload")
    (root / "VibeOCR.exe").write_bytes(b"MZ-fake-exe")
    (root / "LICENSE").write_text("license", encoding="utf-8")
    return root


@pytest.fixture()
def locks(tmp_path: Path) -> tuple[Path, Path]:
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "backend": {
                    "channel": "workspace",
                    "accelerator": "cpu",
                    "repository": "FelixJI/vibeocr-classic",
                },
                "protocol": {
                    "repository": "FelixJI/vibeocr-protocol",
                    "sdk_version": "2.9.0",
                    "version": "2.0.0",
                },
                "required_capabilities": ["ocr.recognition.v2"],
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    backend_wheel = tmp_path / "vibeocr_backend-0.15.1-py3-none-any.whl"
    backend_wheel.write_bytes(b"fake-wheel-bytes")
    return generate(
        output_dir=tmp_path / "locks",
        backend_wheel=backend_wheel,
        backend_version="0.15.1",
        policy_path=policy,
    )


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    backend_wheel = tmp_path / "vibeocr_backend-0.15.1-py3-none-any.whl"
    backend_wheel.write_bytes(b"fake-wheel-bytes")
    uv_binary = tmp_path / "uv.exe"
    uv_binary.write_bytes(b"fake-uv-exe")
    return backend_wheel, uv_binary


def test_finalize_binds_backend_wheel_uv_and_locks(
    tmp_path: Path, product_root: Path, locks: tuple[Path, Path]
) -> None:
    component_lock, frontend_lock = locks
    backend_wheel, uv_binary = _artifacts(tmp_path)

    manifest_path = finalize_product_release(
        product_root=product_root,
        frontend="classic",
        frontend_version="0.11.1",
        source_commit="a" * 40,
        component_lock=component_lock,
        frontend_protocol_lock=frontend_lock,
        backend_wheel=backend_wheel,
        uv_binary=uv_binary,
    )

    assert manifest_path.name == "product-release-manifest.json"
    assert (product_root / "backend" / backend_wheel.name).read_bytes() == (
        b"fake-wheel-bytes"
    )
    assert (product_root / "bin" / "uv.exe").read_bytes() == b"fake-uv-exe"
    assert (product_root / "component-lock.json").is_file()
    assert (product_root / "frontend-protocol-lock.json").is_file()
    assert (product_root / "version.json").is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["frontend"] == "classic"
    assert manifest["frontend_version"] == "0.11.1"
    assert manifest["shared_root"] == "state"
    assert manifest["products"]["classic"]["component_lock"] == "component-lock.json"
    # 文件闭包覆盖新增的 backend/bin 内容。
    relative_files = set(manifest["files"])
    assert f"backend/{backend_wheel.name}" in relative_files
    assert "bin/uv.exe" in relative_files
    assert "_internal/keep.bin" in relative_files

    lock = json.loads((product_root / "component-lock.json").read_text(encoding="utf-8"))
    assert lock["backend"]["version"] == "0.15.1"
    assert lock["backend"]["artifact_sha256"]
    assert lock["protocol"]["version"].startswith("2.")


def test_finalize_rejects_incomplete_component_lock(
    tmp_path: Path, product_root: Path
) -> None:
    component_lock = tmp_path / "component-lock.json"
    component_lock.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    frontend_lock = tmp_path / "frontend-protocol-lock.json"
    frontend_lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "FelixJI/vibeocr-protocol",
                "version": "2.9.0",
                "artifacts": {"a.whl": {"url": "u", "sha256": "s"}},
            }
        ),
        encoding="utf-8",
    )
    backend_wheel, uv_binary = _artifacts(tmp_path)

    with pytest.raises(ValueError, match="component lock is incomplete"):
        finalize_product_release(
            product_root=product_root,
            frontend="classic",
            frontend_version="0.11.1",
            source_commit="b" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=frontend_lock,
            backend_wheel=backend_wheel,
            uv_binary=uv_binary,
        )


def test_finalize_rejects_missing_uv_binary(
    tmp_path: Path, product_root: Path, locks: tuple[Path, Path]
) -> None:
    component_lock, frontend_lock = locks
    backend_wheel, _uv_binary = _artifacts(tmp_path)
    missing_uv = tmp_path / "missing-uv.exe"

    with pytest.raises(ValueError, match="uv binary is required"):
        finalize_product_release(
            product_root=product_root,
            frontend="classic",
            frontend_version="0.11.1",
            source_commit="c" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=frontend_lock,
            backend_wheel=backend_wheel,
            uv_binary=missing_uv,
        )


def test_finalize_rejects_unexpected_product_entries(
    tmp_path: Path, product_root: Path, locks: tuple[Path, Path]
) -> None:
    (product_root / "stray.txt").write_text("x", encoding="utf-8")
    component_lock, frontend_lock = locks
    backend_wheel, uv_binary = _artifacts(tmp_path)

    with pytest.raises(ValueError, match="unexpected product root items"):
        finalize_product_release(
            product_root=product_root,
            frontend="classic",
            frontend_version="0.11.1",
            source_commit="d" * 40,
            component_lock=component_lock,
            frontend_protocol_lock=frontend_lock,
            backend_wheel=backend_wheel,
            uv_binary=uv_binary,
        )
