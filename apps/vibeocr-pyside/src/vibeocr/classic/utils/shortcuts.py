"""Windows 快捷方式共享工具。

提取自 ``views/settings_page_controller.py``，供桌面/开始菜单快捷方式与
开机自启（启动文件夹 .lnk）共用，避免逻辑重复。

实现走 PowerShell + WScript.Shell COM，不引入 pywin32 等额外依赖，与
仓库其它 Windows 互操作（``winreg`` / ``ctypes.windll``）风格一致。
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_windows_startup_dir() -> Path:
    """返回当前用户的启动文件夹路径。

    即 ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``。
    该路径不受 OneDrive 重定向影响（OneDrive 只接管 Desktop/文档等）。
    """
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def create_windows_shortcut(
    target: str,
    shortcut_path: str,
    description: str = "VibeOCR",
    icon_path: str = "",
    working_dir: str = "",
) -> bool:
    """在 Windows 上通过 PowerShell COM 创建 ``.lnk`` 快捷方式。

    Args:
        target: 快捷方式目标可执行文件路径。
        shortcut_path: 生成的 ``.lnk`` 完整路径。
        description: 快捷方式描述。
        icon_path: 图标路径（可选）。
        working_dir: 工作目录（可选）。

    Returns:
        创建成功返回 ``True``，否则 ``False``。
    """
    # 确保目标目录存在
    try:
        Path(shortcut_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    ps_lines = [
        "$WshShell = New-Object -ComObject WScript.Shell",
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}')",
        f"$Shortcut.TargetPath = '{target}'",
        f"$Shortcut.Description = '{description}'",
    ]
    if icon_path:
        ps_lines.append(f"$Shortcut.IconLocation = '{icon_path}'")
    if working_dir:
        ps_lines.append(f"$Shortcut.WorkingDirectory = '{working_dir}'")
    ps_lines.append("$Shortcut.Save()")

    script = "; ".join(ps_lines)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("PowerShell 创建快捷方式失败")
        return False
