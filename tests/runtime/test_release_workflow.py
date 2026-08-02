from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_release_workflow_publishes_only_after_verified_uploads() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    build = workflow.index("- name: Build release assets")
    resolve = workflow.index("- name: Resolve compatible components")
    test_resolved = workflow.index("- name: Test resolved components")
    verify_local = workflow.index("- name: Verify release candidate assets")
    upload_artifact = workflow.index("uses: actions/upload-artifact@v4")
    upload_release = workflow.index("- name: Attach assets to draft Release")
    verify_remote = workflow.index("- name: Verify uploaded Release assets")
    publish = workflow.index("- name: Publish verified Release")

    assert (
        resolve
        < test_resolved
        < build
        < verify_local
        < upload_artifact
        < upload_release
        < verify_remote
        < publish
    )
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event_name == 'workflow_run'" in workflow
    assert "gh release edit $env:RELEASE_TAG --draft=false" in workflow
    assert "already has assets; skipping build" not in workflow
    assert "--require component-lock.json" not in workflow
    assert "VibeOCR-Classic-v*-win64.zip" in workflow
    assert "VibeOCR-Classic-v*-win64.zip.sha256" in workflow
    assert "resolved-components.txt" in workflow
    assert "python -m pip install @resolvedWheels" in workflow
    assert "if ($LASTEXITCODE -ne 0) { throw 'Classic tests failed' }" in workflow
    assert "-ReleaseInput (Resolve-Path .release-input).Path" in workflow
    attach_step = workflow[
        workflow.index("- name: Attach assets to draft Release") : workflow.index(
            "- name: Verify uploaded Release assets"
        )
    ]
    assert "$publicAssets = @(" in attach_step
    assert "component-lock.json" not in attach_step
    assert "SBOM.spdx.json" in attach_step
    assert "SHA256SUMS" in attach_step
    assert "gh release delete-asset $env:RELEASE_TAG $asset --yes" in attach_step


def test_release_workflow_checks_out_draft_target_before_tag_exists() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    resolve_step = workflow[
        workflow.index("- name: Resolve release tag") : workflow.index(
            "- uses: actions/setup-python@v5"
        )
    ]
    auto_path = workflow[
        workflow.index("# Auto path (workflow_run)") : workflow.index(
            "- uses: actions/setup-python@v5"
        )
    ]

    assert "gh release view $ReleaseTag --json targetCommitish" in resolve_step
    assert 'git fetch origin "$target" --force' in resolve_step
    assert "git checkout --detach FETCH_HEAD" in resolve_step
    assert "Set-ReleaseTargetCheckout $tag" in auto_path
    assert 'git fetch origin "$tag"' not in auto_path


def test_cleanup_uses_rest_release_ids_and_preserves_tags() -> None:
    workflow = (ROOT / ".github/workflows/cleanup-releases.yml").read_text(
        encoding="utf-8"
    )

    assert (
        'gh api --paginate "repos/${GITHUB_REPOSITORY}/releases?per_page=100"'
        in workflow
    )
    assert ".id, .tag_name" in workflow
    assert "databaseId" not in workflow
    assert "releases/${release_id}" in workflow
    assert "git push --delete" not in workflow
