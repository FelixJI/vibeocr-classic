from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from scripts.build_release_checksums import (
    build_release_checksums,
    main,
    write_sidecar_checksum,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_checksums_are_sorted_and_exclude_the_index(tmp_path: Path) -> None:
    (tmp_path / "z.bin").write_bytes(b"z")
    (tmp_path / "a.bin").write_bytes(b"a")
    (tmp_path / "SHA256SUMS").write_text("stale", encoding="utf-8")

    output = build_release_checksums(tmp_path)

    assert output.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'a').hexdigest()}  a.bin",
        f"{hashlib.sha256(b'z').hexdigest()}  z.bin",
    ]


def test_checksums_reject_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        build_release_checksums(tmp_path)


def test_writes_release_asset_sidecar(tmp_path: Path) -> None:
    artifact = tmp_path / "VibeOCRClassic-win-Setup.exe"
    artifact.write_bytes(b"setup")
    sidecar = write_sidecar_checksum(artifact)
    assert sidecar.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(b'setup').hexdigest()}  VibeOCRClassic-win-Setup.exe\n"
    )


def test_cli_writes_requested_setup_sidecar(tmp_path: Path) -> None:
    setup = tmp_path / "VibeOCRClassic-win-Setup.exe"
    setup.write_bytes(b"velopack setup")

    assert (
        main(
            [
                str(tmp_path),
                "--sidecar-for",
                str(setup),
            ]
        )
        == 0
    )

    assert (tmp_path / f"{setup.name}.sha256").is_file()
