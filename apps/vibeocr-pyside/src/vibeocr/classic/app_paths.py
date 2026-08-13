"""应用路径单一边界：resolve_app_paths()。

集中定义只读安装根、稳定外部数据/Runtime 路径，以及只读 bundle
资源和 changelog 路径。Classic 的 frozen/dev 路径规则全部收敛到此处，
避免 UI 或更新流程为定位自身资源而导入 Backend。

profile 机制：
- ``"production"``（默认）：可变状态统一位于
  ``%LocalAppData%/VibeOCRClassicData``，与 Velopack 安装根分离。
- ``"winui-dev"``：旁路开发 profile，路径解析到 ``install_root/data/profiles/winui-dev``，
  **不触碰**正式配置。Phase 0–4 期间 WinUI 旁路版必须用此 profile。

此模块是 UI-free 边界（不加载任何 GUI 框架），可被 WorkerHost 和前端壳共享。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 正式 profile 下的子目录名（相对 install_root）
CONFIG_DIR = "config"
RUNTIME_DIR = "runtimes"
MODEL_CACHE_DIR = "models"
OUTPUT_DIR = "output"
DATA_DIR = "data"
CONFIG_FILENAME = "app_settings.json"
STABLE_DATA_ROOT_NAME = "VibeOCRClassicData"

# 旁路 profile 根目录（相对 install_root）
PROFILES_DIR = "data/profiles"

# 允许的旁路 profile 白名单（Phase 0–4 只有 winui-dev）
_ALLOWED_PROFILES: frozenset[str] = frozenset({"production", "winui-dev"})
_active_app_paths: AppPaths | None = None


class DataRootResolver(Protocol):
    """Resolve the stable mutable root without consulting product contents."""

    def resolve(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class LocalAppDataRootResolver:
    """Production resolver for the stable external Classic data root.

    ``local_app_data`` is an explicit test/development seam. Production callers
    omit it and use the Windows ``LOCALAPPDATA`` known folder environment value.
    """

    local_app_data: Path | None = None

    def resolve(self) -> Path:
        configured_root = os.environ.get("VIBEOCR_CLASSIC_DATA_ROOT")
        if configured_root:
            return Path(configured_root).resolve()
        base = self.local_app_data
        if base is None:
            configured = os.environ.get("LOCALAPPDATA")
            base = Path(configured) if configured else Path.home() / "AppData" / "Local"
        return (Path(base) / STABLE_DATA_ROOT_NAME).resolve()


def get_install_root() -> Path:
    """Return the Classic product root without consulting Backend code."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[5]


def get_bundle_root() -> Path:
    """Return the read-only bundle root for packaged application assets."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass is not None else get_install_root()


def get_bundled_resources_dir() -> Path:
    """Return the bundled ``resources`` directory without checking existence."""
    return get_bundle_root() / "resources"


def get_bundled_changelog_path() -> Path | None:
    """Return the first bundled changelog path that exists."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        bundled = Path(meipass) / "CHANGELOG.md"
        if bundled.exists():
            return bundled
        executable_fallback = Path(sys.executable).resolve().parent / "CHANGELOG.md"
        if executable_fallback.exists():
            return executable_fallback
    else:
        source_changelog = get_install_root() / "CHANGELOG.md"
        if source_changelog.exists():
            return source_changelog
    return None


@dataclass(frozen=True, slots=True)
class AppPaths:
    """应用路径集合（不可变）。

    所有路径均为绝对路径（resolve()）。

    Attributes:
        install_root: 安装根目录（exe 所在目录或源码根）。
        state_root: 可变状态根；production 位于安装根之外。
        data_root: 数据根目录（用户数据、缓存）。
        runtime_root: 内容寻址 Runtime 存储根目录。
        model_cache_root: 模型缓存目录。
        output_root: 输出目录（OCR/PDF 产物）。
        config_file: 主配置文件路径。
    """

    install_root: Path
    state_root: Path
    data_root: Path
    runtime_root: Path
    model_cache_root: Path
    output_root: Path
    config_file: Path


def _normalize_executable(executable: str | os.PathLike[str] | Path) -> Path:
    """将 executable（目录或文件路径）归一化为安装根目录。

    - 如果是文件（如 VibeOCR.exe），取其 parent。
    - 如果是目录，直接使用。

    文件检测基于后缀名（.exe/.app 等），而非 is_file()——路径可能指向
    尚未存在的文件（测试或预创建场景）。
    """
    p = Path(executable).resolve()
    # 常见可执行文件后缀 → 取 parent（安装根 = exe 所在目录）
    if p.suffix.lower() in (".exe", ".app", ".bin"):
        return p.parent
    # 已存在的文件也取 parent
    if p.is_file():
        return p.parent
    return p


def resolve_app_paths(
    executable: str | os.PathLike[str] | Path,
    *,
    profile: str = "production",
    data_root_resolver: DataRootResolver | None = None,
) -> AppPaths:
    """解析应用路径。

    Args:
        executable: 安装根目录，或 exe 文件路径（自动取 parent）。
        profile: profile 名称。``"production"``（默认）使用稳定外部路径；
            ``"winui-dev"`` 使用旁路开发路径（``data/profiles/winui-dev``），
            不触碰正式配置。
        data_root_resolver: production 稳定根 resolver；测试/开发可显式注入。

    Returns:
        AppPaths（所有路径已 resolve() 为绝对路径）。

    Raises:
        ValueError: profile 不在允许白名单中。
    """
    if profile not in _ALLOWED_PROFILES:
        raise ValueError(
            f"unsupported profile: {profile!r}; allowed: {sorted(_ALLOWED_PROFILES)}"
        )

    install_root = _normalize_executable(executable)

    if profile == "production":
        state_root = (data_root_resolver or LocalAppDataRootResolver()).resolve()
        data_root = state_root / DATA_DIR
        runtime_root = state_root / RUNTIME_DIR
        model_cache_root = state_root / MODEL_CACHE_DIR
        output_root = state_root / OUTPUT_DIR
        config_file = state_root / CONFIG_DIR / CONFIG_FILENAME
    else:
        # 旁路 profile（如 winui-dev）：路径在 data/profiles/<profile> 下
        # 不触碰正式配置文件
        profile_root = install_root / PROFILES_DIR / profile
        state_root = profile_root
        data_root = profile_root
        runtime_root = profile_root / RUNTIME_DIR
        model_cache_root = profile_root / MODEL_CACHE_DIR
        output_root = profile_root / OUTPUT_DIR
        config_file = profile_root / CONFIG_DIR / CONFIG_FILENAME

    return AppPaths(
        install_root=install_root,
        state_root=state_root.resolve(),
        data_root=data_root,
        runtime_root=runtime_root,
        model_cache_root=model_cache_root,
        output_root=output_root,
        config_file=config_file,
    )


def resolve_legacy_app_paths(
    executable: str | os.PathLike[str] | Path,
) -> AppPaths:
    """Resolve the pre-Velopack portable mutable layout.

    This function exists only for the copy-only migration bridge. New runtime
    state must use :func:`resolve_app_paths` instead.
    """
    install_root = _normalize_executable(executable)
    return AppPaths(
        install_root=install_root,
        state_root=install_root,
        data_root=install_root / DATA_DIR,
        runtime_root=install_root / RUNTIME_DIR,
        model_cache_root=install_root / MODEL_CACHE_DIR,
        output_root=install_root / OUTPUT_DIR,
        config_file=install_root / CONFIG_DIR / CONFIG_FILENAME,
    )


def activate_app_paths(paths: AppPaths) -> None:
    """Select the verified mutable layout for this process before startup."""
    global _active_app_paths
    _active_app_paths = paths


def get_active_app_paths() -> AppPaths:
    """Return the bootstrapped layout, resolving the stable root if needed."""
    if _active_app_paths is not None:
        return _active_app_paths
    return resolve_app_paths(get_install_root())


def get_state_root() -> Path:
    """Return the root used by stateful managers and cache helpers."""
    return get_active_app_paths().state_root
