"""Normalize a vpk output feed to the assets published in the current Release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalize_feed(
    feed_path: Path,
    *,
    pack_id: str,
    target_version: str,
    expected_base_version: str | None,
) -> None:
    try:
        document = json.loads(feed_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Velopack feed is invalid") from error
    assets = document.get("Assets") if isinstance(document, dict) else None
    if not isinstance(assets, list) or not all(
        isinstance(asset, dict) for asset in assets
    ):
        raise RuntimeError("Velopack feed assets are invalid")
    current = [
        asset
        for asset in assets
        if asset.get("PackageId") == pack_id and asset.get("Version") == target_version
    ]
    full = [asset for asset in current if asset.get("Type") == "Full"]
    delta = [asset for asset in current if asset.get("Type") == "Delta"]
    if len(full) != 1 or len(delta) != (1 if expected_base_version else 0):
        raise RuntimeError("Velopack feed current asset set is invalid")
    if len(current) != len(full) + len(delta):
        raise RuntimeError("Velopack feed contains an unsupported current asset type")
    historical = [asset for asset in assets if asset not in current]
    if expected_base_version is None:
        if historical:
            raise RuntimeError("full-only Velopack feed contains historical assets")
    else:
        expected_name = f"{pack_id}-{expected_base_version}-full.nupkg"
        if len(historical) != 1 or any(
            (
                asset.get("PackageId") != pack_id
                or asset.get("Version") != expected_base_version
                or asset.get("Type") != "Full"
                or asset.get("FileName") != expected_name
            )
            for asset in historical
        ):
            raise RuntimeError("Velopack feed delta base is invalid")
    document["Assets"] = current
    feed_path.write_text(
        json.dumps(document, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--expected-base-version")
    args = parser.parse_args()
    normalize_feed(
        args.feed.resolve(strict=True),
        pack_id=args.pack_id,
        target_version=args.target_version,
        expected_base_version=args.expected_base_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
