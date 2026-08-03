from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from scripts.prune_pyside_artifact import prune_pyside_artifact
from scripts.verify_pyside_artifact import (
    MAX_CLASSIC_ARCHIVE_BYTES,
    _verify_archive_size,
    _verify_bound_python_archive,
    _verify_product_file_closure,
    _verify_reduced_layout,
    _verify_runtime_layout,
)


ROOT = Path(__file__).resolve().parents[2]


def test_release_build_avoids_collecting_all_of_pyside() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    project = (ROOT / "apps" / "vibeocr-pyside" / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert "--collect-all PySide6" not in script
    assert "prune_pyside_artifact.py" in script
    assert "'--hidden-import'" in script
    assert "'PySide6.QtWebEngineWidgets'" in script
    assert "'PySide6.QtUiTools'" in script
    assert "'PySide6.QtQuick'" in script
    assert "'PySide6.QtQuickWidgets'" in script
    assert "'--exclude-module'" in script
    assert "'PySide6.QtQuick3D'" in script
    assert "'pymupdf'" in script
    assert "'fitz'" in script
    assert "'lxml'" in script
    assert "'--icon'" in script
    assert "resources/app_icon.ico" in script
    assert '"$root/CHANGELOG.md;."' in script
    assert "Copy-Item -LiteralPath (Join-Path $root 'CHANGELOG.md')" not in script
    assert "pymupdf==1.28.0" not in script
    assert '"pymupdf' not in project
    entry_script = (ROOT / "scripts" / "classic_release_entry.py").read_text(
        encoding="utf-8"
    )
    assert "VIBEOCR_SELF_TEST_WEBENGINE" in entry_script
    assert "os._exit(_run_webengine_smoke())" in entry_script
    assert 'channel.registerObject("smoke", bridge)' in entry_script
    assert 'src="qrc:///qtwebchannel/qwebchannel.js"' in entry_script
    assert "webchannel_round_trip" in entry_script
    assert "VIBEOCR_SELF_TEST_PDF" in entry_script
    assert "QPdfDocument" in entry_script
    assert "_verify_frozen_webengine" in (
        ROOT / "scripts" / "verify_pyside_artifact.py"
    ).read_text(encoding="utf-8")
    assert "_verify_frozen_pdf" in (
        ROOT / "scripts" / "verify_pyside_artifact.py"
    ).read_text(encoding="utf-8")


def test_release_build_uses_candidate_version_and_direct_publish_contract() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))

    assert "[string]$Version" in script
    assert "frontend-version $Version" in script
    assert "VibeOCR-Classic-v$Version-win64.zip" in script
    assert "vibeocr_classic-$Version-*.whl" in script
    assert config["ci"]["release_build"][0][-4:] == [
        "-ReleaseInput",
        "build/automation/release-input",
        "-ArtifactsDir",
        "{artifacts_dir}",
    ]
    assert not config["ci"]["release_build"][0][-3].startswith("{artifacts_dir}")
    assert "name: Download exact CI candidate" in workflow
    assert "name: Publish and reconcile release" in workflow
    assert "draft" not in workflow.lower()
    assert "v0.7.0" not in workflow


def test_ci_and_release_build_resolve_latest_compatible_backend() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))
    policy = ROOT / "component-policy.json"
    project = tomllib.loads(
        (ROOT / "apps" / "vibeocr-pyside" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert policy.is_file()
    assert not (ROOT / "component-lock.json").exists()
    assert "resolve_component_releases.py" not in workflow
    assert config["ci"]["bootstrap"][0][1] == "scripts/resolve_component_releases.py"
    assert config["ci"]["bootstrap"][1][1] == "scripts/install_resolved_components.py"
    assert config["ci"]["e2e"][0][1] == "scripts/verify_component_release_input.py"
    assert workflow.count("python scripts/automation.py") == 1
    assert "[string]$ReleaseInput" in script
    assert "v0.7.0" not in script
    assert "v0.7.0" not in workflow
    assert "vibeocr-backend" in project["project"]["dependencies"]
    assert not any(
        dependency.startswith("vibeocr-backend") and dependency != "vibeocr-backend"
        for dependency in project["project"]["dependencies"]
    )


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
    (pyside / "Qt6Quick3DRuntimeRender.dll").write_bytes(b"quick3d")
    (pyside / "Qt6QuickDialogs2QuickImpl.dll").write_bytes(b"dialogs")
    (pyside / "Qt63DQuick.dll").write_bytes(b"3d quick")
    qmltooling = pyside / "plugins" / "qmltooling"
    qmltooling.mkdir(parents=True)
    (qmltooling / "qmldbg_debugger.dll").write_bytes(b"qml tooling")
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

    assert result.files_removed == 16
    assert result.bytes_removed > 0
    assert keep_resource.is_file()
    assert software_opengl.is_file()
    assert keep_translation.is_file()
    assert (locales / "zh-CN.pak").is_file()
    assert (locales / "en-US.pak").is_file()
    assert not (locales / "de.pak").exists()
    assert not qmltooling.exists()
    assert not (pyside / "Qt6Quick3DRuntimeRender.dll").exists()
    assert not (pyside / "Qt6QuickDialogs2QuickImpl.dll").exists()
    assert not (pyside / "Qt63DQuick.dll").exists()


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

    assert MAX_CLASSIC_ARCHIVE_BYTES <= 260_000_000


def test_runtime_layout_requires_backend_short_id_under_data(tmp_path: Path) -> None:
    good = {
        "runtime_id": "13caec/win-x64-cpu",
        "runtime_root": str(tmp_path / "data" / "runtimes" / "13caec" / "win-x64-cpu"),
    }
    _verify_runtime_layout(good, tmp_path, "win-x64-cpu")

    long_id = "13caec62e2d74e71a6a46bee8dc7fd70b48d22127d1fa93d2477b1956e449f43"
    with pytest.raises(RuntimeError, match="invalid runtime_id"):
        _verify_runtime_layout(
            {
                "runtime_id": f"{long_id}/win-x64-cpu",
                "runtime_root": str(
                    tmp_path / "data" / "runtimes" / long_id / "win-x64-cpu"
                ),
            },
            tmp_path,
            "win-x64-cpu",
        )

    with pytest.raises(RuntimeError, match="invalid runtime_id"):
        _verify_runtime_layout(
            {
                "runtime_id": "13caec/win-x64-cuda",
                "runtime_root": str(
                    tmp_path / "data" / "runtimes" / "13caec" / "win-x64-cuda"
                ),
            },
            tmp_path,
            "win-x64-cpu",
        )


def test_bound_python_archive_is_required_and_hashed(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    python_archive = backend / "python.tar.gz"
    python_archive.write_bytes(b"bound python")
    runtime_manifest = {
        "python": {
            "archive": python_archive.name,
            "sha256": (
                "4fc203c5d4f67d3a1d44e97072f4c5420f3f57abad9589a098ba060075fda875"
            ),
        }
    }

    _verify_bound_python_archive(tmp_path, runtime_manifest)

    python_archive.unlink()
    try:
        _verify_bound_python_archive(tmp_path, runtime_manifest)
    except RuntimeError as error:
        assert "Python archive is missing" in str(error)
    else:
        raise AssertionError("missing bound Python archive was accepted")


def test_product_manifest_requires_exact_reduced_file_closure(tmp_path: Path) -> None:
    (tmp_path / "VibeOCR.exe").write_bytes(b"app")
    records = {
        "VibeOCR.exe": {
            "sha256": "unused here",
            "size": 3,
        }
    }
    _verify_product_file_closure(tmp_path, records)
    _verify_reduced_layout(tmp_path)

    extra = tmp_path / "runtime-installer" / "installer.exe"
    extra.parent.mkdir()
    extra.write_bytes(b"duplicate")
    try:
        _verify_product_file_closure(tmp_path, records)
    except RuntimeError as error:
        assert "file closure" in str(error)
    else:
        raise AssertionError("unbound extra file was accepted")
    try:
        _verify_reduced_layout(tmp_path)
    except RuntimeError as error:
        assert "prohibited" in str(error)
    else:
        raise AssertionError("duplicate top-level Runtime Installer was accepted")
