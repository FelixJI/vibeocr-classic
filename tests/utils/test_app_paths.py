"""app_paths 模块测试（应用路径单一边界 resolve_app_paths）。

覆盖成功路径、失败路径与边界条件。重点验证：
- 非法 profile 抛 ValueError；
- production / winui-dev 两种 profile 的路径拼装与隔离（旁路不碰正式配置）；
- _normalize_executable 对可执行文件后缀/已存在文件/目录的归一化；
- get_install_root 在 frozen / 源码模式下的不同根。
"""

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
    get_install_root,
    resolve_app_paths,
)


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


def test_resolve_app_paths_production_paths(tmp_path):
    """production profile 各路径在 install_root 下正确拼装。"""
    paths = resolve_app_paths(tmp_path, profile="production")
    assert paths.install_root == tmp_path.resolve()
    assert paths.data_root == tmp_path / DATA_DIR
    assert paths.runtime_root == tmp_path / RUNTIME_DIR
    assert paths.model_cache_root == tmp_path / MODEL_CACHE_DIR
    assert paths.output_root == tmp_path / OUTPUT_DIR
    assert paths.config_file == tmp_path / CONFIG_DIR / CONFIG_FILENAME


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
