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
    delta = root / f"VibeOCRClassic-{version}-delta.nupkg"
    portable = root / portable_name
    feed = root / "releases.win.json"
    publishable = (package, *((delta,) if delta.is_file() else ()), portable, feed)
    missing = [path.name for path in publishable if not path.is_file()]
    if missing:
        raise RuntimeError(f"Velopack output is missing: {', '.join(missing)}")

    try:
        document = json.loads(feed.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Velopack releases.win.json is invalid") from error
    assets = document.get("Assets") if isinstance(document, dict) else None
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("Velopack feed must contain release assets")
    if not all(isinstance(asset, dict) for asset in assets):
        raise RuntimeError("Velopack feed assets must be objects")
    current = [
        asset
        for asset in assets
        if asset.get("PackageId") == "VibeOCRClassic"
        and asset.get("Version") == version
    ]
    full_assets = [asset for asset in current if asset.get("Type") == "Full"]
    delta_assets = [asset for asset in current if asset.get("Type") == "Delta"]
    if len(full_assets) != 1 or len(delta_assets) > 1:
        raise RuntimeError(
            "Velopack feed must contain one current full and at most one delta"
        )
    if len(current) != len(full_assets) + len(delta_assets):
        raise RuntimeError("Velopack feed contains an unsupported current asset type")
    historical = [asset for asset in assets if asset not in current]
    if historical:
        raise RuntimeError("published Velopack feed must not contain historical assets")
    asset = full_assets[0]
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
    if bool(delta_assets) != delta.is_file():
        raise RuntimeError("Velopack feed and current delta package disagree")
    if delta_assets:
        delta_asset = delta_assets[0]
        delta_expected = {
            "PackageId": "VibeOCRClassic",
            "Version": version,
            "Type": "Delta",
            "FileName": delta.name,
            "SHA1": _digest(delta, "sha1"),
            "SHA256": _digest(delta, "sha256"),
            "Size": delta.stat().st_size,
        }
        for field, value in delta_expected.items():
            if delta_asset.get(field) != value:
                raise RuntimeError(
                    f"Velopack feed {field} does not match delta package"
                )
    # NotesMarkdown 是客户端"发现新版本"弹窗更新日志的唯一来源；缺失即视为
    # 打包契约破损（vpk pack 未传 --notesFile），fail closed 而不是发布空日志。
    notes = asset.get("NotesMarkdown")
    if not isinstance(notes, str) or not notes.strip():
        raise RuntimeError("Velopack feed full asset must embed release notes")
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
