from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts.prune_pyside_artifact import prune_pyside_artifact
from scripts.verify_velopack_release import verify_velopack_release
from scripts.verify_pyside_artifact import (
    _verify_bound_installer_inspect,
    _verify_bound_python_archive,
    _verify_embedded_app_icon,
    _verify_frontend_protocol_lock,
    _verify_product_file_closure,
    _verify_reduced_layout,
    _verify_runtime_layout,
    verify_component_policy_binding,
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
    # smoke 输出必须经 bootstrap 边界（日志落 state/logs），并以 os._exit
    # 绕过 Qt finalizer。
    assert "os._exit(_run_with_bootstrap(_run_webengine_smoke))" in entry_script
    assert "os._exit(_run_with_bootstrap(_run_pdf_smoke))" in entry_script
    assert "_activate_portable_state" in entry_script
    assert "activate_portable_state" in entry_script
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


def test_product_verifier_requires_the_custom_icon_payload(tmp_path: Path) -> None:
    payload = b"custom-vibeocr-icon-frame"
    ico = tmp_path / "app_icon.ico"
    ico.write_bytes(
        b"\x00\x00\x01\x00\x01\x00"
        + b"\x20\x20\x00\x00\x01\x00\x20\x00"
        + len(payload).to_bytes(4, "little")
        + (22).to_bytes(4, "little")
        + payload
    )
    executable = tmp_path / "VibeOCR.exe"
    executable.write_bytes(b"MZ" + payload)

    _verify_embedded_app_icon(executable, ico)

    executable.write_bytes(b"MZ-default-icon")
    with pytest.raises(RuntimeError, match="custom app icon"):
        _verify_embedded_app_icon(executable, ico)


def test_release_build_uses_candidate_version_and_direct_publish_contract() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))

    assert "[string]$Version" in script
    assert "frontend-version $Version" in script
    assert "VibeOCR-Classic-v$Version-win64.zip" not in script
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


def test_release_build_packages_bound_product_with_pinned_velopack() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    binding_index = script.index("finalize_product_release.py")
    velopack_index = script.index("dnx --yes vpk@1.2.0 -- pack")

    assert velopack_index > binding_index
    assert "--packId VibeOCRClassic" in script
    assert "--packVersion $Version" in script
    assert "prepare_velopack_input.py" in script
    assert "--packDir $velopackProduct" in script
    assert "--mainExe VibeOCR.exe" in script
    assert script.count("--packTitle VibeOCR `") == 2
    assert "--packTitle VibeOCRClassic" not in script
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "根目录的 `VibeOCR.exe`" in readme
    assert "--channel win" in script
    assert "--runtime win-x64" in script
    assert "--delta none" in script
    assert "verify_velopack_release.py" in script
    assert "python -m pip" not in script
    assert "uv venv --python" in script
    assert "uv pip sync --python $buildPython" in script
    assert "requirements-build.lock" in script
    assert script.index("uv venv --python") < script.index("& $buildPython")
    assert "updater.exe" not in script
    assert script.count("dnx --yes vpk@1.2.0 -- pack") == 2
    assert script.count("  --noInst `") == 2
    assert "--packVersion 0.0.1" in script
    assert "verify_velopack_portable_e2e.py" in script
    assert "--old-portable" in script
    assert "--new-feed $velopackOutput" in script
    # vpk 1.2.0 的 notes 选项名是 --releaseNotes（Option<FileInfo>，须存在）；
    # 传错名字（如 --notesFile）要到 CI release build 才暴露，这里锁死拼写，
    # 并要求正式 pack 前从 CHANGELOG.md 提取当前版本段落。
    assert "extract_release_notes.py" in script
    assert script.count("--releaseNotes $releaseNotes") == 1

    e2e = (ROOT / "scripts" / "verify_velopack_portable_e2e.py").read_text(
        encoding="utf-8"
    )
    entry_script = (ROOT / "scripts" / "classic_release_entry.py").read_text(
        encoding="utf-8"
    )
    assert "ThreadingHTTPServer" in e2e
    assert "subprocess.Popen" in e2e
    assert "old-content-" in e2e
    assert "shutil.move" in e2e
    assert "VIBEOCR_SELF_TEST_VELOPACK_UPDATE" in e2e
    assert "probe_runtime_launch" in entry_script
    assert "client.ensure(install_component_ids=())" in entry_script
    assert '"process_id": os.getpid()' in entry_script
    assert "_wait_for_evidence_writer_exit(evidence" in e2e
    assert e2e.index("_wait_for_evidence_writer_exit(evidence") < e2e.index(
        "shutil.move"
    )


def test_release_contract_publishes_only_exact_velopack_assets() -> None:
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))
    required = set(config["release"]["required_assets"])
    smoke = config["ci"]["release_smoke"]
    build_script = (ROOT / "scripts/build-release.ps1").read_text(encoding="utf-8")

    # Portable-only：不再发布 Setup 与其 checksum sidecar；NUPKG/feed 仅服务
    # Velopack 自更新，用户可见交付只有 Portable.zip。
    assert required == {
        "component-lock.json",
        "frontend-protocol-lock.json",
        "SBOM.spdx.json",
        "VibeOCRClassic-*-full.nupkg",
        "VibeOCRClassic-v{version}-win-x64.zip",
        "releases.win.json",
    }
    assert "VibeOCRClassic-win-Setup.exe" not in required
    assert "VibeOCRClassic-win-Setup.exe.sha256" not in required
    assert any("scripts/verify_velopack_release.py" in command for command in smoke)
    assert "--portable-name" in smoke[1]
    assert "VibeOCRClassic-v$Version-win-x64.zip" in build_script
    assert "--exact" in smoke[0]
    assert "VibeOCRClassic-win-Setup.exe" not in " ".join(smoke[0])


def test_velopack_verifier_returns_only_publishable_exact_set(tmp_path: Path) -> None:
    package = tmp_path / "VibeOCRClassic-1.2.3-full.nupkg"
    package.write_bytes(b"bound closure")
    (tmp_path / "VibeOCRClassic-win-Setup.exe").write_bytes(b"setup")
    (tmp_path / "VibeOCRClassic-win-Portable.zip").write_bytes(b"portable")
    (tmp_path / "assets.win.json").write_text("{}", encoding="utf-8")
    (tmp_path / "RELEASES").write_text("legacy vpk feed", encoding="utf-8")
    feed = {
        "Assets": [
            {
                "PackageId": "VibeOCRClassic",
                "Version": "1.2.3",
                "Type": "Full",
                "FileName": package.name,
                "SHA1": hashlib.sha1(package.read_bytes()).hexdigest().upper(),
                "SHA256": hashlib.sha256(package.read_bytes()).hexdigest().upper(),
                "Size": package.stat().st_size,
                "NotesMarkdown": "## 1.2.3\n\n- notes",
            }
        ]
    }
    (tmp_path / "releases.win.json").write_text(json.dumps(feed), encoding="utf-8")

    publishable = verify_velopack_release(tmp_path, "1.2.3")

    # 即使中间目录残留旧 Setup.exe，验证器也不会把它纳入发布集合。
    assert {path.name for path in publishable} == {
        package.name,
        "VibeOCRClassic-win-Portable.zip",
        "releases.win.json",
    }

    canonical = tmp_path / "VibeOCRClassic-v1.2.3-win-x64.zip"
    canonical.write_bytes(b"portable")
    canonical_publishable = verify_velopack_release(
        tmp_path,
        "1.2.3",
        portable_name=canonical.name,
    )
    assert {path.name for path in canonical_publishable} == {
        package.name,
        canonical.name,
        "releases.win.json",
    }


def test_velopack_verifier_rejects_feed_not_bound_to_package(tmp_path: Path) -> None:
    package = tmp_path / "VibeOCRClassic-1.2.3-full.nupkg"
    package.write_bytes(b"bound closure")
    (tmp_path / "VibeOCRClassic-win-Setup.exe").write_bytes(b"setup")
    (tmp_path / "VibeOCRClassic-win-Portable.zip").write_bytes(b"portable")
    (tmp_path / "releases.win.json").write_text(
        json.dumps(
            {
                "Assets": [
                    {
                        "PackageId": "VibeOCRClassic",
                        "Version": "1.2.3",
                        "Type": "Full",
                        "FileName": package.name,
                        "SHA1": hashlib.sha1(package.read_bytes()).hexdigest().upper(),
                        "SHA256": "0" * 64,
                        "Size": package.stat().st_size,
                        "NotesMarkdown": "## 1.2.3\n\n- notes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="SHA256"):
        verify_velopack_release(tmp_path, "1.2.3")


def test_velopack_verifier_rejects_feed_without_release_notes(
    tmp_path: Path,
) -> None:
    package = tmp_path / "VibeOCRClassic-1.2.3-full.nupkg"
    package.write_bytes(b"bound closure")
    (tmp_path / "VibeOCRClassic-win-Portable.zip").write_bytes(b"portable")
    (tmp_path / "releases.win.json").write_text(
        json.dumps(
            {
                "Assets": [
                    {
                        "PackageId": "VibeOCRClassic",
                        "Version": "1.2.3",
                        "Type": "Full",
                        "FileName": package.name,
                        "SHA1": hashlib.sha1(package.read_bytes()).hexdigest().upper(),
                        "SHA256": hashlib.sha256(package.read_bytes())
                        .hexdigest()
                        .upper(),
                        "Size": package.stat().st_size,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # 缺 NotesMarkdown 的 feed 会让"发现新版本"弹窗没有更新日志，fail closed。
    with pytest.raises(RuntimeError, match="release notes"):
        verify_velopack_release(tmp_path, "1.2.3")


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
    assert "--phase plan" in workflow
    assert "--phase finalize" in workflow
    assert "name: required" in workflow
    assert "[string]$ReleaseInput" in script
    assert "Join-Path $inputs 'protocol-sdk'" in script
    assert "frontend-protocol-lock.json" in script
    assert "Resolve-ProtocolSdkWheel 'vibeocr_runtime_contracts'" in script
    assert "Resolve-ProtocolSdkWheel 'vibeocr_runtime_client'" in script
    assert "Get-ChildItem $protocolSdk -Filter" not in script
    assert "v0.7.0" not in script
    assert "v0.7.0" not in workflow
    assert not any(
        dependency.startswith("vibeocr-backend")
        for dependency in project["project"]["dependencies"]
    )
    assert "httpx>=0.28.1" in project["project"]["dependencies"]
    assert "pillow>=12.3.0" in project["project"]["dependencies"]
    build_input = (ROOT / "scripts" / "requirements-build.in").read_text(
        encoding="utf-8"
    )
    build_lock = (ROOT / "scripts" / "requirements-build.lock").read_text(
        encoding="utf-8"
    )
    for requirement in (
        "build==1.5.0",
        "hatchling==1.27.0",
        "pyinstaller==6.21.0",
        "pyside6==6.11.1",
        "qasync==0.28.0",
        "numpy==2.5.2",
        "httpx==0.28.1",
        "jsonschema==4.26.0",
        "pillow==12.3.0",
        "velopack==1.2.0",
    ):
        assert requirement in build_input
        assert requirement in build_lock
    assert "--hash=sha256:" in build_lock
    generation_command = (
        "uv pip compile scripts/requirements-build.in --output-file "
        "scripts/requirements-build.lock --python-version 3.13 --generate-hashes "
        "--no-emit-index-url"
    )
    assert generation_command in build_lock.replace("#    ", "")
    locked_records = {
        line.split(" \\", maxsplit=1)[0]
        for line in build_lock.splitlines()
        if "==" in line and line.endswith(" \\")
    }
    assert set(build_input.splitlines()) <= locked_records
    assert "'--collect-submodules', 'vibeocr.backend'" not in script
    assert "'--collect-data', 'vibeocr.backend'" not in script
    assert "python -m pip install --no-deps" not in script
    assert "vibeocr_backend-$backendVersion" not in script


def _policy_bound_component_lock(policy: dict[str, object]) -> dict[str, object]:
    backend = policy["backend"]
    assert isinstance(backend, dict)
    return {
        "schema_version": 1,
        "backend": {
            "repository": backend["repository"],
            "accelerator": backend["accelerator"],
            "version": "0.13.0",
        },
        "protocol": {
            "repository": "FelixJI/vibeocr-protocol",
            "version": "2.7.0",
        },
        "required_capabilities": policy["required_capabilities"],
    }


def test_artifact_verifier_uses_product_policy_capability_closure(
    tmp_path: Path,
) -> None:
    policy_path = ROOT / "component-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    required = policy["required_capabilities"]
    assert isinstance(required, list)
    assert "runtime.maintenance.v2" in required

    component_lock = tmp_path / "component-lock.json"
    lock = _policy_bound_component_lock(policy)
    component_lock.write_text(json.dumps(lock), encoding="utf-8")
    verify_component_policy_binding(component_lock, policy_path)

    lock["required_capabilities"] = [
        capability for capability in required if capability != "runtime.maintenance.v2"
    ]
    component_lock.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(RuntimeError, match="differs from component policy"):
        verify_component_policy_binding(component_lock, policy_path)


def test_artifact_verifier_rejects_accelerator_outside_product_policy(
    tmp_path: Path,
) -> None:
    policy_path = ROOT / "component-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lock = _policy_bound_component_lock(policy)
    backend = lock["backend"]
    assert isinstance(backend, dict)
    backend["accelerator"] = "nvidia_cuda"
    component_lock = tmp_path / "component-lock.json"
    component_lock.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(RuntimeError, match="accelerator differs from component policy"):
        verify_component_policy_binding(component_lock, policy_path)


def test_artifact_verifier_rejects_protocol_major_outside_product_policy(
    tmp_path: Path,
) -> None:
    policy_path = ROOT / "component-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lock = _policy_bound_component_lock(policy)
    protocol = lock["protocol"]
    assert isinstance(protocol, dict)
    protocol["version"] = "3.0.0"
    component_lock = tmp_path / "component-lock.json"
    component_lock.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="Protocol major differs from component policy"
    ):
        verify_component_policy_binding(component_lock, policy_path)


def test_release_build_passes_policy_to_artifact_verifier() -> None:
    build_script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_pyside_artifact.py").read_text(
        encoding="utf-8"
    )

    assert "verify_pyside_artifact.py') $product --policy $policy" in build_script
    assert 'parser.add_argument("--policy", type=Path, required=True)' in verifier
    assert "verify_component_policy_binding(lock_path, args.policy)" in verifier
    assert "expected_capabilities =" not in verifier


def test_release_build_reuses_the_ci_verified_component_input() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))
    _prefix, release_input_and_rest = script.split("if ($ReleaseInput) {", maxsplit=1)
    supplied_input_branch, fallback_and_rest = release_input_and_rest.split(
        "} else {", maxsplit=1
    )
    fallback_branch, _rest = fallback_and_rest.split("\n}\n", maxsplit=1)

    assert config["ci"]["e2e"] == [
        [
            "python",
            "scripts/verify_component_release_input.py",
            "--release-input",
            "build/automation/release-input",
        ]
    ]
    assert "verify_component_release_input.py" not in supplied_input_branch
    assert "verify_component_release_input.py" in fallback_branch
    assert "gh attestation verify" not in script


def test_release_build_keeps_component_paths_for_product_binding() -> None:
    script = (ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8")

    assert "$protocol = Join-Path $inputs 'protocol'" in script
    assert "$backend = Join-Path $inputs 'backend'" in script
    assert "--protocol-release-dir $protocol" in script
    assert "--backend-release-dir $backend" in script


def test_protocol_sdk_dependencies_match_minor_compatibility_policy() -> None:
    config = json.loads((ROOT / ".ci/project.json").read_text(encoding="utf-8"))
    policy = json.loads((ROOT / "component-policy.json").read_text(encoding="utf-8"))
    project = tomllib.loads(
        (ROOT / "apps" / "vibeocr-pyside" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    compatibility = config["project"]["protocol_compatibility"]
    dependencies = set(project["project"]["dependencies"])

    assert compatibility == {"supported_majors": [2], "minor_compatible": True}
    assert policy["protocol"]["sdk_version"] == "2.8.0"
    assert policy["protocol"]["version"] == "2.0.0"
    assert "vibeocr-runtime-contracts>=2.8.0,<3.0.0" in dependencies
    assert "vibeocr-runtime-client>=2.8.0,<3.0.0" in dependencies


def test_artifact_frontend_protocol_lock_requires_hash_and_same_major(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "frontend-protocol-lock.json"
    lock_path.write_text('{"version":"2.4.0"}', encoding="utf-8")
    digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    assert _verify_frontend_protocol_lock(
        tmp_path,
        {"frontend_protocol_lock_sha256": digest},
        {"protocol": {"version": "2.3.0"}},
    ) == {"version": "2.4.0"}

    with pytest.raises(RuntimeError, match="majors differ"):
        _verify_frontend_protocol_lock(
            tmp_path,
            {"frontend_protocol_lock_sha256": digest},
            {"protocol": {"version": "3.0.0"}},
        )

    lock_path.write_text('{"version":"2.4.1"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        _verify_frontend_protocol_lock(
            tmp_path,
            {"frontend_protocol_lock_sha256": digest},
            {"protocol": {"version": "2.3.0"}},
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


def test_runtime_layout_requires_single_static_path_under_state(tmp_path: Path) -> None:
    good = {
        "accelerator": "cpu",
        "runtime_root": str(tmp_path / "state" / "runtime"),
    }
    _verify_runtime_layout(good, tmp_path, "cpu")

    with pytest.raises(RuntimeError, match="invalid accelerator"):
        _verify_runtime_layout(
            {
                "accelerator": "nvidia_cuda",
                "runtime_root": str(tmp_path / "state" / "runtime"),
            },
            tmp_path,
            "cpu",
        )

    with pytest.raises(RuntimeError, match="escaped"):
        _verify_runtime_layout(
            {
                "accelerator": "cpu",
                "runtime_root": str(tmp_path / "state" / "runtimes" / "cpu"),
            },
            tmp_path,
            "cpu",
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


def test_bound_installer_inspect_uses_backend_timeout_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def run_installer(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        request = json.loads(command[2])
        state = {
            "status": "missing",
            "integrity": "not-installed",
            "accelerator": "cpu",
            "runtime_root": str(tmp_path / "state" / "runtime"),
        }
        envelope = {
            "protocol_version": 2,
            "ok": True,
            "operation": request["operation"],
            "state": state,
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(envelope),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run_installer)

    _verify_bound_installer_inspect(tmp_path, {}, b"installer", "cpu")

    assert observed["timeout"] == 60.0


def test_bound_installer_inspect_reports_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def time_out(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, float(kwargs["timeout"]))

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(RuntimeError, match="inspect timed out after 60 seconds"):
        _verify_bound_installer_inspect(tmp_path, {}, b"installer", "cpu")


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
