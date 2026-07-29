"""Tests for table-extraction helpers used by export + copy.

These pure functions live in ``vibeocr.backend.utils.html_tables`` so they can be
shared between the backend export service and the PySide6 clipboard path.
They guarantee that tables survive Excel export and Excel/Word paste even
when the structured ``content_list`` is empty but the table HTML only lives
in ``html_text`` / ``markdown_text`` / ``text_blocks``.
"""

from __future__ import annotations

from types import SimpleNamespace

from vibeocr.backend.utils.html_tables import (
    html_tables_to_cell_grid,
    tables_from_result,
)


class TestHtmlTablesToCellGrid:
    """``html_tables_to_cell_grid`` parses every <table> into rows×cols."""

    def test_single_table_basic(self):
        html = "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"
        grids = html_tables_to_cell_grid(html)
        assert grids == [[["A", "B"], ["1", "2"]]]

    def test_multiple_tables(self):
        html = (
            "<table><tr><td>T1</td></tr></table>"
            "<table><tr><td>T2a</td><td>T2b</td></tr></table>"
        )
        grids = html_tables_to_cell_grid(html)
        assert grids == [[["T1"]], [["T2a", "T2b"]]]

    def test_strips_html_body_wrapper(self):
        """PaddleX pred_html often comes wrapped in <html><body>...</body></html>."""
        html = (
            "<html><body><table><tr><td>X</td></tr></table></body></html>"
        )
        assert html_tables_to_cell_grid(html) == [[["X"]]]

    def test_strips_inline_style_attributes(self):
        """Inline style (background colour etc.) must not break parsing."""
        html = (
            '<table><tr><td style="background:#eee">A</td>'
            '<th style="color:red">B</th></tr></table>'
        )
        assert html_tables_to_cell_grid(html) == [[["A", "B"]]]

    def test_th_and_td_mixed(self):
        html = (
            "<table><thead><tr><th>H1</th><th>H2</th></tr></thead>"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
        )
        assert html_tables_to_cell_grid(html) == [[["H1", "H2"], ["1", "2"]]]

    def test_decodes_html_entities(self):
        html = "<table><tr><td>a &amp; b</td><td>c &lt; d</td></tr></table>"
        assert html_tables_to_cell_grid(html) == [[["a & b", "c < d"]]]

    def test_br_becomes_newline(self):
        html = "<table><tr><td>line1<br>line2</td></tr></table>"
        assert html_tables_to_cell_grid(html) == [[["line1\nline2"]]]

    def test_no_table_returns_empty(self):
        assert html_tables_to_cell_grid("<p>just text</p>") == []

    def test_empty_string_returns_empty(self):
        assert html_tables_to_cell_grid("") == []

    def test_skips_empty_rows(self):
        """Rows with no cells (e.g. stray <tr></tr>) are dropped."""
        html = "<table><tr></tr><tr><td>only</td></tr></table>"
        assert html_tables_to_cell_grid(html) == [[["only"]]]


class TestTablesFromResult:
    """``tables_from_result`` collects table HTML from any available source.

    Priority order: content_list table_body → text_blocks label=table →
    html_text <table> → markdown rendered table. Returns de-duplicated HTML
    fragments.
    """

    def test_from_content_list_table_body(self):
        result = SimpleNamespace(
            content_list=[
                {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>"},
            ],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        tables = tables_from_result(result)
        assert len(tables) == 1
        assert "<table>" in tables[0]

    def test_content_list_html_field_fallback(self):
        """Old/PaddleX blocks may carry 'html' instead of 'table_body'."""
        result = SimpleNamespace(
            content_list=[{"type": "table", "html": "<table><tr><td>H</td></tr></table>"}],
            text_blocks=[],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        tables = tables_from_result(result)
        assert len(tables) == 1
        assert "<table>" in tables[0]

    def test_from_text_blocks_label_table(self):
        result = SimpleNamespace(
            content_list=[],
            text_blocks=[
                SimpleNamespace(
                    label="table",
                    text="<table><tr><td>from blocks</td></tr></table>",
                ),
            ],
            html_text="",
            markdown_text="",
            raw_text="",
        )
        tables = tables_from_result(result)
        assert len(tables) == 1
        assert "from blocks" in tables[0]

    def test_from_html_text_when_no_content_list(self):
        """content_list empty but html_text carries the rendered <table>."""
        result = SimpleNamespace(
            content_list=[],
            text_blocks=[],
            html_text=(
                "<style>body{}</style><body>"
                "<table><tr><td>via html</td></tr></table>"
                "</body>"
            ),
            markdown_text="",
            raw_text="",
        )
        tables = tables_from_result(result)
        assert len(tables) == 1
        assert "via html" in tables[0]

    def test_no_table_anywhere_returns_empty(self):
        result = SimpleNamespace(
            content_list=[{"type": "text", "text": "just text"}],
            text_blocks=[],
            html_text="<p>no table here</p>",
            markdown_text="plain",
            raw_text="plain",
        )
        assert tables_from_result(result) == []

    def test_dedup_identical_tables(self):
        """Same table appearing in content_list AND html_text is reported once."""
        same = "<table><tr><td>dup</td></tr></table>"
        result = SimpleNamespace(
            content_list=[{"type": "table", "table_body": same}],
            text_blocks=[],
            html_text=same,
            markdown_text="",
            raw_text="",
        )
        tables = tables_from_result(result)
        assert len(tables) == 1

    def test_dict_result_supported(self):
        """When the result is a plain dict (wire form), still extract tables."""
        result = {
            "content_list": [
                {"type": "table", "table_body": "<table><tr><td>D</td></tr></table>"},
            ],
            "text_blocks": [],
            "html_text": "",
            "markdown_text": "",
            "raw_text": "",
        }
        tables = tables_from_result(result)
        assert len(tables) == 1
        assert "<table>" in tables[0]

    def test_canonical_content_list_prevents_stale_projection_rediscovery(self):
        from vibeocr.runtime_contracts.contracts.tables import TableCellV1, TableModelV1

        table = TableModelV1(
            table_id="canonical",
            row_count=1,
            column_count=1,
            cells=(TableCellV1(cell_id="cell", row=0, column=0, text="fresh"),),
        )
        result = SimpleNamespace(
            content_list=[
                {
                    "type": "table",
                    "table": table.to_payload(),
                    "table_body": "<table><tr><td>stale-body</td></tr></table>",
                }
            ],
            text_blocks=[
                SimpleNamespace(
                    label="table",
                    text="<table><tr><td>stale-block</td></tr></table>",
                )
            ],
            html_text="<table><tr><td>stale-html</td></tr></table>",
            markdown_text="",
            raw_text="",
        )

        tables = tables_from_result(result)

        assert len(tables) == 1
        assert "fresh" in tables[0]
        assert "stale" not in tables[0]
