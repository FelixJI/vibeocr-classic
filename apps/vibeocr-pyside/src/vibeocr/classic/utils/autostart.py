"""跨平台开机自启动工具

支持 Windows、macOS 和 Linux 的开机自启配置。

Windows 采用「启动文件夹快捷方式（Startup\\VibeOCR Classic.lnk）」而非注册表
``HKCU\\...\\Run``：后者是杀软启发式重点关注的自启点，前者对杀软更友好，
且同样会被任务管理器/Windows 设置的"启动"标签页识别，用户可统一管理。
快捷方式名带 Classic 后缀，与 VibeOCR Next 的自启项互不干扰。

存量迁移：
- 曾用旧版本通过注册表设置自启的，由 :func:`migrate_legacy_autostart`
  在新版首次启动时静默迁移到 .lnk 并删除旧注册表项；
- 曾用旧命名 ``VibeOCR.lnk`` 的，在启用/关闭自启时若确认指向本产品则
  一并迁移/清理，避免双自启与告警。
"""

import logging
import sys
from pathlib import Path

from vibeocr.classic.utils.shortcuts import (
    create_windows_shortcut,
    get_windows_startup_dir,
)

logger = logging.getLogger(__name__)


def _get_exe_path() -> str:
    """获取当前可执行文件路径"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后
        return sys.executable
    # 开发模式：使用 Python 解释器 + 模块
    return f'"{sys.executable}" -m vibeocr'


def is_autostart_enabled() -> bool:
    """检查开机自启动是否已启用"""
    if sys.platform == "win32":
        return _win32_is_enabled()
    if sys.platform == "darwin":
        return _macos_is_enabled()
    return _linux_is_enabled()


def set_autostart(enabled: bool) -> bool:
    """设置开机自启动

    Args:
        enabled: 是否启用

    Returns:
        是否设置成功
    """
    try:
        if sys.platform == "win32":
            return _win32_set(enabled)
        if sys.platform == "darwin":
            return _macos_set(enabled)
        return _linux_set(enabled)
    except Exception as e:
        logger.error(f"设置开机自启动失败: {e}")
        return False


def migrate_legacy_autostart() -> None:
    """把旧版「注册表 Run」自启方式迁移到「启动文件夹 .lnk」。

    仅 Windows 生效；其它平台直接返回。检测到旧的
    ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\VibeOCR``
    存在时，创建新的 ``Startup\\VibeOCR.lnk`` 并删除旧注册表项。迁移失败
    只记录日志，不抛异常，不影响应用启动。

    幂等：旧注册表项不存在时不会重复创建 .lnk；调用方应用迁移标记防止
    无谓重试。
    """
    if sys.platform != "win32":
        return

    try:
        import winreg
    except ImportError:
        return

    legacy_value = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_READ
        ) as key:
            legacy_value, _ = winreg.QueryValueEx(key, _WIN_APP_NAME)
    except FileNotFoundError:
        # 没有旧注册表项，无需迁移
        return
    except OSError as e:
        logger.warning(f"读取旧版自启注册表项失败，跳过迁移: {e}")
        return

    # 发现旧注册表项：迁移到 .lnk
    logger.info("检测到旧版注册表自启项，开始迁移到启动文件夹 .lnk")
    try:
        if not _win32_ensure_shortcut():
            logger.warning("迁移时创建 .lnk 失败，保留旧注册表项以免丢失自启")
            return
    except Exception as e:
        logger.warning(f"迁移时创建 .lnk 异常，保留旧注册表项: {e}")
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _WIN_APP_NAME)
        logger.info("已删除旧版注册表自启项，迁移完成")
    except FileNotFoundError:
        # 并发或已被清理，视为已迁移
        pass
    except OSError as e:
        logger.warning(f"删除旧版注册表自启项失败（.lnk 已创建）: {e}")


# ============================================================
# Windows 实现 (启动文件夹 .lnk；旧注册表 Run 仅用于迁移)
# ============================================================

_WIN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_APP_NAME = "VibeOCR"
_WIN_LNK_NAME = "VibeOCR Classic.lnk"
# 2026-10 前的旧命名；启动文件夹里若仍存在且指向本产品则迁移，避免双自启。
_WIN_LEGACY_LNK_NAME = "VibeOCR.lnk"


def _win32_shortcut_path() -> Path:
    return get_windows_startup_dir() / _WIN_LNK_NAME


def _win32_legacy_shortcut_path() -> Path:
    return get_windows_startup_dir() / _WIN_LEGACY_LNK_NAME


def _legacy_shortcut_is_ours() -> bool:
    """判断旧命名 .lnk 是否指向本产品（Classic）。

    精确匹配目标可执行文件：冻结态要求目标就是当前 exe；开发态目标
    是当前解释器（快捷方式 TargetPath 只存解释器路径，参数另存）。
    VibeOCR Next 等其它应用的 exe 同名（VibeOCR.exe）但路径不同，
    不能按名称子串判断。
    """
    from vibeocr.classic.utils.shortcuts import read_windows_shortcut_target

    legacy = _win32_legacy_shortcut_path()
    if not legacy.exists():
        return False
    target = read_windows_shortcut_target(str(legacy)).strip().lower()
    if not target:
        return False
    ours = Path(sys.executable).resolve().as_posix().lower()
    theirs = Path(target).resolve().as_posix().lower()
    return theirs == ours


def _remove_legacy_shortcut_if_ours() -> None:
    if _legacy_shortcut_is_ours():
        try:
            _win32_legacy_shortcut_path().unlink(missing_ok=True)
            logger.info("已移除旧命名的启动快捷方式（VibeOCR.lnk → VibeOCR Classic.lnk）")
        except OSError as e:
            logger.warning(f"移除旧命名启动快捷方式失败: {e}")


def _win32_is_enabled() -> bool:
    if _win32_shortcut_path().exists():
        return True
    # 旧命名快捷方式仍有效（升级前已开启自启），视为已启用。
    return _legacy_shortcut_is_ours()


def _win32_ensure_shortcut() -> bool:
    """创建启动文件夹 .lnk（若已存在则覆盖），并清理指向本产品的旧命名。"""
    target = _get_exe_path()
    # 工作目录用目标可执行文件所在目录；开发态 target 形如 '"py" -m ...'，
    # 取不到稳定目录，传空让快捷方式使用默认。
    try:
        working_dir = str(Path(sys.executable).resolve().parent)
    except (OSError, ValueError):
        working_dir = ""
    created = create_windows_shortcut(
        target=target,
        shortcut_path=str(_win32_shortcut_path()),
        description="VibeOCR Classic",
        working_dir=working_dir,
    )
    if created:
        _remove_legacy_shortcut_if_ours()
    return created


def _win32_set(enabled: bool) -> bool:
    if enabled:
        if not _win32_ensure_shortcut():
            return False
        logger.debug(f"已创建启动文件夹快捷方式: {_win32_shortcut_path()}")
    else:
        removed_any = False
        for lnk in (_win32_shortcut_path(),):
            try:
                lnk.unlink(missing_ok=True)
                removed_any = True
            except OSError as e:
                logger.error(f"移除启动文件夹快捷方式失败: {e}")
                return False
        # 关闭自启时也一并移除指向本产品的旧命名快捷方式。
        if _legacy_shortcut_is_ours():
            try:
                _win32_legacy_shortcut_path().unlink(missing_ok=True)
                removed_any = True
            except OSError as e:
                logger.warning(f"移除旧命名启动快捷方式失败: {e}")
        if removed_any:
            logger.debug("已移除启动文件夹快捷方式")
    return True


# ============================================================
# macOS 实现 (LaunchAgents)
# ============================================================

_MACOS_PLIST_NAME = "com.vibeocr.app.plist"


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / _MACOS_PLIST_NAME


def _macos_is_enabled() -> bool:
    return _macos_plist_path().exists()


def _macos_set(enabled: bool) -> bool:
    plist_path = _macos_plist_path()
    if enabled:
        exe_path = _get_exe_path()
        # 分割命令为参数列表
        if exe_path.startswith('"'):
            parts = exe_path.strip('"').split('" ')
        else:
            parts = exe_path.split()

        program_args = "\n".join(f"        <string>{p}</string>" for p in parts)
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.vibeocr.app</string>
    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content, encoding="utf-8")
        logger.debug(f"已创建 macOS LaunchAgent: {plist_path}")
    else:
        if plist_path.exists():
            plist_path.unlink()
            logger.debug(f"已移除 macOS LaunchAgent: {plist_path}")
    return True


# ============================================================
# Linux 实现 (XDG autostart)
# ============================================================

_LINUX_DESKTOP_NAME = "vibeocr.desktop"


def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / _LINUX_DESKTOP_NAME


def _linux_is_enabled() -> bool:
    return _linux_desktop_path().exists()


def _linux_set(enabled: bool) -> bool:
    desktop_path = _linux_desktop_path()
    if enabled:
        exe_path = _get_exe_path()
        desktop_content = f"""[Desktop Entry]
Type=Application
Name=VibeOCR
Exec={exe_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=VibeOCR OCR Tool
"""
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(desktop_content, encoding="utf-8")
        logger.debug(f"已创建 Linux autostart: {desktop_path}")
    else:
        if desktop_path.exists():
            desktop_path.unlink()
            logger.debug(f"已移除 Linux autostart: {desktop_path}")
    return True
