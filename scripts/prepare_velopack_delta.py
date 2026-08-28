"""Prepare the latest stable full NUPKG as a one-hop Velopack delta base."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

_STABLE_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    size: int
    digest: str


@dataclass(frozen=True, slots=True)
class StableRelease:
    tag_name: str
    is_draft: bool
    is_prerelease: bool
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True, slots=True)
class DeltaBasePlan:
    delta_mode: str
    base_version: str | None
    base_package: str | None
    reason: str


class ReleaseClient(Protocol):
    def latest_release(self, repository: str) -> StableRelease: ...

    def previous_stable_release(
        self, repository: str, before_version: tuple[int, int, int]
    ) -> StableRelease | None: ...

    def download_asset(
        self, repository: str, tag: str, asset_name: str, destination: Path
    ) -> None: ...


class GhReleaseClient:
    """Small GitHub CLI adapter; planning and verification stay testable."""

    def latest_release(self, repository: str) -> StableRelease:
        return self._view_release(repository, tag=None)

    def previous_stable_release(
        self, repository: str, before_version: tuple[int, int, int]
    ) -> StableRelease | None:
        completed = subprocess.run(
            [
                "gh",
                "release",
                "list",
                "--repo",
                repository,
                "--limit",
                "100",
                "--json",
                "tagName,isDraft,isPrerelease",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            releases = json.loads(completed.stdout)
            versions = [
                (_parse_stable_version(item["tagName"]), item["tagName"])
                for item in releases
                if not item["isDraft"] and not item["isPrerelease"]
            ]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("GitHub Release list metadata is invalid") from error
        eligible = [
            (version, tag) for version, tag in versions if version < before_version
        ]
        if not eligible:
            return None
        return self._view_release(repository, tag=max(eligible)[1])

    def _view_release(self, repository: str, tag: str | None) -> StableRelease:
        command = ["gh", "release", "view"]
        if tag is not None:
            command.append(tag)
        command.extend(
            [
                "--repo",
                repository,
                "--json",
                "tagName,isDraft,isPrerelease,assets",
            ]
        )
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            document = json.loads(completed.stdout)
            assets = tuple(
                ReleaseAsset(
                    name=asset["name"],
                    size=asset["size"],
                    digest=asset["digest"],
                )
                for asset in document["assets"]
            )
            return StableRelease(
                tag_name=document["tagName"],
                is_draft=document["isDraft"],
                is_prerelease=document["isPrerelease"],
                assets=assets,
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("latest GitHub Release metadata is invalid") from error

    def download_asset(
        self, repository: str, tag: str, asset_name: str, destination: Path
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--pattern",
                asset_name,
                "--dir",
                str(destination.parent),
                "--clobber",
            ],
            check=True,
        )
        if not destination.is_file():
            raise RuntimeError(f"GitHub Release asset was not downloaded: {asset_name}")


def _parse_stable_version(value: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION.fullmatch(value)
    if match is None:
        raise RuntimeError(f"stable release version is invalid: {value}")
    return tuple(int(part) for part in match.groups())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_delta_base(
    client: ReleaseClient,
    *,
    repository: str,
    pack_id: str,
    target_version: str,
    output_dir: Path,
    reproduce_published_delta: bool = False,
) -> DeltaBasePlan:
    """Download and verify the only eligible one-hop base, or choose full-only."""

    target = _parse_stable_version(target_version)
    release = client.latest_release(repository)
    if release.is_draft or release.is_prerelease:
        raise RuntimeError("latest GitHub Release must be stable")
    latest = _parse_stable_version(release.tag_name)
    if target < latest:
        return DeltaBasePlan(
            delta_mode="None",
            base_version=None,
            base_package=None,
            reason="target version is not newer than the latest stable release",
        )

    base_release = release
    if target == latest:
        if not reproduce_published_delta:
            return DeltaBasePlan(
                delta_mode="None",
                base_version=None,
                base_package=None,
                reason="published target reproduction was not requested",
            )
        target_version_value = ".".join(str(part) for part in target)
        published_delta = f"{pack_id}-{target_version_value}-delta.nupkg"
        matches = [asset for asset in release.assets if asset.name == published_delta]
        if not matches:
            return DeltaBasePlan(
                delta_mode="None",
                base_version=None,
                base_package=None,
                reason="published target has no delta to reproduce",
            )
        if len(matches) != 1:
            raise RuntimeError("published target contains ambiguous delta assets")
        previous = client.previous_stable_release(repository, target)
        if previous is None or previous.is_draft or previous.is_prerelease:
            raise RuntimeError("published delta has no stable predecessor")
        base_release = previous

    base = _parse_stable_version(base_release.tag_name)
    latest_version = ".".join(str(part) for part in base)

    asset_name = f"{pack_id}-{latest_version}-full.nupkg"
    matches = [asset for asset in base_release.assets if asset.name == asset_name]
    if len(matches) != 1:
        raise RuntimeError(
            f"latest stable release must contain exactly one delta base: {asset_name}"
        )
    asset = matches[0]
    digest_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", asset.digest)
    if asset.size <= 0 or digest_match is None:
        raise RuntimeError(f"delta base metadata is invalid: {asset_name}")

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / asset_name
    try:
        client.download_asset(
            repository,
            base_release.tag_name,
            asset_name,
            destination,
        )
        if destination.stat().st_size != asset.size:
            raise RuntimeError(f"delta base size mismatch: {asset_name}")
        if _sha256(destination).casefold() != digest_match.group(1).casefold():
            raise RuntimeError(f"delta base SHA-256 mismatch: {asset_name}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return DeltaBasePlan(
        delta_mode="BestSpeed",
        base_version=latest_version,
        base_package=asset_name,
        reason="latest stable full package verified as the one-hop delta base",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--reproduce-published-delta", action="store_true")
    args = parser.parse_args(argv)
    plan = prepare_delta_base(
        GhReleaseClient(),
        repository=args.repository,
        pack_id=args.pack_id,
        target_version=args.target_version,
        output_dir=args.output_dir,
        reproduce_published_delta=args.reproduce_published_delta,
    )
    args.plan_file.parent.mkdir(parents=True, exist_ok=True)
    args.plan_file.write_text(
        json.dumps(asdict(plan), indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(asdict(plan), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
