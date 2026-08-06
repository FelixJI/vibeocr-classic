"""Classic-owned table editing and local result projection rebuilds."""

from __future__ import annotations

import html
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from vibeocr.classic.recognition_result import DISCARDED_BLOCK_TYPES
from vibeocr.classic.table_model import (
    extract_table_html,
    html_table_to_markdown,
    table_model_from_block,
    table_model_from_html,
    table_model_to_html,
    table_model_to_markdown,
    table_model_to_plain_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class TableBlockEdit:
    """Observable projection values produced by one whole-table edit."""

    content_index: int
    canonical_html: str
    plain_text: str


def update_table_cell(
    block: dict[str, Any],
    *,
    table_id: str,
    cell_id: str,
    new_text: str,
) -> dict[str, Any]:
    """Update one canonical cell and refresh compatibility projections."""
    table = table_model_from_block(block, fallback_table_id=table_id)
    if table.table_id != table_id:
        raise KeyError(f"table_id {table_id!r} does not match {table.table_id!r}")
    found = False
    updated_cells = []
    for cell in table.cells:
        if cell.cell_id == cell_id:
            updated_cells.append(replace(cell, text=new_text))
            found = True
        else:
            updated_cells.append(cell)
    if not found:
        raise KeyError(f"unknown cell_id {cell_id!r} in table {table_id!r}")
    updated_table = replace(table, cells=tuple(updated_cells))
    updated = dict(block)
    updated["type"] = "table"
    updated["table"] = updated_table.to_payload()
    updated["table_body"] = table_model_to_html(updated_table)
    updated["text"] = table_model_to_plain_text(updated_table)
    return updated


def update_result_table_cell(
    result: Any,
    *,
    table_id: str,
    cell_id: str,
    new_text: str,
) -> int:
    """Edit by stable IDs and synchronously rebuild all local projections."""
    content_list = getattr(result, "content_list", None)
    if not isinstance(content_list, list):
        raise KeyError(f"unknown table_id {table_id!r}")
    content_index = -1
    updated_table = None
    for index, block in enumerate(content_list):
        if not isinstance(block, dict) or block.get("type") != "table":
            continue
        payload = block.get("table")
        candidate_id = (
            payload.get("table_id")
            if isinstance(payload, dict)
            else block.get("table_id") or block.get("block_id")
        )
        if candidate_id != table_id:
            continue
        updated_block = update_table_cell(
            block,
            table_id=table_id,
            cell_id=cell_id,
            new_text=new_text,
        )
        block.clear()
        block.update(updated_block)
        updated_table = table_model_from_block(block)
        markdown = table_model_to_markdown(updated_table)
        block["projection_warnings"] = list(markdown.warnings)
        content_index = index
        break
    if updated_table is None:
        raise KeyError(f"unknown table_id {table_id!r}")

    block_id = content_list[content_index].get("block_id")
    matched_text_index = None
    text_blocks = getattr(result, "text_blocks", None)
    if isinstance(text_blocks, list):
        for index, text_block in enumerate(text_blocks):
            if (
                block_id is not None
                and getattr(text_block, "content_id", None) == block_id
            ):
                matched_text_index = index
                break
        if matched_text_index is None:
            for index, text_block in enumerate(text_blocks):
                if getattr(text_block, "content_index", None) == content_index:
                    matched_text_index = index
                    break
        if matched_text_index is not None:
            text_block = text_blocks[matched_text_index]
            text_block.text = table_model_to_plain_text(updated_table)
            text_block.is_manually_edited = True
            scores = getattr(result, "text_with_scores", None)
            if isinstance(scores, list) and matched_text_index < len(scores):
                score = scores[matched_text_index][1]
                scores[matched_text_index] = (text_block.text, score)
    rebuild_result_projections(result)
    return content_index


def replace_result_table_from_html(
    result: Any,
    *,
    content_index: int,
    new_html: str,
    preferred_text_index: int | None = None,
    allow_linear_scan: bool = True,
    rebuild_projections: bool = True,
) -> TableBlockEdit:
    """Replace one UI-edited table while preserving IDs and provenance."""
    content_list = getattr(result, "content_list", None)
    if not isinstance(content_list, list) or not 0 <= content_index < len(content_list):
        raise IndexError("table content index is outside the result")
    block = content_list[content_index]
    if not isinstance(block, dict) or block.get("type") != "table":
        raise ValueError("content block is not a table")
    old_table = table_model_from_block(
        block,
        fallback_table_id=str(block.get("block_id") or f"table-{content_index}"),
        strict_canonical=False,
    )
    edited_table = table_model_from_html(new_html, table_id=old_table.table_id)
    if old_table.provenance is not None:
        edited_table = replace(edited_table, provenance=old_table.provenance)
    canonical_html = table_model_to_html(edited_table)
    plain_text = table_model_to_plain_text(edited_table)
    block["table"] = edited_table.to_payload()
    block["table_body"] = canonical_html
    block["text"] = plain_text
    block["projection_warnings"] = list(table_model_to_markdown(edited_table).warnings)

    text_blocks = getattr(result, "text_blocks", None)
    matched_index = None
    if isinstance(text_blocks, list):
        if preferred_text_index is not None and 0 <= preferred_text_index < len(
            text_blocks
        ):
            matched_index = preferred_text_index
        elif (
            content_index < len(text_blocks)
            and getattr(text_blocks[content_index], "content_index", None)
            == content_index
        ):
            matched_index = content_index
        elif allow_linear_scan:
            matched_index = next(
                (
                    index
                    for index, text_block in enumerate(text_blocks)
                    if getattr(text_block, "content_index", None) == content_index
                ),
                None,
            )
        if matched_index is not None:
            text_block = text_blocks[matched_index]
            text_block.text = plain_text
            text_block.is_manually_edited = True
            scores = getattr(result, "text_with_scores", None)
            if isinstance(scores, list) and matched_index < len(scores):
                score = scores[matched_index][1]
                scores[matched_index] = (plain_text, score)
    if rebuild_projections:
        rebuild_result_projections(result)
    return TableBlockEdit(content_index, canonical_html, plain_text)


def build_result_projections(
    result: Any,
    *,
    is_cancelled: Callable[[], bool] | None = None,
    include_raw: bool = True,
    include_markdown: bool = True,
) -> tuple[str, str, str] | None:
    """Build selected raw/Markdown/HTML projections without mutation."""

    def cancelled(index: int, interval: int) -> bool:
        return is_cancelled is not None and index % interval == 0 and is_cancelled()

    text_blocks = getattr(result, "text_blocks", None)
    markdown_parts: list[str] = []
    html_parts: list[str] = []
    content_list = getattr(result, "content_list", None)
    if not isinstance(content_list, list):
        raw_parts: list[str] = []
        if include_raw and isinstance(text_blocks, list):
            for index, text_block in enumerate(text_blocks):
                if cancelled(index, 256):
                    return None
                if (
                    str(getattr(text_block, "label", "")).lower()
                    not in DISCARDED_BLOCK_TYPES
                    and text_block.text
                ):
                    raw_parts.append(text_block.text)
        return "\n".join(raw_parts), "", ""

    if include_raw:
        raw_parts = _raw_parts_from_content(
            content_list,
            text_blocks if isinstance(text_blocks, list) else [],
            cancelled=cancelled,
        )
        if raw_parts is None:
            return None
        raw_text = "\n".join(raw_parts)
    else:
        raw_text = ""
    for index, block in enumerate(content_list):
        if cancelled(index, 128):
            return None
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "text")).lower()
        if block_type in DISCARDED_BLOCK_TYPES:
            continue
        if block_type == "table":
            table = table_model_from_block(block)
            if include_markdown:
                markdown_projection = table_model_to_markdown(table)
                markdown_parts.extend(_text_items(block.get("table_caption")))
                if markdown_projection.text:
                    markdown_parts.append(markdown_projection.text)
                markdown_parts.extend(_text_items(block.get("table_footnote")))
            html_parts.extend(
                f'<p class="table-caption">{_escaped_text(caption)}</p>'
                for caption in _text_items(block.get("table_caption"))
            )
            html_parts.append(table_model_to_html(table))
            html_parts.extend(
                f'<p class="table-footnote">{_escaped_text(footnote)}</p>'
                for footnote in _text_items(block.get("table_footnote"))
            )
            continue
        if block_type in {"image", "figure", "chart", "seal"}:
            captions = _text_items(
                block.get("image_caption") or block.get("chart_caption")
            )
            source = (
                block.get("img_path") or block.get("image_path") or block.get("src")
            )
            if include_markdown:
                caption_text = " ".join(captions)
                if source:
                    markdown_parts.append(f"![{caption_text}]({source})")
                else:
                    markdown_parts.extend(captions)
            html_parts.extend(
                f'<p class="image-caption">{_escaped_text(caption)}</p>'
                for caption in captions
            )
            if source:
                html_parts.append(
                    f'<img src="{html.escape(str(source), quote=True)}" alt="">'
                )
            continue
        if block_type == "list":
            items = _text_items(block.get("list_items"))
            if items:
                if include_markdown:
                    markdown_parts.append("\n".join(f"- {item}" for item in items))
                html_parts.append(
                    "<ul>"
                    + "".join(f"<li>{_escaped_text(item)}</li>" for item in items)
                    + "</ul>"
                )
            continue

        text = str(
            block.get("text") or block.get("code_body") or block.get("content") or ""
        )
        if not text:
            continue
        escaped = _escaped_text(text)
        if block_type == "title":
            level = _heading_level(block.get("level", block.get("text_level")))
            if include_markdown:
                markdown_parts.append(f"{'#' * level} {text}")
            html_parts.append(f"<h{level}>{escaped}</h{level}>")
        elif block_type == "code":
            if include_markdown:
                markdown_parts.append(f"```\n{text}\n```")
            html_parts.append(f"<pre><code>{escaped}</code></pre>")
        elif block_type in {
            "equation",
            "formula",
            "interline_equation",
            "inline_equation",
        }:
            if include_markdown:
                markdown_parts.append(f"$${text}$$")
            html_parts.append(f'<div class="equation">{escaped}</div>')
        else:
            if include_markdown:
                markdown_parts.append(text)
            html_parts.append(f"<p>{escaped}</p>")
    return raw_text, "\n\n".join(markdown_parts), "\n".join(html_parts)


def _raw_parts_from_content(
    content_list: list[Any],
    text_blocks: list[Any],
    *,
    cancelled: Callable[[int, int], bool],
) -> list[str] | None:
    by_content_id: dict[str, int] = {}
    by_content_index: dict[int, int] = {}
    for text_index, text_block in enumerate(text_blocks):
        if cancelled(text_index, 256):
            return None
        content_id = getattr(text_block, "content_id", None)
        content_index = getattr(text_block, "content_index", None)
        if isinstance(content_id, str) and content_id:
            by_content_id.setdefault(content_id, text_index)
        if isinstance(content_index, int):
            by_content_index.setdefault(content_index, text_index)
    used_text_indices: set[int] = set()
    raw_parts: list[str] = []
    for content_index, block in enumerate(content_list):
        if cancelled(content_index, 128):
            return None
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "text")).lower()
        block_id = block.get("block_id")
        text_index = by_content_id.get(block_id) if isinstance(block_id, str) else None
        if text_index is None:
            text_index = by_content_index.get(content_index)
        if (
            text_index is None
            and content_index < len(text_blocks)
            and content_index not in used_text_indices
            and getattr(text_blocks[content_index], "content_id", None) in {None, ""}
            and getattr(text_blocks[content_index], "content_index", None) is None
        ):
            text_index = content_index
        if text_index is not None:
            used_text_indices.add(text_index)
        if block_type in DISCARDED_BLOCK_TYPES:
            continue
        if block_type == "table":
            text = table_model_to_plain_text(table_model_from_block(block))
        elif text_index is not None:
            text = str(getattr(text_blocks[text_index], "text", "") or "")
        elif block_type == "list":
            text = "\n".join(_text_items(block.get("list_items")))
        elif block_type in {"image", "figure", "chart", "seal"}:
            text = "\n".join(
                _text_items(block.get("image_caption") or block.get("chart_caption"))
            )
        else:
            text = str(
                block.get("text")
                or block.get("code_body")
                or block.get("content")
                or ""
            )
        if text:
            raw_parts.append(text)
    for text_index, text_block in enumerate(text_blocks):
        if text_index in used_text_indices:
            continue
        if str(getattr(text_block, "label", "")).lower() in DISCARDED_BLOCK_TYPES:
            continue
        text = str(getattr(text_block, "text", "") or "")
        if text:
            raw_parts.append(text)
    return raw_parts


def rebuild_result_projections(result: Any) -> None:
    projections = build_result_projections(result)
    if projections is not None:
        result.raw_text, result.markdown_text, result.html_text = projections


def build_copy_snapshot(
    snapshot: Any,
    *,
    is_cancelled: Callable[[], bool],
    include_markdown: bool,
) -> Any | None:
    """Rebuild clipboard aggregates from a detached worker-owned snapshot."""
    raw_parts: list[str] = []
    for index, block in enumerate(snapshot.text_blocks):
        if index % 128 == 0 and is_cancelled():
            return None
        if block.text:
            raw_parts.append(block.text)
    raw_text = "\n".join(raw_parts) or snapshot.raw_text
    markdown_text = raw_text
    if include_markdown and snapshot.content_list:
        markdown_parts: list[str] = []
        for index, block in enumerate(snapshot.content_list):
            if index % 128 == 0 and is_cancelled():
                return None
            if block.get("type") == "table":
                markdown = html_table_to_markdown(
                    extract_table_html(block.get("table_body", ""))
                )
                if markdown:
                    markdown_parts.append(markdown)
            else:
                text = str(block.get("text", "") or "")
                if text:
                    markdown_parts.append(text)
        if markdown_parts:
            markdown_text = "\n\n".join(markdown_parts)
    if is_cancelled():
        return None
    return replace(
        snapshot, raw_text=raw_text, markdown_text=markdown_text, html_text=""
    )


def _text_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item)]


def _escaped_text(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def _heading_level(value: Any) -> int:
    try:
        return min(max(int(value or 1), 1), 6)
    except (TypeError, ValueError):
        return 1


__all__ = [
    "TableBlockEdit",
    "build_copy_snapshot",
    "build_result_projections",
    "rebuild_result_projections",
    "replace_result_table_from_html",
    "update_result_table_cell",
    "update_table_cell",
]
