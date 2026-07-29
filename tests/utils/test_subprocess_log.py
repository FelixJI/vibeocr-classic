"""``SubprocessLogForwarder`` 单元测试。

本测试从 ``tests/services/test_ocr_worker_process.py::TestParseAndForwardLog``
迁移而来（该类是这套逻辑最早的使用方），并参数化以验证对 PDF 后端、MinerU
等其他子进程通道同样成立。
"""

from __future__ import annotations

import pytest

from vibeocr.backend.utils.subprocess_log import SubprocessLogForwarder

# 参数化：覆盖项目里三个子进程通道的 logger 名 + source_label 组合。
# 每个用例都应给出一致的行为，这是"统一日志通道"的核心契约。
FORWARDER_CASES = [
    pytest.param(
        "vibeocr.subprocess.ocr_worker", "[Worker 0]", id="ocr_worker"
    ),
    pytest.param(
        "vibeocr.subprocess.pdf_backend", "[PDF Backend]", id="pdf_backend"
    ),
    pytest.param(
        "vibeocr.subprocess.mineru_api", "[MinerU API]", id="mineru_api"
    ),
]


@pytest.mark.parametrize("logger_name,source_label", FORWARDER_CASES)
class TestSubprocessLogForwarder:
    """子进程 stdout 转发为日志的行为。

    背景：PaddleX/transformers 等库会向 stdout 直接 print 识别结果/文本内容
    （如 "/x86" 这类用户文档片段），这些裸 print 不带标准日志格式，
    此前会被原样转发到日志，导致用户文档内容泄漏。
    期望：结构化日志行仍按级别转发；裸 print 只输出概括（行数），
    不输出具体内容。
    """

    def test_structured_line_forwarded_at_its_level(
        self, caplog, logger_name, source_label
    ):
        """标准日志格式（带时间戳+级别）按原级别转发，内容保留。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        line = (
            "2024-01-15 10:30:45 [INFO] vibeocr.classic.workers.ocr_worker: OCR 服务初始化完成"
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward(line)

        assert any(
            "OCR 服务初始化完成" in r.message and r.levelname == "INFO"
            for r in caplog.records
        )

    def test_structured_line_with_milliseconds_forwarded(
        self, caplog, logger_name, source_label
    ):
        """带逗号毫秒的标准行（logging 默认 asctime 格式）必须转发。

        回归 bug：_STRUCTURED_LINE_RE 原正则只匹配到秒（不含 ,123 毫秒），
        导致 worker 子进程的所有结构化日志（logging.basicConfig 默认格式
        ``%(asctime)s`` = "2024-01-15 10:30:45,123"）被当成裸 print 折叠丢弃。
        用户日志里完全看不到 worker 的 [Worker] 前缀消息、PipelineTTLWatcher
        启动、TTL 回收等日志。
        """
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        line = (
            "2024-01-15 10:30:45,123 [INFO] vibeocr.backend.services.pipeline_cache_manager: "
            "PipelineTTLWatcher 启动 (ttls={'OCR': 0}, max_heavy=1, tick=30s)"
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward(line)

        assert any(
            "PipelineTTLWatcher 启动" in r.message and r.levelname == "INFO"
            for r in caplog.records
        ), "带逗号毫秒的结构化日志行未被转发——worker 日志丢失根因"

    def test_structured_warning_line_forwarded_at_warning_level(
        self, caplog, logger_name, source_label
    ):
        """WARNING 级别的标准行按 WARNING 转发。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        line = "2024-01-15 10:30:45 [WARNING] foo: 模型加载较慢"

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward(line)

        assert any(
            "模型加载较慢" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_all_levels_mapped(self, caplog, logger_name, source_label):
        """DEBUG/INFO/WARNING/ERROR/CRITICAL 五个级别都正确还原。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        cases = [
            ("DEBUG", "d"),
            ("INFO", "i"),
            ("WARNING", "w"),
            ("ERROR", "e"),
            ("CRITICAL", "c"),
        ]

        with caplog.at_level("DEBUG", logger=logger_name):
            for level, tag in cases:
                forwarder.forward(
                    f"2024-01-15 10:30:45 [{level}] mod: {tag}"
                )

        got = {r.levelname for r in caplog.records}
        assert {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} <= got

    def test_raw_print_does_not_leak_content(
        self, caplog, logger_name, source_label
    ):
        """裸 print（无标准日志格式）不得把原始内容写进日志。

        模拟库 print 出识别到的文本片段 "/x86"。这些内容绝不能出现在日志里。
        """
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        line = "/x86  这是用户文档里的敏感文本片段"

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward(line)

        leaked = [r.message for r in caplog.records if "/x86" in r.message]
        assert leaked == [], f"裸 print 内容泄漏到日志: {leaked}"

    def test_raw_print_summarized_as_count(
        self, caplog, logger_name, source_label
    ):
        """连续多条裸 print 只输出一条概括（行数），不逐条 dump。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        raw_lines = [
            "/x86  内容1",
            "some raw paddle debug 一二三",
            "另一行裸输出",
        ]

        with caplog.at_level("DEBUG", logger=logger_name):
            for line in raw_lines:
                forwarder.forward(line)
            # 触发 flush（例如来了一个结构化行，或显式 flush）
            forwarder.flush()

        # 内容绝不出现在任何日志记录里
        assert all("内容1" not in r.message for r in caplog.records)
        assert all("一二三" not in r.message for r in caplog.records)
        # 至少有一条概括记录，且提到行数 3
        summary = [r.message for r in caplog.records if "3" in r.message]
        assert summary, "应有概括记录（行数）"

    def test_structured_line_after_raw_flushes_summary(
        self, caplog, logger_name, source_label
    ):
        """结构化行到来时，先 flush 之前的裸 print 概括，再转发结构化行。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )
        structured = "2024-01-15 10:30:45 [INFO] mod: 完成"

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward("裸输出A")
            forwarder.forward("裸输出B")
            forwarder.forward(structured)

        msgs = [r.message for r in caplog.records]
        levels = [r.levelname for r in caplog.records]
        # 第一条是概括（不含裸内容），最后一条是结构化 INFO
        assert "裸输出A" not in msgs[0]
        assert "完成" in msgs[-1]
        assert levels[-1] == "INFO"
        # 概括记录提到了 2 行
        assert "2" in msgs[0]

    def test_newline_only_raw_print_is_ignored(
        self, caplog, logger_name, source_label
    ):
        """空行/纯空白的裸 print 不计入概括。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward("   ")
            forwarder.flush()

        assert caplog.records == []

    def test_source_label_appears_in_forwarded_message(
        self, caplog, logger_name, source_label
    ):
        """转发的结构化行带上 source_label 前缀，便于日志中区分来源。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward("2024-01-15 10:30:45 [INFO] mod: hi")

        assert any(source_label in r.message for r in caplog.records)

    def test_traceback_start_forwarded_at_error(
        self, caplog, logger_name, source_label
    ):
        """Python traceback 起始行以 ERROR 级别原样转发（不折叠）。

        子进程 import 失败退出码 1 时会输出 Traceback，折叠掉就无法定位
        真实错误（历史上 PDF 后端启动失败只剩"退出码1"无法排查）。
        """
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward("Traceback (most recent call last):")

        assert any(
            "Traceback (most recent call last):" in r.message
            and r.levelname == "ERROR"
            for r in caplog.records
        )

    def test_exception_line_forwarded_at_error(
        self, caplog, logger_name, source_label
    ):
        """traceback 末行异常名（如 ModuleNotFoundError）以 ERROR 转发。"""
        forwarder = SubprocessLogForwarder(
            logger_name=logger_name, source_label=source_label
        )

        with caplog.at_level("DEBUG", logger=logger_name):
            forwarder.forward("ModuleNotFoundError: No module named 'vibeocr'")

        assert any(
            "ModuleNotFoundError" in r.message and r.levelname == "ERROR"
            for r in caplog.records
        )


class TestSplitMixedLines:
    """``SubprocessLogForwarder.split_mixed_lines`` 的行为。"""

    def test_single_line_returned_as_is(self):
        """无拼接的单行原样返回。"""
        text = "2024-01-15 10:30:45 [INFO] mod: hi"
        assert SubprocessLogForwarder.split_mixed_lines(text) == [text]

    def test_empty_text_returns_empty(self):
        assert SubprocessLogForwarder.split_mixed_lines("") == []

    def test_multiple_datetime_patterns_split(self):
        """PaddlePaddle 无换行拼接的多行按日期时间模式切分。"""
        text = (
            "2024-01-15 10:30:45 [WARNING] foo: bar"
            "2024-01-15 10:30:46 [INFO] baz: qux"
            "2024-01-15 10:30:47 [ERROR] mod: boom"
        )
        lines = SubprocessLogForwarder.split_mixed_lines(text)
        assert len(lines) == 3
        assert "[WARNING]" in lines[0]
        assert "[INFO]" in lines[1]
        assert "[ERROR]" in lines[2]


def test_raw_log_auto_flush_at_threshold(caplog):
    """裸 print 累积达阈值时自动 flush（line 110-116），无需显式 flush。"""
    from vibeocr.backend.utils.subprocess_log import SubprocessLogForwarder

    forwarder = SubprocessLogForwarder(
        logger_name="test_auto_flush",
        source_label="[T]",
        raw_flush_threshold=3,
    )
    with caplog.at_level("DEBUG", logger="test_auto_flush"):
        # 发 3 条裸 print，第 3 条触发自动 flush
        for i in range(3):
            forwarder.forward(f"raw line {i} 内容")
        # 不显式 flush，应已自动产生概括记录
    # 内容不泄露
    assert all("内容" not in r.message for r in caplog.records)
    # 有概括记录提到 3 行
    assert any("3" in r.message for r in caplog.records)


def test_split_lines_returns_multiple_for_multiple_datetime_patterns():
    """含多个日期时间模式的文本被分割成多行（line 154-159）。"""
    from vibeocr.backend.utils.subprocess_log import SubprocessLogForwarder

    text = (
        "2024-01-01 12:00:00 first line\n"
        "2024-01-01 12:00:01 second line\n"
        "2024-01-01 12:00:02 third"
    )
    lines = SubprocessLogForwarder.split_mixed_lines(text)
    assert len(lines) == 3
    assert "first line" in lines[0]
    assert "third" in lines[2]
