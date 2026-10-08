"""本产品运行标识锁（Windows 命名 Mutex 实现）。

Classic 与 VibeOCR Next 是两个独立产品，允许同时运行；本锁只承载 Classic
自身的会话级运行标识，用于在极端竞态（同产品单实例管道尚未建立时）兜底
拒绝第二个 Classic 实例，不再与任何其它产品互斥。

为什么保留命名 Mutex 作为兜底：
- 创建 Mutex 是原子操作，不受 exe 改名、PID 复用和同时启动的竞态影响；
- 前端崩溃后由操作系统自动释放（无需额外清理子进程）；
- 与同产品单实例（``utils/single_instance.py``）的 activation pipe 解耦。

与同产品单实例（``utils/single_instance.py``）的关系：
- 同产品第二实例：优先由 activation pipe 拦截（转发参数后退出）；
- 本锁仅在管道路径失效时兜底，提示"另一个 VibeOCR Classic 正在运行"。

本模块**不依赖 PySide6/Qt**，可在 ``QApplication`` 创建之前调用，确保 Mutex
成功前绝不启动后端。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)

# Classic 专属运行标识 Mutex 名称。前缀 ``Local\`` 限定在当前登录会话。
# 该名称仅在本产品（Classic）内使用；VibeOCR Next 不共享此字符串，
# 因此两个产品可以同时运行。历史上这里曾是跨产品互斥名
# ``Local\VibeOCR.Frontend.Exclusive.v2``，2026-10 起按产品隔离。
EXCLUSIVE_MUTEX_NAME = r"Local\VibeOCRClassic.Frontend.Exclusive.v1"

# Windows GetLastError 错误码：表示命名对象已存在（即另一个进程持有 Mutex）。
_ERROR_ALREADY_EXISTS = 183


class FrontendExclusiveLock:
    """本产品会话级运行标识锁，基于 Windows 命名 Mutex。

    用法::

        lock = FrontendExclusiveLock()
        if not lock.try_acquire():
            # 另一个 VibeOCR Classic 实例正在运行，提示后退出
            return 1
        # 本实例独占运行；退出时调用 lock.release() 或依赖上下文管理器

    也可作为上下文管理器使用::

        with FrontendExclusiveLock() as acquired:
            if not acquired:
                show_another_running_message(); return 1
            # ... 启动主窗口与后端 ...

    线程安全：单实例使用，不在多线程中共享。Mutex 句柄在 ``release`` 或
    对象析构时关闭；进程崩溃时由 OS 回收，不会产生孤儿。
    """

    def __init__(self, name: str = EXCLUSIVE_MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._acquired = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_acquired(self) -> bool:
        return self._acquired

    def try_acquire(self) -> bool:
        """尝试原子获取本产品运行标识 Mutex。

        Returns:
            True  —— 本实例成功获得运行标识（当前会话内无另一个 Classic 实例）；
            False —— 另一个 VibeOCR Classic 实例已持有该 Mutex，本实例应提示退出。
        """
        if self._acquired:
            return True
        if os_name() != "nt":
            # 非 Windows：VibeOCR 仅面向 Windows，此处放行（无 Mutex 语义）。
            # 生产路径不会走到这里；测试在非 Windows 上跑时跳过实际互斥。
            self._acquired = True
            return True

        handle = _create_mutex(self._name)
        if handle == 0:
            # 无法证明独占时必须 fail closed；否则可能启动第二个后端。
            logger.error("[FrontendExclusiveLock] CreateMutex 失败，拒绝启动")
            self._acquired = False
            return False

        already_exists = _last_error() == _ERROR_ALREADY_EXISTS
        self._handle = handle
        if already_exists:
            logger.info(
                "[FrontendExclusiveLock] 另一个 VibeOCR Classic 实例正在运行，本实例退出"
            )
            # 释放刚创建的句柄——本实例不持有锁，只是确认了对方存在。
            self._close()
            return False

        self._acquired = True
        logger.debug("[FrontendExclusiveLock] 已获得本产品运行标识 Mutex")
        return True

    def release(self) -> None:
        """关闭 Mutex 句柄，释放独占。

        进程正常退出时调用。进程崩溃时由 OS 自动回收，无需额外处理。
        重复调用安全（幂等）。
        """
        self._close()
        self._acquired = False

    def _close(self) -> None:
        if self._handle is not None:
            _close_handle(self._handle)
            self._handle = None

    # -- 上下文管理器 ---------------------------------------------------

    def __enter__(self) -> bool:
        return self.try_acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        with _suppress(Exception):
            self._close()


# ---------------------------------------------------------------------------
# Windows ctypes 绑定（延迟到函数内 import，保持模块顶层无 Windows 依赖）
# ---------------------------------------------------------------------------


def os_name() -> str:
    """隔离 ``os.name`` 读取，便于测试注入。"""
    import os

    return os.name


def _create_mutex(name: str) -> int:
    """调用 ``kernel32.CreateMutexW``，返回句柄（0 表示失败）。"""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,  # lpMutexAttributes (NULL)
        ctypes.c_int,  # bInitialOwner (False)
        ctypes.c_wchar_p,  # lpName
    ]
    handle = kernel32.CreateMutexW(None, False, name)
    return int(handle) if handle else 0


def _last_error() -> int:
    import ctypes

    return ctypes.get_last_error()


def _close_handle(handle: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(handle)


class _suppress:
    """``contextlib.suppress`` 的轻量内联副本，避免顶层 import 依赖。"""

    def __init__(self, *exceptions: type[BaseException]) -> None:
        self._exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, self._exceptions)


__all__ = ["EXCLUSIVE_MUTEX_NAME", "FrontendExclusiveLock"]
