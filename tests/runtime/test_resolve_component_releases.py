from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.resolve_component_releases import (
    InvalidReleaseError,
    ComponentPolicy,
    ReleaseInfo,
    resolve_component_releases,
    select_latest_compatible_backend,
    validate_bound_protocol,
    validate_frontend_protocol_sdk,
)


def _policy() -> ComponentPolicy:
    return ComponentPolicy(
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        protocol_sdk_version="2.3.0",
        backend_repository="FelixJI/vibeocr-backend",
        accelerator="cpu",
        required_capabilities=frozenset({"ocr.recognition.v2", "runtime.settings.v2"}),
    )


def _manifest(version: str, protocol: str = ">=2.0.0,<3.0.0") -> dict[str, object]:
    return {
        "schema_version": 1,
        "backend_version": version,
        "protocol": protocol,
        "capabilities": ["ocr.recognition.v2", "runtime.settings.v2"],
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


def test_frontend_sdk_minor_is_independent_from_runtime_protocol() -> None:
    policy = ComponentPolicy(
        protocol_repository="FelixJI/vibeocr-protocol",
        protocol_version="2.0.0",
        protocol_sdk_version="2.4.0",
        backend_repository="FelixJI/vibeocr-backend",
        accelerator="cpu",
        required_capabilities=frozenset({"ocr.recognition.v2"}),
    )

    validate_bound_protocol(policy, _manifest("0.8.0", ">=2.3.0,<3.0.0"), "2.3.0")
    validate_frontend_protocol_sdk(policy, "2.4.0")


def test_frontend_sdk_rejects_a_different_major() -> None:
    with pytest.raises(InvalidReleaseError, match="unsupported major"):
        validate_frontend_protocol_sdk(_policy(), "3.0.0")


def test_resolver_downloads_and_binds_frontend_sdk_independently(
    monkeypatch, tmp_path: Path
) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": {
                    "repository": "FelixJI/vibeocr-protocol",
                    "version": "2.0.0",
                    "sdk_version": "2.4.0",
                },
                "backend": {
                    "channel": "stable",
                    "accelerator": "cpu",
                    "repository": "FelixJI/vibeocr-backend",
                },
                "required_capabilities": ["ocr.recognition.v2"],
            }
        ),
        encoding="utf-8",
    )

    class Source:
        downloads: list[tuple[str, str, str]] = []

        def list_releases(self, _repository: str):
            return (ReleaseInfo("v0.10.0", False, False),)

        def download_asset(
            self,
            _repository: str,
            _tag: str,
            _name: str,
            output_dir: Path,
        ) -> Path:
            output_dir.mkdir(parents=True)
            path = output_dir / "runtime-manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "backend_version": "0.10.0",
                        "protocol": ">=2.3.0,<3.0.0",
                        "capabilities": ["ocr.recognition.v2"],
                        "profiles": {"win-x64-cpu": {}},
                    }
                ),
                encoding="utf-8",
            )
            return path

        def download_release(self, repository: str, tag: str, output_dir: Path) -> None:
            self.downloads.append((repository, tag, output_dir.name))
            output_dir.mkdir(parents=True)
            if repository.endswith("vibeocr-backend"):
                (output_dir / "protocol-release-manifest.json").write_text(
                    json.dumps({"protocol_version": "2.3.0"}), encoding="utf-8"
                )

    frontend_bindings: list[tuple[str, str]] = []

    def bind_frontend(**kwargs) -> Path:
        frontend_bindings.append((kwargs["version"], kwargs["release_dir"].name))
        kwargs["output"].write_text("{}", encoding="utf-8")
        return kwargs["output"]

    def bind_runtime(**kwargs) -> Path:
        assert kwargs["protocol_version"] == "2.3.0"
        assert kwargs["protocol_release_dir"].name == "protocol"
        kwargs["output"].write_text("{}", encoding="utf-8")
        return kwargs["output"]

    monkeypatch.setattr(
        "scripts.resolve_component_releases.bind_protocol_release", bind_frontend
    )
    monkeypatch.setattr(
        "scripts.resolve_component_releases.bind_product_releases", bind_runtime
    )
    source = Source()

    resolve_component_releases(
        policy_path=policy_path,
        output_root=tmp_path / "release-input",
        source=source,
    )

    assert frontend_bindings == [("2.4.0", "protocol-sdk")]
    assert (
        "FelixJI/vibeocr-protocol",
        "v2.3.0",
        "protocol",
    ) in source.downloads
    assert (
        "FelixJI/vibeocr-protocol",
        "v2.4.0",
        "protocol-sdk",
    ) in source.downloads


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
    (tmp_path / "protocol-release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "project": {"component": "protocol"},
                "protocol": {"version": "2.1.0"},
                "release": {"version": "2.1.0", "tag": "v2.1.0"},
            }
        ),
        encoding="utf-8",
    )
    assert bound_protocol_version(tmp_path) == "2.1.0"
    (tmp_path / "protocol-release-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(InvalidReleaseError, match="bound Protocol version"):
        bound_protocol_version(tmp_path)


def test_policy_rejects_non_integer_schema_versions(tmp_path) -> None:
    for invalid_schema in (True, 1.0):
        policy_path = tmp_path / f"policy-{invalid_schema!r}.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": invalid_schema,
                    "protocol": {
                        "repository": "FelixJI/vibeocr-protocol",
                        "sdk_version": "2.3.0",
                        "version": "2.0.0",
                    },
                    "backend": {
                        "channel": "stable",
                        "accelerator": "cpu",
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
