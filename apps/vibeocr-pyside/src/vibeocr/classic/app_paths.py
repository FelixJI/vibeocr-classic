"""应用路径单一边界：resolve_app_paths()。

集中定义只读安装根、便携稳定状态根以及只读 bundle 资源路径。Classic 的
frozen/dev 路径规则全部收敛到此处，避免 UI 或更新流程为定位自身资源而导入
Backend。

production profile（默认）遵循“完全便携”约束：所有产品拥有的可变状态位于
``<portable-root>/state``。``<portable-root>`` 是 exe 所在的稳定根（Velopack
Portable 解压根 / 每用户安装目录），不是会被更新替换的版本目录；生产 resolver
不做 LocalAppData/系统 Temp 回退，不可写或越界时启动 fail closed。

profile 机制：
- ``"production"``（默认）：可变状态统一位于 ``<portable-root>/state``。
- ``"winui-dev"``：旁路开发 profile，路径解析到 ``install_root/data/profiles/winui-dev``，
  **不触碰**正式配置。Phase 0–4 期间 WinUI 旁路版必须用此 profile。

跨进程 artifact smoke 可显式注入 ``EnvironmentTestDataRootResolver``；环境
适配器要求 test mode 与随机 nonce 同时匹配。普通生产环境变量不能重定向状态根。

此模块是 UI-free 边界（不加载任何 GUI 框架），可被 WorkerHost 和前端壳共享。
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 正式 profile 下的状态目录名（相对 portable root）
CONFIG_DIR = "config"
RUNTIME_DIR = "runtime"
MODEL_CACHE_DIR = "models"
OUTPUT_DIR = "output"
DATA_DIR = "data"
STATE_DIR = "state"
CONFIG_FILENAME = "app_settings.json"

# 旁路 profile 根目录（相对 install_root）
PROFILES_DIR = "data/profiles"

# 允许的旁路 profile 白名单（Phase 0–4 只有 winui-dev）
_ALLOWED_PROFILES: frozenset[str] = frozenset({"production", "winui-dev"})
_active_app_paths: AppPaths | None = None

# Windows FILE_ATTRIBUTE_REPARSE_POINT（目录 junction/symlink 探测）
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class PortableStateError(RuntimeError):
    """便携状态根不可用；启动必须 fail closed，不得回退用户目录。"""


class DataRootResolver(Protocol):
    """Resolve the portable mutable state root without consulting product contents."""

    def resolve(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class VelopackRootResolver:
    """Resolve the stable Windows ``RootAppDir`` from the executable layout.

    Velopack replaces ``current`` as a unit.  A process running from that
    directory is accepted only when the canonical marker set proves the
    relationship to its parent; ambiguous layouts fail closed.
    """

    executable: Path

    def resolve(self) -> Path:
        content_root = _lexical_executable_root(self.executable)
        if content_root.name.casefold() != "current":
            if _is_reparse_point(content_root):
                raise PortableStateError(
                    "Velopack RootAppDir 不允许经过 reparse point"
                )
            return content_root.resolve()
        root = content_root.parent
        for segment in (root, content_root):
            if _is_reparse_point(segment):
                raise PortableStateError(
                    f"Velopack RootAppDir 不允许经过 reparse point: {segment}"
                )
        required = (
            content_root / "sq.version",
            root / "Update.exe",
            root / ".portable",
        )
        reparsed = [path.name for path in required if _is_reparse_point(path)]
        if reparsed:
            raise PortableStateError(
                "Velopack RootAppDir 标记不允许是 reparse point: "
                + ", ".join(reparsed)
            )
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise PortableStateError(
                "Velopack RootAppDir 布局含糊，缺少标记: " + ", ".join(missing)
            )
        try:
            canonical_root = root.resolve(strict=True)
            content_root.resolve(strict=True).relative_to(canonical_root)
        except (OSError, ValueError) as exc:
            raise PortableStateError("Velopack current 已逃逸 RootAppDir") from exc
        return canonical_root


@dataclass(frozen=True, slots=True)
class PortableRootResolver:
    """Production resolver: ``<portable-root>/state`` without user-directory fallback.

    ``portable_root`` is the stable Velopack ``RootAppDir``.  Test overrides are
    deliberately a different injected adapter, so production semantics cannot
    be changed by an ordinary environment variable.
    """

    portable_root: Path

    def resolve(self) -> Path:
        return (self.portable_root / STATE_DIR).resolve()


@dataclass(frozen=True, slots=True)
class EnvironmentTestDataRootResolver:
    """Authenticated cross-process adapter used only by frozen artifact smoke."""

    state_root: Path

    @classmethod
    def from_environment(cls) -> EnvironmentTestDataRootResolver:
        configured = os.environ.get("VIBEOCR_CLASSIC_DATA_ROOT")
        mode = os.environ.get("VIBEOCR_CLASSIC_TEST_MODE")
        nonce = os.environ.get("VIBEOCR_CLASSIC_TEST_NONCE", "")
        if (
            not configured
            or mode != "artifact-smoke"
            or re.fullmatch(r"[0-9a-fA-F]{32,128}", nonce) is None
            or nonce.casefold() not in Path(configured).name.casefold()
        ):
            raise PortableStateError(
                "VIBEOCR_CLASSIC_DATA_ROOT 是 test-only override，"
                "必须由 artifact-smoke mode 与匹配 nonce 显式授权"
            )
        return cls(Path(configured))

    def resolve(self) -> Path:
        return self.state_root.resolve()


def _is_reparse_point(path: Path) -> bool:
    """目录 junction/symlink 探测；非 Windows 平台退化为普通 symlink 判定。"""

    try:
        st = path.stat(follow_symlinks=False)
    except OSError:
        return False
    if path.is_symlink():
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def ensure_portable_state_usable(
    state_root: Path,
    *,
    portable_root: Path | None = None,
) -> Path:
    """Containment + 可写性探针；失败抛 :class:`PortableStateError`。

    - 状态根必须是绝对路径且不是文件系统根。
    - 默认生产解析（无环境注入）时，``<portable-root>/state`` 上的目录
      junction/symlink 或解析后越界（``..``/重定向）一律 fail closed。
    - 通过 create/write/rename/delete 探针验证可写性；不可写时提示用户
      将程序移动到可写位置，不请求管理员权限、不回退 LocalAppData/Temp。
    """

    resolved = state_root.resolve()
    message = (
        "VibeOCR 状态目录不可用：请将程序移动到可写位置后重试。"
        "（不会回退或写入用户目录）"
    )
    if not resolved.is_absolute() or resolved.parent == resolved:
        raise PortableStateError(f"{message} 无效状态根: {state_root}")
    if portable_root is not None:
        declared = portable_root / STATE_DIR
        if _is_reparse_point(declared):
            raise PortableStateError(
                f"{message} {declared} 是 junction/symlink，不允许重定向状态根"
            )
        try:
            resolved.relative_to(portable_root.resolve())
        except ValueError as exc:
            raise PortableStateError(f"{message} 状态根越界: {resolved}") from exc
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / f".write-probe-{uuid.uuid4().hex[:8]}"
        probe.write_text("probe", encoding="utf-8")
        renamed = probe.with_suffix(".renamed")
        os.replace(probe, renamed)
        renamed.unlink()
    except OSError as exc:
        raise PortableStateError(f"{message} {exc}") from exc
    return resolved


def _owned_directory(state_root: Path, relative: str | Path) -> Path:
    """Safely create and return one directory owned by ``state_root``.

    Every existing and newly-created segment is checked for a symlink/junction
    before the next segment is touched, and the final resolved path must remain
    contained by the declared state root.
    """

    relative_path = Path(relative)
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise PortableStateError(f"状态子目录无效: {relative}")
    root = state_root.resolve(strict=True)
    current = state_root
    for part in relative_path.parts:
        current = current / part
        if _is_reparse_point(current):
            raise PortableStateError(
                f"状态子目录 {current} 是 junction/symlink/reparse point"
            )
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            raise PortableStateError(f"状态子目录无法创建: {current} ({exc})") from exc
        if _is_reparse_point(current):
            raise PortableStateError(f"状态子目录创建后成为 reparse point: {current}")
        try:
            current.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise PortableStateError(f"状态子目录越界: {current}") from exc
    return current.resolve(strict=True)


def _tree_entries(root: Path) -> tuple[tuple[str, bool, int], ...]:
    records: list[tuple[str, bool, int]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in os.scandir(directory):
            path = Path(item.path)
            if _is_reparse_point(path):
                raise PortableStateError(f"旧状态包含 reparse point: {path}")
            relative = path.relative_to(root).as_posix()
            if item.is_dir(follow_symlinks=False):
                records.append((relative, True, 0))
                pending.append(path)
            elif item.is_file(follow_symlinks=False):
                records.append((relative, False, item.stat().st_size))
            else:
                raise PortableStateError(f"旧状态包含不受支持的条目: {path}")
    return tuple(sorted(records))


def _migrate_legacy_current_state(content_root: Path, stable_state: Path) -> None:
    """Copy/verify/promote ``current/state`` without deleting the source."""

    legacy = content_root / STATE_DIR
    if content_root == stable_state.parent or not legacy.exists():
        return
    if _is_reparse_point(legacy):
        raise PortableStateError("旧 current/state 是 reparse point，拒绝迁移")
    if stable_state.exists():
        return
    _copy_verify_promote(legacy, stable_state, label="旧 current/state")


def _copy_verify_promote(source: Path, target: Path, *, label: str) -> None:
    """Copy one owned tree, verify exact contents, and atomically promote it."""

    source_entries = _tree_entries(source)
    staging = target.parent / f".{target.name}-migration-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, staging, symlinks=False)
        if _tree_entries(staging) != source_entries:
            raise PortableStateError(f"{label} 迁移验证失败")
        for relative, is_directory, _size in source_entries:
            if not is_directory and not filecmp.cmp(
                source / relative, staging / relative, shallow=False
            ):
                raise PortableStateError(f"{label} 文件迁移验证失败: {relative}")
        os.replace(staging, target)
    except OSError as exc:
        raise PortableStateError(f"{label} 迁移失败: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _migrate_legacy_runtime_directory(state_root: Path) -> None:
    """Preserve the pre-release ``runtimes`` tree under canonical ``runtime``."""

    legacy = state_root / "runtimes"
    target = state_root / RUNTIME_DIR
    if not legacy.exists() or target.exists():
        return
    if _is_reparse_point(legacy):
        raise PortableStateError("旧 state/runtimes 是 reparse point，拒绝迁移")
    _copy_verify_promote(legacy, target, label="旧 state/runtimes")


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

    所有路径均为绝对路径（resolve()）。派生目录（logs/cache/temp/web 等）
    以属性形式从 ``state_root`` 计算，保持手构实例的兼容性。

    Attributes:
        install_root: 便携产品根目录（exe 所在目录或源码根）。
        state_root: 可变状态根；production 位于 ``<portable-root>/state``。
        data_root: 数据根目录（sidecar/更新状态等历史子树，位于 state 内）。
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

    def owned_directory(self, relative: str | Path) -> Path:
        """Return a verified state directory, creating it segment-by-segment."""

        if not self.state_root.is_dir():
            production_root = (
                self.install_root
                if self.state_root == (self.install_root / STATE_DIR).resolve()
                else None
            )
            ensure_portable_state_usable(
                self.state_root,
                portable_root=production_root,
            )
        return _owned_directory(self.state_root, relative)

    def ensure_state_tree(self) -> None:
        """Create every product-owned mutable directory via the safe seam."""

        for relative in _STATE_TREE_DIRS:
            self.owned_directory(relative)

    @property
    def logs_root(self) -> Path:
        return self.owned_directory("logs")

    @property
    def cache_root(self) -> Path:
        return self.owned_directory("cache")

    @property
    def temp_root(self) -> Path:
        return self.owned_directory("temp")

    @property
    def clipboard_temp_dir(self) -> Path:
        return self.owned_directory("temp/clipboard")

    @property
    def update_root(self) -> Path:
        return self.owned_directory("update")

    @property
    def locks_root(self) -> Path:
        return self.owned_directory("locks")

    @property
    def webengine_root(self) -> Path:
        return self.owned_directory("web/qtwebengine")

    @property
    def webengine_cache_dir(self) -> Path:
        return self.owned_directory("web/qtwebengine/cache")

    @property
    def webengine_persistent_dir(self) -> Path:
        return self.owned_directory("web/qtwebengine/persistent")


def _lexical_executable_root(
    executable: str | os.PathLike[str] | Path,
) -> Path:
    """Return an absolute executable directory without following reparse points."""

    path = Path(os.path.abspath(os.fspath(executable)))
    if path.suffix.lower() in (".exe", ".app", ".bin") or path.is_file():
        return path.parent
    return path


def _normalize_executable(executable: str | os.PathLike[str] | Path) -> Path:
    """Canonicalize an executable file or directory to its containing root."""

    path = Path(executable).resolve()
    if path.suffix.lower() in (".exe", ".app", ".bin") or path.is_file():
        return path.parent
    return path


def resolve_app_paths(
    executable: str | os.PathLike[str] | Path,
    *,
    profile: str = "production",
    data_root_resolver: DataRootResolver | None = None,
) -> AppPaths:
    """解析应用路径。

    Args:
        executable: 便携根目录，或 exe 文件路径（自动取 parent）。
        profile: profile 名称。``"production"``（默认）使用
            ``<portable-root>/state``；``"winui-dev"`` 使用旁路开发路径
            （``data/profiles/winui-dev``），不触碰正式配置。
        data_root_resolver: production 状态根 resolver；测试/开发可显式注入。

    Returns:
        AppPaths（所有路径已 resolve() 为绝对路径）。

    Raises:
        ValueError: profile 不在允许白名单中。
    """
    if profile not in _ALLOWED_PROFILES:
        raise ValueError(
            f"unsupported profile: {profile!r}; allowed: {sorted(_ALLOWED_PROFILES)}"
        )

    install_root = VelopackRootResolver(Path(executable)).resolve()

    if profile == "production":
        state_root = (
            data_root_resolver or PortableRootResolver(install_root)
        ).resolve()
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


# 启动时随可用性探针一并创建的 state 子树（产品拥有的全部可变目录）。
_STATE_TREE_DIRS = (
    CONFIG_DIR,
    "cache",
    "logs",
    RUNTIME_DIR,
    MODEL_CACHE_DIR,
    OUTPUT_DIR,
    "update",
    "locks",
    "web/qtwebengine/cache",
    "web/qtwebengine/persistent",
    "temp/clipboard",
)


def activate_portable_state(
    executable: str | os.PathLike[str] | Path,
    *,
    profile: str = "production",
    data_root_resolver: DataRootResolver | None = None,
) -> AppPaths:
    """Resolve, probe and activate the portable state layout for this process.

    Production entry points call this exactly once before Qt, Runtime,
    Supervisor, logging, or any other application startup work. Containment
    or writability failures raise :class:`PortableStateError` and the caller
    must fail closed (no LocalAppData/temp fallback).
    """

    paths = resolve_app_paths(
        executable,
        profile=profile,
        data_root_resolver=data_root_resolver,
    )
    if profile == "production":
        _migrate_legacy_current_state(
            _normalize_executable(executable), paths.state_root
        )
        ensure_portable_state_usable(
            paths.state_root,
            portable_root=None if data_root_resolver is not None else paths.install_root,
        )
        _migrate_legacy_runtime_directory(paths.state_root)
        paths.ensure_state_tree()
    activate_app_paths(paths)
    return paths


def activate_app_paths(paths: AppPaths) -> None:
    """Select the verified mutable layout for this process before startup."""
    global _active_app_paths
    _active_app_paths = paths


def get_active_app_paths() -> AppPaths:
    """Return the bootstrapped layout, resolving the portable root if needed."""
    if _active_app_paths is not None:
        return _active_app_paths
    return resolve_app_paths(get_install_root())


def get_state_root() -> Path:
    """Return the root used by stateful managers and cache helpers."""
    return get_active_app_paths().state_root


def get_clipboard_temp_dir() -> Path:
    """剪贴板临时 PNG 目录（``state/temp/clipboard``）。

    产品拥有、随 state 收口；不使用系统 Temp。由调用方滚动清理，
    启动时的目录创建已由 :func:`activate_portable_state` 完成。
    """

    directory = get_active_app_paths().clipboard_temp_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory
