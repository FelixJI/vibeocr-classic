"""管理器模块

包含：
- ConfigManager: 统一配置管理
- DependencyManager: 依赖管理
- LayoutManager: 布局管理
- SubprocessManager: 子进程管理
"""

from vibeocr.classic.managers.config_manager import ConfigManager
from vibeocr.classic.managers.dependency_manager import DependencyManager
from vibeocr.classic.managers.layout_manager import LayoutManager
from vibeocr.classic.managers.subprocess_manager import SubprocessManager

__all__ = [
    "ConfigManager",
    "DependencyManager",
    "LayoutManager",
    "SubprocessManager",
]
