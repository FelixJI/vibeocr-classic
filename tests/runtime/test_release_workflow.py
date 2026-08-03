from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_workflows_are_limited_to_thin_automation_callers() -> None:
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))

    assert [path.name for path in workflows] == ["cd.yml", "ci.yml"]
    for workflow in workflows:
        assert "python scripts/automation.py" in workflow.read_text(encoding="utf-8")


def test_project_declares_protocol_compatibility_and_one_build_command() -> None:
    project = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))

    assert project["project"]["protocol_compatibility"] == {
        "supported_majors": [2],
        "minor_compatible": True,
    }
    assert len(project["ci"]["release_build"]) == 1
    assert "-ArtifactsDir" in project["ci"]["release_build"][0]
    assert "component-lock.json" in project["release"]["required_assets"]
    assert project["release"]["identity_asset"] == "component-lock.json"
    assert "release-manifest.json" not in project["release"]["required_assets"]
    assert "SHA256SUMS" not in project["release"]["required_assets"]
    build_script = (ROOT / "scripts/build-release.ps1").read_text(encoding="utf-8")
    assert "AUTOMATION_ARTIFACTS_DIR" in build_script
    assert build_script.count("build_release_checksums.py") == 1
    assert "--no-checksum-index" in project["ci"]["release_smoke"][0]
