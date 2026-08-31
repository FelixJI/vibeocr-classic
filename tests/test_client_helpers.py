"""client 模块 helper 函数测试（导出文件名 + 唯一路径 + shutdown 回退）。

覆盖成功路径、失败路径与边界条件。重点验证：
- get_output_filename 的 5 种导出格式扩展名 + 未知格式回退；
- get_unique_output_path 的不存在/已存在/_1 也存在 → _2 计数；
- shutdown_backend_client 的 ImportError 回退 no-op + 正常调用。

注意：与 test_classic_main_runtime.py 区分（后者测 main 入口，本文件测 helper）。
"""

from pathlib import Path

import pytest

from vibeocr.classic import client
from vibeocr.classic.client import (
    get_output_filename,
    get_unique_output_path,
    shutdown_backend_client,
)


# ---------------------------------------------------------------------------
# get_output_filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,ext",
    [
        ("markdown", ".md"),
        ("html", ".html"),
        ("txt", ".txt"),
        ("docx", ".docx"),
        ("xlsx", ".xlsx"),
    ],
)
def test_get_output_filename_known_formats(fmt, ext):
    """已知导出格式映射到正确扩展名。"""
    assert get_output_filename("report", fmt) == f"report{ext}"


def test_get_output_filename_unknown_format_defaults_txt():
    """未知格式回退到 .txt。"""
    assert get_output_filename("report", "pdf") == "report.txt"
    assert get_output_filename("report", "") == "report.txt"


def test_get_output_filename_strips_source_extension():
    """source_name 带扩展名时取 stem。"""
    assert get_output_filename("scan.png", "markdown") == "scan.md"
    assert get_output_filename("doc.pdf", "html") == "doc.html"


def test_get_output_filename_strips_path_components():
    """source_name 含路径时只取文件名 stem。"""
    assert get_output_filename("dir/sub/file", "txt") == "file.txt"


def test_get_output_filename_no_extension_source():
    """source_name 无扩展名时整体作 stem。"""
    assert get_output_filename("notes", "markdown") == "notes.md"


def test_get_output_filename_multiple_dots():
    """多扩展名取第一个 stem。"""
    assert get_output_filename("archive.tar.gz", "txt") == "archive.tar.txt"


# ---------------------------------------------------------------------------
# get_unique_output_path
# ---------------------------------------------------------------------------


def test_get_unique_output_path_nonexistent(tmp_path):
    """目标不存在时原样返回。"""
    p = tmp_path / "out.md"
    assert get_unique_output_path(p) == p


def test_get_unique_output_path_exists_appends_1(tmp_path):
    """目标已存在时追加 _1。"""
    p = tmp_path / "out.md"
    p.write_text("x")
    result = get_unique_output_path(p)
    assert result == tmp_path / "out_1.md"


def test_get_unique_output_path_1_exists_appends_2(tmp_path):
    """_1 也存在时追加 _2。"""
    (tmp_path / "out.md").write_text("a")
    (tmp_path / "out_1.md").write_text("b")
    result = get_unique_output_path(tmp_path / "out.md")
    assert result == tmp_path / "out_2.md"


def test_get_unique_output_path_finds_first_gap(tmp_path):
    """跳过已占用的序号，找到第一个空位。"""
    (tmp_path / "out.md").write_text("a")
    (tmp_path / "out_1.md").write_text("b")
    (tmp_path / "out_2.md").write_text("c")
    result = get_unique_output_path(tmp_path / "out.md")
    assert result == tmp_path / "out_3.md"


def test_get_unique_output_path_preserves_suffix(tmp_path):
    """保留原始后缀（包括多段后缀的 suffix）。"""
    p = tmp_path / "data.tar.gz"
    p.write_text("x")
    result = get_unique_output_path(p)
    assert result.name == "data.tar_1.gz"


def test_get_unique_output_path_no_suffix(tmp_path):
    """无后缀文件也能处理。"""
    p = tmp_path / "notes"
    p.write_text("x")
    result = get_unique_output_path(p)
    assert result == tmp_path / "notes_1"


def test_get_unique_output_path_returns_path_type(tmp_path):
    """返回 Path 类型。"""
    p = tmp_path / "out.md"
    result = get_unique_output_path(p)
    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# shutdown_backend_client
# ---------------------------------------------------------------------------


def test_shutdown_backend_client_calls_bg_loop(monkeypatch):
    """正常情况调用 pdf_client._shutdown_bg_loop。"""
    called = {"n": 0}

    def _fake_shutdown():
        called["n"] += 1

    # patch pdf_client 模块的 _shutdown_bg_loop，client.shutdown_backend_client 会
    # 通过 lazy import 取到它
    import vibeocr.classic.pdf_client as pdf_client_mod

    monkeypatch.setattr(pdf_client_mod, "_shutdown_bg_loop", _fake_shutdown)
    shutdown_backend_client()
    assert called["n"] == 1


def test_shutdown_backend_client_does_not_import_unloaded_pdf_client(monkeypatch):
    """没有已创建的 PDF 后台资源时，关闭操作不得触发重型模块首次导入。"""
    import sys

    monkeypatch.delitem(sys.modules, "vibeocr.classic.pdf_client", raising=False)
    real_import = __import__

    def _guarded_import(name, *args, **kwargs):
        if name == "vibeocr.classic.pdf_client":
            raise AssertionError("shutdown must not import an unloaded PDF client")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guarded_import)
    shutdown_backend_client()


def test_shutdown_backend_client_idempotent(monkeypatch):
    """多次调用安全（幂等）。"""
    import vibeocr.classic.pdf_client as pdf_client_mod

    monkeypatch.setattr(pdf_client_mod, "_shutdown_bg_loop", lambda: None)
    shutdown_backend_client()
    shutdown_backend_client()  # 不抛


def test_module_all_export():
    """__all__ 导出预期函数。"""
    assert set(client.__all__) == {
        "get_output_filename",
        "get_unique_output_path",
        "shutdown_backend_client",
    }
