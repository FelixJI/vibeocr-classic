"""Bind verified Runtime assets and finalize the Velopack pack directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

if __package__:
    from .bind_component_releases import bind_product_releases, bind_protocol_release
else:
    from bind_component_releases import bind_product_releases, bind_protocol_release

PROHIBITED_ROOTS = {".git", "apps", "contracts", "packages", "supervisor", "tests"}
EXPECTED_PRODUCT_ROOTS = {
    "_internal",
    "frontend-protocol-lock.json",
    "LICENSE",
    "VibeOCR.exe",
}


def _runtime_pack_names(record: dict[str, object]) -> set[str]:
    """Backend 0.12 起 runtime_pack 是分片列表；兼容旧版的单字符串/缺省。"""

    names: set[str] = set()
    runtime_pack = record.get("runtime_pack")
    if runtime_pack is None:
        return names
    if isinstance(runtime_pack, str):
        parts: list[str] = [runtime_pack]
    elif isinstance(runtime_pack, list) and all(
        isinstance(part, str) and part for part in runtime_pack
    ):
        parts = list(runtime_pack)
    else:
        raise ValueError("Backend runtime_pack must be a file name or name list")
    names.update(parts)
    return names


def _runtime_asset_names(manifest: dict[str, object]) -> set[str]:
    names = {
        "runtime-manifest.json",
        str(manifest["backend_wheel"]),
        str(manifest["protocol_manifest"]),
        str(manifest["protocol_wheel"]),
    }
    for field in ("python", "installer"):
        record = manifest[field]
        if not isinstance(record, dict):
            raise ValueError(f"Backend runtime manifest {field} must be an object")
        names.add(str(record["archive"]))
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Backend runtime manifest profiles must be an object")
    for record in profiles.values():
        if not isinstance(record, dict):
            raise ValueError("Backend runtime profile must be an object")
        names.add(str(record["lock"]))
        names.update(_runtime_pack_names(record))
        # Backend 0.12 起 profile 可声明 install_scopes；每个 scope 自带
        # 精确 lock（如 cu126 的 gpu-runtime 闭包），缺文件会让绑定
        # Installer 的 inspect 失败。
        scopes = record.get("install_scopes")
        if scopes is None:
            continue
        if not isinstance(scopes, list):
            raise ValueError("Backend runtime install_scopes must be a list")
        for scope in scopes:
            if not isinstance(scope, dict):
                raise ValueError("Backend runtime install scope must be an object")
            names.add(str(scope["lock"]))
            names.update(_runtime_pack_names(scope))
    return names


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
    frontend_protocol_release_dir: Path,
    protocol_release_dir: Path,
    backend_release_dir: Path,
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
        not isinstance(frontend_lock, dict)
        or not isinstance(frontend_lock.get("repository"), str)
        or not isinstance(frontend_lock.get("version"), str)
        or not isinstance(frontend_lock.get("artifacts"), dict)
    ):
        raise ValueError("frontend Protocol lock is incomplete")
    protocol = lock["protocol"]
    backend = lock["backend"]
    required_capabilities = tuple(lock["required_capabilities"])
    with tempfile.TemporaryDirectory(prefix="vibeocr-component-lock-") as temp:
        generated = Path(temp) / "component-lock.json"
        bind_product_releases(
            protocol_release_dir=protocol_release_dir,
            backend_release_dir=backend_release_dir,
            protocol_repository=str(protocol["repository"]),
            protocol_version=str(protocol["version"]),
            backend_repository=str(backend["repository"]),
            backend_version=str(backend["version"]),
            accelerator=str(backend["accelerator"]),
            required_capabilities=required_capabilities,
            output=generated,
        )
        if json.loads(generated.read_text(encoding="utf-8")) != lock:
            raise ValueError("committed component lock differs from verified releases")
        frontend_generated = Path(temp) / "frontend-protocol-lock.json"
        bind_protocol_release(
            release_dir=frontend_protocol_release_dir,
            repository=str(frontend_lock["repository"]),
            version=str(frontend_lock["version"]),
            output=frontend_generated,
        )
        if json.loads(frontend_generated.read_text(encoding="utf-8")) != frontend_lock:
            raise ValueError(
                "committed frontend Protocol lock differs from verified release"
            )

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
    runtime_manifest = json.loads(
        (backend_release_dir / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    for name in sorted(_runtime_asset_names(runtime_manifest)):
        source = backend_release_dir.resolve(strict=True) / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copyfile(source, backend_output / name)

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
    parser.add_argument("--frontend-protocol-release-dir", type=Path, required=True)
    parser.add_argument("--protocol-release-dir", type=Path, required=True)
    parser.add_argument("--backend-release-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        finalize_product_release(
            product_root=args.product_root,
            frontend=args.frontend,
            frontend_version=args.frontend_version,
            source_commit=args.source_commit,
            component_lock=args.component_lock,
            frontend_protocol_lock=args.frontend_protocol_lock,
            frontend_protocol_release_dir=args.frontend_protocol_release_dir,
            protocol_release_dir=args.protocol_release_dir,
            backend_release_dir=args.backend_release_dir,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
