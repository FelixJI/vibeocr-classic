"""Install verified Classic CI wheels without editable or path development modes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def install(release_input: Path) -> None:
    root = release_input.resolve(strict=True)
    wheels = sorted((root / "protocol").glob("*.whl")) + sorted(
        (root / "backend").glob("vibeocr_backend-*.whl")
    )
    if not wheels:
        raise ValueError("resolved release input contains no wheels")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", *(str(path) for path in wheels)],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "pytest",
            "pytest-asyncio",
            "pytest-qt",
            "./apps/vibeocr-pyside",
        ],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-input", type=Path, required=True)
    args = parser.parse_args(argv)
    install(args.release_input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
