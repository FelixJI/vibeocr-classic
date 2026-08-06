"""Resolve the latest stable Backend compatible with the Classic policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .bind_component_releases import (
        bind_protocol_release,
        bind_product_releases,
        protocol_manifest_version,
    )
else:
    from bind_component_releases import (
        bind_protocol_release,
        bind_product_releases,
        protocol_manifest_version,
    )

_STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_RANGE_PART = re.compile(r"^(>=|<=|==|>|<)(\d+)\.(\d+)\.(\d+)$")


class InvalidReleaseError(ValueError):
    """A published release cannot satisfy the component policy."""


@dataclass(frozen=True)
class ComponentPolicy:
    protocol_repository: str
    protocol_version: str
    protocol_sdk_version: str
    backend_repository: str
    accelerator: str
    required_capabilities: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> ComponentPolicy:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
        ):
            raise ValueError("component policy schema_version must be 1")
        protocol = value.get("protocol")
        backend = value.get("backend")
        capabilities = value.get("required_capabilities")
        if not isinstance(protocol, dict) or not isinstance(backend, dict):
            raise ValueError("component policy repositories are missing")
        if backend.get("channel") != "stable":
            raise ValueError("only the stable Backend channel is supported")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item for item in capabilities
        ):
            raise ValueError("component policy capabilities must be strings")
        protocol_version = str(protocol["version"])
        protocol_sdk_version = str(protocol["sdk_version"])
        _parse_version(protocol_version)
        _parse_version(protocol_sdk_version)
        if protocol_sdk_version.split(".", 1)[0] != protocol_version.split(".", 1)[0]:
            raise ValueError("frontend Protocol SDK must use the supported major")
        return cls(
            protocol_repository=str(protocol["repository"]),
            protocol_version=protocol_version,
            protocol_sdk_version=protocol_sdk_version,
            backend_repository=str(backend["repository"]),
            accelerator=str(backend["accelerator"]),
            required_capabilities=frozenset(capabilities),
        )


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    is_draft: bool
    is_prerelease: bool

    @property
    def version(self) -> tuple[int, int, int] | None:
        match = _STABLE_TAG.fullmatch(self.tag)
        if match is None:
            return None
        return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class ResolvedBackend:
    release: ReleaseInfo
    manifest: dict[str, object]


def bound_protocol_version(backend_release_dir: Path) -> str:
    """Read the immutable Protocol release identity bundled by Backend."""
    path = backend_release_dir / "protocol-release-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidReleaseError(
            "Backend is missing its bound Protocol manifest"
        ) from error
    try:
        version = protocol_manifest_version(manifest)
        _parse_version(version)
    except ValueError as error:
        raise InvalidReleaseError(
            "Backend bound Protocol version is invalid"
        ) from error
    return version


def _parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"stable SemVer required: {value}")
    return tuple(int(part) for part in match.groups())


def _matches_range(version: str, expression: str) -> bool:
    candidate = _parse_version(version)
    for raw_part in expression.split(","):
        match = _RANGE_PART.fullmatch(raw_part.strip())
        if match is None:
            raise ValueError(f"unsupported Protocol range: {expression}")
        operator = match.group(1)
        bound = tuple(int(part) for part in match.groups()[1:])
        comparisons = {
            ">=": candidate >= bound,
            "<=": candidate <= bound,
            "==": candidate == bound,
            ">": candidate > bound,
            "<": candidate < bound,
        }
        if not comparisons[operator]:
            return False
    return True


def _is_compatible(
    policy: ComponentPolicy,
    release: ReleaseInfo,
    manifest: dict[str, object],
) -> bool:
    version = release.version
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        return False
    if version is None or manifest.get("backend_version") != release.tag.removeprefix(
        "v"
    ):
        return False
    protocol_range = manifest.get("protocol")
    capabilities = manifest.get("capabilities")
    profiles = manifest.get("profiles")
    plan = {"cpu": "win-x64-cpu", "nvidia_cuda": "win-x64-cu126"}.get(
        policy.accelerator
    )
    if not isinstance(protocol_range, str):
        return False
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        return False
    if plan is None or not isinstance(profiles, dict) or plan not in profiles:
        return False
    return policy.required_capabilities <= set(capabilities)


def validate_bound_protocol(
    policy: ComponentPolicy,
    manifest: dict[str, object],
    protocol_version: str,
) -> None:
    supported_major = policy.protocol_version.split(".", 1)[0]
    actual_major = protocol_version.split(".", 1)[0]
    protocol_range = manifest.get("protocol")
    if actual_major != supported_major:
        raise InvalidReleaseError(
            f"Backend binds unsupported Protocol major: {protocol_version}"
        )
    if not isinstance(protocol_range, str) or not _matches_range(
        protocol_version, protocol_range
    ):
        raise InvalidReleaseError(
            "Backend bound Protocol is outside its declared runtime range: "
            f"{protocol_version}"
        )


def validate_frontend_protocol_sdk(
    policy: ComponentPolicy,
    protocol_version: str,
) -> None:
    """Require the frontend SDK to use a supported major, not a Runtime minor."""
    supported_major = policy.protocol_version.split(".", 1)[0]
    actual_major = protocol_version.split(".", 1)[0]
    _parse_version(protocol_version)
    if actual_major != supported_major:
        raise InvalidReleaseError(
            f"frontend Protocol SDK uses unsupported major: {protocol_version}"
        )


def select_latest_compatible_backend(
    policy: ComponentPolicy,
    releases: Iterable[ReleaseInfo],
    load_manifest: Callable[[ReleaseInfo], dict[str, object]],
) -> ResolvedBackend:
    stable = sorted(
        (
            release
            for release in releases
            if not release.is_draft
            and not release.is_prerelease
            and release.version is not None
        ),
        key=lambda release: release.version or (0, 0, 0),
        reverse=True,
    )
    if not stable:
        raise RuntimeError("no stable Backend release was found")
    latest = stable[0]
    try:
        manifest = load_manifest(latest)
    except (InvalidReleaseError, ValueError, KeyError) as error:
        raise RuntimeError(
            f"latest stable Backend release is invalid: {latest.tag}"
        ) from error
    if not _is_compatible(policy, latest, manifest):
        raise RuntimeError(
            f"latest stable Backend release is incompatible: {latest.tag}"
        )
    return ResolvedBackend(release=latest, manifest=manifest)


class GitHubReleaseSource:
    def _run(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["gh", *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout

    def list_releases(self, repository: str) -> tuple[ReleaseInfo, ...]:
        raw = self._run(
            "release",
            "list",
            "--repo",
            repository,
            "--limit",
            "100",
            "--json",
            "tagName,isDraft,isPrerelease",
        )
        values = json.loads(raw)
        return tuple(
            ReleaseInfo(
                tag=str(value["tagName"]),
                is_draft=bool(value["isDraft"]),
                is_prerelease=bool(value["isPrerelease"]),
            )
            for value in values
        )

    def download_asset(
        self,
        repository: str,
        tag: str,
        name: str,
        output_dir: Path,
    ) -> Path:
        release = json.loads(
            self._run(
                "release",
                "view",
                tag,
                "--repo",
                repository,
                "--json",
                "assets",
            )
        )
        asset_names = {
            str(asset["name"])
            for asset in release.get("assets", [])
            if isinstance(asset, dict) and "name" in asset
        }
        if name not in asset_names:
            raise InvalidReleaseError(f"release {tag} has no {name}")
        output_dir.mkdir(parents=True, exist_ok=True)
        self._run(
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--pattern",
            name,
            "--dir",
            str(output_dir),
        )
        return output_dir / name

    def download_release(self, repository: str, tag: str, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._run(
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--dir",
            str(output_dir),
        )


def resolve_component_releases(
    *,
    policy_path: Path,
    output_root: Path,
    source: GitHubReleaseSource | None = None,
) -> Path:
    policy = ComponentPolicy.load(policy_path)
    github = source or GitHubReleaseSource()
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError(f"release input directory must be empty: {output_root}")

    with tempfile.TemporaryDirectory(prefix="vibeocr-release-resolution-") as temp:
        manifests_root = Path(temp)

        def load_manifest(release: ReleaseInfo) -> dict[str, object]:
            path = github.download_asset(
                policy.backend_repository,
                release.tag,
                "runtime-manifest.json",
                manifests_root / release.tag,
            )
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise InvalidReleaseError(
                    f"Backend manifest is unreadable: {release.tag}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(f"Backend manifest must be an object: {release.tag}")
            return value

        selected = select_latest_compatible_backend(
            policy,
            github.list_releases(policy.backend_repository),
            load_manifest,
        )

    backend_root = output_root / "backend"
    github.download_release(
        policy.backend_repository,
        selected.release.tag,
        backend_root,
    )
    protocol_version = bound_protocol_version(backend_root)
    validate_bound_protocol(policy, selected.manifest, protocol_version)
    protocol_root = output_root / "protocol"
    github.download_release(
        policy.protocol_repository,
        f"v{protocol_version}",
        protocol_root,
    )
    sdk_version = policy.protocol_sdk_version
    validate_frontend_protocol_sdk(policy, sdk_version)
    sdk_root = output_root / "protocol-sdk"
    github.download_release(
        policy.protocol_repository,
        f"v{sdk_version}",
        sdk_root,
    )
    bind_protocol_release(
        release_dir=sdk_root,
        repository=policy.protocol_repository,
        version=sdk_version,
        output=output_root / "frontend-protocol-lock.json",
    )
    return bind_product_releases(
        protocol_release_dir=protocol_root,
        backend_release_dir=backend_root,
        protocol_repository=policy.protocol_repository,
        protocol_version=protocol_version,
        backend_repository=policy.backend_repository,
        backend_version=selected.release.tag.removeprefix("v"),
        accelerator=policy.accelerator,
        required_capabilities=tuple(sorted(policy.required_capabilities)),
        output=output_root / "component-lock.json",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    lock_path = resolve_component_releases(
        policy_path=args.policy,
        output_root=args.output_root,
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    frontend_lock = json.loads(
        (args.output_root / "frontend-protocol-lock.json").read_text(encoding="utf-8")
    )
    print(
        f"Resolved Backend v{lock['backend']['version']} for "
        f"Runtime Protocol v{lock['protocol']['version']} and frontend SDK "
        f"v{frontend_lock['version']}"
    )
    print(lock_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
