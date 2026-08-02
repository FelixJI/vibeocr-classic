import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CHANGELOG_SECTIONS = [
    {"type": "feat", "section": "Features"},
    {"type": "fix", "section": "Bug Fixes"},
    {"type": "perf", "section": "Performance Improvements"},
    {"type": "security", "section": "Security"},
    {"type": "deps", "section": "Dependencies"},
    {"type": "build", "section": "Build and Packaging"},
    {"type": "revert", "section": "Reverts"},
    {"type": "docs", "section": "Documentation", "hidden": True},
    {"type": "refactor", "section": "Code Refactoring", "hidden": True},
    {"type": "test", "section": "Tests", "hidden": True},
    {"type": "ci", "section": "Continuous Integration", "hidden": True},
    {"type": "style", "section": "Styles", "hidden": True},
    {"type": "chore", "section": "Miscellaneous Chores", "hidden": True},
]


def test_release_please_uses_the_shared_changelog_filter() -> None:
    config = json.loads(
        (ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )

    assert config["changelog-sections"] == EXPECTED_CHANGELOG_SECTIONS


def test_release_please_keeps_repository_metadata_version_in_sync() -> None:
    config = json.loads(
        (ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )
    metadata = json.loads((ROOT / "repository.json").read_text(encoding="utf-8"))
    project = tomllib.loads(
        (ROOT / "apps" / "vibeocr-pyside" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    extra_files = config["packages"]["."]["extra-files"]
    assert {
        "type": "json",
        "path": "repository.json",
        "jsonpath": "$.version",
    } in extra_files
    assert metadata["version"] == project["project"]["version"]


def test_manual_release_passes_requested_version_to_manifest_cli() -> None:
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text(
        encoding="utf-8"
    )
    manual_job = workflow.split("  draft-release:", maxsplit=1)[0]

    assert "release-please@17.6.0 release-pr" in manual_job
    assert '--release-as="${{ steps.version.outputs.next }}"' in manual_job
    assert "--config-file=release-please-config.json" in manual_job
    assert "--manifest-file=.release-please-manifest.json" in manual_job
    assert "googleapis/release-please-action" not in manual_job
