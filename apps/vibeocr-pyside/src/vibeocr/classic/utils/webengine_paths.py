"""QtWebEngine 便携存储路径配置。

QtWebEngine 的默认 cache/persistent storage 落在用户 profile；完全便携
约束下必须在创建任何 page 之前把它们指到
``<portable-root>/state/web/qtwebengine``。进程内只配置一次；磁盘缓存另由
入口设置 ``QTWEBENGINE_DISK_CACHE_PATH``（Chromium 子进程读取）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_configured = False


def configure_webengine_storage() -> bool:
    """把默认 profile 的 cache/persistent storage 指到 state 内。

    必须在首个 QWebEngineView/page 创建之前调用；重复调用是幂等空操作。
    返回是否配置成功（QtWebEngine 不可用时由调用方自行降级）。
    """

    global _configured
    if _configured:
        return True
    try:
        from PySide6.QtWebEngineCore import QWebEngineProfile

        from vibeocr.classic.app_paths import get_active_app_paths

        paths = get_active_app_paths()
        paths.webengine_cache_dir.mkdir(parents=True, exist_ok=True)
        paths.webengine_persistent_dir.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile.defaultProfile()
        profile.setCachePath(str(paths.webengine_cache_dir))
        profile.setPersistentStoragePath(str(paths.webengine_persistent_dir))
        _configured = True
        return True
    except Exception as exc:  # noqa: BLE001 — WebEngine 缺失不应阻断宿主功能
        logger.warning(f"QtWebEngine 便携存储配置失败: {exc}")
        return False


__all__ = ["configure_webengine_storage"]
