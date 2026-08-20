"""Copy the verified frozen closure into a clean, immutable VPK input tree."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def prepare_velopack_input(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not source.is_dir() or not (source / "VibeOCR.exe").is_file():
        raise RuntimeError("verified PyInstaller product root is invalid")
    if not (source / "product-release-manifest.json").is_file():
        raise RuntimeError("verified product binding is missing")
    if destination.exists():
        raise RuntimeError("Velopack input destination must not already exist")
    if destination == source or source in destination.parents:
        raise RuntimeError("Velopack input destination must be outside the source")

    def ignore_ephemeral(directory: str, names: list[str]) -> set[str]:
        if Path(directory).resolve() != source:
            return set()
        return {
            name
            for name in names
            if name == "state"
            or name == ".smoke-runtime"
            or name.startswith(".smoke-data-")
        }

    shutil.copytree(source, destination, ignore=ignore_ephemeral)
    prohibited = [
        child.name
        for child in destination.iterdir()
        if child.name == "state"
        or child.name == ".smoke-runtime"
        or child.name.startswith(".smoke-data-")
    ]
    if prohibited:
        raise RuntimeError(f"Velopack input contains mutable smoke state: {prohibited}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare_velopack_input(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
