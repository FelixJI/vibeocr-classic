"""Verify resolved Backend/Protocol releases and their attestations for CI."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from .bind_component_releases import bind_protocol_release
else:
    from bind_component_releases import bind_protocol_release


def verify(release_input: Path) -> dict[str, object]:
    root = release_input.resolve(strict=True)
    lock = json.loads((root / "component-lock.json").read_text(encoding="utf-8"))
    for component in ("protocol", "backend"):
        identity = lock.get(component)
        if not isinstance(identity, dict):
            raise ValueError(f"component lock is missing {component}")
        repository = identity.get("repository")
        version = identity.get("version")
        if not isinstance(repository, str) or not isinstance(version, str):
            raise ValueError(f"invalid {component} identity")
        directory = root / component
        if not directory.is_dir():
            raise ValueError(f"missing resolved {component} release")
        for asset in directory.iterdir():
            if asset.is_file() and asset.name != "SHA256SUMS":
                subprocess.run(
                    ["gh", "attestation", "verify", str(asset), "--repo", repository],
                    check=True,
                )
    frontend_lock_path = root / "frontend-protocol-lock.json"
    frontend_lock = json.loads(frontend_lock_path.read_text(encoding="utf-8"))
    if not isinstance(frontend_lock, dict):
        raise ValueError("invalid frontend Protocol lock")
    repository = frontend_lock.get("repository")
    version = frontend_lock.get("version")
    if not isinstance(repository, str) or not isinstance(version, str):
        raise ValueError("invalid frontend Protocol identity")
    runtime_protocol = lock.get("protocol")
    runtime_version = (
        runtime_protocol.get("version") if isinstance(runtime_protocol, dict) else None
    )
    if (
        not isinstance(runtime_version, str)
        or version.split(".", 1)[0] != runtime_version.split(".", 1)[0]
    ):
        raise ValueError("frontend and Runtime Protocol majors differ")
    sdk_directory = root / "protocol-sdk"
    if not sdk_directory.is_dir():
        raise ValueError("missing frontend Protocol release")
    for asset in sdk_directory.iterdir():
        if asset.is_file() and asset.name != "SHA256SUMS":
            subprocess.run(
                ["gh", "attestation", "verify", str(asset), "--repo", repository],
                check=True,
            )
    with tempfile.TemporaryDirectory(prefix="vibeocr-frontend-protocol-lock-") as temp:
        generated = Path(temp) / frontend_lock_path.name
        bind_protocol_release(
            release_dir=sdk_directory,
            repository=repository,
            version=version,
            output=generated,
        )
        if json.loads(generated.read_text(encoding="utf-8")) != frontend_lock:
            raise ValueError("frontend Protocol lock differs from verified release")
    lock["frontend_protocol"] = frontend_lock
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-input", type=Path, required=True)
    args = parser.parse_args(argv)
    lock = verify(args.release_input)
    print(
        f"Verified Backend v{lock['backend']['version']} bound to "
        f"Protocol v{lock['protocol']['version']} with frontend SDK "
        f"v{lock['frontend_protocol']['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
