"""Verify the release assets emitted by the pinned Velopack packaging seam."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_velopack_release(
    root: Path,
    version: str,
    *,
    portable_name: str = "VibeOCRClassic-win-Portable.zip",
) -> tuple[Path, ...]:
    """Return the exact publishable set after validating the pinned vpk output.

    Portable-only delivery: users get the Portable zip, machines get the
    NUPKG/feed for Velopack self-update. The build uses ``--noInst`` so a
    redundant Setup executable is not generated.
    """
    root = root.resolve(strict=True)
    package = root / f"VibeOCRClassic-{version}-full.nupkg"
    portable = root / portable_name
    feed = root / "releases.win.json"
    publishable = (package, portable, feed)
    missing = [path.name for path in publishable if not path.is_file()]
    if missing:
        raise RuntimeError(f"Velopack output is missing: {', '.join(missing)}")

    try:
        document = json.loads(feed.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Velopack releases.win.json is invalid") from error
    assets = document.get("Assets") if isinstance(document, dict) else None
    if not isinstance(assets, list) or len(assets) != 1:
        raise RuntimeError("Velopack feed must contain exactly one full asset")
    asset = assets[0]
    if not isinstance(asset, dict):
        raise RuntimeError("Velopack feed asset must be an object")
    expected = {
        "PackageId": "VibeOCRClassic",
        "Version": version,
        "Type": "Full",
        "FileName": package.name,
        "SHA1": _digest(package, "sha1"),
        "SHA256": _digest(package, "sha256"),
        "Size": package.stat().st_size,
    }
    for field, value in expected.items():
        if asset.get(field) != value:
            raise RuntimeError(f"Velopack feed {field} does not match full package")
    return publishable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--portable-name",
        default="VibeOCRClassic-win-Portable.zip",
        help="Portable ZIP name (raw Velopack output by default)",
    )
    args = parser.parse_args(argv)
    for path in verify_velopack_release(
        args.root,
        args.version,
        portable_name=args.portable_name,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
