"""日志服务模块"""

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from vibeocr.classic.logging_context import LOG_CONTEXT_FIELDS, ui_status_extra

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance


class _SignalEmitter(QObject):
    """Qt 信号发射器，与 logging.Handler 分离以避免 emit 方法冲突"""

    status_signal = Signal(str)  # 发射状态栏消息


class HumanReadableFormatter(logging.Formatter):
    """人可读文本格式，用于落盘 vibeocr.log。

    替换原 JsonLogFormatter——历史排查时单行 JSON 难读，且代码内无消费者读回
    该文件（worker 转发链路仍走 stderr JSONL，与此独立）。保留上下文字段兜底：
    LOG_CONTEXT_FIELDS 与 worker_context 在生产代码里极少被填充，但少数场景
    （batch 提交、worker 转发）会携带，丢了可惜，故以 ``[k=v, k=v]`` 追加到行尾。
    """

    _BASE_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s"
    _DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self._BASE_FMT, datefmt=self._DATE_FMT)

    def format(self, record: logging.LogRecord) -> str:
        # 基础行：时间戳(毫秒) 级别(左对齐5) logger: 消息
        line = super().format(record)

        # 附加非空上下文字段（request_id/task_id/pipeline/page/batch）
        pairs: list[str] = []
        for field in LOG_CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                pairs.append(f"{field}={value}")

        # worker 转发的额外字段（forward_worker_output_line 打包的非标准键）
        worker_context = getattr(record, "worker_context", None)
        if isinstance(worker_context, dict):
            for key, value in worker_context.items():
                pairs.append(f"{key}={value}")

        if pairs:
            line += "  [" + ", ".join(pairs) + "]"

        # 异常 traceback 走 logging 默认多行输出，附加在行尾
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += "\n" + record.exc_text

        return line


class QtLogHandler(logging.Handler):
    """将 Python logging 重定向到状态栏的处理器"""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _SignalEmitter()

    @property
    def status_signal(self) -> "SignalInstance":
        return self._emitter.status_signal

    def emit(self, record: logging.LogRecord) -> None:
        """处理日志记录"""
        try:
            try:
                _ = self.status_signal
            except RuntimeError:
                return

            msg = self.format(record)
            if self._should_show_in_status(record):
                self.status_signal.emit(msg)
        except RuntimeError:
            pass
        except Exception:
            self.handleError(record)

    def _should_show_in_status(self, record: logging.LogRecord) -> bool:
        """状态栏只接收调用方显式标记的日志。"""
        return bool(getattr(record, "ui_status", False))


def log_ui_status(
    logger: logging.Logger,
    message: str,
    *args,
    level: int = logging.INFO,
    **context,
) -> None:
    """记录一条显式的状态栏消息，不依赖文本关键词。"""
    logger.log(level, message, *args, extra=ui_status_extra(**context))


def _cleanup_old_logs(log_dir: Path, max_age_days: int = 7) -> None:
    """删除超过指定天数的旧日志文件"""
    cutoff = time.time() - max_age_days * 86400
    for f in log_dir.iterdir():
        if f.is_file() and f.name != "vibeocr.log" and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    value = logging.getLevelNamesMapping().get(str(level).upper())
    return value if isinstance(value, int) else logging.INFO


def apply_log_level(level: int | str) -> int:
    """立即调整本进程日志级别，并传递给后续启动的 WorkerHost。"""
    effective_level = _coerce_level(level)
    os.environ["VIBEOCR_LOG_LEVEL"] = logging.getLevelName(effective_level)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.setLevel(effective_level)
    return effective_level


def setup_logging(level: int | str = logging.INFO) -> QtLogHandler:
    """配置全局日志处理器

    Returns:
        QtLogHandler 实例
    """
    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    # 根日志器设为 DEBUG，由各 handler 自行过滤
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清除已有的 handler（某些库 import 时会调用 basicConfig 添加默认 handler，
    # 导致同一消息被输出两次，格式分别为 LEVEL:name:msg 和 [LEVEL] name: msg）
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    root_logger.addHandler(handler)

    # 控制台 handler：开发环境 DEBUG，打包环境 WARNING
    console_handler = logging.StreamHandler()
    console_handler.setLevel(
        logging.DEBUG if not getattr(sys, "frozen", False) else logging.WARNING
    )
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(console_handler)

    # 文件 handler：DEBUG 及以上（全量记录，便于排查）
    # 主程序和 updater 统一写入 data/logs，避免在便携包根目录散落运行期文件。
    # 落盘为人可读文本（HumanReadableFormatter）——历史排查时直接打开阅读，
    # 不再有 JSON 解析开销与可读性损失。worker 转发链路仍用 stderr JSONL，
    # 由 forward_worker_output_line 解析后转成普通 LogRecord，经此 formatter 输出。
    from vibeocr.classic.app_paths import get_install_root

    log_dir = get_install_root() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(log_dir)

    file_handler = RotatingFileHandler(
        log_dir / "vibeocr.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(_coerce_level(level))
    file_handler.setFormatter(HumanReadableFormatter())
    root_logger.addHandler(file_handler)

    # 第三方库降噪：根日志器是 DEBUG，若不显式降级，fontTools/paddle/urllib3 等
    # 库的 INFO/DEBUG 会大量混入（如 PDF 渲染时 fontTools.subset 的逐字形日志）。
    # 仅 vibeocr.* 保持 DEBUG 全量记录；以下库降到 WARNING。
    _noisy_loggers = (
        "fontTools",
        "PIL",
        "paddle",
        "paddlex",
        "paddleocr",
        "urllib3",
        "matplotlib",
        "huggingface_hub",
        "filelock",
        "asyncio",
        # 更新检查走 qasync+httpx/httpcore，DEBUG 级会刷出大量 IO 轮询日志
        # （每读一个 64KB chunk 打两行 poll/event），把真正有用的 INFO 淹没。
        "qasync",
        "httpcore",
        "httpx",
    )
    for name in _noisy_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)

    apply_log_level(level)

    return handler
