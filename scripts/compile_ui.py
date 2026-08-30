#!/usr/bin/env python3
"""将 Qt .ui 文件编译为 Python 代码

用法:
    python scripts/compile_ui.py
    uv run python scripts/compile_ui.py
"""

import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_uic_command() -> list[str]:
    """解析 pyside6-uic 命令。

    PySide6 6.11 起，`python -m PySide6.QtUiTools.uic` 模块入口已不可用
    （ModuleNotFoundError），实际入口是随 PySide6 安装的 pyside6-uic 可执行文件。
    通过 shutil.which 定位它（与具体 venv 路径解耦），找不到时报错退出。
    """
    uic_exe = shutil.which("pyside6-uic")
    if uic_exe:
        return [uic_exe]
    # 兜底：同解释器目录下的 pyside6-uic(.exe)
    candidate = Path(sys.executable).parent / (
        "pyside6-uic.exe" if sys.platform == "win32" else "pyside6-uic"
    )
    if candidate.exists():
        return [str(candidate)]
    raise FileNotFoundError(
        "未找到 pyside6-uic。请确认 PySide6 已安装（pip install pyside6）。"
    )


def compile_ui_file(ui_path: Path, output_path: Path) -> bool:
    """编译单个 UI 文件"""
    print(f"编译: {ui_path.name} -> {output_path.name}")

    result = subprocess.run(
        [
            *_resolve_uic_command(),
            "-g",
            "python",
            str(ui_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"错误: {result.stderr}")
        return False

    print(f"成功: {output_path}")
    return True


def main():
    """编译所有 UI 文件"""
    # 项目根目录：scripts/ 的上一级（仓库根）；UI 位于 classic 应用源码树内
    project_root = Path(__file__).parent.parent

    # UI 文件目录
    ui_dir = (
        project_root / "apps" / "vibeocr-pyside" / "src" / "vibeocr" / "classic" / "ui"
    )

    # 输出目录 (相同目录)
    output_dir = ui_dir

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    # 查找所有 .ui 文件
    ui_files = list(ui_dir.glob("*.ui"))

    if not ui_files:
        print("警告: 未找到 .ui 文件")
        return

    print(f"找到 {len(ui_files)} 个 UI 文件")

    success = True
    for ui_file in ui_files:
        # 输出文件名: ui_XXX.py
        output_name = f"ui_{ui_file.stem}.py"
        output_path = output_dir / output_name

        if not compile_ui_file(ui_file, output_path):
            success = False

    if success:
        print("\n所有 UI 文件编译成功!")
    else:
        print("\n部分 UI 文件编译失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
