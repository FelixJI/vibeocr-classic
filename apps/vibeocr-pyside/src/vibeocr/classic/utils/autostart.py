"""跨平台开机自启动工具

支持 Windows、macOS 和 Linux 的开机自启配置。
"""

import logging
import sys
from pathlib import Path

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


# ============================================================
# Windows 实现 (注册表)
# ============================================================

_WIN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_APP_NAME = "VibeOCR"


def _win32_is_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _WIN_APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def _win32_set(enabled: bool) -> bool:
    import winreg

    if enabled:
        exe_path = _get_exe_path()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _WIN_APP_NAME, 0, winreg.REG_SZ, exe_path)
        logger.debug(f"已添加开机自启注册表项: {exe_path}")
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, _WIN_APP_NAME)
            logger.debug("已移除开机自启注册表项")
        except FileNotFoundError:
            pass  # 本来就没有，忽略
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
