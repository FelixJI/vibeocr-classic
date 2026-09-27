"""mineru_connection 纯逻辑契约测试。

覆盖 URL/Key 校验、payload 构建（保留/清除已保存 Key、本地仅存 mode）、
settings extra 容错解析、会话模式状态，以及提交 seam 在远程模式下
省略旧 hybrid-engine 选项的回归。
"""

from __future__ import annotations

import pytest

from vibeocr.classic.mineru_connection import (
    MINERU_CONNECTION_EXTRA_KEY,
    MINERU_CONNECTION_MODE_LOCAL,
    MINERU_CONNECTION_MODE_REMOTE,
    active_mineru_connection_mode,
    build_mineru_connection,
    mineru_remote_active,
    parse_mineru_connection,
    set_active_mineru_connection_mode,
    validate_mineru_api_key,
    validate_mineru_api_url,
)


@pytest.fixture(autouse=True)
def _local_mode_default():
    """每个用例后恢复默认本地模式，避免会话状态跨测试污染。"""
    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)
    yield
    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)


@pytest.mark.parametrize(
    "url",
    [
        "http://mineru.internal",
        "https://mineru.example.com/",
        "https://mineru.example.com:8443",
        "https://example.com/mineru-proxy/",
        "https://example.com/mineru-proxy/v1",
        "HTTP://EXAMPLE.COM/",
    ],
)
def test_valid_root_urls_pass(url):
    assert validate_mineru_api_url(url) is None


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("", "不能为空"),
        ("   ", "不能为空"),
        ("https://h/ a", "空白"),
        ("https://h/\tpath", "空白"),
        ("mineru.example.com", "http"),
        ("ftp://mineru.example.com/", "http"),
        ("https://", "主机名"),
        ("https://user:pass@example.com/", "用户名"),
        ("https://token@example.com/", "用户名"),
        ("https://example.com/?token=1", "查询"),
        ("https://example.com/#section", "锚点"),
        ("https://[invalid", "无效"),
        ("https://example.com:bad/", "无效"),
    ],
)
def test_invalid_root_urls_are_rejected_with_reason(url, fragment):
    error = validate_mineru_api_url(url)
    assert error is not None
    assert fragment in error


def test_api_key_only_rejects_crlf_without_length_cap():
    assert validate_mineru_api_key("") is None
    assert validate_mineru_api_key("sk-plain-token") is None
    # 无后端契约依据不设长度上限：超长 Key 应被接受。
    assert validate_mineru_api_key("k" * 1024) is None
    assert "换行" in validate_mineru_api_key("a\nb")
    assert "换行" in validate_mineru_api_key("a\rb")


def test_build_local_mode_saves_only_mode():
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_LOCAL,
        "https://stale.example.com/",
        "stale-key",
        clear_key=True,
        previous={"mode": "remote", "api_key": "old"},
    )
    assert error is None
    assert payload == {"mode": MINERU_CONNECTION_MODE_LOCAL}


def test_build_remote_with_new_key():
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE, "https://m.example.com/", "sk-new"
    )
    assert error is None
    assert payload == {
        "mode": MINERU_CONNECTION_MODE_REMOTE,
        "api_url": "https://m.example.com/",
        "api_key": "sk-new",
    }


def test_build_remote_without_key_omits_field():
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE, "https://m.example.com/", ""
    )
    assert error is None
    assert payload == {
        "mode": MINERU_CONNECTION_MODE_REMOTE,
        "api_url": "https://m.example.com/",
    }


def test_build_remote_empty_key_keeps_previous_key():
    """仅改 URL 时不得意外清除已保存 Key。"""
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE,
        "https://new.example.com/",
        "",
        previous=parse_mineru_connection(
            {
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": "remote",
                    "api_url": "https://old.example.com/",
                    "api_key": "saved-key",
                }
            }
        ),
    )
    assert error is None
    assert payload is not None
    assert payload["api_key"] == "saved-key"
    assert payload["api_url"] == "https://new.example.com/"


def test_build_remote_explicit_clear_removes_previous_key():
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE,
        "https://m.example.com/",
        "",
        clear_key=True,
        previous={"api_key": "saved-key"},
    )
    assert error is None
    assert payload is not None
    assert "api_key" not in payload


def test_build_remote_invalid_inputs_return_error_without_payload():
    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE, "https://m.example.com/?q=1", ""
    )
    assert payload is None
    assert "查询" in error

    payload, error = build_mineru_connection(
        MINERU_CONNECTION_MODE_REMOTE, "https://m.example.com/", "bad\nkey"
    )
    assert payload is None
    assert "换行" in error

    payload, error = build_mineru_connection(
        "future-mode", "https://m.example.com/", ""
    )
    assert payload is None
    assert "模式" in error


@pytest.mark.parametrize(
    "extra",
    [
        None,
        {},
        {MINERU_CONNECTION_EXTRA_KEY: "garbage"},
        {MINERU_CONNECTION_EXTRA_KEY: {"mode": "future"}},
        {MINERU_CONNECTION_EXTRA_KEY: {"mode": "remote", "api_url": 42}},
    ],
)
def test_parse_falls_back_to_local_view_on_bad_payloads(extra):
    view = parse_mineru_connection(extra)
    assert view == {
        "mode": MINERU_CONNECTION_MODE_LOCAL,
        "api_url": "",
        "api_key": None,
        "has_api_key": False,
    }


def test_parse_remote_view_distinguishes_key_presence():
    with_key = parse_mineru_connection(
        {
            MINERU_CONNECTION_EXTRA_KEY: {
                "mode": "remote",
                "api_url": "https://m.example.com/",
                "api_key": "secret",
            }
        }
    )
    assert with_key["mode"] == MINERU_CONNECTION_MODE_REMOTE
    assert with_key["has_api_key"] is True
    assert with_key["api_key"] == "secret"

    without_key = parse_mineru_connection(
        {
            MINERU_CONNECTION_EXTRA_KEY: {
                "mode": "remote",
                "api_url": "https://m.example.com/",
                "api_key": "",
            }
        }
    )
    assert without_key["has_api_key"] is False
    assert without_key["api_key"] is None


def test_session_mode_state_defaults_local_and_tracks_publish():
    assert active_mineru_connection_mode() == MINERU_CONNECTION_MODE_LOCAL
    assert mineru_remote_active() is False

    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_REMOTE)
    assert mineru_remote_active() is True

    set_active_mineru_connection_mode("bogus")
    assert active_mineru_connection_mode() == MINERU_CONNECTION_MODE_LOCAL


def test_submission_seam_emits_typed_mineru_config_in_remote_mode():
    """单文件与批量共用的提交入口须产生 SDK 2.9 合法 typed 请求。"""

    from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

    from vibeocr.classic.recognition_settings import OCROptions

    options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)

    local_selection = options.to_pipeline_selection()
    assert local_selection.pipeline_id == "MinerU"
    assert local_selection.options["backend"] == "hybrid-engine"

    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_REMOTE)
    remote_selection = options.to_pipeline_selection()
    assert remote_selection.pipeline_id == "MinerU"
    assert remote_selection.options == {}
    assert remote_selection.to_payload()["mineru"] == {
        "tier": "basic",
        "ocr_mode": "auto",
        "page_range": "all",
        "language": "ch",
    }

    chosen = options.copy(
        effort="high",
        parse_method="ocr",
        lang_list=["korean"],
        start_page_id=1,
        end_page_id=3,
    ).to_pipeline_selection()
    assert chosen.to_payload()["mineru"] == {
        "tier": "standard",
        "ocr_mode": "ocr",
        "page_range": "2-4",
        "language": "korean",
    }


def test_remote_mineru_rejects_unrepresentable_legacy_options():
    from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline
    from vibeocr.classic.recognition_settings import OCROptions

    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_REMOTE)
    with pytest.raises(ValueError, match="不支持当前旧解析选项"):
        OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING, enable_table=False
        ).to_pipeline_selection()
    with pytest.raises(ValueError, match="不支持所选文档语言"):
        OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING, lang_list=["en"]
        ).to_pipeline_selection()
