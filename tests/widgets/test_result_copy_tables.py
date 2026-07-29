"""Tests for table-aware copy in ResultViewWidget.

The copy helper builds ``(html, tab_text)`` so that pasting into Excel keeps
the row/column grid (tab-separated plain text) and pasting into Word keeps a
real table (HTML). Pure helper — no Qt clipboard required.
"""

from __future__ import annotations

from types import SimpleNamespace

from vibeocr.classic.widgets.result_view_widget import build_table_copy_payload


class TestBuildTableCopyPayload:
    """``build_table_copy_payload`` produces HTML + tab-separated text for tables."""

    def test_single_table_html_and_tab_text(self):
        result = SimpleNamespace(
            content_list=[
                {"type": "table", "table_body": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"},
            ],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        html, tab_text = build_table_copy_payload(result)
        assert "<table>" in html
        assert "<td>A</td>" in html
        # Tab-separated: rows split by \n, cells by \t
        lines = tab_text.split("\n")
        assert lines[0] == "A\tB"
        assert lines[1] == "1\t2"

    def test_multiple_tables_separated(self):
        result = SimpleNamespace(
            content_list=[
                {"type": "table", "table_body": "<table><tr><td>T1</td></tr></table>"},
                {"type": "table", "table_body": "<table><tr><td>T2a</td><td>T2b</td></tr></table>"},
            ],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        html, tab_text = build_table_copy_payload(result)
        assert html.count("<table>") == 2
        # Tables separated by a blank line in the plain-text form
        assert "T1" in tab_text and "T2a" in tab_text and "T2b" in tab_text
        assert "\t" in tab_text

    def test_table_from_html_text_only(self):
        """content_list empty but html_text carries the table."""
        result = SimpleNamespace(
            content_list=[],
            text_blocks=[],
            html_text="<body><table><tr><td>via html</td></tr></table></body>",
            markdown_text="",
            raw_text="",
        )
        html, tab_text = build_table_copy_payload(result)
        assert "<table>" in html
        assert tab_text == "via html"

    def test_no_tables_returns_empty(self):
        """No table anywhere → both html and tab_text are empty (caller falls back)."""
        result = SimpleNamespace(
            content_list=[{"type": "text", "text": "just words"}],
            text_blocks=[],
            html_text="<p>no table</p>",
            markdown_text="plain",
            raw_text="plain",
        )
        html, tab_text = build_table_copy_payload(result)
        assert html == ""
        assert tab_text == ""

    def test_html_has_no_inline_style(self):
        """Copy HTML must not carry inline styles (Excel/Word paste clean)."""
        result = SimpleNamespace(
            content_list=[
                {"type": "table", "table_body": '<table><tr><td style="background:#eee">X</td></tr></table>'},
            ],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        html, _ = build_table_copy_payload(result)
        assert "style" not in html
        assert "<td>X</td>" in html

    def test_entities_decoded_in_plain_text(self):
        result = SimpleNamespace(
            content_list=[
                {"type": "table", "table_body": "<table><tr><td>a &amp; b</td></tr></table>"},
            ],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        _, tab_text = build_table_copy_payload(result)
        assert tab_text == "a & b"

    def test_dict_result_supported(self):
        result = {
            "content_list": [
                {"type": "table", "table_body": "<table><tr><td>D</td></tr></table>"},
            ],
            "text_blocks": [],
            "html_text": "",
            "markdown_text": "",
            "raw_text": "",
        }
        html, tab_text = build_table_copy_payload(result)
        assert "<table>" in html
        assert tab_text == "D"
