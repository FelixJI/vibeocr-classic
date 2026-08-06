"""html_tables.py 纯函数测试（补充未覆盖的解析函数）。

html_tables.py 仅 19% 覆盖。现有 test_html_tables_export.py 只测
html_table_to_cell_grid 与 tables_from_result。本文件补齐：
extract_table_html / html_table_to_markdown / _cell_text / _span_value /
normalize_table_html / html_tables_to_cell_grid / _collect_table_htmls_from_text。
"""

from __future__ import annotations

from vibeocr.classic.table_model import (
    _cell_text,
    _collect_table_htmls_from_text,
    _span_value,
    extract_table_html,
    html_table_to_cell_grid,
    html_table_to_markdown,
    html_tables_to_cell_grid,
    normalize_table_html,
)


class TestExtractTableHtml:
    def test_extracts_first_table(self):
        html = "<div><table><tr><td>A</td></tr></table></div>"
        assert extract_table_html(html) == "<table><tr><td>A</td></tr></table>"

    def test_no_table_returns_input(self):
        assert extract_table_html("<p>no table</p>") == "<p>no table</p>"


class TestHtmlTableToMarkdown:
    def test_header_and_body(self):
        html = "<table><tr><th>H</th></tr><tr><td>v</td></tr></table>"
        md = html_table_to_markdown(html)
        lines = md.split("\n")
        assert lines[0] == "| H |"
        assert lines[1] == "| --- |"
        assert lines[2] == "| v |"

    def test_br_in_cell_becomes_md_br(self):
        """单元格内换行 → Markdown <br>（GFM 表格要求）"""
        html = "<table><tr><td>a<br>b</td></tr></table>"
        md = html_table_to_markdown(html)
        assert "a<br>b" in md

    def test_pipe_escaped(self):
        html = "<table><tr><td>x|y</td></tr></table>"
        assert r"x\|y" in html_table_to_markdown(html)

    def test_empty_returns_empty(self):
        assert html_table_to_markdown("<table></table>") == ""
        assert html_table_to_markdown("") == ""

    def test_uneven_columns_padded(self):
        html = "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></table>"
        md = html_table_to_markdown(html)
        body = md.split("\n")[2]
        assert body == "| 3 |  |"


class TestCellText:
    def test_strips_html_tags(self):
        assert _cell_text("<b>bold</b>") == "bold"

    def test_br_becomes_newline(self):
        assert _cell_text("a<br>b") == "a\nb"
        assert _cell_text("a<br/>b") == "a\nb"

    def test_unescapes_entities(self):
        assert _cell_text("a&amp;b") == "a&b"
        assert _cell_text("&lt;tag&gt;") == "<tag>"

    def test_collapses_inline_whitespace(self):
        assert _cell_text("a    b\t c") == "a b c"

    def test_drops_empty_lines(self):
        assert _cell_text("\n\n  \n") == ""

    def test_plain_text_unchanged(self):
        assert _cell_text("hello") == "hello"


class TestSpanValue:
    def test_no_attr_returns_one(self):
        assert _span_value("", "colspan") == 1
        assert _span_value('class="x"', "rowspan") == 1

    def test_double_quote(self):
        assert _span_value('colspan="3"', "colspan") == 3

    def test_single_quote(self):
        assert _span_value("rowspan='2'", "rowspan") == 2

    def test_unquoted(self):
        assert _span_value("colspan=4", "colspan") == 4

    def test_zero_or_invalid_returns_one(self):
        # 正则要求 [1-9]\d*，故 0 不匹配
        assert _span_value('colspan="0"', "colspan") == 1

    def test_capped_at_1000(self):
        assert _span_value('colspan="9999"', "colspan") == 1000


class TestNormalizeTableHtml:
    def test_strips_inline_style(self):
        html = '<table><tr><td style="background:red">A</td></tr></table>'
        result = normalize_table_html(html)
        assert "style" not in result
        assert "A" in result

    def test_preserves_rowspan_colspan(self):
        html = '<table><tr><td colspan="2">merged</td></tr></table>'
        result = normalize_table_html(html)
        assert 'colspan="2"' in result

    def test_pads_uneven_rows(self):
        """列数不齐时补空 td"""
        html = "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></table>"
        result = normalize_table_html(html)
        # 第二行应有 2 个 td（补了一个空）
        assert result.count("<td>") + result.count("</td>") >= 4

    def test_empty_table_returns_empty_table(self):
        assert normalize_table_html("<table></table>") == "<table></table>"

    def test_wraps_bare_html(self):
        """无 <table> 标签时原样处理"""
        result = normalize_table_html("<tr><td>x</td></tr>")
        assert "<table>" in result


class TestHtmlTablesToCellGrid:
    def test_multiple_tables(self):
        html = (
            "<table><tr><td>A</td></tr></table>"
            "<table><tr><td>B</td></tr></table>"
        )
        grids = html_tables_to_cell_grid(html)
        assert len(grids) == 2
        assert grids[0] == [["A"]]
        assert grids[1] == [["B"]]

    def test_no_tables_returns_empty(self):
        assert html_tables_to_cell_grid("<p>none</p>") == []

    def test_skips_empty_tables(self):
        html = "<table></table><table><tr><td>X</td></tr></table>"
        grids = html_tables_to_cell_grid(html)
        assert len(grids) == 1
        assert grids[0] == [["X"]]


class TestCollectTableHtmlsFromText:
    def test_collects_all_unique(self):
        text = (
            "<table><tr><td>A</td></tr></table>"
            "<p>middle</p>"
            "<table><tr><td>B</td></tr></table>"
        )
        result = _collect_table_htmls_from_text(text)
        assert len(result) == 2
        assert "A" in result[0]
        assert "B" in result[1]

    def test_deduplicates_identical(self):
        frag = "<table><tr><td>X</td></tr></table>"
        result = _collect_table_htmls_from_text(frag + frag)
        assert len(result) == 1

    def test_empty_or_none(self):
        assert _collect_table_htmls_from_text("") == []
        assert _collect_table_htmls_from_text(None) == []

    def test_no_tables(self):
        assert _collect_table_htmls_from_text("just text <b>bold</b>") == []


class TestCellGridEdgeCases:
    """html_table_to_cell_grid 的 rowspan/colspan 边界（现有测试未覆盖）。"""

    def test_rowspan_creates_placeholder(self):
        """rowspan 让下一行对应位置留空"""
        html = (
            "<table>"
            "<tr><td rowspan='2'>top</td><td>a</td></tr>"
            "<tr><td>b</td></tr>"
            "</table>"
        )
        grid = html_table_to_cell_grid(html)
        assert grid[0] == ["top", "a"]
        # 第二行第一列被 rowspan 占据 → 空，第二列是 b
        assert grid[1][0] == ""
        assert grid[1][1] == "b"

    def test_colspan_spans_multiple_cols(self):
        html = "<table><tr><td colspan='2'>wide</td></tr><tr><td>x</td><td>y</td></tr></table>"
        grid = html_table_to_cell_grid(html)
        assert grid[0][0] == "wide"
        assert len(grid[0]) == 2

    def test_strips_html_body_wrapper(self):
        html = "<html><body><table><tr><td>Z</td></tr></table></body></html>"
        assert html_table_to_cell_grid(html) == [["Z"]]
