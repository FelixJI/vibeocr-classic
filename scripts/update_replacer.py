"""VibeOCR 更新替换逻辑（共享模块）

由独立 updater.exe 和产品启动后的后台残留清理共同复用。

设计约束（重要）：
- **纯 stdlib**，不依赖 vibeocr 任何模块。原因：updater.exe 用 PyInstaller ``--onefile``
  打包且 ``pathex=[]``、无工作区 ``--paths``，无法 import ``vibeocr`` 生产模块。
  本模块放在 ``scripts/``，与 ``updater_main.py`` 同目录，updater 打包时自动收集；
  主程序侧把本文件作为 ``--add-data`` 资源打进 ``_internal/``，运行时注入 sys.path。

新架构（黄金法则）：旧主程序只"递送"（testzip + 从 zip 抽取新 updater），由新 updater
（从暂存目录运行，新代码）完成部署。

核心流程：verify_sha256 → signal_ready → extract → replace_app_files
        （备份-删除-复制-失败回滚）→ sync deps → launch_app
        （cleanup 已移交新主程序后台线程，见 main.py _cleanup_update_artifacts）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# 只保留用户数据、内容寻址 Runtime、锁与状态；旧的产品内 python/ 不再保留。
_PRESERVE_DIRS = {
    "config",
    "data",
    "locks",
    "logs",
    "models",
    "output",
    "runtimes",
    "state",
}

logger = logging.getLogger("updater")

# 单阶段耗时超过此阈值（秒）时，日志额外标 [SLOW]，便于事后在 updater.log /
# self_update.log 里一眼定位瓶颈（典型来源：杀独占扫描新 exe、HDD 随机 IO、
# 网络盘 app_dir）。阈值取经验值——握手超时是 15s，单阶段接近或超过即值得警觉。
_SLOW_STAGE_THRESHOLD = 10.0

# 本轮替换流程的阶段耗时记录（模块级，替换器单进程单次运行，全局状态安全）。
# _StageTimer 每次退出追加一条；run_replacement 入口 reset、出口落盘成
# progress.json，供新版关于页读取展示「上次更新各阶段耗时」。
# 嵌套阶段（如「替换应用文件」内含 5 个子阶段）按进入顺序平铺记录，展示时
# 用层次（parent）标记父子关系。
_stage_records: list[dict] = []
# 当前嵌套深度：顶层阶段为 0，replace_app_files 内的子阶段为 1。
# 用于在展示时区分父子阶段（子阶段缩进显示，汇总行不重复计总耗时）。
_stage_depth = 0


class _StageTimer:
    """替换流程各阶段耗时埋点的轻量上下文管理器。

    用 ``time.monotonic()``（不受系统时钟回调影响，适合测耗时）。退出时：
    1. 写一条 ``[计时] <阶段名> 耗时 <dt>s`` 日志（超阈值追加 ``[SLOW]``）；
    2. 追加一条记录到模块级 ``_stage_records``，供 ``run_replacement`` 出口
       落盘成 ``progress.json``，新版关于页据此展示各阶段耗时分布。

    纯 stdlib、零依赖，符合替换器「不 import vibeocr」的约束。

    用法::

        with _StageTimer("解压更新包"):
            new_files_dir = extract_zip(zip_path, app_dir)

    失败路径（块内抛异常）也会记录耗时——异常在 ``__exit__`` 计时后才向上抛，
    故障排查时能看到「失败发生在哪个阶段、卡了多久」。
    """

    __slots__ = ("_depth", "_name", "_t0")

    def __init__(self, name: str) -> None:
        self._name = name
        self._t0 = 0.0
        self._depth = 0

    def __enter__(self) -> _StageTimer:
        global _stage_depth
        self._t0 = time.monotonic()
        self._depth = _stage_depth
        _stage_depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        global _stage_depth
        dt = time.monotonic() - self._t0
        _stage_depth = self._depth  # 退回到本层（防御异常路径下深度错位）
        slow = " [SLOW]" if dt >= _SLOW_STAGE_THRESHOLD else ""
        # exc 为真说明阶段内抛了异常，额外标注，方便事后定位「失败卡在哪一步」。
        failed = " [FAILED]" if exc is not None else ""
        indent = "  " * self._depth
        logger.info(f"[计时] {indent}{self._name} 耗时 {dt:.2f}s{slow}{failed}")
        _stage_records.append(
            {
                "name": self._name,
                "seconds": round(dt, 3),
                "depth": self._depth,
                "slow": dt >= _SLOW_STAGE_THRESHOLD,
                "failed": exc is not None,
            }
        )


def _flush_progress(app_dir: Path, success: bool, version: str = "") -> None:
    """把本轮阶段耗时记录落盘成 progress.json，供新版关于页读取展示。

    落盘路径 ``app_dir/data/cache/update/progress.json``（与 updater.ready 同目录，
    更新缓存清理时一并删除）。失败路径也落盘——这样「更新失败」的现场同样可见，
    便于排查「卡在哪一步」。落盘失败本身仅记录，不影响主流程（progress 是辅助信息）。

    Args:
        app_dir: 应用目录，progress.json 落在其 data/cache/update/ 下。
        success: 本轮替换是否成功（展示时区分「成功更新」「失败回滚」两次记录）。
        version: 目标版本号（从 version.json 读取，展示用）。
    """
    if not _stage_records:
        return
    progress_path = app_dir / "data" / "cache" / "update" / "progress.json"
    payload = {
        "version": version,
        "success": success,
        "total_seconds": round(sum(r["seconds"] for r in _stage_records), 3),
        "stages": _stage_records,
        "recorded_at": datetime.now().isoformat(),
    }
    try:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"已写入更新进度记录: {progress_path}")
    except Exception as e:
        logger.warning(f"写入 progress.json 失败（不影响更新，仅丢失耗时展示）: {e}")


def _read_version(app_dir: Path) -> str:
    """读 app_dir/version.json 的 version 字段，供 progress.json 展示。

    替换成功后 version.json 已是新版；失败/校验阶段 version.json 仍是旧版或不存在。
    任何异常都返回空串——progress.json 的 version 仅作展示，非关键数据。
    """
    try:
        data = json.loads((app_dir / "version.json").read_text(encoding="utf-8"))
        return str(data.get("version", ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 日志（与 updater_main / self-update 共用，写到 app_dir/data/logs/<name>.log）
# ---------------------------------------------------------------------------


def setup_logging(app_dir: Path, log_filename: str) -> None:
    """配置日志：写到 ``app_dir/data/logs/<log_filename>``。

    updater.exe / VibeOCR.exe 都是 ``console=False``（windowed），stdout/stderr 全部丢弃。
    不写文件的话，更新阶段一旦失败就完全没有现场（v0.1.13→v0.2.2 更新就是 updater
    在替换文件阶段崩溃，但因为没日志而无法排查）。同时对 stdout 也输出一份，开发态可见。
    """
    log_dir = app_dir / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 连日志目录都建不出来时，退化到只输出 stdout（总比啥都没有强）
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_dir / log_filename,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# 就绪握手信号
# ---------------------------------------------------------------------------


def signal_ready(app_dir: Path, ready_filename: str) -> None:
    """写就绪标记文件，供主程序端确认替换器已通过接管前校验。

    仅在 SHA256 校验通过后、做任何替换前调用。这个文件证明：进程已起来、Python
    解释器已初始化、更新包可信且日志/数据目录可写——即已具备接管条件。旧逻辑在
    任何校验前就写 ready，随后即使包损坏也会让主程序先硬退出，用户看到的就是
    “下载成功后闪退”。

    Args:
        app_dir: 应用安装目录，ready 文件落在 ``app_dir/data/cache/update/``。
        ready_filename: 就绪文件名（``updater.ready``），主程序端据此轮询握手。
    """
    try:
        ready_path = app_dir / "data" / "cache" / "update" / ready_filename
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception as e:
        # 写 ready 失败不致命：最坏情况是主程序端误判握手失败、走兜底路径，
        # 兜底路径同样能完成替换。仅记录。
        logger.warning(f"写就绪信号失败（主程序端可能误判握手失败）: {e}")


# ---------------------------------------------------------------------------
# 校验与解压
# ---------------------------------------------------------------------------


def verify_zip(zip_path: Path) -> bool:
    if not zip_path.exists():
        logger.error(f"zip 文件不存在: {zip_path}")
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                logger.error(f"zip 文件损坏，损坏条目: {bad}")
                return False
        return True
    except zipfile.BadZipFile:
        logger.error("无效的 zip 文件")
        return False


def verify_sha256(zip_path: Path) -> bool:
    sha256_path = Path(str(zip_path) + ".sha256")
    if not sha256_path.exists():
        # 与主程序下载阶段（update_service.verify_sha256）保持一致：
        # 缺失校验文件即视为不可信，拒绝更新，而不是放行。
        # 此前这里「找不到就跳过」会让更新包在下载阶段之后绕过完整性校验。
        logger.error(f"未找到 SHA256 校验文件，拒绝更新: {sha256_path}")
        return False

    expected = sha256_path.read_text(encoding="utf-8").strip().split()[0].lower()
    # 分块流式计算哈希，而非 ``hashlib.sha256(zip_path.read_bytes())``。
    # 后者一次性把整个 zip（~227–378MB）读进内存，峰值占用 = 包体大小，在弱内存机器
    # 上与解压/复制阶段的内存叠加可能触发换页抖动，反而更慢。流式按 8MB 块喂给
    # hashlib，峰值恒定 ~8MB，速度持平或略快（省去一次大 buffer 分配）。
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 23), b""):  # 1<<23 == 8MB
            h.update(chunk)
    actual = h.hexdigest().lower()

    if actual != expected:
        logger.error("SHA256 校验失败")
        logger.error(f"  expected: {expected}")
        logger.error(f"  actual:   {actual}")
        return False
    return True


def extract_zip(zip_path: Path, app_dir: Path) -> Path:
    tmp_dir = app_dir / "data" / "cache" / "update" / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    logger.info("解压更新包...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # zip 内可能有一层 VibeOCR/ 目录
    contents = list(tmp_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        return contents[0]
    return tmp_dir


# ---------------------------------------------------------------------------
# 文件替换（备份-删除-复制-回滚）
# ---------------------------------------------------------------------------


def _detect_self_exe_names(app_dir: Path) -> tuple[str, ...]:
    """判断本替换器是否需要避让自己（``updater.exe``）。

    新架构（黄金法则）下，updater 从暂存目录 ``data/cache/update/`` 运行，
    不在 app_dir，故 ``app_dir/updater.exe``（旧版）无人运行、无 PE 映射锁，
    可被 ``replace_app_files`` 直接覆盖——无需避让，返回空元组。

    过渡期旧路径下，旧主程序仍用旧式调用（启动 ``app_dir/updater.exe``），
    updater 自身在 app_dir，必须避让自己（Windows 禁止删/覆盖运行中 exe），
    返回 ``("updater.exe",)``。

    判定依据：``sys.argv[0]``（updater 自身 exe 路径）的父目录是否等于 app_dir。
    无法解析时保守走旧路径（需避让），宁可多一次无害 rename 也不漏判导致锁冲突。

    非 Windows：无 PE 映射锁，始终返回空元组。

    产品正式入口的避让由调用方显式拼入 ``self_exe_names``；本函数只判断
    updater 自身是否位于 app_dir。

    Args:
        app_dir: 应用安装目录（由 ``--app-dir`` 参数传入）。

    Returns:
        ``("updater.exe",)``（需避让，旧路径）或 ``()``（无需避让，新路径/非 Windows）。
    """
    if os.name != "nt":
        return ()
    if not sys.argv[0]:
        # 空 argv[0] 无法定位自身 exe，保守走旧路径（需避让）。
        # 注意：Path("").resolve() 在 Windows 下不抛错而是退化为 cwd，
        # 故必须在 resolve 之前对原始 argv[0] 判空。
        return ("updater.exe",)
    try:
        self_exe = Path(sys.argv[0]).resolve()
    except (OSError, ValueError):
        return ("updater.exe",)
    if not self_exe.name:
        return ("updater.exe",)
    try:
        app_dir_resolved = app_dir.resolve()
    except (OSError, ValueError):
        return ("updater.exe",)
    if self_exe.parent == app_dir_resolved:
        return ("updater.exe",)
    return ()


def rename_locked_self_exe(app_dir: Path, self_name: str) -> None:
    """处理正在运行、无法被删除/覆盖的可执行文件。

    Windows 允许对正在运行的可执行文件执行 rename，但禁止 delete/overwrite。
    替换器在替换 app_dir 时会试图 rmtree 正在运行的旧 exe（即它自己或调用方主程序），
    该删除会因文件被 OS 锁定而失败，导致替换流程中断、应用停在半残状态。

    本函数在替换前把旧 exe 改名（加 ``.old`` 后缀），让随后的复制能写入新版。
    改名后的旧文件由新主程序启动时后台清理（``cleanup_leftover_old_exes``）。

    Args:
        app_dir: 应用安装目录。
        self_name: 要避让的 exe 文件名。
            新架构下 ``self_exe_names`` 由 ``_detect_self_exe_names`` 自动判断：
            旧路径（过渡期，updater 在 app_dir）需避让 ``updater.exe``；
            新路径（暂存目录运行）无需避让；产品入口由调用方额外传入。
    """
    if os.name != "nt":
        return
    self_path = app_dir / self_name
    if not self_path.exists():
        return
    old_path = app_dir / f"{self_name}.old"
    try:
        # 上次更新残留的 .old 优先清掉
        if old_path.exists():
            old_path.unlink(missing_ok=True)
        self_path.rename(old_path)
        logger.info(f"已重命名运行中的旧 {self_name} -> {old_path.name}")
    except OSError as e:
        # 改名失败不致命：可能旧 exe 已退出、锁已释放。记录后继续，让后续删除/复制
        # 流程按正常路径走（失败会被 replace_app_files 的回滚逻辑接住）。
        logger.warning(f"重命名 {self_name} 失败（继续按原流程替换）: {e}")


def replace_app_files(
    new_files_dir: Path,
    app_dir: Path,
    self_exe_names: tuple[str, ...] = ("updater.exe",),
) -> bool:
    """用新文件替换 app_dir 中的非保留内容。

    采用「先备份 → 删除旧 → 复制新 → 失败回滚」策略，确保 app_dir 永远不会
    处于半残状态（旧文件已删、新文件未拷全），否则用户机器上的应用将无法启动。

    Args:
        new_files_dir: 解压后的新文件目录（可能含一层 VibeOCR/ 子目录）。
        app_dir: 应用安装目录。
        self_exe_names: 替换前需「改名避让」的运行中 exe 列表。
            新架构由 ``_detect_self_exe_names`` 判断：旧路径含 ``updater.exe``，
            新路径为空；调用方另行加入自身产品入口。
    """
    logger.info("替换应用文件...")

    # 运行中的 exe 必须先改名，否则下面 rmtree 删它必然失败
    with _StageTimer("改名避让运行中 exe"):
        for self_name in self_exe_names:
            rename_locked_self_exe(app_dir, self_name)

    # 待替换的旧条目（保留目录除外）
    old_items = [item for item in app_dir.iterdir() if item.name not in _PRESERVE_DIRS]

    # 1) 备份将要删除/覆盖的旧条目，以便复制失败时回滚
    backup_dir = app_dir / "data" / "cache" / "update" / "_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up: list[tuple[Path, Path]] = []  # (原位置, 备份位置)
    with _StageTimer("备份旧文件"):
        try:
            for item in old_items:
                bak = backup_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, bak, dirs_exist_ok=True, copy_function=_busy_copy2)
                else:
                    _busy_copy2(item, bak)
                backed_up.append((item, bak))
        except Exception as e:
            logger.error(f"备份旧文件失败，中止更新: {e}")
            shutil.rmtree(backup_dir, ignore_errors=True)
            return False

    # 2) 删除旧条目
    #    注意：删除失败（含被占用）不致命——复制阶段会再尝试覆盖。但「文件被占用」
    #    属瞬时抖动，先退避重试吸收，避免残留旧文件干扰后续复制判定。
    with _StageTimer("删除旧文件"):
        for item in old_items:
            # *.exe.old（如 updater.exe.old）是 rename_locked_self_exe 刚改名避让的
            # 运行中进程映像，此刻 100% 被 PE 映射区锁定，_busy_remove 轮询 10s 必失败
            # （历史 bug 的 WARNING 噪音 + 无谓等待即源于此）。它不阻塞后续复制（新版
            # updater.exe 是另一个文件名），故此处直接跳过，交给 cleanup 末尾的
            # _safe_remove_running_exe 用 MoveFileEx 标记重启清理。
            if item.name.endswith(".exe.old"):
                continue
            is_dir = item.is_dir()
            try:
                if is_dir:
                    # rmtree 对占用中的目录整体失败，对子文件逐项重试更稳；
                    # ignore_errors=True 兜底（目录里混有运行中 exe 时逐文件删不干净
                    # 是常态，交给复制阶段的 _busy_copy_file / 回滚处理）。
                    shutil.rmtree(item, ignore_errors=True)
                elif not _busy_remove(item, is_dir=False):
                    logger.warning(f"删除 {item} 失败: 文件持续被占用")
            except Exception as e:
                logger.warning(f"删除 {item} 失败: {e}")

    # 3) 复制新文件；任一失败则回滚
    #    单文件复制对「文件被占用」退避重试（杀独占扫描、OS 句柄释放延迟），
    #    避免瞬时抖动误触发整包回滚。目录用 copytree（内部逐项复制，无法单点重试），
    #    失败仍走原回滚逻辑。
    with _StageTimer("复制新文件"):
        try:
            for item in new_files_dir.iterdir():
                if item.name in _PRESERVE_DIRS:
                    continue
                dest = app_dir / item.name
                if item.is_dir():
                    try:
                        shutil.copytree(
                            item, dest, dirs_exist_ok=True, copy_function=_busy_copy2
                        )
                    except Exception as e:
                        logger.error(f"复制目录 {item} 失败: {e}")
                        logger.info("正在回滚到更新前状态...")
                        _restore_backup(app_dir, backed_up, backup_dir)
                        return False
                else:
                    if not _busy_copy_file(item, dest):
                        logger.error(f"复制 {item} 失败: 文件持续被占用")
                        logger.info("正在回滚到更新前状态...")
                        _restore_backup(app_dir, backed_up, backup_dir)
                        return False
        except Exception as e:
            # iterdir() 自身失败（目录不存在/无权限等），item 此时未绑定
            logger.error(f"读取更新包内容失败: {e}")
            logger.info("正在回滚到更新前状态...")
            _restore_backup(app_dir, backed_up, backup_dir)
            return False

    # 4) 复制成功，清理备份
    shutil.rmtree(backup_dir, ignore_errors=True)

    return True


def _restore_backup(
    app_dir: Path, backed_up: list[tuple[Path, Path]], backup_dir: Path
) -> None:
    """从备份恢复 app_dir 中被删除/覆盖的条目。"""
    # 先清掉复制阶段可能已写入的残缺文件（非保留、非备份目录）
    for item in app_dir.iterdir():
        if item.name in _PRESERVE_DIRS:
            continue
        # 跳过备份目录自身（在 data/ 下，属于保留目录，这里保险起见再判一次）
        try:
            if backup_dir in item.parents or item == backup_dir:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception:
            pass

    for original, bak in backed_up:
        try:
            if bak.is_dir():
                shutil.copytree(
                    bak, original, dirs_exist_ok=True, copy_function=_busy_copy2
                )
            else:
                _busy_copy2(bak, original)
        except Exception as e:
            logger.warning(f"回滚 {original} 失败: {e}")

    shutil.rmtree(backup_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 瞬时文件占用重试（Windows 杀软扫描 / OS 句柄释放延迟）
# ---------------------------------------------------------------------------

# 删除 / 复制单文件时，遇到「拒绝访问(5) / 文件被占用(32)」的累计等待上限。
# 实测主程序退出后 DLL 锁释放 + 杀软扫描新文件可达数秒；给 10s 既覆盖慢机器，
# 又不致让失败用户干等过久。超时后照常落入删除失败（warning）→ 复制失败（回滚）路径。
_BUSY_TIMEOUT = 10.0
_BUSY_POLL_INTERVAL = 0.2


def _is_busy_error(exc: OSError) -> bool:
    """判断 OSError 是否为「文件被占用」类瞬时错误（WinError 5 / 32）。"""
    return getattr(exc, "winerror", None) in (5, 32)


def _busy_remove(path: Path, *, is_dir: bool) -> bool:
    """带重试地删除文件或目录，遇到文件占用错误时退避等待。

    Windows 上杀毒软件会在新文件出现瞬间扫描、短暂独占；OS 释放已退出进程的
    句柄也有延迟。直接 unlink/rmtree 常在此刻失败（WinError 5/32），徒增回滚。
    本函数对这类瞬时占用退避重试，对其它错误（如权限不足）立即放弃交上层处理。

    Returns:
        True 表示删除成功（或目标本就不存在）；False 表示最终仍失败。
    """
    if not path.exists():
        return True
    deadline = time.monotonic() + _BUSY_TIMEOUT
    while True:
        try:
            if is_dir:
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
            return True
        except OSError as e:
            if not _is_busy_error(e) or time.monotonic() >= deadline:
                return False
            logger.debug(f"删除 {path.name} 被占用，等待重试: {e}")
            time.sleep(_BUSY_POLL_INTERVAL)


def _busy_copy_file(src: Path, dest: Path) -> bool:
    """带重试地复制单文件，遇到文件占用错误时退避等待（覆盖已存在目标）。

    复制阶段的目标文件可能是旧版本（尚未删除/改名）或被杀软扫描的新写入文件，
    瞬时占用会导致 copy2 抛 WinError 5/32。退避重试可吸收这类抖动，避免误触发
    整体回滚。对其它错误（磁盘满、路径不存在）不重试，由调用方记录并回滚。

    Returns:
        True 表示复制成功；False 表示最终仍失败（调用方应回滚）。
    """
    deadline = time.monotonic() + _BUSY_TIMEOUT
    while True:
        try:
            shutil.copy2(src, dest)
            return True
        except OSError as e:
            if not _is_busy_error(e) or time.monotonic() >= deadline:
                return False
            logger.debug(f"复制 {src.name} 被占用，等待重试: {e}")
            time.sleep(_BUSY_POLL_INTERVAL)


def _busy_copy2(src, dst):
    """``shutil.copytree`` 的 copy_function 适配：与 ``copy2`` 同签名，遇占用则重试。

    copytree 复制目录时逐文件调用 copy_function；默认 copy2 不重试，目录内任一
    文件被杀独占/句柄延迟占用即整体抛错。本函数把重试逻辑注入 copytree，使
    ``_internal/`` 这类大目录的复制也能吸收瞬时占用。重试耗尽后照常抛出 OSError，
    让上层 copytree 异常传播 → 触发回滚。
    """
    src_path = Path(src)
    deadline = time.monotonic() + _BUSY_TIMEOUT
    while True:
        try:
            return shutil.copy2(src, dst)
        except OSError as e:
            if not _is_busy_error(e) or time.monotonic() >= deadline:
                raise
            logger.debug(f"复制 {src_path.name} 被占用，等待重试: {e}")
            time.sleep(_BUSY_POLL_INTERVAL)


def _safe_remove_running_exe(path: Path, *, label: str = "") -> None:
    """删除可能是运行中进程映像的 exe。

    Windows 不允许删除正在运行的 exe（PE 映射区锁定，WinError 5）。updater.exe
    更新时会把自己改名为 ``updater.exe.old`` 后继续运行，导致 cleanup 阶段删除
    ``.old`` 必然失败——它此刻就是正在跑的进程映像，退避重试 10s 也等不到锁释放。

    本函数的删除策略（层层降级，保证不留永久残留）：
    1. 先用 ``_busy_remove`` 退避重试——吸收杀毒瞬时独占（非进程映像锁的情况）；
    2. 仍失败 → Windows 上调 ``MoveFileExW(MOVEFILE_DELAY_UNTIL_REBOOT=4)`` 标记，
       OS 在下次重启时（进程已退出、锁已释放）自动删除。这是删运行中 exe 的标准
       Windows 惯用法（``machine_cache.py`` 的原子写注释亦引用此 API）。
    3. 非 Windows / MoveFileEx 不可用：仅记录，留待下次更新入口
       （``rename_locked_self_exe`` 开头会清残留 ``.old``）兜底。

    Args:
        path: 要删除的 ``*.exe.old`` 文件。
        label: 日志里的人类可读名，缺省用文件名。
    """
    if not path.exists():
        return
    name = label or path.name
    # 1. 退避重试吸收瞬时占用（杀毒扫描新 exe / OS 句柄释放延迟）
    if _busy_remove(path, is_dir=False):
        logger.info(f"已清理上次更新残留: {name}")
        return

    # 2. Windows: 标记重启时删除（运行中 exe 的唯一可靠删除途径）
    if os.name == "nt":
        try:
            import ctypes

            # MOVEFILE_DELAY_UNTIL_REBOOT = 4。把文件加入 SMSS 的延迟删除队列，
            # 下次开机时（无进程占用）由系统删除。返回 0 表示失败，需 get_last_error。
            # 用 use_last_error=True 让 ctypes.get_last_error() 拿到真实错误码。
            move_file_ex = ctypes.windll.kernel32.MoveFileExW  # type: ignore[attr-defined]
            move_file_ex.restype = ctypes.c_int
            move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            ok = move_file_ex(str(path), None, 4)
            if ok:
                logger.info(f"{name} 被占用，已标记在下次重启时删除")
                return
            err = ctypes.get_last_error()
            logger.debug(f"MoveFileEx 标记 {name} 失败（错误码 {err}），留待下次清理")
        except Exception as e:
            logger.debug(f"MoveFileEx 不可用，放弃标记 {name}: {e}")
    else:
        # 非 Windows 不会有 PE 映射锁，能走到这说明真有其它占用，留待下次入口清理
        logger.debug(f"{name} 仍被占用，留待下次清理")


def cleanup_leftover_old_exes(app_dir: Path) -> None:
    """清理上次更新残留的 ``*.exe.old``（主程序启动入口）。

    背景：updater 和产品入口更新时会把运行中的自己改名为 ``.old`` 后继续运行，
    Windows 禁止删运行中 exe（PE 映射锁），所以改名后的旧进程映像在 updater 的
    cleanup 阶段**必然删不掉**。updater 侧已有 ``MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)``
    标记重启清理，但：

    1. 旧版 updater（如 v0.4.13）没有 MoveFileEx 兜底，残留 ``.old`` 会永久堆积；
    2. 即便标记了，笔记本用户从不重启 → 标记永远不生效，``.old`` 一直占着 8-9MB；
    3. ``rename_locked_self_exe`` 入口会清残留 ``.old``，但前提是「再次发生更新」——
       若用户停在当前版本，残留就永远在。

    本函数是兜底的兜底：主程序每次启动时（进程此刻是新版 exe，旧的已退出）扫一遍
    app_dir，把残留的 ``.exe.old`` 用 ``_safe_remove_running_exe`` 清掉。此刻旧进程
    早已退出、PE 锁已释放，普通删除即可成功（实测瞬时完成）。复用现有清理函数保证
    行为一致（含 MoveFileEx 降级）。非 Windows 直接 no-op（无残留）。

    Args:
        app_dir: 包含 ``*.exe.old`` 的应用安装目录。
    """
    if os.name != "nt" or not app_dir.is_dir():
        return
    for old_exe in app_dir.glob("*.exe.old"):
        if old_exe.exists():
            _safe_remove_running_exe(old_exe, label=old_exe.name)


# ---------------------------------------------------------------------------
# 旧依赖同步兼容 helper（生产更新路径不再调用）
# ---------------------------------------------------------------------------


def _normalize_dep_value(v: object) -> str:
    """把 dep_versions 的值归一化为 constraint 串（如 ">=3.3.1"）。

    兼容三种历史格式（替换器读到的 version.json 可能由不同版本写入）：
    - 当前版：约束串 str（如 ">=3.3.1" / "==3.3.1+cu126" / ">=1,<2"）→ 直接用
    - 曾用版：{"version": "3.3.1", "op": ">="} dict → 拼成 ">=3.3.1"
    - 旧旧版：裸版本号 str（如 "3.3.1"）→ 按 ">=3.3.1"

    替换器需在 diff 前归一化，避免不同格式因类型/字符串差异被误判为变化。
    """
    if isinstance(v, dict):
        ver = str(v.get("version", "")).strip()
        op = str(v.get("op", ">=")).strip() or ">="
        return f"{op}{ver}"
    s = str(v).strip()
    # 已是约束串（以 PEP 440 操作符开头）→ 直接返回；否则视为裸版本号
    if s and (
        s.startswith(("==", "!=", ">=", "<=", "~="))
        or (s[:1] in "><" and len(s) > 1)
    ):
        return s
    return f">={s}" if s else ""


def _sync_dependencies(
    old_deps: dict, new_data: dict, app_dir: Path, old_locked: dict | None = None
) -> None:
    """检查 AI 依赖版本变化并写入"待同步"标记。

    替换器不能 import vibeocr（python/ 里没装 vibeocr，updater 是独立 --onefile
    打包；self-update 虽然在主程序进程内，但同样不应在此引入 vibeocr 重模块），
    因此不在替换器里直接 pip 安装。改为：若 dep_versions 有变化，把变更项
    写入 data/settings/pending_sync.json，由覆盖后的新版 VibeOCR 启动时用
    env_manager.install_embedded_dependencies（含 GPU/CUDA tag/镜像/PyPI 回退的完整
    逻辑）执行升级。这样避免替换器用裸 pip 走 PyPI 把 paddle/torch 装成 CPU 版。

    变化检测有两路（任一触发即同步，确保便携环境紧跟 uv.lock）：
    1. 约束变化（dep_versions）：pyproject 显式改版（如 paddleocr >=3.7.0 →3.8.0）。
    2. 锁定版变化（dep_locked_versions）：uv.lock 在下界内升级（如 mineru 3.4.0→3.4.2，
       约束 >=3.4.0 不变）。仅靠约束比对会漏掉此类升级，便携环境会永久停留在旧锁定版，
       故必须把锁定版基准一并比较。旧版无 dep_locked_versions 字段时，新版首次携带
       视为全部变化（确保便携环境与新版 lock 对齐）。

    写入字段：
    - dep_versions：变化的包 → constraint 串（完整 PEP 440，支持 local version、
        多段约束、精确锁 ==/降级）
    - dep_extras：变化的包的 extras 列表（透传新版 dep_extras 中对应项）
    - removed：新版从 dep_versions 中移除的包名列表（由主程序 pip uninstall）
    - attempts：失败重试计数，初始为 1，主程序每次同步失败递增
    """
    new_deps = new_data.get("dep_versions", {})
    new_extras = new_data.get("dep_extras", {})
    new_locked = new_data.get("dep_locked_versions", {})
    old_locked = old_locked or {}

    # diff 路径 1：约束变化（归一化为 constraint 串后比较）
    changed: dict[str, str] = {}
    for pkg, new_v in new_deps.items():
        new_norm = _normalize_dep_value(new_v)
        old_norm = _normalize_dep_value(old_deps.get(pkg))
        if old_norm != new_norm:
            changed[pkg] = new_norm

    # diff 路径 2：锁定版变化（捕获约束不变但 uv.lock 下界内升级的场景）。
    # 仅对新版 dep_locked_versions 中的包比较；约束已判变的包不重复处理。
    # 旧版无 dep_locked_versions 字段（old_locked 为空 dict）时，新版首次携带的
    # 全部锁定版都视为变化，确保从无锁定版到有锁定版的过渡也触发同步。
    lock_changed: list[str] = []
    for pkg, new_lock in new_locked.items():
        if pkg in changed:
            continue  # 约束已变，无需重复
        old_lock = old_locked.get(pkg)
        if old_lock != new_lock:
            lock_changed.append(pkg)
            # 用新版约束串填值，使主程序 install 按约束重装（约束未变时取自 new_deps）
            changed[pkg] = _normalize_dep_value(new_deps.get(pkg))

    # removed：旧版有、新版无的包（仅范围 dep_versions，非全部 EXCLUDED_PACKAGES）
    removed = [pkg for pkg in old_deps if pkg not in new_deps]
    # 过滤掉非追踪的包（旧 version.json 可能含 _TRACKED_PREFIXES 之外的残留）
    _TRACKED_PREFIXES = ("paddle", "paddleocr", "mineru", "torch", "nvidia")
    removed = [p for p in removed if any(p.startswith(pre) for pre in _TRACKED_PREFIXES)]

    if not changed and not removed:
        logger.info("AI 依赖版本无变化")
        return

    if changed:
        logger.info(f"检测到依赖变化: {changed}")
    if lock_changed:
        logger.info(f"检测到锁定版升级（约束不变）: {lock_changed}")
    if removed:
        logger.info(f"检测到依赖移除: {removed}")
    logger.info("写入待同步标记，将由新版 VibeOCR 启动时升级...")

    settings_dir = app_dir / "data" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    pending_path = settings_dir / "pending_sync.json"

    pending: dict = {
        "version": new_data.get("version", ""),
        "dep_versions": changed,
        "written_at": datetime.now().isoformat(),
        # 失败重试计数：主程序 _on_sync_finished 失败时递增。
        # 达 SYNC_MAX_ATTEMPTS（env_config）后提示用户重装嵌入式 Python。
        "attempts": 1,
    }
    # extras 透传：只写 changed 中带 extras 的包，避免空 dict 污染
    changed_extras = {k: v for k, v in new_extras.items() if k in changed}
    if changed_extras:
        pending["dep_extras"] = changed_extras
    if removed:
        pending["removed"] = removed

    try:
        pending_path.write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"已写入待同步标记: {pending_path}")
    except Exception as e:
        logger.warning(f"写入待同步标记失败（依赖将不会自动升级）: {e}")


# ---------------------------------------------------------------------------
# 收尾：清理临时文件 + 启动主程序
# ---------------------------------------------------------------------------


def _safe_cleanup_artifacts(zip_path: Path, tmp_dir: Path | None) -> None:
    """失败路径兜底清理：删 zip / sha256 / 解压临时目录 / 备份目录。

    成功路径由 ``cleanup`` 负责（且 ``replace_app_files`` 已删 _backup）；
    失败路径下 ``cleanup`` 不会被调用，若不清理会有三处残留堆积：
    1. 解压目录 ``data/cache/update/tmp``（数百 MB）——``download_update`` 重下时
       只删 update 目录里的「文件」，不清子目录，残留会越积越多；
    2. 备份目录 ``data/cache/update/_backup``（回滚后会留空目录或残留文件）；
    3. 已下载的 zip / sha256（下次下载会覆盖，但留着占用空间）。

    所有删除都用 ignore_errors / 容错，确保清理本身不会抛异常打断通知流程。
    """
    # zip + sha256
    zip_path.unlink(missing_ok=True)
    sha256_path = Path(str(zip_path) + ".sha256")
    sha256_path.unlink(missing_ok=True)

    update_dir = zip_path.parent  # data/cache/update
    # 解压临时目录：tmp_dir 可能指向 tmp/ 本身或其下唯一的 VibeOCR/ 子目录，
    # 一律回到 tmp/ 整个删。即便 tmp_dir 为 None（校验阶段就失败、未解压），
    # 也按路径清掉可能残留的 tmp/（上次失败遗留）。
    tmp_root: Path | None = None
    if tmp_dir is not None:
        tmp_root = tmp_dir if tmp_dir.name == "tmp" else tmp_dir.parent
    if tmp_root is None or tmp_root.name != "tmp":
        tmp_root = update_dir / "tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    # 备份目录（失败时仍存在，回滚只复制不删 _backup）
    backup_dir = update_dir / "_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def cleanup(zip_path: Path, tmp_dir: Path | None) -> None:
    """成功路径收尾：删解压临时目录、zip、sha256，并清理上次残留的 *.exe.old。

    注意本函数不清 ``_backup`` 目录——成功路径下 ``replace_app_files`` 已在复制成功后
    删除它。失败路径的清理（含 _backup）走 ``_safe_cleanup_artifacts``。
    """
    if tmp_dir and tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    zip_path.unlink(missing_ok=True)
    sha256_path = Path(str(zip_path) + ".sha256")
    sha256_path.unlink(missing_ok=True)

    # 清理 update 缓存目录（如果为空）
    update_dir = zip_path.parent
    if update_dir.exists():
        try:
            update_dir.rmdir()
        except OSError:
            pass

    # 清理上次更新残留的旧 *.exe.old。
    # 注意：本次更新的 .old（如 updater.exe.old）此刻可能仍是运行中的进程映像——
    # updater.exe 会把自己改名后继续跑，Windows 禁止删运行中 exe（WinError 5）。
    # 用 _safe_remove_running_exe：退避重试吸收瞬时锁，仍失败则 MoveFileEx 标记
    # 重启清理，彻底消除残留（历史 bug：.old 永久残留 + cleanup WARNING 噪音）。
    # zip_path 形如 <app>/data/cache/update/VibeOCR-vX-win64.zip，向上回溯到 app_dir。
    app_dir = zip_path.parents[3] if len(zip_path.parents) >= 4 else None
    if app_dir is not None and os.name == "nt":
        for old_exe in app_dir.glob("*.exe.old"):
            if old_exe.exists():
                _safe_remove_running_exe(old_exe, label=old_exe.name)


def launch_app(
    app_dir: Path,
    exe_name: str,
    *,
    entry_args: tuple[str, ...] = (),
    health_file: Path | None = None,
) -> None:
    """启动调用方明确指定的新版正式入口。

    更新器是跨产品的纯机制，不猜测 Classic 或 Next 的入口，也不在一个产品失败时
    回退启动另一个产品。入口名称由各产品在启动 updater 时通过 ``--entry`` 绑定。
    """
    if not exe_name or Path(exe_name).name != exe_name:
        raise ValueError("正式启动入口必须是单个文件名")
    exe_path = app_dir / exe_name
    if not exe_path.is_file():
        raise FileNotFoundError(f"未找到正式启动入口: {exe_path}")

    command = [str(exe_path), *entry_args]
    if health_file is not None:
        health_file.parent.mkdir(parents=True, exist_ok=True)
        health_file.unlink(missing_ok=True)
    logger.info("启动产品正式入口: %s", command)
    subprocess.Popen(
        command,
        creationflags=0x8 if os.name == "nt" else 0,
        cwd=str(app_dir),
    )
    if health_file is None:
        return
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if health_file.is_file():
            logger.info("产品启动健康信号已确认: %s", health_file)
            return
        time.sleep(0.1)
    raise TimeoutError(f"产品未在 30 秒内发布健康信号: {health_file}")


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def run_replacement(
    zip_path: Path,
    app_dir: Path,
    self_exe_names: tuple[str, ...] = ("updater.exe",),
    ready_filename: str = "updater.ready",
    launch_entry: str = "",
    launch_args: tuple[str, ...] = (),
    launch_health_file: Path | None = None,
    on_failure: Callable[[str], None] | None = None,
) -> int:
    """替换流程统一入口：校验 → 写就绪信号 → 解压 → 替换 → 清理 → 启动。

    Args:
        zip_path: 已下载的更新包 zip。
        app_dir: 应用安装目录。
        self_exe_names: 替换前需改名避让的运行中 exe（见 replace_app_files）。
        ready_filename: 就绪信号文件名（见 signal_ready），由调用方区分路径来源。
        launch_entry: 替换完成后必须启动的产品专属入口文件名。
        launch_args: 原样传给产品入口的参数。
        launch_health_file: 可选健康信号路径；提供时等待该文件出现。
        on_failure: 可选的失败通知回调。替换流程在任何阶段失败时调用，传入面向用户
            的提示文案。替换器以 ``console=False``（windowed）运行，stdout 不可见，
            仅写日志文件的话用户无法感知失败（历史问题：「应用关了什么都没发生」）。
            调用方传入「弹窗」实现（如 ctypes MessageBox），确保失败时用户能看到。

    Returns:
        0 成功，1 失败（已写日志 + 已调用 on_failure，调用方据此 sys.exit）。
    """
    logger.info("VibeOCR 替换流程启动")
    logger.info(f"更新包: {zip_path}")
    logger.info(f"应用目录: {app_dir}")

    # 重置本轮阶段耗时记录（模块级状态，防御上次运行残留）。
    _stage_records.clear()

    fail_reason = ""
    # 解压目录引用：无论哪个阶段失败，都要清理掉，避免数百 MB 的临时文件长期堆积
    # （download_update 只清 update 目录里的「文件」，不清子目录，残留 tmp/ 会越积越多）。
    new_files_dir: Path | None = None
    try:
        if not launch_entry or Path(launch_entry).name != launch_entry:
            raise ValueError("必须提供单个文件名形式的产品启动入口")
        # verify_zip(testzip) 已移交旧主程序端（递送时确保 zip 可读，可安全抽取 updater）。
        # 此处仅做 verify_sha256（更强，且由新代码校验自己要部署的包——黄金法则）。
        with _StageTimer("校验 SHA256"):
            sha_ok = verify_sha256(zip_path)
        if not sha_ok:
            fail_reason = "更新包完整性（SHA256）校验失败，文件可能损坏或被篡改。"
            return 1

        # SHA 校验通过后才允许旧主程序退出。ready 是“安全接管”信号，不再只是
        # “进程启动”信号；校验失败时旧程序保持运行，避免无谓闪退。
        with _StageTimer("写就绪信号"):
            signal_ready(app_dir, ready_filename)

        # Classic 收到 ready 后会先让出一个事件循环 turn，再硬退出。给文件映射和
        # DLL 句柄一个释放窗口，减少替换阶段 WinError 5/32；后续 busy retry 仍是
        # 最终兜底。实测主程序退出后 PE 映射、DLL 句柄、Qt 资源释放可达 1~2s（慢机器
        # 或杀独占扫描更久），0.5s 余量不足会偶发瞬时占用误触发回滚。提到 2s 既覆盖
        # 慢机器释放窗口，又不致显著拉长更新等待（发生在后台 updater，UI 已退出）。
        time.sleep(2.0)

        with _StageTimer("解压更新包"):
            new_files_dir = extract_zip(zip_path, app_dir)

        with _StageTimer("替换应用文件（备份-删除-复制-依赖同步）"):
            replace_ok = replace_app_files(new_files_dir, app_dir, self_exe_names)
        if not replace_ok:
            # replace_app_files 已记录详细日志并尝试回滚；这里给用户一个明确结论。
            fail_reason = (
                "更新失败：替换文件时出错（可能文件被占用或权限不足）。\n"
                "已尝试回滚到更新前状态。"
            )
            return 1

        # 清理（tmp/zip/sha256/暂存 updater）移交新主程序后台线程完成
        # （见 main.py _cleanup_update_artifacts）。updater 关键路径到此结束——
        # 启动主程序后立即 os._exit，不再做任何 I/O 密集的删除。
        with _StageTimer("启动新版主程序"):
            launch_app(
                app_dir,
                launch_entry,
                entry_args=launch_args,
                health_file=launch_health_file,
            )

        logger.info("更新完成!")
        # 落盘进度记录（success=True）。必须在 os._exit 之前——os._exit 跳过 finally，
        # 不在这里写就永远丢了。版本号从替换后的 version.json 读，展示「更新到 vX」。
        _flush_progress(app_dir, success=True, version=_read_version(app_dir))
        # 显式硬退出，确保替换器进程立即终止。updater.exe 是 --onefile --windowed，
        # return 0 后进程才退出；某些机器上主程序启动慢或文件锁释放延迟会让 updater
        # 卡在退出阶段，表现为「下载完成后无响应」（主程序已起来但旧 updater 还挂着）。
        # os._exit 跳过解释器常规关闭流程，与主程序 _force_quit 一致。短暂 sleep 让
        # 刚 Popen 的主程序子进程有时间真正接管，避免主程序还没初始化就失去父进程。
        time.sleep(0.3)
        os._exit(0)
    except Exception:
        # 兜底：任何未捕获异常都写进日志文件，避免「静默崩溃、无现场」。
        logger.error("更新过程中发生未捕获异常:\n%s", traceback.format_exc())
        fail_reason = "更新过程中发生异常，请查看日志或手动下载最新版。"
        return 1
    finally:
        # 落盘进度记录（success=False）：失败现场同样值得留存，便于排查「卡在哪一步」。
        # 放在清理之前——清理删除 zip/sha256，但 progress.json 在 cache/update/ 下，
        # _safe_cleanup_artifacts 只删文件不删 progress.json（它写时已是清理之后），
        # 故此处先 flush 再清理，progress.json 能留存。
        if fail_reason:
            _flush_progress(app_dir, success=False, version=_read_version(app_dir))
        # 失败时务必清理临时产物（zip / sha256 / 解压目录 / 备份目录），避免长期堆积。
        # 成功路径不做 cleanup（清理由新主程序后台线程负责，见 main.py
        # _cleanup_update_artifacts），仅失败路径在此清理现场。
        if fail_reason:
            _safe_cleanup_artifacts(zip_path, new_files_dir)
            # 正式切换后失败只能进入可见的修复/手工恢复路径，绝不能重新拉起旧 UI。
            # replace_app_files 会尽力回滚文件，但应用保持关闭，等待用户处理失败提示。
            # 通知用户：替换器无 GUI 主体，windowed 运行下 stdout 不可见，唯一可见反馈
            # 是调用方注入的弹窗。历史 bug「更新失败无任何提示」即源于此缺失。
            if on_failure is not None:
                try:
                    on_failure(fail_reason)
                except Exception as notify_err:
                    logger.error(f"失败通知回调异常: {notify_err}")
