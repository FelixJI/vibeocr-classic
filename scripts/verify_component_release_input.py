"""Verify resolved Backend/Protocol releases and their attestations for CI."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


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
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-input", type=Path, required=True)
    args = parser.parse_args(argv)
    lock = verify(args.release_input)
    print(
        f"Verified Backend v{lock['backend']['version']} bound to "
        f"Protocol v{lock['protocol']['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
