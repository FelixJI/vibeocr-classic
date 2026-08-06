"""机器码生成和 Classic 产品缓存管理模块。"""

import contextlib
import hashlib
import json
import os
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

# 缓存版本号（用于缓存格式升级时失效旧缓存）。
# v2：markdown 纳入 OCR_CHECK_MODULES / required_deps，旧缓存（无 markdown key）
# 必须失效，否则会被判为"已装"，掩盖真实缺失。
# v3：paddlex[ocr] leaf 包（einops/scipy/.../tokenizers 等 10 个）纳入
# OCR_CHECK_LEAF_MODULES 检测，旧缓存（无这些 leaf key）必须失效重建，
# 否则会被判为"已装"，掩盖便携安装中途失败导致的漏装（表格识别爆炸的根因）。
# v4：补充 PaddleX[ocr] 当前必需的 beautifulsoup4（import 名 bs4）。
# v5：旧依赖/硬件快照降级为 Classic 诊断信息，禁止再作为 Runtime readiness
# 判据；Runtime 是否就绪只由 Runtime Installer inspect 与 ready capability 决定。
CACHE_VERSION = 5

# =============================================================================
# cache.json Schema（权威定义——所有读写方必须遵守）
# =============================================================================
# 此文件是 VibeOCR 的机器本地状态缓存，存放于 <project_root>/.vibeocr/cache.json。
# .vibeocr/ 在 .gitignore 中，不会进入版本库。
#
# 顶层字段：
#   version: int                    schema 版本号，= CACHE_VERSION。bump 即失效
#                                   全部旧缓存（machine_id 校验之外的第二道防线）。
#   machine_id: str                 SHA256(CPU ID | 主板序列号 | MAC)，跨机器失效。
#   last_check_time: ISO 8601 str   历史诊断快照的检测时间戳（展示用）。
#   python_version: str             检测时的嵌入式 Python 版本（展示用）。
#   dependencies: {pkg: bool}       迁移前的依赖诊断快照，仅用于兼容旧缓存工具；
#                                   任何启动、识别或安装决策都不得读取它。
#   hardware_info: {has_gpu: bool, cuda_version: str|None}
#                                   历史 GPU 展示快照，不决定 Runtime accelerator。
#   preload_pipelines: [str]        历史字段。已迁移至 app_settings.json，
#                                   config_manager.get_preload_pipelines 仅做
#                                   一次性读迁移（只读，不再写入此文件）。
#
# 写入规约（所有写入方必须遵守，避免再次分叉）：
#   1. 通过本模块的 save_cache / update_cache_field / create_cache_entry 写入，
#      不要自行 open(cache.json, 'w')——原子写与字段保留逻辑由本模块统一保证。
#   2. 增量改单字段用 update_cache_field；全量重建用 create_cache_entry。
#   3. 写入的数据必须包含 version + machine_id（create_cache_entry 自动补）。
# =============================================================================

# 历史诊断快照的展示有效期。不得据此推导 Runtime readiness。
CACHE_TTL_DAYS = 7


def get_cache_age_seconds(project_root: Path) -> float | None:
    """返回缓存距今的秒数。

    用于 TTL 抽检判断。无缓存、无 last_check_time 字段、或时间戳解析失败时
    返回 None（调用方据此决定是否做完整实时检测）。

    Args:
        project_root: 项目根目录

    Returns:
        距今秒数，或 None
    """
    cache_data = load_cache(project_root)
    if cache_data is None:
        return None
    last_check = cache_data.get("last_check_time")
    if not last_check:
        return None
    try:
        checked_at = datetime.fromisoformat(last_check)
    except (ValueError, TypeError):
        return None
    return (datetime.now() - checked_at).total_seconds()


def _get_cpu_id() -> str:
    """获取 CPU ID"""
    if os.name == "nt":
        # wmic 缺失(旧系统)或超时均属正常，失败返回空字符串
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            result = subprocess.run(
                ["wmic", "cpu", "get", "processorid"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    return ""


def _get_baseboard_serial() -> str:
    """获取主板序列号"""
    if os.name == "nt":
        # wmic 缺失(旧系统)或超时均属正常，失败返回空字符串
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            result = subprocess.run(
                ["wmic", "baseboard", "get", "serialnumber"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    return ""


def _get_mac_address() -> str:
    """获取第一个有效网卡 MAC 地址"""
    mac = uuid.getnode()
    if mac == uuid.getnode():
        return f"{mac:012X}"
    return ""


_cached_machine_id: str | None = None
_machine_id_lock = threading.Lock()


def generate_machine_id() -> str:
    """
    生成机器唯一标识码

    组合以下硬件信息生成 SHA256 哈希：
    - CPU ID
    - 主板序列号
    - 第一个有效网卡 MAC 地址

    Returns:
        64字符的十六进制机器码
    """
    global _cached_machine_id
    if _cached_machine_id is not None:
        return _cached_machine_id

    # 启动期依赖检查、设置页状态和缓存预热可能并发请求机器码。串行化首次
    # WMIC 探测并在锁内二次检查，避免同时拉起多组 wmic 子进程。
    with _machine_id_lock:
        if _cached_machine_id is not None:
            return _cached_machine_id

        hardware_info = [_get_cpu_id(), _get_baseboard_serial(), _get_mac_address()]
        combined = "|".join(hardware_info)
        _cached_machine_id = hashlib.sha256(combined.encode()).hexdigest()
        return _cached_machine_id


def warmup_machine_id(project_root: Path | None = None) -> None:
    """启动期后台预热机器码，避免后续 GUI 操作感知 wmic 延迟。

    安全在任何线程调用。若 _cached_machine_id 已设置则立即返回。
    project_root 参数仅为 API 一致性保留，实际不使用。

    Args:
        project_root: 项目根目录（未使用，仅为 API 一致性保留）
    """
    generate_machine_id()


def get_cache_dir(project_root: Path) -> Path:
    """
    获取缓存目录路径

    Args:
        project_root: 项目根目录

    Returns:
        .vibeocr 目录路径
    """
    return project_root / ".vibeocr"


def get_cache_path(project_root: Path) -> Path:
    """
    获取缓存文件路径

    Args:
        project_root: 项目根目录

    Returns:
        cache.json 文件路径
    """
    return get_cache_dir(project_root) / "cache.json"


def save_cache(project_root: Path, data: dict) -> bool:
    """
    保存缓存到文件（原子写）

    采用"写临时文件 → os.replace 原子替换"模式，防止写到一半崩溃/断电
    留下半截损坏的 JSON。os.replace 在同盘上是原子的（POSIX rename /
    Windows MoveFileEx 都保证），失败时清理临时文件。

    Args:
        project_root: 项目根目录
        data: 缓存数据

    Returns:
        是否保存成功
    """
    cache_file = get_cache_path(project_root)
    tmp_file = cache_file.with_suffix(".json.tmp")
    try:
        cache_dir = get_cache_dir(project_root)
        cache_dir.mkdir(parents=True, exist_ok=True)

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(cache_file)  # 同盘原子替换（Path.replace 内部用 os.replace）
        return True
    except Exception as e:
        print(f"[缓存] 保存缓存失败: {e}")
        # 清理可能残留的临时文件（os.replace 失败时 tmp_file 仍存在）
        with contextlib.suppress(OSError):
            tmp_file.unlink(missing_ok=True)
        return False


def load_cache(project_root: Path) -> dict | None:
    """
    加载缓存

    Args:
        project_root: 项目根目录

    Returns:
        缓存数据，如果不存在或损坏则返回 None
    """
    try:
        cache_file = get_cache_path(project_root)
        if not cache_file.exists():
            return None

        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[缓存] 缓存文件损坏，将重新检测")
        return None
    except Exception as e:
        print(f"[缓存] 加载缓存失败: {e}")
        return None


def is_cache_valid(project_root: Path) -> tuple[bool, dict | None]:
    """
    检查缓存是否有效

    缓存有效的条件：
    1. 缓存文件存在
    2. 缓存版本匹配
    3. 机器码匹配

    Args:
        project_root: 项目根目录

    Returns:
        (是否有效, 缓存数据或None)
    """
    cache_data = load_cache(project_root)
    if cache_data is None:
        return False, None

    # 检查版本
    if cache_data.get("version") != CACHE_VERSION:
        print(f"[缓存] 缓存版本不匹配: {cache_data.get('version')} != {CACHE_VERSION}")
        return False, None

    # 检查机器码
    current_machine_id = generate_machine_id()
    cached_machine_id = cache_data.get("machine_id", "")
    if current_machine_id != cached_machine_id:
        return False, None

    return True, cache_data


def clear_cache(project_root: Path) -> bool:
    """
    清除缓存文件

    Args:
        project_root: 项目根目录

    Returns:
        是否清除成功
    """
    try:
        cache_file = get_cache_path(project_root)
        if cache_file.exists():
            cache_file.unlink()
            print("[缓存] 缓存已清除")
        return True
    except Exception as e:
        print(f"[缓存] 清除缓存失败: {e}")
        return False


def create_cache_entry(
    project_root: Path, dependencies: dict, hardware_info: dict
) -> dict | None:
    """
    创建新的缓存条目

    Args:
        project_root: 项目根目录
        dependencies: 依赖检测结果
        hardware_info: 硬件信息

    Returns:
        创建的缓存数据，失败返回 None
    """
    import sys

    cache_data = {
        "version": CACHE_VERSION,
        "machine_id": generate_machine_id(),
        "last_check_time": datetime.now().isoformat(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dependencies": dependencies,
        "hardware_info": hardware_info,
    }

    if save_cache(project_root, cache_data):
        print("[缓存] 缓存已更新")
        return cache_data
    return None


def update_cache_field(project_root: Path, key: str, value: object) -> bool:
    """原地更新缓存中的单个顶层字段（保留其余字段）

    用于后台任务安全更新单个缓存字段，避免重建整个缓存条目。

    Args:
        project_root: 项目根目录
        key: 顶层字段名
        value: 字段值

    Returns:
        是否更新成功（缓存不存在/无效时返回 False）
    """
    is_valid, cached_data = is_cache_valid(project_root)
    if not (is_valid and cached_data):
        return False
    cached_data[key] = value
    return save_cache(project_root, cached_data)


def reset_cache_to_empty(project_root: Path) -> bool:
    """重置历史诊断快照，不触碰产品绑定的 Runtime 状态。

    UI 的“验证 Runtime 状态”按钮直接调用 Runtime Installer ``inspect``。

    Args:
        project_root: 项目根目录

    Returns:
        是否重置成功
    """
    import sys

    try:
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": generate_machine_id(),
            "last_check_time": datetime.now().isoformat(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "dependencies": {},
            "hardware_info": {},
        }
        if save_cache(project_root, cache_data):
            print("[缓存] 缓存已重置为空壳")
            return True
        return False
    except Exception as e:
        print(f"[缓存] 重置缓存失败: {e}")
        return False


def get_cache_info(project_root: Path) -> str:
    """
    获取缓存信息字符串（多行，覆盖所有顶层字段，便于调试）

    Args:
        project_root: 项目根目录

    Returns:
        缓存信息字符串
    """
    cache_data = load_cache(project_root)
    if cache_data is None:
        return "无缓存"

    version = cache_data.get("version", "未知")
    machine_id = cache_data.get("machine_id", "未知")
    last_check = cache_data.get("last_check_time", "未知")
    py_ver = cache_data.get("python_version", "未知")
    deps = cache_data.get("dependencies", {})
    deps_summary = (
        ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in deps.items())
        if deps
        else "(空)"
    )
    hw = cache_data.get("hardware_info", {})
    lines = [
        f"version={version} (current CACHE_VERSION={CACHE_VERSION})",
        f"machine_id={machine_id[:16]}...",
        f"last_check_time={last_check}",
        f"python_version={py_ver}",
        f"dependencies: {deps_summary}",
        f"hardware: has_gpu={hw.get('has_gpu', '?')}, cuda={hw.get('cuda_version', '?')}",
    ]
    return "\n".join(lines)
