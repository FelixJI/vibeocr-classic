"""Bind verified Runtime assets and finalize the Velopack pack directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import shutil
import stat
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


def _release_file_name(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or ntpath.basename(value) != value
        or ntpath.isreserved(value)
    ):
        raise ValueError(
            f"Backend runtime manifest {field} must be a release file name"
        )
    return value


def _runtime_pack_names(record: dict[str, object], *, field: str) -> set[str]:
    """Backend 0.12 起 runtime_pack 是分片列表；兼容旧版的单字符串/缺省。"""

    names: set[str] = set()
    runtime_pack = record.get("runtime_pack")
    if runtime_pack is None:
        return names
    if isinstance(runtime_pack, str):
        parts: list[object] = [runtime_pack]
    elif isinstance(runtime_pack, list) and runtime_pack:
        parts = runtime_pack
    else:
        raise ValueError("Backend runtime_pack must be a file name or name list")
    names.update(
        _release_file_name(part, f"{field}[{index}]")
        for index, part in enumerate(parts)
    )
    return names


def _runtime_asset_names(manifest: dict[str, object]) -> set[str]:
    names = {
        "runtime-manifest.json",
        _release_file_name(manifest["backend_wheel"], "backend_wheel"),
        _release_file_name(manifest["protocol_manifest"], "protocol_manifest"),
        _release_file_name(manifest["protocol_wheel"], "protocol_wheel"),
    }
    for field in ("python", "installer"):
        record = manifest[field]
        if not isinstance(record, dict):
            raise ValueError(f"Backend runtime manifest {field} must be an object")
        names.add(_release_file_name(record["archive"], f"{field}.archive"))
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Backend runtime manifest profiles must be an object")
    base_packs: set[str] = set()
    for profile_id, record in profiles.items():
        if not isinstance(profile_id, str) or not isinstance(record, dict):
            raise ValueError("Backend runtime profile must be an object")
        names.add(_release_file_name(record["lock"], f"profiles.{profile_id}.lock"))
        packs = _runtime_pack_names(record, field=f"profiles.{profile_id}.runtime_pack")
        if profile_id == "win-x64-base":
            base_packs.update(packs)
        # Backend 0.12 起 profile 可声明 install_scopes；每个 scope 自带
        # 精确 lock（如 cu126 的 gpu-runtime 闭包），缺文件会让绑定
        # Installer 的 inspect 失败。
        scopes = record.get("install_scopes")
        if scopes is None:
            continue
        if not isinstance(scopes, list):
            raise ValueError("Backend runtime install_scopes must be a list")
        for scope_index, scope in enumerate(scopes):
            if not isinstance(scope, dict):
                raise ValueError("Backend runtime install scope must be an object")
            scope_field = f"profiles.{profile_id}.install_scopes[{scope_index}]"
            names.add(
                _release_file_name(
                    scope["lock"],
                    f"{scope_field}.lock",
                )
            )
            _runtime_pack_names(
                scope,
                field=f"{scope_field}.runtime_pack",
            )  # validate advanced pack declarations
    if "win-x64-base" not in profiles or not base_packs:
        raise ValueError("Backend runtime manifest is missing the base Runtime Pack")
    names.update(base_packs)
    return names


def _require_regular_release_file(release_root: Path, name: str) -> Path:
    source = release_root / name
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise FileNotFoundError(source) from exc
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if not stat.S_ISREG(metadata.st_mode) or file_attributes & reparse_point:
        raise ValueError(
            "Backend runtime release entry must be a regular non-reparse file: "
            f"{source}"
        )
    resolved = source.resolve(strict=True)
    if resolved.parent != release_root:
        raise ValueError(f"Backend runtime release entry escapes its root: {source}")
    return source


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
    backend_release_root = backend_release_dir.resolve(strict=True)
    runtime_manifest_path = _require_regular_release_file(
        backend_release_root, "runtime-manifest.json"
    )
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    for name in sorted(_runtime_asset_names(runtime_manifest)):
        source = _require_regular_release_file(backend_release_root, name)
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
