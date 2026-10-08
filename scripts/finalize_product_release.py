"""Finalize the fused Velopack pack directory（融合形态）。

后端已内置在本仓库：发布绑定不再从外部 Release 拷贝运行时资产，改为：

- 嵌入两把发布身份锁（由 ``generate_workspace_locks.py`` 生成）；
- 拷贝随包分发的 ``vibeocr-backend`` wheel（供引擎环境安装使用）与
  ``uv.exe``（供引擎环境管理使用）到 ``backend/`` 与 ``bin/``；
- 写 ``product-release-manifest.json``（``shared_root: state``）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

PROHIBITED_ROOTS = {".git", "apps", "contracts", "packages", "supervisor", "tests"}
EXPECTED_PRODUCT_ROOTS = {
    "_internal",
    "frontend-protocol-lock.json",
    "LICENSE",
    "VibeOCR.exe",
    "backend",
    "bin",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def finalize_product_release(
    *,
    product_root: Path,
    frontend: str,
    frontend_version: str,
    source_commit: str,
    component_lock: Path,
    frontend_protocol_lock: Path,
    backend_wheel: Path,
    uv_binary: Path,
) -> Path:
    product_root = product_root.resolve(strict=True)
    if not product_root.is_dir():
        raise ValueError("product_root must be a directory")
    prohibited = sorted(
        child.name
        for child in product_root.iterdir()
        if child.name.lower() in PROHIBITED_ROOTS
    )
    if prohibited:
        raise ValueError(f"prohibited source roots in product layout: {prohibited}")
    unexpected = sorted(
        child.name
        for child in product_root.iterdir()
        if child.name not in EXPECTED_PRODUCT_ROOTS
    )
    if unexpected:
        raise ValueError(f"unexpected product root items: {unexpected}")

    lock_path = component_lock.resolve(strict=True)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    frontend_lock_path = frontend_protocol_lock.resolve(strict=True)
    frontend_lock = json.loads(frontend_lock_path.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, dict)
        or not isinstance(lock.get("protocol"), dict)
        or not isinstance(lock["protocol"].get("version"), str)
        or not isinstance(lock.get("backend"), dict)
        or not isinstance(lock["backend"].get("version"), str)
        or not isinstance(lock.get("required_capabilities"), list)
    ):
        raise ValueError("component lock is incomplete")
    if (
        not isinstance(frontend_lock, dict)
        or not isinstance(frontend_lock.get("repository"), str)
        or not isinstance(frontend_lock.get("version"), str)
        or not isinstance(frontend_lock.get("artifacts"), dict)
        or not frontend_lock["artifacts"]
    ):
        raise ValueError("frontend Protocol lock is incomplete")

    backend_wheel = backend_wheel.resolve(strict=True)
    if not backend_wheel.is_file() or backend_wheel.suffix != ".whl":
        raise ValueError(f"backend wheel is required: {backend_wheel}")
    if not uv_binary.is_file():
        raise ValueError(f"uv binary is required: {uv_binary}")
    uv_binary = uv_binary.resolve(strict=True)

    embedded_lock = product_root / "component-lock.json"
    shutil.copyfile(lock_path, embedded_lock)
    embedded_frontend_lock = product_root / "frontend-protocol-lock.json"
    shutil.copyfile(frontend_lock_path, embedded_frontend_lock)
    (product_root / "version.json").write_text(
        _canonical_json({"version": frontend_version}),
        encoding="utf-8",
        newline="\n",
    )

    backend_output = product_root / "backend"
    if backend_output.exists():
        raise ValueError("product layout already contains a backend directory")
    backend_output.mkdir()
    shutil.copyfile(backend_wheel, backend_output / backend_wheel.name)

    bin_output = product_root / "bin"
    if bin_output.exists():
        raise ValueError("product layout already contains a bin directory")
    bin_output.mkdir()
    shutil.copyfile(uv_binary, bin_output / uv_binary.name)

    manifest_path = product_root / "product-release-manifest.json"
    files = sorted(
        path
        for path in product_root.rglob("*")
        if path.is_file() and path != manifest_path
    )
    manifest_path.write_text(
        _canonical_json(
            {
                "schema_version": 1,
                "frontend": frontend,
                "frontend_version": frontend_version,
                "source_commit": source_commit,
                "component_lock_sha256": _sha256(embedded_lock),
                "frontend_protocol_lock_sha256": _sha256(embedded_frontend_lock),
                "shared_root": "state",
                "products": {
                    frontend: {
                        "root": ".",
                        "component_lock": "component-lock.json",
                        "frontend_protocol_lock": "frontend-protocol-lock.json",
                    }
                },
                "files": {
                    path.relative_to(product_root).as_posix(): {
                        "sha256": _sha256(path),
                        "size": path.stat().st_size,
                    }
                    for path in files
                },
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--frontend", required=True)
    parser.add_argument("--frontend-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--component-lock", type=Path, required=True)
    parser.add_argument("--frontend-protocol-lock", type=Path, required=True)
    parser.add_argument("--backend-wheel", type=Path, required=True)
    parser.add_argument("--uv-binary", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        finalize_product_release(
            product_root=args.product_root,
            frontend=args.frontend,
            frontend_version=args.frontend_version,
            source_commit=args.source_commit,
            component_lock=args.component_lock,
            frontend_protocol_lock=args.frontend_protocol_lock,
            backend_wheel=args.backend_wheel,
            uv_binary=args.uv_binary,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
