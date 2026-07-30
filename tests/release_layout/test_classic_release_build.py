from __future__ import annotations

from pathlib import Path

from scripts.prune_pyside_artifact import prune_pyside_artifact
from scripts.verify_pyside_artifact import (
    MAX_CLASSIC_ARCHIVE_BYTES,
    _verify_archive_size,
)


ROOT = Path(__file__).resolve().parents[2]


def test_release_build_avoids_collecting_all_of_pyside() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")

    assert "--collect-all PySide6" not in script
    assert "prune_pyside_artifact.py" in script
    assert "'--hidden-import'" in script
    assert "'PySide6.QtWebEngineWidgets'" in script
    assert "'PySide6.QtUiTools'" in script
    assert "'PySide6.QtQuick'" in script
    assert "'PySide6.QtQuickWidgets'" in script
    assert "'--exclude-module'" in script
    assert "'PySide6.QtQuick3D'" in script
    entry_script = (ROOT / "scripts" / "classic_release_entry.py").read_text(
        encoding="utf-8"
    )
    assert "VIBEOCR_SELF_TEST_WEBENGINE" in entry_script
    assert "os._exit(_run_webengine_smoke())" in entry_script
    assert 'channel.registerObject("smoke", bridge)' in entry_script
    assert 'src="qrc:///qtwebchannel/qwebchannel.js"' in entry_script
    assert "webchannel_round_trip" in entry_script
    assert "_verify_frozen_webengine" in (
        ROOT / "scripts" / "verify_pyside_artifact.py"
    ).read_text(encoding="utf-8")


def test_release_build_is_driven_by_tag_and_project_metadata() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "[string]$Version" in script
    assert "frontend-version $Version" in script
    assert "VibeOCR-Classic-v$Version-win64.zip" in script
    assert "vibeocr_classic-$Version-*.whl" in script
    assert "tags: ['v*']" in workflow
    assert workflow.count("RELEASE_TAG: ${{ github.ref_name }}") == 3
    assert "-Version $env:RELEASE_TAG" in workflow
    assert "gh release create $env:RELEASE_TAG" in workflow
    assert "gh release create ${{ github.ref_name }}" not in workflow
    assert "'${{ github.ref_name }}'" not in workflow
    assert "v0.7.0" not in workflow


def test_pruner_removes_development_and_debug_qt_payload(tmp_path: Path) -> None:
    pyside = tmp_path / "_internal" / "PySide6"
    for directory in (
        "include",
        "typesystems",
        "doc",
        "glue",
        "scripts",
        "support",
        "metatypes",
        "qml",
    ):
        target = pyside / directory
        target.mkdir(parents=True)
        (target / "unused.bin").write_bytes(b"x")
    resources = pyside / "resources"
    resources.mkdir(parents=True)
    software_opengl = pyside / "opengl32sw.dll"
    software_opengl.write_bytes(b"software renderer")
    keep_resource = resources / "qtwebengine_resources.pak"
    keep_resource.write_bytes(b"required")
    (resources / "qtwebengine_devtools_resources.debug.pak").write_bytes(b"debug")
    (resources / "qtwebengine_devtools_resources.pak").write_bytes(b"devtools")
    translations = pyside / "translations"
    translations.mkdir()
    keep_translation = translations / "qtbase_zh_CN.qm"
    keep_translation.write_bytes(b"zh")
    (translations / "qtbase_de.qm").write_bytes(b"de")
    locales = translations / "qtwebengine_locales"
    locales.mkdir()
    (locales / "zh-CN.pak").write_bytes(b"zh")
    (locales / "en-US.pak").write_bytes(b"en")
    (locales / "de.pak").write_bytes(b"de")

    result = prune_pyside_artifact(tmp_path)

    assert result.files_removed == 12
    assert result.bytes_removed > 0
    assert keep_resource.is_file()
    assert software_opengl.is_file()
    assert keep_translation.is_file()
    assert (locales / "zh-CN.pak").is_file()
    assert (locales / "en-US.pak").is_file()
    assert not (locales / "de.pak").exists()


def test_classic_archive_budget_rejects_regression(tmp_path: Path) -> None:
    artifact = tmp_path / "classic.zip"
    artifact.write_bytes(b"x" * 11)

    _verify_archive_size(artifact, max_bytes=11)

    try:
        _verify_archive_size(artifact, max_bytes=10)
    except RuntimeError as error:
        assert "size budget" in str(error)
    else:
        raise AssertionError("oversized Classic archive was accepted")

    assert MAX_CLASSIC_ARCHIVE_BYTES < 380_997_298
