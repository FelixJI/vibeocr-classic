"""Classic 启动阶段使用的窄平台默认值。"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)

_FALLBACK_CPU_THREADS = 4
_CPU_THREADS_CAP = 16


def get_cpu_thread_count() -> int:
    """返回 Classic 启动时使用的 CPU 线程数。"""
    override = os.environ.get("VIBEOCR_CPU_THREADS", "").strip()
    if override:
        try:
            thread_count = int(override)
            if thread_count > 0:
                return thread_count
        except ValueError:
            logger.warning("忽略无效的 VIBEOCR_CPU_THREADS=%r", override)

    try:
        thread_count = os.cpu_count() or _FALLBACK_CPU_THREADS
    except Exception:
        thread_count = _FALLBACK_CPU_THREADS
    return max(1, min(thread_count, _CPU_THREADS_CAP))
