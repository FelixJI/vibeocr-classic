from __future__ import annotations

import pytest

from scripts.resolve_component_releases import (
    InvalidReleaseError,
    ComponentPolicy,
    ReleaseInfo,
    select_latest_compatible_backend,
    validate_bound_protocol,
)


def _policy() -> ComponentPolicy:
    return ComponentPolicy(
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        backend_repository="FelixJI/vibeocr-backend",
        profile="win-x64-cpu",
        required_capabilities=frozenset({"ocr.recognition.v2"}),
    )


def _manifest(version: str, protocol: str = ">=2.0.0,<3.0.0") -> dict[str, object]:
    return {
        "schema_version": 1,
        "backend_version": version,
        "protocol": protocol,
        "capabilities": ["ocr.recognition.v2"],
        "profiles": {"win-x64-cpu": {}},
    }


def test_selects_the_latest_formal_backend_when_compatible() -> None:
    releases = [
        ReleaseInfo(tag="v0.7.1", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
    ]

    selected = select_latest_compatible_backend(
        _policy(), releases, lambda release: _manifest(release.tag.removeprefix("v"))
    )

    assert selected.release.tag == "v0.7.2"


def test_rejects_latest_missing_capability_without_fallback() -> None:
    releases = [
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
        ReleaseInfo(tag="v0.7.1", is_draft=False, is_prerelease=False),
    ]

    with pytest.raises(
        RuntimeError, match="latest stable Backend release is incompatible: v0.7.2"
    ):
        select_latest_compatible_backend(
            _policy(),
            releases,
            lambda release: {
                **_manifest(release.tag.removeprefix("v")),
                "capabilities": []
                if release.tag == "v0.7.2"
                else ["ocr.recognition.v2"],
            },
        )


def test_minor_compatible_selection_uses_the_backend_bound_protocol() -> None:
    release = ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False)
    manifest = _manifest("0.7.2", ">=2.1.0,<3.0.0")

    selected = select_latest_compatible_backend(
        _policy(), [release], lambda _: manifest
    )
    validate_bound_protocol(_policy(), selected.manifest, "2.1.4")

    with pytest.raises(InvalidReleaseError, match="outside its declared runtime range"):
        validate_bound_protocol(_policy(), selected.manifest, "2.0.0")


def test_ignores_draft_and_prerelease_when_selecting_latest() -> None:
    releases = [
        ReleaseInfo(tag="v0.8.0", is_draft=True, is_prerelease=False),
        ReleaseInfo(tag="v0.7.3", is_draft=False, is_prerelease=True),
        ReleaseInfo(tag="v0.7.2", is_draft=False, is_prerelease=False),
    ]

    selected = select_latest_compatible_backend(
        _policy(), releases, lambda release: _manifest(release.tag.removeprefix("v"))
    )

    assert selected.release.tag == "v0.7.2"


def test_reads_backend_bound_protocol_identity(tmp_path) -> None:
    (tmp_path / "protocol-release-manifest.json").write_text(
        '{"protocol_version":"2.1.0"}', encoding="utf-8"
    )

    from scripts.resolve_component_releases import bound_protocol_version

    assert bound_protocol_version(tmp_path) == "2.1.0"
    (tmp_path / "protocol-release-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(InvalidReleaseError, match="bound Protocol version"):
        bound_protocol_version(tmp_path)
