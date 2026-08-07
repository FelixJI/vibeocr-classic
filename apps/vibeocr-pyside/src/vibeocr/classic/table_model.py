"""Classic-owned table presentation over the Protocol canonical model.

``TableModelV1`` and ``TableCellV1`` remain the only semantic wire source.
This module owns Classic's legacy HTML adaptation, deterministic display and
clipboard projections, and canonical payload validation.  Backend inference
canonicalization intentionally stays outside this module.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from vibeocr.runtime_contracts.contracts.tables import (
    MAX_TABLE_CELLS,
    MAX_TABLE_COVERAGE,
    MAX_TABLE_DIMENSION,
    TableCellV1,
    TableModelV1,
)

MAX_HTML_TABLE_TEXT_CHARS = 10_000_000
MAX_HTML_TABLE_SOURCE_CHARS = 20_000_000


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, dict[str, str], str]]] = []
        self._table_depth = 0
        self.found_table = False
        self.completed_table = False
        self.nested_table = False
        self.multiple_tables = False
        self._row: list[tuple[str, dict[str, str], str]] | None = None
        self._cell_tag: str | None = None
        self._cell_attrs: dict[str, str] = {}
        self._cell_text: list[str] = []
        self._cell_count = 0
        self._coverage = 0
        self._text_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if self.completed_table:
            if lowered == "table":
                self.multiple_tables = True
            return
        if lowered == "table":
            if not self.found_table:
                self.found_table = True
            elif self._table_depth >= 1:
                self.nested_table = True
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if lowered == "tr":
            self._close_cell()
            self._close_row()
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._close_cell()
            self._cell_tag = lowered
            self._cell_attrs = {name.lower(): value or "" for name, value in attrs}
            self._cell_text = []
        elif lowered == "br" and self._cell_tag is not None:
            self._append_cell_text("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in {"td", "th"}:
            self._close_cell()
        elif tag.lower() == "table":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self.completed_table:
            return
        if lowered == "table":
            if self._table_depth == 1:
                self._close_cell()
                self._close_row()
                self.completed_table = True
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if lowered in {"td", "th"} and self._cell_tag is not None:
            self._close_cell()
        elif lowered == "tr" and self._row is not None:
            self._close_cell()
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._table_depth == 1 and self._cell_tag is not None:
            self._append_cell_text(data)

    def _append_cell_text(self, value: str) -> None:
        self._text_chars += len(value)
        if self._text_chars > MAX_HTML_TABLE_TEXT_CHARS:
            raise ValueError("HTML table text exceeds supported limit")
        self._cell_text.append(value)

    def _close_cell(self) -> None:
        if self._cell_tag is None or self._row is None:
            return
        self._cell_count += 1
        if self._cell_count > MAX_TABLE_CELLS:
            raise ValueError("HTML table cell count exceeds supported limit")
        self._coverage += _span(self._cell_attrs, "rowspan") * _span(
            self._cell_attrs, "colspan"
        )
        if self._coverage > MAX_TABLE_COVERAGE:
            raise ValueError("HTML table cell coverage exceeds supported limit")
        self._row.append((self._cell_tag, self._cell_attrs, "".join(self._cell_text)))
        self._cell_tag = None
        self._cell_attrs = {}
        self._cell_text = []

    def _close_row(self) -> None:
        if self._row is None:
            return
        if len(self.rows) >= MAX_TABLE_DIMENSION:
            raise ValueError("HTML table row count exceeds supported limit")
        self.rows.append(self._row)
        self._row = None


def _span(attrs: dict[str, str], name: str) -> int:
    raw = attrs.get(name)
    if raw is None:
        return 1
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"invalid HTML table {name}: {raw!r}") from error
    if value < 1 or value > MAX_TABLE_DIMENSION:
        raise ValueError(f"invalid HTML table {name}: {raw!r}")
    return value


def table_model_from_html(html_text: str, *, table_id: str) -> TableModelV1:
    """Parse the first HTML table into canonical logical coordinates."""
    if len(html_text) > MAX_HTML_TABLE_SOURCE_CHARS:
        raise ValueError("HTML table source exceeds supported limit")
    parser = _TableParser()
    parser.feed(html_text)
    if not parser.found_table:
        raise ValueError("HTML does not contain a table")
    if parser.nested_table:
        raise ValueError("nested HTML tables are not supported")
    if parser.multiple_tables:
        raise ValueError("multiple top-level HTML tables require separate blocks")
    if not parser.completed_table:
        raise ValueError("HTML table is not closed")

    occupied: set[tuple[int, int]] = set()
    cells: list[TableCellV1] = []
    row_count = 0
    column_count = 0
    coverage = 0
    for row_index, raw_row in enumerate(parser.rows):
        column = 0
        row_count = max(row_count, row_index + 1)
        for tag, attrs, text in raw_row:
            while (row_index, column) in occupied:
                column += 1
            rowspan = _span(attrs, "rowspan")
            colspan = _span(attrs, "colspan")
            coverage += rowspan * colspan
            if coverage > MAX_TABLE_COVERAGE:
                raise ValueError("HTML table cell coverage exceeds supported limit")
            cells.append(
                TableCellV1(
                    cell_id=attrs.get("data-cell-id") or f"r{row_index}c{column}",
                    row=row_index,
                    column=column,
                    rowspan=rowspan,
                    colspan=colspan,
                    text=text,
                    is_header=tag == "th",
                )
            )
            for covered_row in range(row_index, row_index + rowspan):
                for covered_column in range(column, column + colspan):
                    occupied.add((covered_row, covered_column))
            row_count = max(row_count, row_index + rowspan)
            column_count = max(column_count, column + colspan)
            column += colspan
    return TableModelV1(
        table_id=table_id,
        row_count=row_count,
        column_count=column_count,
        cells=tuple(cells),
    )


@dataclass(frozen=True, slots=True)
class TableCellSourceSpan:
    content_start: int
    content_end: int
    source_text: str


@dataclass(frozen=True, slots=True)
class TableSourceLayout:
    model: TableModelV1
    cells: tuple[TableCellSourceSpan, ...]


class _TableSourceSpanParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._offset_line = 1
        self._line_start = 0
        self._table_depth = 0
        self._completed_table = False
        self._cell_start: int | None = None
        self.spans: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        while self._offset_line < line:
            newline = self._source.find("\n", self._line_start)
            if newline < 0:
                raise ValueError("HTML parser position exceeds source text")
            self._line_start = newline + 1
            self._offset_line += 1
        return self._line_start + column

    def _close_cell(self, end: int) -> None:
        if self._cell_start is None:
            return
        self.spans.append((self._cell_start, max(self._cell_start, end)))
        self._cell_start = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if self._completed_table:
            return
        position = self._offset()
        if lowered == "table":
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if lowered in {"tr", "td", "th"}:
            self._close_cell(position)
        if lowered in {"td", "th"}:
            raw_tag = self.get_starttag_text() or ""
            self._cell_start = position + len(raw_tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        position = self._offset()
        self.handle_starttag(tag, attrs)
        if tag.lower() in {"td", "th"}:
            raw_tag = self.get_starttag_text() or ""
            self._close_cell(position + len(raw_tag))
        elif tag.lower() == "table":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self._completed_table:
            return
        position = self._offset()
        if lowered == "table":
            if self._table_depth == 1:
                self._close_cell(position)
                self._completed_table = True
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth == 1 and lowered in {"td", "th", "tr"}:
            self._close_cell(position)


def parse_table_source_layout(html_text: str, *, table_id: str) -> TableSourceLayout:
    """Pair canonical cells with safe original inner-HTML source spans."""
    if len(html_text) > MAX_HTML_TABLE_SOURCE_CHARS:
        raise ValueError("HTML table source exceeds supported limit")
    model = table_model_from_html(html_text, table_id=table_id)
    parser = _TableSourceSpanParser(html_text)
    parser.feed(html_text)
    if len(parser.spans) != len(model.cells):
        raise ValueError(
            "HTML table source span count does not match canonical cell count"
        )
    spans = tuple(
        TableCellSourceSpan(start, end, cell.text)
        for (start, end), cell in zip(parser.spans, model.cells, strict=True)
    )
    return TableSourceLayout(model=model, cells=spans)


def table_model_to_html(table: TableModelV1) -> str:
    """Render canonical anchor cells as compact HTML with stable IDs."""
    cells_by_row: dict[int, list[TableCellV1]] = {}
    for cell in table.cells:
        cells_by_row.setdefault(cell.row, []).append(cell)
    rendered_rows: list[str] = []
    for row_index in range(table.row_count):
        rendered_cells: list[str] = []
        for cell in sorted(
            cells_by_row.get(row_index, ()), key=lambda item: item.column
        ):
            tag = "th" if cell.is_header else "td"
            attrs = f' data-cell-id="{html.escape(cell.cell_id, quote=True)}"'
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
            text = html.escape(cell.text).replace("\n", "<br>")
            rendered_cells.append(f"<{tag}{attrs}>{text}</{tag}>")
        rendered_rows.append(f"<tr>{''.join(rendered_cells)}</tr>")
    table_id = html.escape(table.table_id, quote=True)
    return f'<table data-table-id="{table_id}">{"".join(rendered_rows)}</table>'


def table_model_from_block(
    block: dict[str, Any],
    *,
    fallback_table_id: str = "table",
    strict_canonical: bool = True,
) -> TableModelV1:
    """Read canonical payload first, falling back to legacy HTML when allowed."""
    payload = block.get("table")
    if isinstance(payload, dict):
        try:
            return TableModelV1.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            if strict_canonical:
                raise
    html_text = (
        block.get("table_body")
        or block.get("html")
        or (block.get("source") or {}).get("source_html")
    )
    if not isinstance(html_text, str) or not html_text.strip():
        raise ValueError("table block has neither canonical table nor legacy HTML")
    table_id = str(block.get("table_id") or block.get("block_id") or fallback_table_id)
    return table_model_from_html(html_text, table_id=table_id)


def validate_table_blocks(content_list: Any) -> None:
    """Validate canonical table payloads at detached export snapshot time."""
    if not isinstance(content_list, (list, tuple)):
        return
    for block in content_list:
        if not isinstance(block, dict) or block.get("type") != "table":
            continue
        payload = block.get("table")
        if isinstance(payload, dict):
            TableModelV1.from_payload(payload)


def table_model_to_grid(table: TableModelV1) -> list[list[str]]:
    grid = [["" for _ in range(table.column_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        grid[cell.row][cell.column] = cell.text
    return grid


def table_model_to_plain_text(table: TableModelV1) -> str:
    cells_by_row: dict[int, list[tuple[int, str]]] = {}
    for cell in table.cells:
        cells_by_row.setdefault(cell.row, []).append((cell.column, cell.text))
    return "\n".join(
        "\t".join(
            text
            for _column, text in sorted(
                cells_by_row.get(row_index, ()), key=lambda item: item[0]
            )
        )
        for row_index in range(table.row_count)
    )


def table_model_to_tsv(table: TableModelV1) -> str:
    return "\n".join("\t".join(row) for row in table_model_to_grid(table))


@dataclass(frozen=True, slots=True)
class MarkdownTableProjection:
    text: str
    warnings: tuple[str, ...] = ()


def table_model_to_markdown(table: TableModelV1) -> MarkdownTableProjection:
    grid = table_model_to_grid(table)
    if not grid:
        return MarkdownTableProjection(text="")

    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")

    rows = ["| " + " | ".join(escape(value) for value in row) + " |" for row in grid]
    separator = "| " + " | ".join("---" for _ in range(table.column_count)) + " |"
    rows.insert(1, separator)
    warnings = ("lossy_markdown_source",) if table.merged_ranges() else ()
    return MarkdownTableProjection(text="\n".join(rows), warnings=warnings)


_RE_TABLE = re.compile(r"(<table\b.*?</table>)", re.DOTALL | re.IGNORECASE)
_RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_RE_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_RE_CELL = re.compile(r"<(td|th)([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)


def extract_table_html(html_str: str) -> str:
    match = _RE_TABLE.search(html_str)
    return match.group(1) if match else html_str


def html_table_to_markdown(html_text: str) -> str:
    rows: list[list[str]] = []
    for tr_match in _RE_TR.finditer(html_text):
        cells: list[str] = []
        for cell_match in _RE_TD.finditer(tr_match.group(1)):
            text = _cell_text(cell_match.group(1))
            cells.append(text.replace("\n", "<br>").replace("|", "\\|"))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    max_columns = max(len(row) for row in rows)
    for row in rows:
        row.extend("" for _ in range(max_columns - len(row)))
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(max_columns)) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(part for part in (header, separator, body) if part)


def _cell_text(inner: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", inner, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _span_value(attrs: str, name: str) -> int:
    match = re.search(rf"\b{name}\s*=\s*['\"]?([1-9]\d*)", attrs, flags=re.IGNORECASE)
    return min(int(match.group(1)), 1000) if match else 1


def _layout_rows(
    rows: list[list[tuple[str, str, int, int]]], target_width: int | None = None
) -> tuple[list[str], int]:
    active: list[int] = []
    rendered: list[str] = []
    max_width = 0
    for row in rows:
        cells_html: list[str] = []
        column = 0
        for tag, text, rowspan, colspan in row:
            while column < len(active) and active[column] > 0:
                column += 1
            needed = column + colspan
            if needed > len(active):
                active.extend([0] * (needed - len(active)))
            for occupied_column in range(column, needed):
                active[occupied_column] = max(active[occupied_column], rowspan)
            attrs = ""
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            safe = html.escape(text).replace("\n", "<br>")
            cells_html.append(f"<{tag}{attrs}>{safe}</{tag}>")
            column = needed
        width = max(len(active), column)
        max_width = max(max_width, width)
        if target_width is not None:
            if len(active) < target_width:
                active.extend([0] * (target_width - len(active)))
            for padding_column in range(column, target_width):
                if active[padding_column] == 0:
                    cells_html.append("<td></td>")
        rendered.append(f"<tr>{''.join(cells_html)}</tr>")
        active = [max(0, remaining - 1) for remaining in active]
    return rendered, max_width


def normalize_table_html(html_text: str) -> str:
    table_match = _RE_TABLE.search(html_text)
    table_html = table_match.group(1) if table_match else html_text
    rows: list[list[tuple[str, str, int, int]]] = []
    for tr_match in _RE_TR.finditer(table_html):
        row: list[tuple[str, str, int, int]] = []
        for cell_match in _RE_CELL.finditer(tr_match.group(1)):
            attrs = cell_match.group(2)
            row.append(
                (
                    cell_match.group(1).lower(),
                    _cell_text(cell_match.group(3)),
                    _span_value(attrs, "rowspan"),
                    _span_value(attrs, "colspan"),
                )
            )
        if row:
            rows.append(row)
    if not rows:
        return "<table></table>"
    _, max_columns = _layout_rows(rows)
    rows_html, _ = _layout_rows(rows, max_columns)
    return f"<table>{''.join(rows_html)}</table>"


def html_table_to_cell_grid(html_text: str) -> list[list[str]]:
    table_html = extract_table_html(html_text)
    rows: list[list[str]] = []
    active: list[int] = []
    for tr_match in _RE_TR.finditer(table_html):
        cells: list[str] = []
        column = 0
        for cell_match in _RE_CELL.finditer(tr_match.group(1)):
            while column < len(active) and active[column] > 0:
                while len(cells) <= column:
                    cells.append("")
                column += 1
            rowspan = _span_value(cell_match.group(2), "rowspan")
            colspan = _span_value(cell_match.group(2), "colspan")
            needed = column + colspan
            if len(active) < needed:
                active.extend([0] * (needed - len(active)))
            while len(cells) < needed:
                cells.append("")
            cells[column] = _cell_text(cell_match.group(3))
            for occupied_column in range(column, needed):
                active[occupied_column] = max(active[occupied_column], rowspan)
            column = needed
        if cells or any(active):
            if len(cells) < len(active):
                cells.extend([""] * (len(active) - len(cells)))
            rows.append(cells)
        active = [max(0, remaining - 1) for remaining in active]
    width = max((len(row) for row in rows), default=0)
    for row in rows:
        row.extend([""] * (width - len(row)))
    return rows


def html_tables_to_cell_grid(html_text: str) -> list[list[list[str]]]:
    grids: list[list[list[str]]] = []
    for table_match in _RE_TABLE.finditer(html_text):
        grid = html_table_to_cell_grid(table_match.group(1))
        if grid:
            grids.append(grid)
    return grids


def _collect_table_htmls_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    tables: list[str] = []
    for match in _RE_TABLE.finditer(text or ""):
        fragment = match.group(1)
        if fragment not in seen:
            seen.add(fragment)
            tables.append(fragment)
    return tables


def tables_from_result(result: object) -> list[str]:
    """Collect one canonical-first ordered table list for copy/export."""

    def value(name: str) -> object:
        return (
            result.get(name)
            if isinstance(result, dict)
            else getattr(result, name, None)
        )

    seen: set[str] = set()
    tables: list[str] = []
    content_list = value("content_list") or []
    if isinstance(content_list, (list, tuple)):
        canonical_present = any(
            isinstance(block, dict)
            and str(block.get("type", "")).lower() == "table"
            and isinstance(block.get("table"), dict)
            for block in content_list
        )
        if canonical_present:
            for index, block in enumerate(content_list):
                if (
                    not isinstance(block, dict)
                    or str(block.get("type", "")).lower() != "table"
                ):
                    continue
                table_html = table_model_to_html(
                    table_model_from_block(
                        block,
                        fallback_table_id=f"table-{index}",
                        strict_canonical=False,
                    )
                )
                if table_html not in seen:
                    seen.add(table_html)
                    tables.append(table_html)
            return tables
        for block in content_list:
            if (
                not isinstance(block, dict)
                or str(block.get("type", "")).lower() != "table"
            ):
                continue
            table_html = block.get("table_body") or block.get("html") or ""
            if isinstance(table_html, str) and table_html and table_html not in seen:
                seen.add(table_html)
                tables.append(table_html)

    text_blocks = value("text_blocks") or []
    if isinstance(text_blocks, list):
        for block in text_blocks:
            if isinstance(block, dict):
                label = str(block.get("label", "")).lower()
                text = block.get("text", "")
            else:
                label = str(getattr(block, "label", "")).lower()
                text = getattr(block, "text", "")
            if label != "table" or not isinstance(text, str) or not text:
                continue
            for fragment in _collect_table_htmls_from_text(text):
                if fragment not in seen:
                    seen.add(fragment)
                    tables.append(fragment)
    for field in ("html_text", "markdown_text", "raw_text"):
        for fragment in _collect_table_htmls_from_text(str(value(field) or "")):
            if fragment not in seen:
                seen.add(fragment)
                tables.append(fragment)
    return tables


_extract_table_html = extract_table_html
_html_table_to_markdown = html_table_to_markdown


__all__ = [
    "MAX_HTML_TABLE_SOURCE_CHARS",
    "MAX_HTML_TABLE_TEXT_CHARS",
    "MAX_TABLE_CELLS",
    "MAX_TABLE_COVERAGE",
    "MAX_TABLE_DIMENSION",
    "MarkdownTableProjection",
    "TableCellSourceSpan",
    "TableSourceLayout",
    "extract_table_html",
    "html_table_to_cell_grid",
    "html_table_to_markdown",
    "html_tables_to_cell_grid",
    "normalize_table_html",
    "parse_table_source_layout",
    "table_model_from_block",
    "table_model_from_html",
    "table_model_to_grid",
    "table_model_to_html",
    "table_model_to_markdown",
    "table_model_to_plain_text",
    "table_model_to_tsv",
    "tables_from_result",
    "validate_table_blocks",
]
