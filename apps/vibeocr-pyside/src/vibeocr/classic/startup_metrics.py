"""可信的启动里程碑记录：T0–T6。

定义 7 个启动里程碑事件，提供 StartupRecorder 记录时间戳，并在
``VIBEOCR_STARTUP_TRACE=<path>`` 设置时输出 JSONL。默认不落盘。

里程碑语义（T0–T6）::

    T0 PROCESS_START   进程入口
    T1 RUNTIME_READY   Python bootstrap 完成（env_manager 已就绪）
    T2 SHELL_CREATED   Qt/WinUI 壳创建（QApplication 构造完成）
    T3 FIRST_WINDOW    首窗可见（splash 或 MainWindow.show）
    T4 SUPERVISOR_READY Supervisor ready envelope
    T5 BACKEND_READY   OCR backend ready（预加载完成）
    T6 INTERACTIVE     首次可交互（用户可操作）

设计要点：
- 重复事件只保留首次时间戳（里程碑是"首次到达"语义）。
- 乱序事件各自记录（异步就绪回调可能乱序到达）。
- JSONL 脱敏：不写入本机绝对路径。
- p50/p95 用 nearest-rank 方法（简单、确定、无需 numpy）。
"""

from __future__ import annotations

import json
import os
import time
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

TRACE_ENV_VAR = "VIBEOCR_STARTUP_TRACE"
_startup_origin = time.perf_counter()


class StartupEvent(StrEnum):
    """启动里程碑事件。wire value 固定为 T0–T6。"""

    PROCESS_START = "T0"
    RUNTIME_READY = "T1"
    SHELL_CREATED = "T2"
    FIRST_WINDOW = "T3"
    SUPERVISOR_READY = "T4"
    BACKEND_READY = "T5"
    INTERACTIVE = "T6"


# 有序事件列表（T0 在前，T6 在后）
_ORDERED_EVENTS: tuple[StartupEvent, ...] = tuple(StartupEvent)


class StartupRecorder:
    """记录 T0–T6 里程碑时间戳。

    Usage::

        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        ...
        rec.record(StartupEvent.INTERACTIVE, 3.2)
        rec.flush()  # 若 VIBEOCR_STARTUP_TRACE 已设置则写 JSONL

    线程安全说明：record/flush 非线程安全。调用方应在同一线程（主线程）
    记录里程碑。异步回调应在主线程通过 Qt signal 触发记录。
    """

    def __init__(self) -> None:
        # 用 dict 存储；重复 record 只保留首次
        self._events: dict[StartupEvent, float] = {}
        self._flushed = False

    @property
    def events(self) -> dict[StartupEvent, float]:
        """已记录的事件→时间戳映射（只读视图）。"""
        return dict(self._events)

    def record(self, event: StartupEvent, timestamp: float) -> None:
        """记录一个里程碑事件。

        重复记录同一事件只保留首次时间戳。
        timestamp 应为 monotonic 时间（秒），通常以 T0 为 0 基准。
        """
        if event not in self._events:
            self._events[event] = float(timestamp)

    def is_complete(self) -> bool:
        """是否所有 7 个里程碑都已记录。"""
        return len(self._events) == len(StartupEvent)

    def to_dict(self) -> dict[str, float]:
        """转换为 {event_value: timestamp} dict（用于序列化/汇总）。"""
        return {ev.value: ts for ev, ts in self._events.items()}

    def flush(self) -> None:
        """若 VIBEOCR_STARTUP_TRACE 已设置，写入 JSONL（一行一个 run）。

        未设置环境变量时不做任何事。已 flush 的 recorder 不会重复写入。
        JSONL 内容脱敏：不含本机绝对路径。
        """
        if self._flushed:
            return
        self._flushed = True

        trace_path = os.environ.get(TRACE_ENV_VAR)
        if not trace_path:
            return

        from pathlib import Path as _Path

        p = _Path(trace_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 只写事件值和时间戳——不含路径信息
        record = self.to_dict()
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def percentile(data: Sequence[float], pct: float) -> float:
    """计算百分位数（nearest-rank 方法）。

    Args:
        data: 非空数值序列。
        pct: 百分位（0–100）。

    Returns:
        该百分位对应的值。

    Raises:
        ValueError: data 为空。
    """
    if not data:
        raise ValueError("cannot compute percentile of empty data")
    sorted_data = sorted(data)
    n = len(sorted_data)
    # nearest-rank: rank = ceil(pct/100 * n)，1-based
    import math

    rank = max(1, math.ceil(pct / 100.0 * n))
    rank = min(rank, n)  # 不越界
    return sorted_data[rank - 1]


def summarize_runs(runs: Sequence[dict[str, float]]) -> dict[str, dict[str, float]]:
    """汇总多次启动运行，为每个里程碑计算 p50/p95。

    Args:
        runs: 每次 run 的 to_dict() 结果列表。

    Returns:
        ``{event_value: {"p50": x, "p95": y, "count": n, "min": a, "max": b}}``
        只包含至少有 1 次 run 记录的里程碑。
    """
    # 收集每个里程碑的所有时间戳
    by_event: dict[str, list[float]] = {}
    for run in runs:
        for ev_key, ts in run.items():
            by_event.setdefault(ev_key, []).append(float(ts))

    summary: dict[str, dict[str, float]] = {}
    for ev_key, timestamps in by_event.items():
        summary[ev_key] = {
            "p50": percentile(timestamps, 50),
            "p95": percentile(timestamps, 95),
            "count": float(len(timestamps)),
            "min": min(timestamps),
            "max": max(timestamps),
        }
    return summary


# ---------------------------------------------------------------------------
# 全局单例 recorder（供 main.py 各阶段记录，无需传参）
# ---------------------------------------------------------------------------
_global_recorder: StartupRecorder | None = None


def get_recorder() -> StartupRecorder:
    """获取全局 StartupRecorder 单例。首次调用时创建并自动记录 T0。"""
    global _global_recorder
    if _global_recorder is None:
        _global_recorder = StartupRecorder()
    return _global_recorder


def set_startup_origin(timestamp: float) -> None:
    """Set the monotonic T0 origin used by :func:`record_startup`.

    The process entry point calls this once with a timestamp captured before
    importing application modules. Tests and alternate hosts may do the same.
    """
    global _startup_origin
    _startup_origin = float(timestamp)


def record_startup(event: StartupEvent, timestamp: float | None = None) -> None:
    """便捷函数：在全局 recorder 上记录里程碑。

    Args:
        event: 里程碑事件。
        timestamp: monotonic 时间戳（秒）。None 时用 time.perf_counter()。
    """
    if timestamp is None:
        timestamp = time.perf_counter() - _startup_origin
    get_recorder().record(event, timestamp)


def flush_startup() -> None:
    """便捷函数：flush 全局 recorder。"""
    get_recorder().flush()
