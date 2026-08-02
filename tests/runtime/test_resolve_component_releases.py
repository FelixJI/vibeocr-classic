from __future__ import annotations

import json

from scripts.resolve_component_releases import (
    ComponentPolicy,
    InvalidReleaseError,
    ReleaseInfo,
    select_latest_compatible_backend,
)


def _policy() -> ComponentPolicy:
    return ComponentPolicy(
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        profile="win-x64-cpu",
        required_capabilities=frozenset({"ocr.recognition.v2", "runtime.settings.v2"}),
    )


def _manifest(
    version: str,
    *,
    protocol: str = ">=2.0.0,<3.0.0",
    capabilities: tuple[str, ...] = (
        "ocr.recognition.v2",
        "runtime.settings.v2",
    ),
    profiles: tuple[str, ...] = ("win-x64-cpu",),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "backend_version": version,
        "protocol": protocol,
        "capabilities": list(capabilities),
        "profiles": {name: {} for name in profiles},
    }


def test_selects_latest_stable_compatible_backend() -> None:
    releases = [
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.8.0", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.4", is_draft=True, is_prerelease=False),
        ReleaseInfo(tag="v0.7.3", is_draft=False, is_prerelease=True),
        ReleaseInfo(tag="v0.7.1", is_draft=False, is_prerelease=False),
    ]
    manifests = {
        "v0.8.0": _manifest("0.8.0", protocol=">=3.0.0,<4.0.0"),
        "v0.7.2": _manifest("0.7.2"),
        "v0.7.1": _manifest("0.7.1"),
    }

    selected = select_latest_compatible_backend(
        _policy(), releases, lambda release: manifests[release.tag]
    )

    assert selected.release.tag == "v0.7.2"
    assert selected.manifest["backend_version"] == "0.7.2"


def test_skips_missing_capabilities_and_profile() -> None:
    releases = [
        ReleaseInfo(tag="v0.7.3", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.1", is_draft=False, is_prerelease=False),
    ]
    manifests = {
        "v0.7.3": _manifest("0.7.3", capabilities=("ocr.recognition.v2",)),
        "v0.7.2": _manifest("0.7.2", profiles=("linux-x64-cpu",)),
        "v0.7.1": _manifest("0.7.1"),
    }

    selected = select_latest_compatible_backend(
        _policy(), releases, lambda release: manifests[release.tag]
    )

    assert selected.release.tag == "v0.7.1"


def test_rejects_when_no_compatible_backend_exists() -> None:
    releases = [
        ReleaseInfo(tag="v1.0.0", is_draft=False, is_prerelease=False),
    ]

    try:
        select_latest_compatible_backend(
            _policy(),
            releases,
            lambda _release: _manifest("1.0.0", protocol=">=3.0.0,<4.0.0"),
        )
    except RuntimeError as error:
        assert "no compatible stable Backend release" in str(error)
    else:
        raise AssertionError("incompatible Backend release was selected")


def test_skips_unknown_schema_invalid_range_and_missing_manifest() -> None:
    releases = [
        ReleaseInfo(tag="v0.7.5", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.4", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.3", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
    ]
    schema_two = _manifest("0.7.5")
    schema_two["schema_version"] = 2
    manifests = {
        "v0.7.5": schema_two,
        "v0.7.4": _manifest("0.7.4", protocol="~=2.0"),
        "v0.7.2": _manifest("0.7.2"),
    }

    def load_manifest(release: ReleaseInfo) -> dict[str, object]:
        if release.tag == "v0.7.3":
            raise InvalidReleaseError("runtime-manifest.json is missing")
        return manifests[release.tag]

    selected = select_latest_compatible_backend(_policy(), releases, load_manifest)

    assert selected.release.tag == "v0.7.2"


def test_rejects_non_integer_manifest_schema_versions() -> None:
    release = ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False)

    for invalid_schema in (True, 1.0):
        manifest = _manifest("0.7.2")
        manifest["schema_version"] = invalid_schema
        try:
            select_latest_compatible_backend(
                _policy(), [release], lambda _release: manifest
            )
        except RuntimeError as error:
            assert "no compatible stable Backend release" in str(error)
        else:
            raise AssertionError(f"invalid schema was selected: {invalid_schema!r}")


def test_policy_rejects_non_integer_schema_versions(tmp_path) -> None:
    for invalid_schema in (True, 1.0):
        policy_path = tmp_path / f"policy-{invalid_schema!r}.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": invalid_schema,
                    "protocol": {
                        "repository": "FelixJI/vibeocr-protocol",
                        "version": "2.0.0",
                    },
                    "backend": {
                        "channel": "stable",
                        "profile": "win-x64-cpu",
                        "repository": "FelixJI/vibeocr-backend",
                    },
                    "required_capabilities": ["ocr.recognition.v2"],
                }
            ),
            encoding="utf-8",
        )

        try:
            ComponentPolicy.load(policy_path)
        except ValueError as error:
            assert "schema_version must be 1" in str(error)
        else:
            raise AssertionError(f"invalid policy schema accepted: {invalid_schema!r}")
