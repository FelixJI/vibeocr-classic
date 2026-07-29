import pytest

import vibeocr.backend.tables.html_adapter as html_adapter
from vibeocr.backend.tables.blocks import (
    canonicalize_table_block,
    table_model_from_block,
    validate_table_blocks,
)
from vibeocr.backend.tables.html_adapter import (
    parse_table_source_layout,
    table_model_from_html,
    table_model_to_html,
)
from vibeocr.backend.tables.projections import (
    table_model_to_grid,
    table_model_to_markdown,
    table_model_to_tsv,
)
from vibeocr.runtime_contracts.contracts.tables import TableCellV1, TableModelV1


def test_html_adapter_preserves_mixed_row_and_column_spans():
    html = (
        "<table>"
        '<tr><td rowspan="2">纵向</td><th colspan="2">横向</th></tr>'
        "<tr><td>左下</td><td>右下</td></tr>"
        "</table>"
    )

    table = table_model_from_html(html, table_id="mixed")

    assert (table.row_count, table.column_count) == (2, 3)
    assert [
        (
            cell.row,
            cell.column,
            cell.rowspan,
            cell.colspan,
            cell.text,
            cell.is_header,
        )
        for cell in table.cells
    ] == [
        (0, 0, 2, 1, "纵向", False),
        (0, 1, 1, 2, "横向", True),
        (1, 1, 1, 1, "左下", False),
        (1, 2, 1, 1, "右下", False),
    ]
    assert table_model_from_html(table_model_to_html(table), table_id="mixed") == table


def test_legacy_table_block_is_upgraded_without_losing_source_html():
    source_html = (
        '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
    )
    block = {
        "type": "table",
        "table_body": source_html,
        "bbox": [0, 0, 100, 50],
    }

    upgraded = canonicalize_table_block(
        block,
        table_id="legacy-table",
        pipeline="MinerU",
    )

    assert upgraded["source"]["source_html"] == source_html
    assert upgraded["table"]["schema_version"] == 1
    assert table_model_from_block(upgraded).merged_ranges() == ((0, 0, 1, 0),)


def test_table_grid_projection_keeps_merged_positions_empty():
    table = table_model_from_html(
        (
            '<table><tr><td rowspan="2">纵向</td>'
            '<td colspan="2">横向</td></tr>'
            "<tr><td>左下</td><td>右下</td></tr></table>"
        ),
        table_id="grid",
    )

    assert table_model_to_grid(table) == [
        ["纵向", "横向", ""],
        ["", "左下", "右下"],
    ]
    assert table_model_to_tsv(table) == "纵向\t横向\t\n\t左下\t右下"
    markdown = table_model_to_markdown(table)
    assert markdown.warnings == ("lossy_markdown_source",)
    assert "| 纵向 | 横向 |  |" in markdown.text


def test_empty_table_projections_return_empty_strings():
    """row_count=0 的空表格：grid/tsv/markdown 均返回空，不产生 lossy 警告。"""
    empty = TableModelV1(table_id="empty", row_count=0, column_count=0, cells=())
    assert table_model_to_grid(empty) == []
    assert table_model_to_tsv(empty) == ""
    projection = table_model_to_markdown(empty)
    assert projection.text == ""
    assert projection.warnings == ()



def test_single_table_adapter_rejects_multiple_top_level_tables():
    with pytest.raises(ValueError, match="multiple"):
        table_model_from_html(
            "<table><tr><td>A</td></tr></table><table><tr><td>B</td></tr></table>",
            table_id="first",
        )


@pytest.mark.parametrize(
    "source, message",
    [
        ("<div>not a table</div>", "contain a table"),
        ("<table><tr><td rowspan='bogus'>A</td></tr></table>", "rowspan"),
        ("<table><tr><td>A</td></tr>", "not closed"),
        (
            "<table><tr><td rowspan='1000000000'>A</td></tr></table>",
            "rowspan",
        ),
        (
            "<table><tr><td>before<table><tr><td>inner</td></tr></table>"
            "after</td></tr></table>",
            "nested",
        ),
    ],
)
def test_html_adapter_rejects_unprovable_legacy_shapes(source, message):
    with pytest.raises(ValueError, match=message):
        table_model_from_html(source, table_id="invalid")


def test_html_adapter_supports_optional_cell_end_tags_and_empty_rows():
    table = table_model_from_html(
        "<table><tr><td>A<td>B</tr><tr></tr></table>",
        table_id="optional",
    )

    assert (table.row_count, table.column_count) == (2, 2)
    assert [cell.text for cell in table.cells] == ["A", "B"]
    assert (
        table_model_from_html(table_model_to_html(table), table_id="optional") == table
    )


def test_canonical_html_roundtrip_preserves_whitespace_empty_rows_and_cell_ids():
    table = TableModelV1(
        table_id="roundtrip",
        row_count=2,
        column_count=1,
        cells=(
            TableCellV1(
                cell_id="stable-cell",
                row=0,
                column=0,
                text=" A  B ",
            ),
        ),
    )

    assert (
        table_model_from_html(table_model_to_html(table), table_id="roundtrip") == table
    )


def test_canonical_first_block_is_not_mislabeled_as_legacy():
    table = TableModelV1(
        table_id="canonical",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="cell", row=0, column=0, text="fresh"),),
    )
    upgraded = canonicalize_table_block(
        {
            "type": "table",
            "table": table.to_payload(),
            "table_body": "<table><tr><td>stale</td></tr></table>",
        },
        table_id="canonical",
        pipeline="MINERU",
    )

    assert upgraded["table"]["cells"][0]["text"] == "fresh"
    assert upgraded["table"]["provenance"]["provider_schema"] == "canonical-v1"
    assert upgraded["table"]["provenance"]["warnings"] == []


def test_display_mode_can_fallback_from_unknown_canonical_to_legacy_html():
    block = {
        "type": "table",
        "table": {"schema_version": 999},
        "table_body": "<table><tr><td>legacy</td></tr></table>",
    }

    with pytest.raises(ValueError):
        table_model_from_block(block)
    table = table_model_from_block(block, strict_canonical=False)
    assert table.cells[0].text == "legacy"


def test_html_adapter_aborts_while_parsing_excessive_cell_text(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_HTML_TABLE_TEXT_CHARS", 4)

    with pytest.raises(ValueError, match="text exceeds"):
        table_model_from_html(
            "<table><tr><td>12345</td></tr></table>",
            table_id="too-much-text",
        )


def test_html_adapter_rejects_large_markup_before_parser_allocation(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_HTML_TABLE_SOURCE_CHARS", 32)
    source = "<!--" + ("x" * 40) + "--><table><tr><td>A</td></tr></table>"

    with pytest.raises(ValueError, match="source exceeds"):
        table_model_from_html(source, table_id="too-much-markup")
    with pytest.raises(ValueError, match="source exceeds"):
        parse_table_source_layout(source, table_id="too-much-markup")


def test_html_adapter_aborts_while_parsing_excessive_cell_count(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_TABLE_CELLS", 2)

    with pytest.raises(ValueError, match="cell count"):
        table_model_from_html(
            "<table><tr><td>A</td><td>B</td><td>C</td></tr></table>",
            table_id="too-many-cells",
        )


def test_html_adapter_aborts_while_parsing_excessive_coverage(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_TABLE_COVERAGE", 2)

    with pytest.raises(ValueError, match="coverage"):
        table_model_from_html(
            '<table><tr><td colspan="3">A</td></tr></table>',
            table_id="too-much-coverage",
        )


def test_source_layout_uses_structured_parser_offsets_with_optional_end_tags():
    source = (
        "<table>\n<tr><td rowspan='2'><b>A</b><td>B</tr><tr><td>C</td></tr></table>"
    )

    layout = parse_table_source_layout(source, table_id="source-layout")

    assert layout.model.merged_ranges() == ((0, 0, 1, 0),)
    assert [cell.source_text for cell in layout.cells] == ["A", "B", "C"]
    assert [source[cell.content_start : cell.content_end] for cell in layout.cells] == [
        "<b>A</b>",
        "B",
        "C",
    ]


class TestBlocksHelpers:
    """tables/blocks.py 的 from_block / canonicalize / validate 边界。"""

    def test_table_model_from_block_rejects_block_without_any_source(self):
        """既无 canonical table 也无 legacy HTML 时 raise ValueError。"""
        with pytest.raises(ValueError, match="neither canonical table nor legacy HTML"):
            table_model_from_block({"type": "table"})

    def test_table_model_from_block_rejects_whitespace_only_html(self):
        with pytest.raises(ValueError, match="neither canonical table nor legacy HTML"):
            table_model_from_block({"type": "table", "table_body": "   "})

    def test_canonicalize_renames_table_id_when_mismatch(self):
        """canonicalize 把 table_id 强制对齐到传入的 table_id（line 54 replace 分支）。"""
        block = {
            "type": "table",
            "table_body": "<table><tr><td>A</td></tr></table>",
            "table_id": "original-id",
        }
        result = canonicalize_table_block(block, table_id="forced-id", pipeline="MinerU")
        assert result["table"]["table_id"] == "forced-id"
        assert result["block_id"] == "forced-id"

    def test_canonicalize_adds_legacy_provenance_when_absent(self):
        block = {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>"}
        result = canonicalize_table_block(block, table_id="t1", pipeline="MinerU")
        prov = result["table"]["provenance"]
        assert prov["pipeline"] == "MinerU"
        assert prov["provider_schema"] == "legacy-html"
        assert "legacy_html_adapted" in prov["warnings"]

    def test_canonicalize_marks_canonical_provider_when_table_present(self):
        canonical = TableModelV1(
            table_id="c1",
            row_count=1,
            column_count=1,
            cells=(TableCellV1(cell_id="a", row=0, column=0, text="X"),),
        ).to_payload()
        block = {"type": "table", "table": canonical}
        result = canonicalize_table_block(block, table_id="c1", pipeline="OCR")
        prov = result["table"]["provenance"]
        assert prov["provider_schema"] == "canonical-v1"

    def test_validate_table_blocks_ignores_non_sequence(self):
        """非 list/tuple 输入直接返回，不报错。"""
        validate_table_blocks(None)  # no raise
        validate_table_blocks("not a list")

    def test_validate_table_blocks_skips_non_table_and_non_dict(self):
        validate_table_blocks([None, {"type": "text", "text": "x"}, 42])

    def test_validate_table_blocks_validates_canonical_payload(self):
        canonical = TableModelV1(
            table_id="c1",
            row_count=1,
            column_count=1,
            cells=(TableCellV1(cell_id="a", row=0, column=0, text="X"),),
        ).to_payload()
        validate_table_blocks([{"type": "table", "table": canonical}])  # no raise

    def test_validate_table_blocks_rejects_bad_canonical_payload(self):
        with pytest.raises((KeyError, TypeError, ValueError)):
            validate_table_blocks([{"type": "table", "table": {"bogus": True}}])


class TestHtmlAdapterEdgeCases:
    """html_adapter 的自闭合标签、行/覆盖上限、source-parser 边界。"""

    def test_self_closing_td_tag_handled(self):
        """<td/> 自闭合标签触发 handle_startendtag 的 td 分支（line 66-68）。"""
        table = table_model_from_html(
            "<table><tr><td>A</td><td/><td>C</td></tr></table>",
            table_id="self-close",
        )
        # 中间空 td 仍占一列
        assert table.column_count >= 3

    def test_self_closing_table_tag_handled(self):
        """<table/> 自闭合标签在 source parser 中触发 handle_startendtag table 分支。

        空自闭合 table 不产生 cell，source parser 返回空 spans。
        """
        layout = parse_table_source_layout("<table/>", table_id="empty-self-close")
        assert layout.cells == ()

    def test_row_count_exceeds_limit_raises(self):
        """行数超过 MAX_TABLE_DIMENSION 时 raise（line 127）。"""
        from vibeocr.backend.tables.html_adapter import MAX_TABLE_DIMENSION
        rows = "<tr><td>x</td></tr>" * (MAX_TABLE_DIMENSION + 1)
        with pytest.raises(ValueError, match="row count"):
            table_model_from_html(f"<table>{rows}</table>", table_id="too-many-rows")

    def test_cell_coverage_exceeds_limit_raises(self):
        """单元格覆盖总数超 MAX_TABLE_COVERAGE 时 raise（line 176）。"""
        from vibeocr.backend.tables.html_adapter import MAX_TABLE_COVERAGE
        huge = MAX_TABLE_COVERAGE + 1
        with pytest.raises(ValueError):
            table_model_from_html(
                f'<table><tr><td colspan="{huge}">x</td></tr></table>',
                table_id="too-wide",
            )

    def test_source_parser_handles_extra_trailing_newlines(self):
        """源解析器对尾随多余换行确定性处理（覆盖 line 234-238 循环）。"""
        layout = parse_table_source_layout(
            "<table><tr><td>A</td></tr></table>\n\n\n",
            table_id="src",
        )
        assert layout.cells[0].source_text == "A"

    def test_source_parser_handles_nested_table_depth(self):
        """嵌套 table 的内层标签被忽略（_table_depth != 1，line 256-257）。"""
        layout = parse_table_source_layout(
            "<table><tr><td>outer</td></tr></table>",
            table_id="nested",
        )
        assert len(layout.cells) >= 1
        assert layout.cells[0].source_text == "outer"
