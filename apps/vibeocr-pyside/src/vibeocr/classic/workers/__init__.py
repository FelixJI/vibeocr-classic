"""Workers 包 - 子进程 Worker 模块

提供独立运行的子进程 worker，用于隔离重型依赖。
不要在此处提前导入子模块，避免 python -m 运行时的双重加载问题。
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

__all__ = []
