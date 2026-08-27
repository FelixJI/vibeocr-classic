"""Velopack feed release-notes extraction contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extract_release_notes import extract_release_notes, main


ROOT = Path(__file__).resolve().parents[2]


def test_extract_returns_version_section_between_headings() -> None:
    changelog = (
        "# Changelog\n"
        "\n"
        "## 0.11.0\n"
        "\n"
        "### Features\n"
        "\n"
        "- **runtime:** 新能力 (abc1234)\n"
        "\n"
        "## 0.10.10\n"
        "\n"
        "- 旧条目\n"
    )

    assert extract_release_notes(changelog, "0.11.0") == (
        "## 0.11.0\n\n### Features\n\n- **runtime:** 新能力 (abc1234)"
    )
    assert extract_release_notes(changelog, "0.10.10") == ("## 0.10.10\n\n- 旧条目")


def test_extract_accepts_bracketed_heading_and_trailing_suffix() -> None:
    changelog = "## [0.9.0] - 2025-01-01\n\n- 条目\n"

    assert extract_release_notes(changelog, "0.9.0") == (
        "## [0.9.0] - 2025-01-01\n\n- 条目"
    )


def test_extract_rejects_missing_version_section() -> None:
    with pytest.raises(ValueError, match="no section"):
        extract_release_notes("# Changelog\n\n## 0.10.10\n", "9.9.9")


def test_main_writes_notes_file_for_repository_changelog(tmp_path: Path) -> None:
    version = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    output = tmp_path / "notes" / "release-notes.md"

    exit_code = main(
        [
            "--changelog",
            str(ROOT / "CHANGELOG.md"),
            "--version",
            version,
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    notes = output.read_text(encoding="utf-8")
    assert notes.startswith(f"## {version}")
    assert notes.endswith("\n")
