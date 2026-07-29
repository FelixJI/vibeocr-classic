import logging

import pytest

from vibeocr.runtime_contracts.utils import http_log


def test_status_summary_explains_known_and_unknown_codes() -> None:
    assert "200 OK" in http_log.status_summary(200)
    assert "成功处理" in http_log.status_summary(200)
    assert "599 Unknown" in http_log.status_summary(599)
    assert "服务端处理失败" in http_log.status_summary(599)


def test_transaction_redacts_query_values_and_lists_metrics(
) -> None:
    message = http_log.format_http_transaction(
        "post",
        "http://127.0.0.1:61335/session/model?token=secret&page=3",
        422,
        reason="Unprocessable Entity",
        elapsed_ms=12.34,
        request_bytes=1024,
        response_bytes=2048,
        stream=True,
    )

    assert "POST /session/model?token=<redacted>&page=<redacted>" in message
    assert "secret" not in message
    assert "422 " in message
    assert "Unprocessable Entity" in message
    assert "参数校验失败" in message
    assert "耗时=12.3ms" in message
    assert "请求体=1.0 KB" in message
    assert "返回体=2.0 KB" in message
    assert "stream=True" in message


def test_size_helpers_count_utf8_bytes_and_ignore_invalid_header(
) -> None:
    assert http_log.guess_request_size("中文") == 6
    assert http_log.guess_response_size({}, "中文") == 6
    assert http_log.guess_response_size({"Content-Length": "12"}, None) == 12
    assert http_log.guess_response_size({"Content-Length": "bad"}, None) is None


def test_log_level_tracks_status_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(f"test.http.{id(http_log)}")
    logger.propagate = True

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        http_log.log_http_response(logger, "GET", "/ok", 200)
        http_log.log_http_response(logger, "GET", "/bad", 404)
        http_log.log_http_response(logger, "GET", "/error", 503)

    levels = [record.levelno for record in caplog.records[-3:]]
    assert levels == [logging.DEBUG, logging.WARNING, logging.ERROR]


def test_human_bytes_bytes_and_gb_tiers() -> None:
    """_human_bytes 的 B 与 GB 分支（line 99-103）。"""
    from vibeocr.runtime_contracts.utils.http_log import _human_bytes

    assert _human_bytes(0) == "0 B"
    assert _human_bytes(512) == "512 B"
    assert _human_bytes(1023) == "1023 B"
    # GB tier (>= 1GB)
    assert _human_bytes(1024 * 1024 * 1024).endswith(" GB")
    # 远超 GB → 仍用 GB（units[-1]）
    huge = _human_bytes(5 * 1024 * 1024 * 1024)
    assert huge.endswith(" GB")
    assert _human_bytes(None) is None


def test_shorten_path_adds_leading_slash() -> None:
    """path 不以 / 开头时补 /（line 87-88）。"""
    message = http_log.format_http_transaction(
        method="get",
        url="example.com/path?q=1",
        status_code=200,
    )
    # 路径应被补上 /
    assert "/path" in message


def test_format_transaction_without_sizes() -> None:
    """不传 request/response bytes 时不报错（line 140 附近）。"""
    message = http_log.format_http_transaction(
        method="post",
        url="https://api.example.com/v1/submit",
        status_code=201,
    )
    assert "POST" in message
    assert "201" in message


def test_safe_int_handles_none_and_invalid() -> None:
    """_safe_int 对 None/非法值返回 None（line 140）。"""
    from vibeocr.runtime_contracts.utils.http_log import _safe_int

    assert _safe_int(None) is None
    assert _safe_int("123") == 123
    assert _safe_int(456) == 456
    assert _safe_int("not-a-number") is None
    assert _safe_int(object()) is None


def test_guess_response_size_from_content_and_headers() -> None:
    """guess_response_size 从 content(bytes/str) 与 headers 读取（line 178-188）。"""
    from vibeocr.runtime_contracts.utils.http_log import guess_response_size

    # bytes content
    assert guess_response_size(None, b"hello") == 5
    # str content → utf-8 编码后长度
    assert guess_response_size(None, "你好") == len("你好".encode())
    # 无 content，从 headers
    assert guess_response_size({"content-length": "42"}, None) == 42
    assert guess_response_size({"Content-Length": "99"}, None) == 99
    # 都无
    assert guess_response_size(None, None) is None
    assert guess_response_size({}, None) is None


def test_guess_request_size_branches() -> None:
    """guess_request_size 各分支（line 192-199）。"""
    from vibeocr.runtime_contracts.utils.http_log import guess_request_size

    assert guess_request_size(None) is None
    assert guess_request_size(b"abc") == 3
    assert guess_request_size("abc") == 3
    assert guess_request_size(bytearray(b"xy")) == 2
