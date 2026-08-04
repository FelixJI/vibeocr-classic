"""logging_context 模块测试（JSONL 结构化日志协议）。

覆盖成功路径、失败路径与边界条件。重点验证：
- JsonLogFormatter.format 的 worker_exception / exc_info / exc_text 三分支；
- LOG_CONTEXT_FIELDS 非空写入、worker_* 属性覆盖默认；
- _coerce_level 各类型回退；
- forward_worker_output_line 的空行/非 JSON/缺 message/合法 JSON 分支；
- configure_worker_stderr_logging 替换 root handlers + 噪声 logger 降级。

隔离约定：function-scope 还原 root logger handlers，避免污染其他测试。
"""

import json
import logging

import pytest

from vibeocr.classic.logging_context import (
    LOG_CONTEXT_FIELDS,
    JsonLogFormatter,
    _coerce_level,
    configure_worker_stderr_logging,
    forward_worker_output_line,
    ui_status_extra,
)


@pytest.fixture
def formatter():
    """默认 frontend/profile 的 formatter。"""
    return JsonLogFormatter(frontend="pyside", profile="production")


@pytest.fixture
def capture_logger():
    """构造一个带内存 handler 的独立 logger（不污染 root）。"""
    logger = logging.getLogger("test.logging_context")
    logger.handlers.clear()
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    cap = _Capture()
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    return logger, records


def _make_record(**extra) -> logging.LogRecord:
    """构造一个带 extra 属性的 LogRecord。"""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


# ---------------------------------------------------------------------------
# JsonLogFormatter.format
# ---------------------------------------------------------------------------


def test_format_basic_message(formatter):
    """基本消息格式化为合法 JSON。"""
    out = formatter.format(_make_record())
    doc = json.loads(out)
    assert doc["message"] == "hello world"
    assert doc["level"] == "INFO"
    assert doc["logger"] == "test"
    assert doc["frontend"] == "pyside"
    assert doc["profile"] == "production"
    assert doc["event"] == "log"  # 默认 event
    assert doc["exception"] is None
    assert "timestamp" in doc


def test_format_worker_exception_branch(formatter):
    """worker_exception 属性优先于 exc_info/exc_text。"""
    record = _make_record(worker_exception="boom trace")
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["exception"] == "boom trace"


def test_format_exc_info_branch(formatter):
    """exc_info 分支：formatException 输出。"""
    try:
        raise ValueError("inner")
    except ValueError:
        import sys

        record = _make_record(exc_info=sys.exc_info())
    out = formatter.format(record)
    doc = json.loads(out)
    assert "ValueError" in doc["exception"]
    assert "inner" in doc["exception"]


def test_format_exc_text_branch(formatter):
    """exc_text 分支（预先格式化的异常文本）。"""
    record = _make_record(exc_text="preformatted traceback")
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["exception"] == "preformatted traceback"


def test_format_worker_exception_overrides_exc_info(formatter):
    """worker_exception 优先于 exc_info。"""
    try:
        raise ValueError("inner")
    except ValueError:
        import sys

        record = _make_record(exc_info=sys.exc_info(), worker_exception="worker-trace")
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["exception"] == "worker-trace"


def test_format_worker_attributes_override_defaults(formatter):
    """worker_* 属性覆盖默认 timestamp/logger/process/thread/frontend/profile。"""
    record = _make_record(
        worker_timestamp="2026-01-01T00:00:00Z",
        worker_logger="worker.pdf",
        worker_process=99,
        worker_thread="ThreadX",
        worker_frontend="worker",
        worker_profile="winui-dev",
    )
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["timestamp"] == "2026-01-01T00:00:00Z"
    assert doc["logger"] == "worker.pdf"
    assert doc["process"] == 99
    assert doc["thread"] == "ThreadX"
    assert doc["frontend"] == "worker"
    assert doc["profile"] == "winui-dev"


def test_format_event_attribute(formatter):
    """record.event 属性覆盖默认 'log'。"""
    record = _make_record(event="custom.event")
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["event"] == "custom.event"


def test_format_log_context_fields(formatter):
    """LOG_CONTEXT_FIELDS 非空时写入 document。"""
    record = _make_record(
        request_id="req-1",
        task_id="task-2",
        pipeline="OCR",
        page=5,
        batch="b1",
    )
    out = formatter.format(record)
    doc = json.loads(out)
    assert doc["request_id"] == "req-1"
    assert doc["task_id"] == "task-2"
    assert doc["pipeline"] == "OCR"
    assert doc["page"] == 5
    assert doc["batch"] == "b1"


def test_format_log_context_fields_none_omitted(formatter):
    """LOG_CONTEXT_FIELDS 为 None 时不写入。"""
    record = _make_record(request_id=None)
    out = formatter.format(record)
    doc = json.loads(out)
    assert "request_id" not in doc


def test_format_timestamp_iso_with_z(formatter):
    """timestamp 是 ISO 格式且带 Z 后缀。"""
    out = formatter.format(_make_record())
    doc = json.loads(out)
    assert doc["timestamp"].endswith("Z")


def test_format_valid_json_separators(formatter):
    """输出用紧凑分隔符（无多余空格）。"""
    out = formatter.format(_make_record())
    assert ", " not in out  # 紧凑分隔符
    assert ": " not in out


# ---------------------------------------------------------------------------
# _coerce_level
# ---------------------------------------------------------------------------


def test_coerce_level_int_valid():
    """正整数直接返回。"""
    assert _coerce_level(logging.WARNING, logging.INFO) == logging.WARNING


def test_coerce_level_int_nonpositive_falls_back():
    """非正整数回退到 fallback。"""
    assert _coerce_level(0, logging.INFO) == logging.INFO
    assert _coerce_level(-1, logging.DEBUG) == logging.DEBUG


def test_coerce_level_str_valid():
    """合法级别字符串返回对应 int。"""
    assert _coerce_level("WARNING", logging.INFO) == logging.WARNING
    assert _coerce_level("debug", logging.INFO) == logging.DEBUG  # 大小写不敏感
    assert _coerce_level("error", logging.INFO) == logging.ERROR


def test_coerce_level_str_invalid_falls_back():
    """非法级别字符串回退。"""
    assert _coerce_level("TRACE", logging.INFO) == logging.INFO
    assert _coerce_level("nonsense", logging.DEBUG) == logging.DEBUG


def test_coerce_level_none_falls_back():
    """None 回退。"""
    assert _coerce_level(None, logging.INFO) == logging.INFO


def test_coerce_level_other_type_falls_back():
    """非 int/str 类型回退。"""
    assert _coerce_level(3.14, logging.INFO) == logging.INFO
    assert _coerce_level([], logging.WARNING) == logging.WARNING


# ---------------------------------------------------------------------------
# forward_worker_output_line
# ---------------------------------------------------------------------------


def test_forward_empty_line_returns_false(capture_logger):
    """空行（含纯换行）返回 False，不产生 fallback 日志。

    注意：rstrip 只去 \\r\\n，不去空格；"   \\n" 经 rstrip 后为 "   "（非空），
    会走 fallback。真正的空行是 "" 或 "\\n"。
    """
    logger, records = capture_logger
    assert (
        forward_worker_output_line(
            logger, "", fallback_level=logging.INFO, stream_name="stderr"
        )
        is False
    )
    assert (
        forward_worker_output_line(
            logger, "\n", fallback_level=logging.INFO, stream_name="stderr"
        )
        is False
    )
    assert (
        forward_worker_output_line(
            logger, "\r\n", fallback_level=logging.INFO, stream_name="stderr"
        )
        is False
    )
    assert records == []


def test_forward_non_json_returns_false(capture_logger):
    """非 JSON 文本返回 False 并走 fallback log。"""
    logger, records = capture_logger
    result = forward_worker_output_line(
        logger, "not json at all", fallback_level=logging.WARNING, stream_name="stdout"
    )
    assert result is False
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].event == "worker.output"


def test_forward_json_without_message_returns_false(capture_logger):
    """合法 JSON 但缺 message 字段返回 False。"""
    logger, records = capture_logger
    result = forward_worker_output_line(
        logger,
        json.dumps({"level": "INFO"}),  # 无 message
        fallback_level=logging.INFO,
        stream_name="stderr",
    )
    assert result is False
    assert len(records) == 1  # 走 fallback


def test_forward_valid_json_returns_true(capture_logger):
    """合法 JSON 带 message 返回 True 并按原 severity 转发。"""
    logger, records = capture_logger
    line = json.dumps({"message": "started", "level": "ERROR", "event": "worker.ready"})
    result = forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    assert result is True
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "started"


def test_forward_unknown_level_uses_fallback(capture_logger):
    """未知 level 字符串回退到 fallback_level。"""
    logger, records = capture_logger
    line = json.dumps({"message": "x", "level": "TRACE"})
    result = forward_worker_output_line(
        logger, line, fallback_level=logging.DEBUG, stream_name="stderr"
    )
    assert result is True
    assert records[0].levelno == logging.DEBUG


def test_forward_carries_worker_attributes(capture_logger):
    """转发的 record 带 worker_* 属性。"""
    logger, records = capture_logger
    line = json.dumps(
        {
            "message": "hi",
            "level": "INFO",
            "logger": "worker.ocr",
            "process": 1,
            "thread": "T",
            "frontend": "worker",
            "profile": "prod",
            "timestamp": "2026-01-01T00:00:00Z",
            "exception": "err",
        }
    )
    forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    rec = records[0]
    assert rec.worker_logger == "worker.ocr"
    assert rec.worker_process == 1
    assert rec.worker_thread == "T"
    assert rec.worker_frontend == "worker"
    assert rec.worker_profile == "prod"
    assert rec.worker_timestamp == "2026-01-01T00:00:00Z"
    assert rec.worker_exception == "err"
    assert rec.worker_stream == "stderr"


def test_forward_extracts_context_fields(capture_logger):
    """LOG_CONTEXT_FIELDS 从 JSON 提取到 record。"""
    logger, records = capture_logger
    line = json.dumps({"message": "x", "request_id": "r1", "task_id": "t1", "page": 3})
    forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    rec = records[0]
    assert rec.request_id == "r1"
    assert rec.task_id == "t1"
    assert rec.page == 3


def test_forward_extracts_custom_context(capture_logger):
    """非 base/context 字段提取到 worker_context。"""
    logger, records = capture_logger
    line = json.dumps({"message": "x", "custom_field": "value", "extra": 42})
    forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    rec = records[0]
    assert rec.worker_context == {"custom_field": "value", "extra": 42}


def test_forward_carries_event(capture_logger):
    """JSON 的 event 字段转发到 record.event。"""
    logger, records = capture_logger
    line = json.dumps({"message": "x", "event": "pipeline.done"})
    forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    assert records[0].event == "pipeline.done"


def test_forward_strips_whitespace(capture_logger):
    """行尾换行被去除。"""
    logger, records = capture_logger
    line = json.dumps({"message": "x"}) + "\r\n"
    result = forward_worker_output_line(
        logger, line, fallback_level=logging.INFO, stream_name="stderr"
    )
    assert result is True


# ---------------------------------------------------------------------------
# configure_worker_stderr_logging
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_root_logger():
    """保存并还原 root logger 的 handlers 与 level。"""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_noisy = {
        name: logging.getLogger(name).level for name in ("fontTools", "PIL", "urllib3")
    }
    yield root
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    for name, lvl in saved_noisy.items():
        logging.getLogger(name).setLevel(lvl)


def test_configure_replaces_root_handlers(restore_root_logger):
    """configure 后 root 只有一个新 handler。"""
    configure_worker_stderr_logging(
        frontend="pyside", profile="production", stream=__import__("io").StringIO()
    )
    root = restore_root_logger
    assert len(root.handlers) == 1


def test_configure_returns_handler(restore_root_logger):
    """返回创建的 handler。"""
    handler = configure_worker_stderr_logging(
        frontend="pyside", profile="production", stream=__import__("io").StringIO()
    )
    assert isinstance(handler, logging.StreamHandler)
    assert isinstance(handler.formatter, JsonLogFormatter)


def test_configure_sets_root_level(restore_root_logger):
    """显式 level 注入 root。"""
    configure_worker_stderr_logging(
        frontend="pyside",
        profile="production",
        stream=__import__("io").StringIO(),
        level=logging.DEBUG,
    )
    assert restore_root_logger.level == logging.DEBUG


def test_configure_noisy_loggers_demoted_to_warning(restore_root_logger):
    """噪声 logger 降级到至少 WARNING。"""
    configure_worker_stderr_logging(
        frontend="pyside",
        profile="production",
        stream=__import__("io").StringIO(),
        level=logging.DEBUG,
    )
    for name in ("fontTools", "PIL", "urllib3"):
        assert logging.getLogger(name).level >= logging.WARNING


def test_configure_default_level_when_none(restore_root_logger, monkeypatch):
    """level=None 且无环境变量时默认 INFO。"""
    monkeypatch.delenv("VIBEOCR_LOG_LEVEL", raising=False)
    configure_worker_stderr_logging(
        frontend="pyside", profile="production", stream=__import__("io").StringIO()
    )
    assert restore_root_logger.level == logging.INFO


def test_configure_level_from_env(restore_root_logger, monkeypatch):
    """level=None 时从 VIBEOCR_LOG_LEVEL 环境变量读取。"""
    monkeypatch.setenv("VIBEOCR_LOG_LEVEL", "DEBUG")
    configure_worker_stderr_logging(
        frontend="pyside", profile="production", stream=__import__("io").StringIO()
    )
    assert restore_root_logger.level == logging.DEBUG


def test_configure_emits_json(restore_root_logger):
    """configure 后实际日志输出为 JSON。"""
    import io

    stream = io.StringIO()
    configure_worker_stderr_logging(
        frontend="pyside", profile="production", stream=stream
    )
    logging.getLogger("test.emit").info("emitted")
    out = stream.getvalue().strip()
    doc = json.loads(out)
    assert doc["message"] == "emitted"


# ---------------------------------------------------------------------------
# ui_status_extra
# ---------------------------------------------------------------------------


def test_ui_status_extra_basic():
    """ui_status_extra 拼装 ui_status + event + context。"""
    extra = ui_status_extra()
    assert extra == {"ui_status": True, "event": "ui.status"}


def test_ui_status_extra_with_context():
    """context 字段合并进 extra。"""
    extra = ui_status_extra(page=1, pipeline="OCR")
    assert extra["ui_status"] is True
    assert extra["event"] == "ui.status"
    assert extra["page"] == 1
    assert extra["pipeline"] == "OCR"


def test_log_context_fields_contents():
    """LOG_CONTEXT_FIELDS 包含预期字段。"""
    assert set(LOG_CONTEXT_FIELDS) == {
        "request_id",
        "task_id",
        "pipeline",
        "page",
        "batch",
    }
