"""app_paths 模块测试（应用路径单一边界 resolve_app_paths）。

覆盖成功路径、失败路径与边界条件。重点验证：
- 非法 profile 抛 ValueError；
- production / winui-dev 两种 profile 的路径拼装与隔离（旁路不碰正式配置）；
- _normalize_executable 对可执行文件后缀/已存在文件/目录的归一化；
- get_install_root 在 frozen / 源码模式下的不同根。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vibeocr.classic.app_paths import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    DATA_DIR,
    MODEL_CACHE_DIR,
    OUTPUT_DIR,
    PROFILES_DIR,
    RUNTIME_DIR,
    STATE_DIR,
    EnvironmentTestDataRootResolver,
    PortableRootResolver,
    PortableStateError,
    VelopackRootResolver,
    activate_portable_state,
    ensure_portable_state_usable,
    get_bundle_root,
    get_bundled_changelog_path,
    get_bundled_resources_dir,
    get_install_root,
    resolve_app_paths,
)


def _directory_reparse(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


# ---------------------------------------------------------------------------
# resolve_app_paths: profile 校验
# ---------------------------------------------------------------------------


def test_resolve_app_paths_invalid_profile(tmp_path):
    """非法 profile 抛 ValueError 且错误信息含允许列表。"""
    with pytest.raises(ValueError, match="unsupported profile"):
        resolve_app_paths(tmp_path, profile="evil")


def test_resolve_app_paths_invalid_profile_message_lists_allowed(tmp_path):
    """错误信息列出允许的 profile。"""
    with pytest.raises(ValueError) as exc_info:
        resolve_app_paths(tmp_path, profile="dev")
    assert "production" in str(exc_info.value)
    assert "winui-dev" in str(exc_info.value)


# ---------------------------------------------------------------------------
# resolve_app_paths: production profile
# ---------------------------------------------------------------------------


def test_resolve_app_paths_production_paths(tmp_path, monkeypatch):
    """production 可变路径统一位于 <portable-root>/state。"""
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable_root = tmp_path / "install"
    paths = resolve_app_paths(portable_root, profile="production")
    state_root = (portable_root / STATE_DIR).resolve()
    assert paths.install_root == portable_root.resolve()
    assert paths.state_root == state_root
    assert paths.data_root == state_root / DATA_DIR
    assert paths.runtime_root == state_root / RUNTIME_DIR
    assert paths.model_cache_root == state_root / MODEL_CACHE_DIR
    assert paths.output_root == state_root / OUTPUT_DIR
    assert paths.config_file == state_root / CONFIG_DIR / CONFIG_FILENAME


def _write_velopack_layout(root: Path) -> Path:
    current = root / "current"
    current.mkdir(parents=True)
    (current / "sq.version").write_text("{}", encoding="utf-8")
    (current / "VibeOCR.exe").write_bytes(b"app")
    (root / "Update.exe").write_bytes(b"updater")
    (root / ".portable").write_text("", encoding="utf-8")
    return current / "VibeOCR.exe"


def test_velopack_root_resolver_uses_stable_root_app_dir(tmp_path: Path) -> None:
    executable = _write_velopack_layout(tmp_path / "portable")

    assert VelopackRootResolver(executable).resolve() == executable.parents[1]


@pytest.mark.parametrize("missing", ["sq.version", "Update.exe", ".portable"])
def test_velopack_root_resolver_rejects_ambiguous_current_layout(
    tmp_path: Path, missing: str
) -> None:
    executable = _write_velopack_layout(tmp_path / "portable")
    target = (
        executable.parent / missing
        if missing == "sq.version"
        else executable.parents[1] / missing
    )
    target.unlink()

    with pytest.raises(PortableStateError, match="Velopack"):
        VelopackRootResolver(executable).resolve()


def test_portable_root_resolver_never_falls_back_to_user_dirs(tmp_path, monkeypatch):
    """默认解析固定 portable 根；普通环境变量不能重定向正式状态根。"""
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    assert PortableRootResolver(portable).resolve() == (portable / "state").resolve()

    monkeypatch.setenv("VIBEOCR_CLASSIC_DATA_ROOT", str(tmp_path / "injected-state"))
    assert PortableRootResolver(portable).resolve() == (portable / "state").resolve()


def test_explicit_test_override_requires_mode_and_matching_nonce(
    tmp_path, monkeypatch
):
    nonce = "a" * 32
    injected = tmp_path / f"smoke-state-{nonce}"
    monkeypatch.setenv("VIBEOCR_CLASSIC_DATA_ROOT", str(injected))
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_MODE", "artifact-smoke")
    monkeypatch.setenv("VIBEOCR_CLASSIC_TEST_NONCE", nonce)
    paths = resolve_app_paths(
        tmp_path / "install",
        profile="production",
        data_root_resolver=EnvironmentTestDataRootResolver.from_environment(),
    )
    assert paths.state_root == injected.resolve()
    assert paths.config_file.parent == (injected / CONFIG_DIR).resolve()


def test_test_override_rejects_ordinary_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEOCR_CLASSIC_DATA_ROOT", str(tmp_path / "outside"))
    monkeypatch.delenv("VIBEOCR_CLASSIC_TEST_MODE", raising=False)
    monkeypatch.delenv("VIBEOCR_CLASSIC_TEST_NONCE", raising=False)

    with pytest.raises(PortableStateError, match="test-only"):
        EnvironmentTestDataRootResolver.from_environment()


def test_resolve_app_paths_production_all_absolute(tmp_path):
    """所有路径均为绝对路径。"""
    paths = resolve_app_paths(tmp_path, profile="production")
    for p in (
        paths.install_root,
        paths.data_root,
        paths.runtime_root,
        paths.model_cache_root,
        paths.output_root,
        paths.config_file,
    ):
        assert p.is_absolute()


def test_resolve_app_paths_default_profile_is_production(tmp_path):
    """默认 profile 是 production。"""
    explicit = resolve_app_paths(tmp_path, profile="production")
    default = resolve_app_paths(tmp_path)
    assert explicit == default


# ---------------------------------------------------------------------------
# resolve_app_paths: winui-dev 旁路 profile
# ---------------------------------------------------------------------------


def test_resolve_app_paths_winui_dev_uses_profile_root(tmp_path):
    """winui-dev profile 路径在 data/profiles/winui-dev 下。"""
    paths = resolve_app_paths(tmp_path, profile="winui-dev")
    profile_root = tmp_path / PROFILES_DIR / "winui-dev"
    assert paths.data_root == profile_root
    assert paths.runtime_root == profile_root / RUNTIME_DIR
    assert paths.model_cache_root == profile_root / MODEL_CACHE_DIR
    assert paths.output_root == profile_root / OUTPUT_DIR
    assert paths.config_file == profile_root / CONFIG_DIR / CONFIG_FILENAME


def test_resolve_app_paths_winui_dev_does_not_touch_production(tmp_path):
    """旁路 profile 的 config_file 与正式 config_file 不同（隔离）。"""
    prod = resolve_app_paths(tmp_path, profile="production")
    dev = resolve_app_paths(tmp_path, profile="winui-dev")
    assert dev.config_file != prod.config_file
    assert dev.data_root != prod.data_root
    # 正式 config 路径不含 profiles/winui-dev
    assert "profiles" not in prod.config_file.parts


def test_resolve_app_paths_install_root_same_across_profiles(tmp_path):
    """两种 profile 共享同一 install_root。"""
    prod = resolve_app_paths(tmp_path, profile="production")
    dev = resolve_app_paths(tmp_path, profile="winui-dev")
    assert prod.install_root == dev.install_root


# ---------------------------------------------------------------------------
# resolve_app_paths: executable 归一化
# ---------------------------------------------------------------------------


def test_resolve_app_paths_exe_file_takes_parent(tmp_path):
    """传入 .exe 文件路径时取其 parent 作 install_root。"""
    exe = tmp_path / "VibeOCR.exe"
    paths = resolve_app_paths(exe)
    assert paths.install_root == tmp_path.resolve()


def test_resolve_app_paths_directory_passthrough(tmp_path):
    """传入目录时直接用作 install_root。"""
    paths = resolve_app_paths(tmp_path)
    assert paths.install_root == tmp_path.resolve()


def test_resolve_app_paths_str_path(tmp_path):
    """接受 str 路径输入。"""
    paths = resolve_app_paths(str(tmp_path))
    assert paths.install_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# _normalize_executable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".exe", ".app", ".bin"])
def test_normalize_executable_executable_suffixes(tmp_path, suffix):
    """可执行文件后缀（.exe/.app/.bin）取 parent。"""
    from vibeocr.classic.app_paths import _normalize_executable

    f = tmp_path / f"app{suffix}"
    assert _normalize_executable(f) == tmp_path.resolve()


def test_normalize_executable_existing_file(tmp_path):
    """已存在文件（无 exe 后缀）取 parent。"""
    from vibeocr.classic.app_paths import _normalize_executable

    f = tmp_path / "launcher"
    f.write_text("x")
    assert _normalize_executable(f) == tmp_path.resolve()


def test_normalize_executable_directory(tmp_path):
    """目录原样返回。"""
    from vibeocr.classic.app_paths import _normalize_executable

    assert _normalize_executable(tmp_path) == tmp_path.resolve()


def test_normalize_executable_nonexistent_no_suffix(tmp_path):
    """不存在的无后缀路径原样返回（不取 parent）。"""
    from vibeocr.classic.app_paths import _normalize_executable

    ghost = tmp_path / "ghost"
    assert _normalize_executable(ghost) == ghost.resolve()


def test_normalize_executable_uppercase_suffix(tmp_path):
    """大写后缀 .EXE 也被识别（大小写不敏感）。"""
    from vibeocr.classic.app_paths import _normalize_executable

    f = tmp_path / "APP.EXE"
    assert _normalize_executable(f) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# AppPaths dataclass 不可变性
# ---------------------------------------------------------------------------


def test_app_paths_is_frozen(tmp_path):
    """AppPaths 是 frozen dataclass（不可变）。"""
    paths = resolve_app_paths(tmp_path)
    with pytest.raises(Exception):
        paths.install_root = tmp_path / "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_install_root
# ---------------------------------------------------------------------------


def test_get_install_root_source_mode():
    """源码模式下 get_install_root 返回仓库根。

    app_paths.py 在 apps/vibeocr-pyside/src/vibeocr/classic/app_paths.py，
    parents[5] = 仓库根。本测试文件在 tests/utils/，parents[2] = 同一仓库根。
    """
    assert not getattr(sys, "frozen", False)
    root = get_install_root()
    expected = Path(__file__).resolve().parents[2]  # tests/utils/ → repo root
    assert root == expected


def test_get_install_root_frozen_mode(monkeypatch, tmp_path):
    """frozen 模式下 get_install_root 返回 sys.executable.parent。"""
    fake_exe = tmp_path / "VibeOCR.exe"
    fake_exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    try:
        root = get_install_root()
        assert root == tmp_path.resolve()
    finally:
        # 恢复，避免污染后续测试
        monkeypatch.setattr(sys, "frozen", False, raising=False)


def test_get_bundle_root_prefers_meipass(monkeypatch, tmp_path):
    """PyInstaller 资源根必须使用 _MEIPASS，而非 exe 同级目录。"""
    bundle_root = tmp_path / "_internal"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert get_bundle_root() == bundle_root


def test_get_bundled_resources_dir_uses_meipass(monkeypatch, tmp_path):
    """frozen 资源目录位于 _MEIPASS/resources，不要求目录已存在。"""
    bundle_root = tmp_path / "_internal"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert get_bundled_resources_dir() == bundle_root / "resources"


def test_get_bundled_resources_dir_uses_repository_in_source_mode(monkeypatch):
    """源码模式的资源目录位于 Classic 仓库根。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    repository_root = Path(__file__).resolve().parents[2]

    assert get_bundle_root() == repository_root
    assert get_bundled_resources_dir() == repository_root / "resources"


def test_get_bundled_changelog_path_prefers_meipass(monkeypatch, tmp_path):
    """_MEIPASS 中的内置 changelog 优先于 exe 同级用户文件。"""
    bundle_root = tmp_path / "_internal"
    executable_root = tmp_path / "app"
    bundle_root.mkdir()
    executable_root.mkdir()
    bundled = bundle_root / "CHANGELOG.md"
    bundled.write_text("bundled", encoding="utf-8")
    (executable_root / "CHANGELOG.md").write_text("fallback", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable_root / "VibeOCR.exe"))

    assert get_bundled_changelog_path() == bundled


def test_get_bundled_changelog_path_falls_back_to_executable_dir(monkeypatch, tmp_path):
    """_MEIPASS 中缺失时，frozen 应回退到 exe 同级 changelog。"""
    bundle_root = tmp_path / "_internal"
    executable_root = tmp_path / "app"
    bundle_root.mkdir()
    executable_root.mkdir()
    fallback = executable_root / "CHANGELOG.md"
    fallback.write_text("fallback", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable_root / "VibeOCR.exe"))

    assert get_bundled_changelog_path() == fallback


def test_get_bundled_changelog_path_uses_repository_in_source_mode(monkeypatch):
    """源码模式从 Classic 仓库根读取 changelog。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    repository_changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

    assert repository_changelog.is_file()
    assert get_bundled_changelog_path() == repository_changelog


def test_get_bundled_changelog_path_returns_none_when_frozen_candidates_absent(
    monkeypatch, tmp_path
):
    """frozen 的内置与 exe 同级 changelog 都缺失时返回 None。"""
    bundle_root = tmp_path / "_internal"
    executable_root = tmp_path / "app"
    bundle_root.mkdir()
    executable_root.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable_root / "VibeOCR.exe"))

    assert get_bundled_changelog_path() is None


# ---------------------------------------------------------------------------
# 便携状态根探针：containment / 可写性 / fail closed
# ---------------------------------------------------------------------------


def test_ensure_portable_state_usable_creates_and_probes(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    state = portable / STATE_DIR

    resolved = ensure_portable_state_usable(state, portable_root=portable)

    assert resolved == state.resolve()
    assert state.is_dir()
    assert not list(state.glob(".write-probe*"))


def test_ensure_portable_state_rejects_state_declared_as_symlink(tmp_path, monkeypatch):
    """portable/state 上的 junction/symlink 一律 fail closed。"""
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    portable.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    declared = portable / STATE_DIR
    _directory_reparse(declared, redirected)
    # 解析后仍在 tmp 内（可写），但声明路径是重定向 → 必须拒绝
    with pytest.raises(PortableStateError, match="junction/symlink"):
        ensure_portable_state_usable(declared, portable_root=portable)


def test_ensure_portable_state_rejects_escape_outside_portable_root(
    tmp_path, monkeypatch
):
    """解析后越界（.. 或重定向）fail closed。"""
    import os as _os

    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    outside = tmp_path / "outside-state"
    declared = (
        portable / ".." / outside.name
        if _os.name != "nt"
        else Path(str(portable) + f"\\..\\{outside.name}")
    )

    with pytest.raises(PortableStateError, match="越界"):
        ensure_portable_state_usable(declared, portable_root=portable)


def test_ensure_portable_state_rejects_unwritable_root(
    tmp_path, monkeypatch, mocker=None
):
    """create/write/rename/delete 探针失败（模拟只读）时 fail closed。"""
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    state = portable / STATE_DIR
    state.mkdir(parents=True)

    from unittest.mock import patch

    with patch(
        "vibeocr.classic.app_paths.os.replace",
        side_effect=PermissionError("read-only"),
    ):
        with pytest.raises(PortableStateError, match="不可用"):
            ensure_portable_state_usable(state, portable_root=portable)


def test_ensure_portable_state_rejects_state_blocked_by_file(tmp_path, monkeypatch):
    """state 位置被同名文件占据（无法创建目录）时 fail closed。"""
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    portable.mkdir()
    (portable / STATE_DIR).write_text("blocked", encoding="utf-8")

    with pytest.raises(PortableStateError, match="不可用"):
        ensure_portable_state_usable(portable / STATE_DIR, portable_root=portable)


def test_activate_portable_state_builds_state_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable 便携"

    paths = activate_portable_state(portable)

    assert paths.state_root == (portable / STATE_DIR).resolve()
    for relative in (
        "config",
        "cache",
        "logs",
        "runtime",
        "models",
        "output",
        "update",
        "locks",
        "web/qtwebengine/cache",
        "web/qtwebengine/persistent",
        "temp/clipboard",
    ):
        assert (paths.state_root / relative).is_dir(), relative
    assert paths.clipboard_temp_dir == paths.state_root / "temp" / "clipboard"
    assert (
        paths.webengine_cache_dir == paths.state_root / "web" / "qtwebengine" / "cache"
    )


def test_activate_portable_state_migrates_current_state_to_root_app_dir(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    executable = _write_velopack_layout(tmp_path / "portable")
    legacy = executable.parent / "state"
    (legacy / "config").mkdir(parents=True)
    (legacy / "config" / "app_settings.json").write_text(
        '{"language": "zh-CN"}', encoding="utf-8"
    )

    paths = activate_portable_state(executable)

    assert paths.install_root == executable.parents[1]
    assert paths.state_root == executable.parents[1] / "state"
    assert paths.config_file.read_text(encoding="utf-8") == '{"language": "zh-CN"}'
    assert legacy.is_dir()  # 源在 current 被更新替换前保持可恢复。


def test_activate_portable_state_migrates_legacy_runtimes_name(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    state = tmp_path / "portable/state"
    legacy = state / "runtimes"
    legacy.mkdir(parents=True)
    (legacy / "runtime-id.txt").write_text("bound-runtime", encoding="utf-8")

    paths = activate_portable_state(tmp_path / "portable")

    assert paths.runtime_root == state / "runtime"
    assert (paths.runtime_root / "runtime-id.txt").read_text(encoding="utf-8") == (
        "bound-runtime"
    )
    assert legacy.is_dir()


@pytest.mark.parametrize("relative", ["config", "logs", "cache", "models", "runtime"])
def test_activate_portable_state_rejects_reparse_in_each_writable_root(
    tmp_path, monkeypatch, relative: str
):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    state = portable / "state"
    state.mkdir(parents=True)
    outside = tmp_path / f"outside-{relative}"
    outside.mkdir()
    redirected = state / relative
    _directory_reparse(redirected, outside)

    with pytest.raises(PortableStateError, match="junction/symlink|reparse"):
        activate_portable_state(portable)


def test_activate_portable_state_fail_closed_leaves_no_partial_layout(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    portable = tmp_path / "portable"
    portable.mkdir()
    (portable / STATE_DIR).write_text("blocked", encoding="utf-8")

    with pytest.raises(PortableStateError):
        activate_portable_state(portable)
    # 不回退、不在别处创建目录
    assert not (tmp_path / "VibeOCRClassicData").exists()
    import tempfile as _tempfile
    from pathlib import Path as _Path

    assert not (_Path(_tempfile.gettempdir()) / "VibeOCRClassicData").exists()


def test_app_paths_derived_directories_stay_under_state(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBEOCR_CLASSIC_DATA_ROOT", raising=False)
    paths = resolve_app_paths(tmp_path / "portable", profile="production")

    for directory in (
        paths.logs_root,
        paths.cache_root,
        paths.temp_root,
        paths.clipboard_temp_dir,
        paths.update_root,
        paths.locks_root,
        paths.webengine_cache_dir,
        paths.webengine_persistent_dir,
    ):
        assert (
            paths.state_root
            == directory.parents[
                len(directory.parents) - len(paths.state_root.parents) - 1
            ]
            or paths.state_root in directory.parents
        )
