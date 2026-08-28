from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.normalize_velopack_feed import normalize_feed
from scripts.prepare_velopack_delta import (
    ReleaseAsset,
    StableRelease,
    prepare_delta_base,
)


class _ReleaseClient:
    def __init__(
        self,
        release: StableRelease,
        payload: bytes,
        previous: StableRelease | None = None,
    ) -> None:
        self.release = release
        self.previous = previous
        self.payload = payload
        self.downloads: list[tuple[str, str, Path]] = []

    def latest_release(self, _repository: str) -> StableRelease:
        return self.release

    def previous_stable_release(
        self, _repository: str, _before_version: tuple[int, int, int]
    ) -> StableRelease | None:
        return self.previous

    def download_asset(
        self, _repository: str, tag: str, asset_name: str, destination: Path
    ) -> None:
        self.downloads.append((tag, asset_name, destination))
        destination.write_bytes(self.payload)


def _release(pack_id: str, version: str, payload: bytes) -> StableRelease:
    name = f"{pack_id}-{version}-full.nupkg"
    return StableRelease(
        tag_name=f"v{version}",
        is_draft=False,
        is_prerelease=False,
        assets=(
            ReleaseAsset(
                name=name,
                size=len(payload),
                digest=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            ),
        ),
    )


def test_new_target_downloads_and_verifies_latest_full_as_delta_base(
    tmp_path: Path,
) -> None:
    payload = b"previous-full"
    client = _ReleaseClient(_release("VibeOCRTest", "1.2.3", payload), payload)

    plan = prepare_delta_base(
        client,
        repository="FelixJI/vibeocr-test",
        pack_id="VibeOCRTest",
        target_version="1.2.4",
        output_dir=tmp_path,
    )

    assert plan.delta_mode == "BestSpeed"
    assert plan.base_version == "1.2.3"
    assert plan.base_package == "VibeOCRTest-1.2.3-full.nupkg"
    assert (tmp_path / plan.base_package).read_bytes() == payload
    assert len(client.downloads) == 1


def test_same_or_older_target_is_full_only_without_download(tmp_path: Path) -> None:
    payload = b"published-full"
    client = _ReleaseClient(_release("VibeOCRTest", "1.2.3", payload), payload)

    for target in ("1.2.3", "1.2.2"):
        plan = prepare_delta_base(
            client,
            repository="FelixJI/vibeocr-test",
            pack_id="VibeOCRTest",
            target_version=target,
            output_dir=tmp_path,
        )
        assert plan.delta_mode == "None"
        assert plan.base_package is None

    assert client.downloads == []


def test_published_target_with_delta_rebuilds_from_previous_stable_release(
    tmp_path: Path,
) -> None:
    previous_payload = b"previous-full"
    current_payload = b"current-full"
    current = _release("VibeOCRTest", "1.2.4", current_payload)
    current = StableRelease(
        tag_name=current.tag_name,
        is_draft=False,
        is_prerelease=False,
        assets=(
            *current.assets,
            ReleaseAsset("VibeOCRTest-1.2.4-delta.nupkg", 1, "sha256:" + "0" * 64),
        ),
    )
    client = _ReleaseClient(
        current,
        previous_payload,
        previous=_release("VibeOCRTest", "1.2.3", previous_payload),
    )

    plan = prepare_delta_base(
        client,
        repository="FelixJI/vibeocr-test",
        pack_id="VibeOCRTest",
        target_version="1.2.4",
        output_dir=tmp_path,
        reproduce_published_delta=True,
    )

    assert plan.delta_mode == "BestSpeed"
    assert plan.base_version == "1.2.3"
    assert client.downloads[0][:2] == (
        "v1.2.3",
        "VibeOCRTest-1.2.3-full.nupkg",
    )


def test_published_target_with_delta_is_full_only_without_reproduce_intent(
    tmp_path: Path,
) -> None:
    payload = b"published-full"
    current = _release("VibeOCRTest", "1.2.4", payload)
    current = StableRelease(
        tag_name=current.tag_name,
        is_draft=False,
        is_prerelease=False,
        assets=(
            *current.assets,
            ReleaseAsset("VibeOCRTest-1.2.4-delta.nupkg", 1, "sha256:" + "0" * 64),
        ),
    )
    client = _ReleaseClient(
        current, payload, previous=_release("VibeOCRTest", "1.2.3", payload)
    )

    plan = prepare_delta_base(
        client,
        repository="FelixJI/vibeocr-test",
        pack_id="VibeOCRTest",
        target_version="1.2.4",
        output_dir=tmp_path,
    )

    assert plan.delta_mode == "None"
    assert client.downloads == []


def test_downloaded_base_digest_mismatch_fails_closed_and_removes_file(
    tmp_path: Path,
) -> None:
    expected = b"expected"
    client = _ReleaseClient(_release("VibeOCRTest", "1.2.3", expected), b"tampered")

    with pytest.raises(RuntimeError, match="size|SHA-256"):
        prepare_delta_base(
            client,
            repository="FelixJI/vibeocr-test",
            pack_id="VibeOCRTest",
            target_version="1.2.4",
            output_dir=tmp_path,
        )

    assert not list(tmp_path.glob("*.nupkg"))


def test_normalize_feed_validates_then_removes_historical_base(tmp_path: Path) -> None:
    feed = tmp_path / "releases.win.json"
    feed.write_text(
        json.dumps(
            {
                "Assets": [
                    {"PackageId": "VibeOCRTest", "Version": "1.2.4", "Type": "Full"},
                    {"PackageId": "VibeOCRTest", "Version": "1.2.4", "Type": "Delta"},
                    {
                        "PackageId": "VibeOCRTest",
                        "Version": "1.2.3",
                        "Type": "Full",
                        "FileName": "VibeOCRTest-1.2.3-full.nupkg",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    normalize_feed(
        feed,
        pack_id="VibeOCRTest",
        target_version="1.2.4",
        expected_base_version="1.2.3",
    )

    assets = json.loads(feed.read_text(encoding="utf-8"))["Assets"]
    assert {(asset["Version"], asset["Type"]) for asset in assets} == {
        ("1.2.4", "Full"),
        ("1.2.4", "Delta"),
    }
