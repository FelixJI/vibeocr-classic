"""识别结果显示组件

使用 QWebEngineView 渲染结构化 OCR 结果，支持：
- 块类型注册表渲染（text/table/image/equation/list/code/chart）
- KaTeX 离线公式渲染
- 图片 data URI 内嵌显示
- QWebChannel 双向高亮通信
"""

from __future__ import annotations

import base64
import contextvars
import html as html_lib
import json
import logging
import time
from dataclasses import is_dataclass, replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend.models.ocr_result import DISCARDED_BLOCK_TYPES
from vibeocr.backend.utils.html_tables import (
    html_tables_to_cell_grid,
    normalize_table_html,
    tables_from_result,
)
from vibeocr.classic.utils.export_jobs import (
    ExportJobCancelled,
    ExportSaveJob,
    export_single_operation,
    snapshot_ocr_result,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    # QWebEngineView / QWebChannel 仅作类型注解引用，运行时延迟 import
    # （WebEngine 内置主包：Qt6WebEngineCore.dll 随 _internal/ 一起分发，
    # 延迟 import 仅为避免顶层立即触发 DLL 加载）。
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)

_DOCUMENT_TOKEN_PLACEHOLDER = "__VIBEOCR_DOCUMENT_TOKEN_8E2C4A75__"


def _get_resources_dir() -> Path:
    """获取 resources 目录路径（打包态/开发态通用）

    委托 env_manager.get_bundled_resources_dir() 作为 SSOT：
    打包态 resources 由 ``--add-data`` 打入 ``sys._MEIPASS``（``_internal/resources``），
    而非 exe 同级；开发态位于仓库根。
    采用函数惰性求值，避免模块导入时触发 env_manager 的循环导入。
    """
    from vibeocr.backend.env_manager import get_bundled_resources_dir

    return get_bundled_resources_dir()


# 块类型 → CSS 左边框颜色
BLOCK_BORDER_COLORS: dict[str, str] = {
    "text": "#3b82f6",
    "title": "#ef4444",
    "table": "#22c55e",
    "image": "#a855f7",
    "figure": "#a855f7",
    "chart": "#a855f7",
    "equation": "#f97316",
    "interline_equation": "#f97316",
    "inline_equation": "#f97316",
    # PaddleX 公式管道（pipeline_formula / pipeline_pp_structure）输出 label="formula"，
    # 在渲染层归一到公式渲染（KaTeX），避免下游（导出/Markdown）受影响。
    "formula": "#f97316",
    "list": "#06b6d4",
    "code": "#8b5cf6",
    "seal": "#6b7280",
}

BLOCK_TYPE_LABELS: dict[str, str] = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "chart": "图表",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
    "formula": "公式",
    "list": "列表",
    "code": "代码",
    "seal": "印章",
}

# 存储当前结果的 images 字典，供渲染函数访问
_current_images: dict[str, bytes] = {}
_render_images: contextvars.ContextVar[dict[str, bytes] | None] = (
    contextvars.ContextVar("result_render_images", default=None)
)


def _active_render_images() -> dict[str, bytes]:
    """返回当前纯数据构建上下文的图片；兼容直接调用 renderer 的旧测试。"""
    images = _render_images.get()
    return _current_images if images is None else images


# ── 块类型渲染函数 ──────────────────────────────────────────


def _render_text(block: dict, index: int) -> str:
    text = html_lib.escape(block.get("text", ""))
    return f"<p>{text}</p>"


def _render_title(block: dict, index: int) -> str:
    level = min(block.get("text_level", block.get("level", 1)), 6)
    text = html_lib.escape(block.get("text", ""))
    return f"<h{level}>{text}</h{level}>"


def _render_table(block: dict, index: int) -> str:
    from vibeocr.backend.utils.html_tables import normalize_table_html

    parts: list[str] = []
    captions = block.get("table_caption") or []
    if captions:
        parts.append(
            f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>'
        )
    canonical_table = ""
    if isinstance(block.get("table"), dict):
        from vibeocr.backend.tables.blocks import table_model_from_block
        from vibeocr.backend.tables.html_adapter import table_model_to_html

        try:
            canonical_table = table_model_to_html(table_model_from_block(block))
        except (KeyError, TypeError, ValueError):
            parts.append(
                '<p class="table-schema-warning" '
                'style="color:#b45309;font-size:12px;">'
                "表格结构版本不受支持，已使用兼容视图。</p>"
            )
            try:
                canonical_table = table_model_to_html(
                    table_model_from_block(block, strict_canonical=False)
                )
            except (KeyError, TypeError, ValueError):
                canonical_table = ""
    table_body = block.get("table_body", "")
    html_content = block.get("html", "")
    raw_table = canonical_table or table_body or html_content
    if raw_table:
        # 规整化：剥离 PaddleX 自带的 inline style（避免复制带底纹），
        # 并补齐空单元格（避免 Excel 粘贴错位）。
        clean_table = raw_table if canonical_table else normalize_table_html(raw_table)
        parts.append(f'<div class="ocr-table">{clean_table}</div>')
    else:
        text = html_lib.escape(block.get("text", ""))
        parts.append(f"<p>{text}</p>")
    footnotes = block.get("table_footnote") or []
    if footnotes:
        parts.append(
            f'<p style="color:#888;font-size:11px;">{html_lib.escape(footnotes[0])}</p>'
        )
    return "\n".join(parts)


def _render_image(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    images = _active_render_images()
    if img_path and img_path in images:
        img_bytes = images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        ext = img_path.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            ext, "image/png"
        )
        parts.append(
            f'<img src="data:{mime};base64,{b64}" style="max-width:100%;border-radius:4px;">'
        )
    else:
        img_idx = block.get("img_idx")
        if img_idx is not None:
            parts.append(f'<p style="color:#888;">[图片 #{img_idx}]</p>')
        else:
            text = html_lib.escape(block.get("text", ""))
            parts.append(
                f"<p>[图片] {text}</p>" if text else '<p style="color:#888;">[图片]</p>'
            )
    captions = block.get("image_caption") or []
    if captions:
        parts.append(
            f'<p style="color:#888;font-size:12px;">{html_lib.escape(captions[0])}</p>'
        )
    return "\n".join(parts)


def _render_chart(block: dict, index: int) -> str:
    parts: list[str] = []
    img_path = block.get("img_path", "")
    images = _active_render_images()
    if img_path and img_path in images:
        img_bytes = images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        parts.append(
            f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:4px;">'
        )
    content = block.get("content", "")
    if content:
        parts.append(
            f'<p style="color:#555;font-size:13px;">{html_lib.escape(content)}</p>'
        )
    if not parts:
        parts.append('<p style="color:#888;">[图表]</p>')
    return "\n".join(parts)


def _render_equation(block: dict, index: int) -> str:
    latex = html_lib.escape(block.get("text", ""))
    # 注意：不再在此处加 border-left。外层 _render_block 已为公式块加了
    # 橙色左边框（#f97316）作为类型标识；此处再叠加会形成"双条色标"，
    # 且蓝色（#0078d4）与文本蓝（#3b82f6）混淆，难以区分。
    return (
        f'<div class="math-block" data-latex="{latex}" '
        f'style="background:#f8f9fa;padding:8px 12px;border-radius:4px;'
        f'font-family:Consolas,Monaco,monospace;font-size:13px;">'
        f"{latex}</div>"
    )


def _render_list(block: dict, index: int) -> str:
    items = block.get("list_items", [])
    li_html = "".join(f"<li>{html_lib.escape(item)}</li>" for item in items)
    return f'<ul style="padding-left:20px;">{li_html}</ul>'


def _render_code(block: dict, index: int) -> str:
    body = html_lib.escape(block.get("code_body", ""))
    sub = block.get("sub_type", "")
    lang_label = (
        f'<span style="color:#888;font-size:11px;">[{html_lib.escape(sub)}]</span>'
        if sub
        else ""
    )
    return (
        f"{lang_label}"
        f'<pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;'
        f'overflow-x:auto;font-size:13px;"><code>{body}</code></pre>'
    )


def _render_seal(block: dict, index: int) -> str:
    img_path = block.get("img_path", "")
    images = _active_render_images()
    if img_path and img_path in images:
        img_bytes = images[img_path]
        b64 = base64.b64encode(img_bytes).decode()
        return f'<img src="data:image/png;base64,{b64}" style="max-width:60%;border-radius:4px;">'
    return '<p style="color:#888;font-size:12px;">[印章]</p>'


def _render_fallback(block: dict, index: int) -> str:
    text = html_lib.escape(block.get("text", ""))
    return f"<p>{text}</p>" if text else ""


# 块类型注册表
BLOCK_RENDERERS: dict[str, Callable[[dict, int], str]] = {
    "text": _render_text,
    "table": _render_table,
    "image": _render_image,
    "chart": _render_chart,
    "equation": _render_equation,
    "interline_equation": _render_equation,
    "inline_equation": _render_equation,
    # PaddleX 公式管道输出 type="formula"，归一到公式渲染（KaTeX）。
    "formula": _render_equation,
    "list": _render_list,
    "code": _render_code,
    "seal": _render_seal,
}


def _build_text_layout_html(
    text_blocks: list | tuple,
    options: Any,
    cancel_event: Event | None = None,
) -> str:
    """按文本块处理选项排版纯文本块为 HTML，保留逐块可编辑性。

    复用 TextBlockProcessor 的分段逻辑（去空白块 / 排序 / smart 分段），但
    输出的是 DOM 结构而非拼接字符串：每个文本块仍是一个 ``.ocr-block``
    （带 data-block-index = 在**原始 text_blocks** 中的下标），段内块以
    ``display:inline-block`` 横向排列（视觉上合并），段间留 margin。

    这样换行模式/空格/缩进在视觉上生效，同时：
    - 双击编辑、悬停联动按 index 仍可命中（DOM 结构未变）；
    - 编辑回写按 content_index 反查 text_block 的契约不变（即使 drop_blank
      过滤掉中间块，保留下来的块 index 仍指向原始列表位置）。

    block_join_space / chinese_indent 在内联块间通过 HTML 文本节点体现：
    块间加空格 → 段内块之间插入一个空格文本节点；中文缩进 → 每段首块前置
    两个全角空格。keep 模式每块独立成行（block 级）。
    """
    from vibeocr.classic.recognition_settings import (
        LINE_MODE_KEEP,
        LINE_MODE_MERGE,
        LINE_MODE_SMART,
    )
    from vibeocr.backend.utils.text_layout import TextBlockProcessor

    # 用 (原始下标, 块) 配对跟踪位置，避免 drop_blank / 排序后 index 错位。
    if cancel_event is not None and cancel_event.is_set():
        raise ExportJobCancelled
    indexed = list(enumerate(text_blocks))
    if options.drop_blank_blocks:
        filtered: list[tuple[int, Any]] = []
        for position, (index, block) in enumerate(indexed):
            if position % 128 == 0 and cancel_event is not None:
                if cancel_event.is_set():
                    raise ExportJobCancelled
            if block.text and block.text.strip():
                filtered.append((index, block))
        indexed = filtered
    if not indexed:
        return ""
    indexed = TextBlockProcessor._sort_indexed(indexed)

    cjk_indent = "\u3000\u3000" if options.chinese_indent else ""

    # keep：每块独立成行（块级 .ocr-block）。
    if options.line_mode == LINE_MODE_KEEP:
        parts: list[str] = []
        for pos, (index, block) in enumerate(indexed):
            if pos % 128 == 0 and cancel_event is not None:
                if cancel_event.is_set():
                    raise ExportJobCancelled
            parts.append(
                _text_layout_block_html(
                    index, block, prefix=cjk_indent if pos == 0 else ""
                )
            )
        return "\n".join(parts)

    # merge / smart：段内块横排（inline），段间空行。
    if options.line_mode == LINE_MODE_SMART:
        segments = TextBlockProcessor._split_indexed_into_segments(indexed)
    elif options.line_mode == LINE_MODE_MERGE:
        segments = [indexed]
    else:  # 防御：未知模式按 merge 处理
        segments = [indexed]

    sep = " " if options.block_join_space else ""
    parts: list[str] = []
    processed = 0
    for seg in segments:
        chunks: list[str] = []
        for pos, (i, b) in enumerate(seg):
            if processed % 128 == 0 and cancel_event is not None:
                if cancel_event.is_set():
                    raise ExportJobCancelled
            processed += 1
            prefix = cjk_indent if pos == 0 else ""
            chunks.append(_text_layout_block_html(i, b, inline=True, prefix=prefix))
            # 段内块间插入分隔（HTML 文本节点，非编辑内容）
            if sep and pos < len(seg) - 1:
                chunks.append(sep)
        parts.append(f'<div class="ocr-segment">{"".join(chunks)}</div>')
    return '<div class="ocr-segments">' + "</div>".join(parts) + "</div>"


def _text_layout_block_html(
    index: int,
    block: Any,
    inline: bool = False,
    prefix: str = "",
) -> str:
    """单个文本块 → .ocr-block HTML（纯文本）。

    inline=True 时块横排（display:inline-block），用于 merge/smart 段内；
    否则块级（display:block），用于 keep 模式。prefix 是段首缩进等装饰文本
    （放在块内文本之前，不可单独编辑）。
    """
    display = "inline-block" if inline else "block"
    text = html_lib.escape(prefix + (block.text or ""))
    return (
        f'<div class="ocr-block" data-block-index="{index}" '
        f'data-block-type="text" id="block-{index}" '
        f'style="display:{display};padding:2px 6px;border-left:3px solid #3b82f6;'
        f'margin:2px 0;border-radius:2px;">'
        f"<p>{text}</p></div>"
    )


def _render_block(block: dict, index: int) -> str:
    """根据块类型查找渲染器并生成 HTML"""
    block_type = block.get("type", "text")
    border_color = BLOCK_BORDER_COLORS.get(block_type, "#3b82f6")
    type_label = BLOCK_TYPE_LABELS.get(block_type, block_type)

    renderer: Callable[[dict, int], str] = _render_fallback
    if block_type == "text" and "text_level" in block:
        renderer = _render_title
        type_label = "标题"
        border_color = BLOCK_BORDER_COLORS["title"]
    elif block_type == "title":
        renderer = _render_title
    elif block_type in BLOCK_RENDERERS:
        renderer = BLOCK_RENDERERS[block_type]

    content_html = renderer(block, index)
    if not content_html:
        return ""

    title_parts = [f"类型: {type_label}"]
    confidence = block.get("confidence")
    if confidence is not None:
        title_parts.append(f"置信度: {confidence * 100:.0f}%")
    page_idx = block.get("page_idx")
    if page_idx is not None:
        title_parts.append(f"页码: {page_idx}")
    title_attr = html_lib.escape(" | ".join(title_parts))

    return (
        f'<div class="ocr-block" data-block-index="{index}" '
        f'data-block-type="{html_lib.escape(block_type)}" id="block-{index}" '
        f'style="padding:4px 8px;border-left:3px solid {border_color};'
        f'margin:2px 0;border-radius:2px;" '
        f'title="{title_attr}">'
        f"{content_html}"
        f"</div>"
    )


def _build_full_html(
    blocks_html: str,
    katex_dir: Path | None = None,
    resources_dir: Path | None = None,
) -> str:
    """构建完整 HTML 页面（含 KaTeX、qwebchannel.js、CSS、JS）"""
    katex_css = ""
    katex_js_tag = ""  # 外部 KaTeX <script>（onload 触发渲染）
    if katex_dir and katex_dir.exists():
        # 必须用绝对路径：早期版本传相对路径 resources/katex/katex.min.js，
        # QUrl.fromLocalFile 会生成畸形 URL（file:resources/... 而非 file:///...），
        # Chromium WebEngine 无法加载 → KaTeX 不执行 → 公式显示为原始 LaTeX。
        katex_css_url = (katex_dir / "katex.min.css").resolve().as_uri()
        katex_js_url = (katex_dir / "katex.min.js").resolve().as_uri()
        katex_css = f'<link rel="stylesheet" href="{katex_css_url}">'
        # KaTeX 加载完成后再触发渲染（onload），避免外部脚本加载失败时
        # 阻塞其后内联脚本（编辑/光标逻辑）的执行。
        katex_js_tag = (
            f'<script src="{katex_js_url}" onload="renderAllMath()"></script>'
        )

    # qwebchannel.js：QWebChannel 桥接必需。
    # PySide6/Qt 不附带该文件，项目 resources/ 内置。必须在内联 <script>
    # 之前加载：内联脚本里的 `new QWebChannel(...)`（见下方）依赖此处定义的
    # 全局 QWebChannel 构造函数；不加载则 typeof QWebChannel === 'undefined'
    # → 桥回调永不执行 → _bridge 恒为 null → 悬停联动（onBlockHover 等）失效。
    qwebchannel_js_tag = ""
    if resources_dir:
        qwebchannel_path = resources_dir / "qwebchannel.js"
        if qwebchannel_path.exists():
            qwebchannel_js_url = qwebchannel_path.resolve().as_uri()
            qwebchannel_js_tag = f'<script src="{qwebchannel_js_url}"></script>'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
{katex_css}
{qwebchannel_js_tag}
<style>
body {{ margin:0; padding:8px; font-family:"Microsoft YaHei","Segoe UI",sans-serif; font-size:14px; }}
.ocr-block {{ transition: background-color 0.15s; }}
.ocr-block:hover {{ background-color: #f0f9ff; }}
.ocr-block.highlight {{ background-color: #fef08a !important; border-left-width: 4px !important; }}
/* 光标：文本区显示 I-beam（提示可编辑），表格单元格默认箭头。
   不在 .ocr-block 内联 style 设 cursor:pointer（旧版这样做会压过编辑态样式表）。 */
.ocr-block p, .ocr-block h1, .ocr-block h2, .ocr-block h3,
.ocr-block h4, .ocr-block h5, .ocr-block h6, .ocr-block li {{ cursor: text; }}
/* 重置文本块直接子 <p> 的浏览器默认外边距：_render_text 输出 <p>，其默认
   margin（约 1em）叠加 .ocr-block 的 padding 会让单行文本上下空白过大。
   仅作用于直接子节点，避免影响表格/图片 caption 等带 inline 样式的 <p>。 */
.ocr-block > p {{ margin: 0; }}
/* 文本块排版（display_text_layout）：段内块横排，段间留空行。
   仅影响纯文本结果按换行模式 merge/smart 渲染时的视觉分组，
   不改变 .ocr-block 的编辑/悬停契约（块仍可按 index 双击编辑）。 */
.ocr-segments {{ line-height: 1.8; }}
.ocr-segment {{ margin-bottom: 1em; }}
.ocr-segment:last-child {{ margin-bottom: 0; }}
.ocr-table {{ overflow-x: auto; }}
.ocr-table table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.ocr-table td, .ocr-table th {{ border: 1px solid #d1d5db; padding: 6px 8px; }}
.ocr-table th {{ font-weight: 600; }}
/* 不加 th 背景与斑马纹：避免原生 Ctrl+C 把底纹带进剪贴板（Excel/Word 粘贴出灰底）。
   视觉区分靠边框 + th 加粗即可；复制时另有 copy 拦截器输出无样式 HTML。 */
.ocr-table td.sel-cell, .ocr-table th.sel-cell {{ background-color: rgba(25,118,210,0.18) !important; }}
.manually-edited {{ border-left-color: #ff9800 !important; border-left-width: 4px !important; }}
/* 编辑态：!important 压过任何继承/内联 cursor，确保进入编辑时光标变 I-beam。 */
[contenteditable="true"] {{ outline: 2px solid #1976d2; background-color: rgba(255,255,255,0.95); cursor: text !important; }}
</style>
</head>
<body>
<div id="content">
{blocks_html}
</div>
<script>
var _documentToken = "{_DOCUMENT_TOKEN_PLACEHOLDER}";
// 公式渲染函数：由 KaTeX <script onload> 触发，也可被编辑后手动调用。
// 放在内联脚本最前面定义，确保 KaTeX 加载完成时函数已存在。
function renderAllMath() {{
    if (typeof katex === 'undefined') return;
    document.querySelectorAll('.math-block').forEach(function(el) {{
        var latex = el.getAttribute('data-latex');
        if (latex) {{
            try {{
                katex.render(latex, el, {{ displayMode: true, throwOnError: false }});
            }} catch(e) {{
                // 保留原始 LaTeX 文本
            }}
        }}
    }});
}}

// 编辑状态
var _bridge = null;
var _editOriginals = {{}};
var _tableCellOriginals = {{}};
var _NON_EDITABLE = ['image', 'figure', 'chart', 'seal'];

function _finishTextEdit(block) {{
    var index = parseInt(block.getAttribute('data-block-index'));
    var newText = block.innerText.trim();
    block.removeAttribute('contenteditable');
    if (newText !== _editOriginals[index]) {{
        block.classList.add('manually-edited');
        if (_bridge) _bridge.onBlockEditedForDocument(_documentToken, index, newText);
    }}
    delete _editOriginals[index];
}}

function _finishTableEdit(block) {{
    var index = parseInt(block.getAttribute('data-block-index'));
    var tableEl = block.querySelector('.ocr-table');
    // 传回表格 HTML（不是 innerText 纯文本），与 update_block_text 重建
    // .ocr-table.innerHTML 的契约一致；Python 侧据此更新 table_body。
    var newHtml = tableEl ? tableEl.innerHTML.trim() : '';
    block.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
        cell.removeAttribute('contenteditable');
    }});
    var semanticTable = tableEl ? tableEl.querySelector('table[data-table-id]') : null;
    if (!semanticTable && newHtml !== _editOriginals[index]) {{
        block.classList.add('manually-edited');
        if (_bridge) _bridge.onBlockEditedForDocument(_documentToken, index, newHtml);
    }}
    delete _editOriginals[index];
}}

function _startEquationEdit(block, index) {{
    var mathBlock = block.querySelector('.math-block');
    if (!mathBlock) return;
    var latex = mathBlock.getAttribute('data-latex') || '';
    _editOriginals[index] = latex;

    var existing = document.getElementById('eq-editor');
    if (existing) existing.remove();

    var textarea = document.createElement('textarea');
    textarea.id = 'eq-editor';
    textarea.value = latex;
    textarea.style.cssText = 'width:100%;min-height:60px;padding:8px;font-family:Consolas,Monaco,monospace;font-size:13px;border:2px solid #1976d2;border-radius:4px;background:white;resize:vertical;';

    mathBlock.innerHTML = '';
    mathBlock.appendChild(textarea);
    textarea.focus();
    textarea.select();

    textarea.addEventListener('blur', function() {{
        var newLatex = this.value.trim();
        mathBlock.setAttribute('data-latex', newLatex);
        if (typeof katex !== 'undefined') {{
            try {{ katex.render(newLatex, mathBlock, {{ displayMode: true, throwOnError: false }}); }}
            catch(e) {{ mathBlock.innerText = newLatex; }}
        }} else {{
            mathBlock.innerText = newLatex;
        }}
        if (newLatex !== _editOriginals[index]) {{
            block.classList.add('manually-edited');
            if (_bridge) _bridge.onBlockEditedForDocument(_documentToken, index, newLatex);
        }}
        delete _editOriginals[index];
    }});
}}

// ── 块事件绑定（顶层立即执行，不依赖 QWebChannel 回调时机）──
// 历史：早期版本把 addEventListener 放在 `new QWebChannel(...)` 回调里，
// 回调异步执行，绑定时机晚于 DOM 交互 → dblclick/click 监听器漏绑。
// 现改为顶层立即绑定事件（与 bridge 就绪与否解耦）；QWebChannel 回调
// 只负责赋值 _bridge，所有 _bridge.* 调用都用 if(_bridge) 守卫，
// bridge 未就绪时编辑/光标/复制照常工作（仅悬停联动需要 _bridge）。
// 注：qwebchannel.js 现由 <head> 的 <script> 加载（见 _build_full_html），
// 若该文件缺失，下方 QWebChannel 分支不会执行，_bridge 保持 null。
document.querySelectorAll('.ocr-block').forEach(function(el) {{
    el.addEventListener('mouseenter', function() {{
        if (_bridge) _bridge.onBlockHover(parseInt(this.getAttribute('data-block-index')));
    }});
    el.addEventListener('mouseleave', function() {{
        if (_bridge) _bridge.onBlockLeave();
    }});
    el.addEventListener('click', function() {{
        if (_bridge) _bridge.onBlockClick(parseInt(this.getAttribute('data-block-index')));
    }});
    el.addEventListener('dblclick', function(e) {{
        var blockType = this.getAttribute('data-block-type');
        if (_NON_EDITABLE.indexOf(blockType) >= 0) return;
        e.preventDefault();
        e.stopPropagation();
        var index = parseInt(this.getAttribute('data-block-index'));

        if (blockType === 'table') {{
            // 基线与 _finishTableEdit 的比较值统一用 innerHTML（表格 HTML），
            // 保证"未改动不标黄"，且 onBlockEdited 回传的就是新表格 HTML，
            // 与 Python 侧 table_body 更新 / update_block_text 重建契约一致。
            var tableEl = this.querySelector('.ocr-table');
            _editOriginals[index] = tableEl ? tableEl.innerHTML.trim() : '';
            this.querySelectorAll('.ocr-table td, .ocr-table th').forEach(function(cell) {{
                cell.setAttribute('contenteditable', 'true');
            }});
            var firstCell = this.querySelector('.ocr-table td, .ocr-table th');
            if (firstCell) firstCell.focus();
        }} else if (['equation', 'interline_equation', 'inline_equation', 'formula'].indexOf(blockType) >= 0) {{
            _startEquationEdit(this, index);
        }} else {{
            _editOriginals[index] = this.innerText;
            this.setAttribute('contenteditable', 'true');
            this.focus();
        }}
    }});
}});

// 高亮通信：仅赋值 _bridge。失败（qwebchannel.js 缺失）不影响上方编辑逻辑。
if (typeof QWebChannel !== 'undefined') {{
    new QWebChannel(qt.webChannelTransport, function(channel) {{
        _bridge = channel.objects.bridge;
    }});
}}

document.addEventListener('focusin', function(e) {{
    if (!e.target.matches || !e.target.matches('.ocr-table td[contenteditable], .ocr-table th[contenteditable]')) return;
    var table = e.target.closest('table[data-table-id]');
    var cellId = e.target.getAttribute('data-cell-id');
    if (!table || !cellId) return;
    var key = table.getAttribute('data-table-id') + '\\n' + cellId;
    if (!Object.prototype.hasOwnProperty.call(_tableCellOriginals, key)) {{
        _tableCellOriginals[key] = e.target.innerText;
    }}
}});

// 全局 blur 处理
document.addEventListener('focusout', function(e) {{
    if (e.target.matches && e.target.matches('.ocr-table td[contenteditable], .ocr-table th[contenteditable]')) {{
        var block = e.target.closest('.ocr-block');
        if (block) {{
            var table = e.target.closest('.ocr-table');
            var semanticTable = e.target.closest('table[data-table-id]');
            var tableId = semanticTable ? semanticTable.getAttribute('data-table-id') : '';
            var cellId = e.target.getAttribute('data-cell-id') || '';
            var editedCell = e.target;
            setTimeout(function() {{
                if (tableId && cellId) {{
                    var key = tableId + '\\n' + cellId;
                    var original = _tableCellOriginals[key];
                    var newText = editedCell.innerText;
                    if (newText !== original) {{
                        block.classList.add('manually-edited');
                        if (_bridge) _bridge.onTableCellEditedForDocument(
                            _documentToken, tableId, cellId, newText
                        );
                    }}
                    delete _tableCellOriginals[key];
                }}
                if (!table.contains(document.activeElement)) {{
                    _finishTableEdit(block);
                }}
            }}, 50);
        }}
        return;
    }}
    var block = e.target.closest ? e.target.closest('.ocr-block[contenteditable="true"]') : null;
    if (block) _finishTextEdit(block);
}});

// Escape 取消编辑
document.addEventListener('keydown', function(e) {{
    if (e.key !== 'Escape') return;

    var block = document.querySelector('.ocr-block[contenteditable="true"]');
    if (block) {{
        var index = parseInt(block.getAttribute('data-block-index'));
        block.innerText = _editOriginals[index] || block.innerText;
        block.removeAttribute('contenteditable');
        delete _editOriginals[index];
        e.preventDefault();
        return;
    }}

    var eqEditor = document.getElementById('eq-editor');
    if (eqEditor) {{
        var eqBlock = eqEditor.closest('.ocr-block');
        var mathBlock = eqEditor.closest('.math-block');
        var eqIndex = eqBlock ? parseInt(eqBlock.getAttribute('data-block-index')) : -1;
        var origLatex = _editOriginals[eqIndex] || '';
        mathBlock.setAttribute('data-latex', origLatex);
        if (typeof katex !== 'undefined') {{
            try {{ katex.render(origLatex, mathBlock, {{ displayMode: true, throwOnError: false }}); }}
            catch(e2) {{ mathBlock.innerText = origLatex; }}
        }} else {{
            mathBlock.innerText = origLatex;
        }}
        delete _editOriginals[eqIndex];
        e.preventDefault();
    }}
}});

function highlightBlock(index) {{
    document.querySelectorAll('.ocr-block.highlight').forEach(function(el) {{
        el.classList.remove('highlight');
    }});
    var target = document.getElementById('block-' + index);
    if (target) {{
        target.classList.add('highlight');
        target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}
}}

function getCopyText() {{
    var sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) {{
        return sel.toString();
    }}
    var blocks = document.querySelectorAll('.ocr-block');
    var parts = [];
    blocks.forEach(function(b) {{
        var t = b.innerText.trim();
        if (t) parts.push(t);
    }});
    return parts.join('\\n\\n');
}}

function getCopyPayload() {{
    var tables = Array.from(document.querySelectorAll('.ocr-table table'));
    if (tables.length === 0) {{
        return {{ documentToken: _documentToken, html: '', text: getCopyText() }};
    }}
    var htmlParts = [];
    var textParts = [];
    tables.forEach(function(table) {{
        var clean = table.cloneNode(true);
        clean.querySelectorAll('*').forEach(function(el) {{
            Array.from(el.attributes).forEach(function(attr) {{
                el.removeAttribute(attr.name);
            }});
        }});
        htmlParts.push(clean.outerHTML);
        var rows = [];
        table.querySelectorAll('tr').forEach(function(row) {{
            var cells = Array.from(row.querySelectorAll(':scope > th, :scope > td'));
            rows.push(cells.map(function(cell) {{ return cell.innerText.trim(); }}).join('\\t'));
        }});
        textParts.push(rows.join('\\n'));
    }});
    return {{
        documentToken: _documentToken,
        html: htmlParts.join(''),
        text: textParts.join('\\n')
    }};
}}

// ── 表格单元格级拖选（Word/Excel 式）──
// 当前选中状态：null 表示无单元格选中（回退原生选区）
var _tableSel = null;  // {{ table, r0, c0, r1, c1 }}

function _cellIndex(cell) {{
    // 计算 td/th 在其 table 中的 (row, col)，考虑跨行/跨列已由规整化补齐
    var tr = cell.parentNode;
    var row = Array.prototype.indexOf.call(tr.parentNode.children, tr);
    var col = Array.prototype.indexOf.call(tr.children, cell);
    return {{ row: row, col: col }};
}}

function _clearTableSelHighlight() {{
    document.querySelectorAll('.ocr-table .sel-cell').forEach(function(c) {{
        c.classList.remove('sel-cell');
    }});
}}

function _applyTableSelHighlight(sel) {{
    _clearTableSelHighlight();
    if (!sel) return;
    var rows = sel.table.querySelectorAll('tr');
    var r0 = Math.min(sel.r0, sel.r1), r1 = Math.max(sel.r0, sel.r1);
    var c0 = Math.min(sel.c0, sel.c1), c1 = Math.max(sel.c0, sel.c1);
    for (var r = r0; r <= r1; r++) {{
        var cells = rows[r] ? rows[r].children : [];
        for (var c = c0; c <= c1; c++) {{
            if (cells[c]) cells[c].classList.add('sel-cell');
        }}
    }}
}}

function _startCellSelect(cell, e) {{
    // contenteditable 编辑中的单元格不拦截（让用户正常编辑文字）
    if (cell.getAttribute('contenteditable') === 'true') return;
    var table = cell.closest('table');
    if (!table) return;
    var pos = _cellIndex(cell);
    _tableSel = {{ table: table, r0: pos.row, c0: pos.col, r1: pos.row, c1: pos.col }};
    _applyTableSelHighlight(_tableSel);
    e.preventDefault();  // 阻止原生文本选区
}}

function _extendCellSelect(cell) {{
    if (!_tableSel) return;
    var pos = _cellIndex(cell);
    _tableSel.r1 = pos.row;
    _tableSel.c1 = pos.col;
    _applyTableSelHighlight(_tableSel);
}}

// mousedown：在单元格上启动拖选
document.addEventListener('mousedown', function(e) {{
    var cell = e.target.closest('.ocr-table td, .ocr-table th');
    if (cell) _startCellSelect(cell, e);
}});

// mousemove（按下时）：扩展选区
document.addEventListener('mousemove', function(e) {{
    if (!_tableSel || (e.buttons & 1) === 0) return;  // 仅左键按下时
    var cell = e.target.closest('.ocr-table td, .ocr-table th');
    if (cell && _tableSel.table.contains(cell)) _extendCellSelect(cell);
}});

// 点击表格外的区域：清除单元格选中
document.addEventListener('mousedown', function(e) {{
    if (!e.target.closest('.ocr-table')) {{
        if (_tableSel) {{
            _tableSel = null;
            _clearTableSelHighlight();
        }}
    }}
}});

// ── 从选中区域构建干净 HTML + Tab 分隔文本（复制用）──
function _tableSelToOutput(sel) {{
    var rows = sel.table.querySelectorAll('tr');
    var r0 = Math.min(sel.r0, sel.r1), r1 = Math.max(sel.r0, sel.r1);
    var c0 = Math.min(sel.c0, sel.c1), c1 = Math.max(sel.c0, sel.c1);
    var trHtml = [], lines = [];
    for (var r = r0; r <= r1; r++) {{
        var cells = rows[r] ? rows[r].children : [];
        var ch = [], texts = [];
        for (var c = c0; c <= c1; c++) {{
            var cell = cells[c];
            var text = cell ? cell.innerText : '';
            texts.push(text);
            // 保留原标签（td/th），不加任何属性 → Excel/Word 粘贴无底纹
            var tag = cell ? cell.tagName.toLowerCase() : 'td';
            ch.push('<' + tag + '>' + text.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</' + tag + '>');
        }}
        trHtml.push('<tr>' + ch.join('') + '</tr>');
        lines.push(texts.join('\\t'));
    }}
    return {{ html: '<table>' + trHtml.join('') + '</table>', text: lines.join('\\n') }};
}}

// ── 拦截 copy：表格选中时输出无样式 HTML + Tab 文本 ──
document.addEventListener('copy', function(e) {{
    if (!_tableSel) return;  // 无单元格选中 → 走原生 copy（普通文本块）
    var out = _tableSelToOutput(_tableSel);
    e.clipboardData.setData('text/html', out.html);
    e.clipboardData.setData('text/plain', out.text);
    e.preventDefault();
}});
</script>
{katex_js_tag}
</body>
</html>"""


def build_table_copy_payload(result: Any) -> tuple[str, str]:
    """从 OCR 结果构建「复制到 Excel/Word」所需的 (html, tab_text) 二元组。

    当结果含表格时：
    - ``html``：所有表格的规整化 ``<table>`` HTML（无 inline style，便于
      Excel/Word 识别为原生表格）。多个表格直接拼接。
    - ``tab_text``：Tab 分隔的纯文本矩阵（行内单元格用 ``\\t``、表间空行），
      Excel 粘贴即行列对齐。

    当结果无表格时返回 ``("", "")``，调用方据此回退到普通纯文本复制。

    Args:
        result: ``OCRResult`` / ``SimpleNamespace`` / wire ``dict``。

    Returns:
        ``(html, tab_text)``。
    """
    table_htmls = tables_from_result(result)
    if not table_htmls:
        return "", ""

    html_parts: list[str] = []
    text_lines: list[str] = []
    for raw_html in table_htmls:
        # 规整化：剥离 inline style、补齐空单元格，确保 Excel 粘贴不错位。
        clean_html = normalize_table_html(raw_html)
        html_parts.append(clean_html)
        grids = html_tables_to_cell_grid(clean_html)
        for grid in grids:
            for row in grid:
                text_lines.append("\t".join(row))
            text_lines.append("")  # 表间空行
    # 去掉末尾多余空行
    while text_lines and text_lines[-1] == "":
        text_lines.pop()
    return "".join(html_parts), "\n".join(text_lines)


def _create_cf_html(html_fragment: str) -> str:
    """构建 Microsoft Office CF_HTML 剪贴板格式（字节偏移头部 + 片段）。

    Word/Excel 粘贴时优先读 CF_HTML，能把 ``<table>`` 还原为原生表格。
    """
    full_html = (
        "<!DOCTYPE html>\n<html>\n<head><meta charset='utf-8'></head>\n<body>\n"
        f"<!--StartFragment-->{html_fragment}<!--EndFragment-->\n"
        "</body>\n</html>"
    )
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:0000000000\r\n"
        "EndHTML:0000000000\r\n"
        "StartFragment:0000000000\r\n"
        "EndFragment:0000000000\r\n"
    )
    header_len = len(header_template.encode("utf-8"))
    start_marker = "<!--StartFragment-->"
    end_marker = "<!--EndFragment-->"
    start_frag_pos = full_html.find(start_marker)
    end_frag_pos = full_html.find(end_marker)
    start_fragment_byte = header_len + len(
        full_html[: start_frag_pos + len(start_marker)].encode("utf-8")
    )
    end_fragment_byte = header_len + len(full_html[:end_frag_pos].encode("utf-8"))
    end_html_byte = header_len + len(full_html.encode("utf-8"))
    return (
        f"Version:0.9\r\n"
        f"StartHTML:{header_len:010d}\r\n"
        f"EndHTML:{end_html_byte:010d}\r\n"
        f"StartFragment:{start_fragment_byte:010d}\r\n"
        f"EndFragment:{end_fragment_byte:010d}\r\n"
        f"{full_html}"
    )


def _result_value(result: Any, name: str, default: Any) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _build_result_html(
    result: Any,
    resources_dir: Path,
    cancel_event: Event | None = None,
) -> str:
    """只使用普通 Python 数据构建完整结果 HTML，可安全在线程中执行。"""
    content_list = _result_value(result, "content_list", []) or []
    images = _result_value(result, "images", {}) or {}
    token = _render_images.set(images)
    try:
        if content_list:
            blocks_html: list[str] = []
            for index, block in enumerate(content_list):
                if cancel_event is not None and cancel_event.is_set():
                    raise ExportJobCancelled
                if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                    continue
                blocks_html.append(_render_block(block, index))
            body = "\n".join(blocks_html)
        else:
            text = _result_value(result, "raw_text", "") or ""
            if text:
                body = (
                    f'<pre style="white-space:pre-wrap;">{html_lib.escape(text)}</pre>'
                )
            else:
                body = '<p style="color:#888;">未识别到文字</p>'
        if cancel_event is not None and cancel_event.is_set():
            raise ExportJobCancelled
        return _build_full_html(body, resources_dir / "katex", resources_dir)
    finally:
        _render_images.reset(token)


def _capture_stable_result_snapshot(
    result: Any,
    cancel_event: Event,
    *,
    include_content_list: bool,
    include_images: bool,
    include_text_blocks: bool,
) -> Any:
    """Optimistically detach a stable source revision outside the GUI thread.

    The editable OCR model has no intrinsic revision counter.  Two consecutive,
    equal detached views therefore act as a seqlock read: an intermediate mixed
    view is never returned.  Normal UI edits also cancel the owning generation,
    so retries are only needed for direct source mutations.
    """
    previous = None
    last_error: Exception | None = None
    for _attempt in range(3):
        if cancel_event.is_set():
            raise ExportJobCancelled
        try:
            current = snapshot_ocr_result(
                result,
                include_content_list=include_content_list,
                include_images=include_images,
                include_text_blocks=include_text_blocks,
            )
        except Exception as error:  # mutable containers may change mid-copy
            last_error = error
            previous = None
            continue
        if previous is not None and _stable_values_equal(
            current, previous, cancel_event
        ):
            return current
        previous = current
    if cancel_event.is_set():
        raise ExportJobCancelled
    if last_error is not None:
        raise RuntimeError("OCR result changed while creating a stable snapshot") from last_error
    raise RuntimeError("OCR result did not reach a stable revision")


def _stable_values_equal(left: Any, right: Any, cancel_event: Event) -> bool:
    """Compare detached JSON-like/dataclass trees without a serialized copy."""
    visited = 0
    scalar_types = (type(None), str, bytes, int, float, bool)

    def equal(a: Any, b: Any) -> bool:
        nonlocal visited
        visited += 1
        if visited % 128 == 0 and cancel_event.is_set():
            raise ExportJobCancelled
        if type(a) is not type(b):
            return False
        if type(a) in scalar_types:
            if type(a) is float and a != a and b != b:
                return True
            return a == b
        dataclass_fields = getattr(a, "__dataclass_fields__", None)
        if (
            is_dataclass(a)
            and not isinstance(a, type)
            and isinstance(dataclass_fields, dict)
        ):
            return all(
                equal(getattr(a, str(name)), getattr(b, str(name)))
                for name in dataclass_fields
            )
        if isinstance(a, dict) and isinstance(b, dict):
            if len(a) != len(b):
                return False
            if any(type(key) not in scalar_types for key in a):
                return False
            if any(type(key) not in scalar_types for key in b):
                return False
            for key, value in a.items():
                if key not in b or not equal(value, b[key]):
                    return False
            return True
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(
                equal(a_item, b_item) for a_item, b_item in zip(a, b, strict=True)
            )
        return False

    if cancel_event.is_set():
        raise ExportJobCancelled
    matched = equal(left, right)
    if cancel_event.is_set():
        raise ExportJobCancelled
    return matched


def _rebuild_copy_snapshot(
    snapshot: Any,
    cancel_event: Event,
    *,
    include_markdown: bool,
) -> Any:
    """Rebuild aggregates from an already detached, worker-owned snapshot."""
    raw_parts: list[str] = []
    for index, block in enumerate(snapshot.text_blocks):
        if index % 128 == 0 and cancel_event.is_set():
            raise ExportJobCancelled
        if block.text:
            raw_parts.append(block.text)
    raw_text = "\n".join(raw_parts)
    if not raw_text:
        raw_text = snapshot.raw_text

    markdown_text = raw_text
    if include_markdown and snapshot.content_list:
        from vibeocr.backend.utils.html_tables import (
            _extract_table_html,
            _html_table_to_markdown,
        )

        markdown_parts: list[str] = []
        for index, block in enumerate(snapshot.content_list):
            if index % 128 == 0 and cancel_event.is_set():
                raise ExportJobCancelled
            if block.get("type") == "table":
                markdown = _html_table_to_markdown(
                    _extract_table_html(block.get("table_body", ""))
                )
                if markdown:
                    markdown_parts.append(markdown)
            else:
                text = str(block.get("text", "") or "")
                if text:
                    markdown_parts.append(text)
        if markdown_parts:
            markdown_text = "\n\n".join(markdown_parts)

    if cancel_event.is_set():
        raise ExportJobCancelled

    return replace(
        snapshot,
        raw_text=raw_text,
        markdown_text=markdown_text,
        html_text="",
    )


class _Bridge(QObject):
    """QWebChannel 通信桥"""

    blockHovered = Signal(int)
    blockUnhovered = Signal()
    blockClicked = Signal(int)
    blockEdited = Signal(int, str)  # (block_index, new_text)
    tableCellEdited = Signal(str, str, str)  # (table_id, cell_id, new_text)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._active_document_token = ""

    def set_active_document(self, token: str) -> None:
        """Accept edits only from the document currently owned by the widget."""
        self._active_document_token = token

    @Slot(int)
    def onBlockHover(self, index: int):
        self.blockHovered.emit(index)

    @Slot()
    def onBlockLeave(self):
        self.blockUnhovered.emit()

    @Slot(int)
    def onBlockClick(self, index: int):
        self.blockClicked.emit(index)

    @Slot(str, int, str)
    def onBlockEditedForDocument(self, token: str, index: int, text: str):
        if token == self._active_document_token:
            self.blockEdited.emit(index, text)

    @Slot(str, str, str, str)
    def onTableCellEditedForDocument(
        self,
        token: str,
        table_id: str,
        cell_id: str,
        text: str,
    ) -> None:
        if token == self._active_document_token:
            self.tableCellEdited.emit(table_id, cell_id, text)


class ResultViewWidget(QWidget):
    """OCR 结果显示组件（QWebEngineView 版本）"""

    block_hovered = Signal(int)
    block_unhovered = Signal()
    block_clicked = Signal(int)
    block_edited = Signal(int, str)  # 新增：(block_index, new_text)
    table_cell_edited = Signal(str, str, str)
    # WebEngine 不可用时触发（保留信号：内置打包后通常不会触发，
    # 但作为 import 失败时的防御性通知机制保留）。
    webengine_missing = Signal()
    # Emitted only after the worker has produced the exact immutable payload that
    # is rendered.  Containers such as BatchRecognitionTab can cache this value
    # instead of copying a live, GUI-editable OCRResult when Export is clicked.
    snapshot_ready = Signal(object, object)  # source result, OCRResultSnapshot
    snapshot_failed = Signal(object)  # source result

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        utility_client: Any = None,
    ):
        super().__init__(parent)
        self._utility_client = utility_client
        self._current_result: Any = None
        self._current_snapshot: Any = None
        self._submission_snapshot: Any = None
        self._snapshot_invalidated_by_edit = False
        self._source_generation = 0
        self._highlighted_index: int = -1
        self._copy_job: ExportSaveJob | None = None
        self._copy_generation = 0
        self._copy_js_pending = False
        self._export_job: ExportSaveJob | None = None
        self._export_generation = 0
        self._export_path = ""
        self._closing = False
        self._render_generation = 0
        self._render_jobs: set[ExportSaveJob] = set()
        self._document_generation = 0
        self._active_document_token = "0"
        self._rendered_document_token = ""
        self._pending_document_token = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 工具栏（复制按钮）
        toolbar = QWidget()
        toolbar.setFixedHeight(28)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(0, 0, 4, 0)
        tb_layout.setSpacing(4)
        tb_layout.addStretch()

        self._copy_btn = QPushButton("复制文本")
        self._copy_md_btn = QPushButton("复制MD")
        self._export_docx_btn = QPushButton("导出Word")
        self._export_xlsx_btn = QPushButton("导出Excel")
        for btn in (
            self._copy_btn,
            self._copy_md_btn,
            self._export_docx_btn,
            self._export_xlsx_btn,
        ):
            btn.setFixedHeight(24)
            btn.setStyleSheet("QPushButton { padding: 2px 12px; font-size: 12px; }")
            btn.hide()
        tb_layout.addWidget(self._copy_btn)
        tb_layout.addWidget(self._copy_md_btn)
        tb_layout.addWidget(self._export_docx_btn)
        tb_layout.addWidget(self._export_xlsx_btn)
        layout.addWidget(toolbar)

        # 复制成功浮层提示
        self._copy_toast = QLabel("已复制到剪贴板", self)
        self._copy_toast.setStyleSheet(
            "QLabel { background-color: #1f2937; color: #ffffff;"
            " padding: 6px 12px; border-radius: 4px; font-size: 12px; }"
        )
        self._copy_toast.hide()

        # 延迟创建：WebEngine 内置主包，import 通常成功；惰性创建避免启动即加载。
        self._web_view: QWebEngineView | None = None
        self._channel: QWebChannel | None = None
        self._bridge: _Bridge | None = None

        self._copy_btn.clicked.connect(self._on_copy_text)
        self._copy_md_btn.clicked.connect(self._on_copy_markdown)
        self._export_docx_btn.clicked.connect(lambda: self._on_export_file("docx"))
        self._export_xlsx_btn.clicked.connect(lambda: self._on_export_file("xlsx"))

    def _ensure_web_view(self) -> QWebEngineView | None:
        """惰性创建并返回 QWebEngineView；WebEngine 未就绪时返回 None。

        WebEngine（Qt6WebEngineCore.dll 等）内置主包，随 _internal/PySide6/ 分发。
        import 通常成功，返回 None 仅作为 DLL 加载异常时的防御性回退
        （调用方据此显示占位提示）。
        """
        if self._web_view is not None:
            return self._web_view

        # 运行时延迟 import：避免模块顶层加载触发 WebEngine DLL
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except (ImportError, OSError) as e:
            # Qt6WebEngineCore.dll 缺失或损坏 → ImportError/OSError
            logger.warning(f"WebEngine 不可用，结果页无法渲染: {e}")
            return None

        self._web_view = QWebEngineView(self)
        self._channel = QWebChannel(self._web_view)
        self._bridge = _Bridge(self)
        self._bridge.set_active_document(self._active_document_token)
        self._channel.registerObject("bridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)
        self._web_view.loadFinished.connect(self._on_web_load_finished)

        self._bridge.blockHovered.connect(self.block_hovered.emit)
        self._bridge.blockUnhovered.connect(self.block_unhovered.emit)
        self._bridge.blockClicked.connect(self.block_clicked.emit)
        self._bridge.blockEdited.connect(self.block_edited.emit)
        self._bridge.tableCellEdited.connect(self.table_cell_edited.emit)

        layout = self.layout()
        assert layout is not None
        layout.addWidget(self._web_view)
        return self._web_view

    def prewarm_webengine(self) -> None:
        """窗口显示后预热 WebEngine，避免首次截图结果前主界面闪烁。

        首次结果渲染会在 GUI 线程里惰性创建 QWebEngineView（Chromium 冷启动）+
        ``layout.addWidget`` 触发父级重排，导致主窗口多次闪烁。本方法在主窗口
        ``show()`` 之后的空闲片段（由 ``MainWindow`` 经 ``QTimer.singleShot``
        调度）提前调用幂等的 ``_ensure_web_view``，把冷启动成本前移到「用户已看到
        界面、尚未首次截图」的时刻。``_ensure_web_view`` 已创建即 return，故可
        安全重复调用；``_closing`` 为真时直接跳过（关闭中无需预热）。
        """
        if self._closing:
            return
        self._ensure_web_view()

    def _on_copy_text(self) -> None:
        """复制选中文本/全部文本/表格到剪贴板。

        结果含表格时写入 HTML + Tab 分隔纯文本 + CF_HTML，使粘贴到
        Excel 保持行列网格、粘贴到 Word 保持原生表格；否则回退到 WebEngine
        的 ``getCopyText()`` 纯文本（保留用户选区）。
        """
        if self._closing or self._current_result is None or self._copy_job is not None:
            return
        if (
            self._web_view is not None
            and self._rendered_document_token == self._active_document_token
        ):
            self._copy_generation += 1
            generation = self._copy_generation
            expected_token = self._active_document_token
            self._copy_js_pending = True
            self._set_copy_busy(True)
            self._web_view.page().runJavaScript(
                "getCopyPayload()",
                lambda payload: self._on_web_copy_payload(
                    generation, expected_token, payload
                ),
            )
            return
        self._start_copy_job("text")

    def _copy_rich_table(self, html: str, tab_text: str) -> None:
        """写入 HTML + Tab 文本 + CF_HTML 到剪贴板（Excel/Word 友好）。"""
        mime = QMimeData()
        mime.setHtml(html)
        mime.setText(tab_text)
        # CF_HTML：Microsoft Office 粘贴时优先读取，能把 <table> 还原为表格。
        mime.setData("HTML Format", _create_cf_html(html).encode("utf-8"))
        QGuiApplication.clipboard().setMimeData(mime)
        self._show_copy_toast()

    def _on_copy_markdown(self) -> None:
        """复制与当前已接纳编辑一致的 Markdown，不走 WebEngine JS。"""
        if self._closing or self._current_result is None or self._copy_job is not None:
            return
        self._start_copy_job("markdown")

    def _start_copy_job(self, kind: str) -> None:
        source_result = self._current_result
        if source_result is None or self._closing:
            return
        detached_snapshot = self._current_snapshot or self._submission_snapshot
        rebuild_aggregates = self._snapshot_invalidated_by_edit
        source_generation = self._source_generation
        self._copy_generation += 1
        generation = self._copy_generation

        def prepare(cancel_event, _progress):
            snapshot = detached_snapshot
            if snapshot is None:
                snapshot = _capture_stable_result_snapshot(
                    source_result,
                    cancel_event,
                    include_content_list=True,
                    include_images=False,
                    include_text_blocks=True,
                )
            if cancel_event.is_set():
                raise ExportJobCancelled
            if rebuild_aggregates:
                snapshot = _rebuild_copy_snapshot(
                    snapshot,
                    cancel_event,
                    include_markdown=kind == "markdown",
                )
            if kind == "markdown":
                text = snapshot.markdown_text or snapshot.raw_text
                return kind, "", text
            from types import SimpleNamespace

            copy_result = SimpleNamespace(
                raw_text=snapshot.raw_text,
                markdown_text=snapshot.markdown_text,
                html_text=snapshot.html_text,
                content_list=list(snapshot.content_list),
                text_blocks=list(snapshot.text_blocks),
            )
            html, tab_text = build_table_copy_payload(copy_result)
            return kind, html, tab_text or snapshot.raw_text

        job = ExportSaveJob(prepare)
        job.setProperty("generation", generation)
        job.setProperty("source_generation", source_generation)
        self._copy_job = job
        self._set_copy_busy(True)
        job.completed.connect(self._on_copy_job_completed)
        job.failed.connect(self._on_copy_job_failed)
        job.stopped.connect(self._on_copy_job_stopped)
        job.start()

    def _is_current_copy_signal(self) -> bool:
        job = self.sender()
        return bool(
            not self._closing
            and job is self._copy_job
            and job.property("generation") == self._copy_generation
            and job.property("source_generation") == self._source_generation
        )

    def _on_copy_job_completed(self, payload: object) -> None:
        if not self._is_current_copy_signal():
            # 结果在复制期间已被刷新/编辑：静默丢弃此次迟到的回调，
            # 不写剪贴板、不打扰用户（新的复制请求会自行反馈）。
            return
        if not isinstance(payload, tuple) or len(payload) != 3:
            logger.error("忽略无效的复制准备 payload")
            self._show_copy_toast("复制失败，请重试")
            return
        kind, html, text = payload
        if html and text:
            self._copy_rich_table(html, text)
        elif text:
            QGuiApplication.clipboard().setText(text)
            self._show_copy_toast(
                "Markdown 已复制" if kind == "markdown" else "已复制到剪贴板"
            )
        else:
            # 结果既无表格也无文本（如纯图片结果）：明确告知用户。
            self._show_copy_toast("无可复制内容")

    def _on_copy_job_failed(self, error: str) -> None:
        if self._is_current_copy_signal():
            logger.error("复制内容准备失败: %s", error)
            self._show_copy_toast("复制失败，请重试")

    def _on_copy_job_stopped(self, job: ExportSaveJob) -> None:
        if job is self._copy_job:
            self._copy_job = None
            self._set_copy_busy(False)
        job.deleteLater()

    def _on_web_copy_payload(
        self, generation: int, expected_token: str, payload: object
    ) -> None:
        if generation != self._copy_generation:
            # 迟到的旧回调：必须同样清理 _copy_js_pending，否则该 flag 会
            # 残留为 True，使下一次渲染后复制按钮一直处于禁用态。
            self._copy_js_pending = False
            logger.debug("丢弃迟到的复制回调（generation 已过期）")
            return
        self._copy_js_pending = False
        if not (
            not self._closing
            and expected_token == self._active_document_token
            and expected_token == self._rendered_document_token
            and isinstance(payload, dict)
            and payload.get("documentToken") == expected_token
        ):
            # 结果在点击后已被刷新：token 失配，明确告知用户重试。
            self._set_copy_busy(False)
            self._show_copy_toast("结果已刷新，请重新复制")
            logger.debug("复制回调 token 失配，未写入剪贴板")
            return
        html = str(payload.get("html") or "")
        text = str(payload.get("text") or "")
        if html and text:
            self._copy_rich_table(html, text)
        elif text:
            QGuiApplication.clipboard().setText(text)
            self._show_copy_toast()
        else:
            self._show_copy_toast("无可复制内容")
        self._set_copy_busy(False)

    def _cancel_copy(self) -> None:
        self._copy_generation += 1
        self._copy_js_pending = False
        if self._copy_job is not None:
            self._copy_job.cancel()
        self._set_copy_busy(self._copy_job is not None)

    def _set_copy_busy(self, busy: bool) -> None:
        self._copy_md_btn.setEnabled(
            not busy and not self._closing and self._current_result is not None
        )
        self._copy_btn.setEnabled(
            not busy
            and not self._closing
            and self._rendered_document_token == self._active_document_token
        )

    def _on_export_file(self, fmt: str) -> None:
        """导出为 Word/Excel 文件（另存为对话框 + ExportService）。"""
        if (
            self._current_result is None
            or self._current_snapshot is None
            or self._export_job is not None
            or self._closing
        ):
            return
        from pathlib import Path

        from vibeocr.classic.client import get_output_filename

        filter_label = {
            "docx": "Word 文档 (*.docx)",
            "xlsx": "Excel 工作簿 (*.xlsx)",
        }[fmt]
        default_name = get_output_filename("ocr_result", fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {fmt.upper()}", default_name, filter_label
        )
        if not path:
            return
        source_result = self._current_snapshot
        self._export_generation += 1

        def export(cancel_event, progress):
            return export_single_operation(
                self._utility_client, source_result, Path(path), fmt
            )(cancel_event, progress)

        job = ExportSaveJob(export)
        job.setProperty("generation", self._export_generation)
        self._export_job = job
        self._export_path = path
        self._set_export_busy(True)
        job.completed.connect(self._on_export_completed)
        job.failed.connect(self._on_export_failed)
        job.stopped.connect(self._on_export_job_finished)
        job.start()

    def _set_export_busy(self, busy: bool) -> None:
        enabled = (
            not busy
            and not self._closing
            and self._current_result is not None
            and self._current_snapshot is not None
        )
        self._export_docx_btn.setEnabled(enabled)
        self._export_xlsx_btn.setEnabled(enabled)

    def _is_current_export_signal(self) -> bool:
        job = self.sender()
        return bool(
            not self._closing
            and job is self._export_job
            and job.property("generation") == self._export_generation
        )

    def _on_export_completed(self, _path: object) -> None:
        if self._is_current_export_signal():
            QMessageBox.information(
                self, "导出成功", f"已导出到：\n{self._export_path}"
            )

    def _on_export_failed(self, _error: str) -> None:
        if self._is_current_export_signal():
            QMessageBox.warning(self, "导出失败", "导出失败，请重试或查看日志。")

    def _on_export_job_finished(self, job: ExportSaveJob) -> None:
        if job is not self._export_job:
            return
        self._export_job = None
        self._export_path = ""
        self._set_export_busy(False)
        job.deleteLater()

    def cancel_export(self) -> None:
        """取消当前导出并使其所有迟到回调失效。"""
        self._export_generation += 1
        if self._export_job is not None:
            self._export_job.cancel()

    def drain(self, timeout_ms: int = 0) -> bool:
        """Drain copy, export, and render jobs under one shared wall-clock budget."""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        copy_job = self._copy_job
        if copy_job is not None and not copy_job.drain(max(0, timeout_ms)):
            return False
        job = self._export_job
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if job is not None and not job.drain(remaining_ms):
            return False
        for render_job in tuple(self._render_jobs):
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not render_job.drain(remaining_ms):
                return False
        # Requiring the GUI stopped slots to remove jobs proves queued callbacks
        # no longer capture this QWidget before MainWindow destroys it.
        return (
            self._copy_job is None
            and self._export_job is None
            and not self._render_jobs
        )

    def set_closing(self, closing: bool) -> None:
        self._closing = closing
        if closing:
            self._cancel_copy()
            self.cancel_export()
            self._invalidate_render_jobs()
            self._activate_next_document()
            self._set_export_busy(True)
        else:
            self._set_export_busy(self._export_job is not None)

    def closeEvent(self, event) -> None:
        self.set_closing(True)
        super().closeEvent(event)

    def _show_copy_toast(self, message: str = "已复制到剪贴板") -> None:
        """显示复制成功浮层"""
        self._copy_toast.setText(message)
        self._copy_toast.adjustSize()
        # 显示在 widget 右上角
        x = self.width() - self._copy_toast.width() - 12
        y = 4
        self._copy_toast.move(x, y)
        self._copy_toast.raise_()
        self._copy_toast.show()
        QTimer.singleShot(1500, self._copy_toast.hide)

    def display_result(self, result: Any) -> None:
        """异步构建 OCR 结果 HTML；WebEngine 交互只发生在 GUI 线程。"""
        if self._closing:
            return
        self._cancel_copy()
        self._invalidate_render_jobs()
        self._source_generation += 1
        self._activate_next_document()
        # 先记录结果并显示 WebEngine 无关的按钮（复制MD/导出），
        # 这样即便 WebEngine 不可用，用户仍可复制 Markdown、导出 Word/Excel。
        self._current_result = result
        self._current_snapshot = None
        self._submission_snapshot = None
        self._snapshot_invalidated_by_edit = False
        self._highlighted_index = -1
        self._copy_md_btn.show()
        self._export_docx_btn.show()
        self._export_xlsx_btn.show()
        self._set_export_busy(self._export_job is not None)
        generation = self._render_generation
        source_generation = self._source_generation
        document_token = self._active_document_token

        def build(cancel_event, _progress):
            result_snapshot = _capture_stable_result_snapshot(
                result,
                cancel_event,
                include_content_list=True,
                include_images=True,
                include_text_blocks=False,
            )
            resources_dir = _get_resources_dir()
            return _build_result_html(
                result_snapshot, resources_dir, cancel_event
            ), str(resources_dir), result_snapshot

        job = ExportSaveJob(build)
        job.setProperty("generation", generation)
        job.setProperty("source_generation", source_generation)
        job.setProperty("document_token", document_token)
        self._render_jobs.add(job)
        job.completed.connect(self._on_render_completed)
        job.failed.connect(self._on_render_failed)
        job.stopped.connect(self._on_render_stopped)
        job.start()

    def _invalidate_render_jobs(self) -> None:
        self._render_generation += 1
        for job in tuple(self._render_jobs):
            job.cancel()

    def _activate_next_document(self) -> None:
        """Invalidate all callbacks from the previously rendered WebEngine DOM."""
        self._document_generation += 1
        self._active_document_token = str(self._document_generation)
        self._rendered_document_token = ""
        self._pending_document_token = ""
        self._copy_btn.hide()
        if self._bridge is not None:
            self._bridge.set_active_document(self._active_document_token)

    def _is_current_render_signal(self) -> bool:
        job = self.sender()
        return bool(
            not self._closing
            and job in self._render_jobs
            and job.property("generation") == self._render_generation
            and job.property("source_generation") == self._source_generation
        )

    def _on_render_completed(self, payload: object) -> None:
        if not self._is_current_render_signal():
            return
        if not isinstance(payload, tuple) or len(payload) != 3:
            logger.error("忽略无效的结果渲染 payload")
            return
        full_html, resources_path, snapshot = payload
        self._current_snapshot = snapshot
        self._submission_snapshot = snapshot
        self._snapshot_invalidated_by_edit = False
        self.snapshot_ready.emit(self._current_result, snapshot)
        self._set_export_busy(self._export_job is not None)
        web_view = self._ensure_web_view()
        if web_view is None:
            self.webengine_missing.emit()
            return
        job = self.sender()
        document_token = str(job.property("document_token"))
        token_literal = json.dumps(document_token)
        full_html = full_html.replace(
            f'"{_DOCUMENT_TOKEN_PLACEHOLDER}"', token_literal, 1
        )
        self._pending_document_token = document_token
        base_url = QUrl.fromLocalFile(str(resources_path) + "/")
        web_view.setHtml(full_html, base_url)

    @Slot(bool)
    def _on_web_load_finished(self, loaded: bool) -> None:
        if not loaded or self._closing or self._web_view is None:
            return
        self._web_view.page().runJavaScript(
            "typeof _documentToken === 'string' ? _documentToken : ''",
            self._on_loaded_document_token,
        )

    def _on_loaded_document_token(self, token: object) -> None:
        document_token = str(token or "")
        if not (
            not self._closing
            and document_token
            and document_token == self._pending_document_token
            and document_token == self._active_document_token
        ):
            return
        self._rendered_document_token = document_token
        self._pending_document_token = ""
        self._copy_btn.show()
        self._set_copy_busy(self._copy_job is not None or self._copy_js_pending)

    def _on_render_failed(self, error: str) -> None:
        if self._is_current_render_signal():
            logger.error("结果 HTML 构建失败: %s", error)
            self.snapshot_failed.emit(self._current_result)

    def _on_render_stopped(self, job: ExportSaveJob) -> None:
        self._render_jobs.discard(job)
        job.deleteLater()

    def display_text_layout(self, result: Any, options: Any) -> None:
        """按文本块处理选项排版纯文本结果，同时保留逐块可编辑性。

        与 display_result 的「每块独立一行」不同：本方法把 text_blocks 按换行
        模式（keep/merge/smart）分组为段落，每段内块按 block_join_space 拼接，
        并对段首按 chinese_indent 加缩进。关键是：**每个文本块仍渲染为独立的
        ``.ocr-block``（带 data-block-index）**，因此：

        - 双击编辑、悬停联动、左侧 ↔ 右侧高亮（按 index）全部保留；
        - 编辑回写按 content_index 反查 text_block（_on_result_block_edited）
          的契约不变。

        merge 模式段内多个块视觉上连成一行（display:inline），但 DOM 仍是
        多个 .ocr-block；smart 模式按垂直间距分段，段间留空行（margin）。
        """
        if self._closing:
            return
        from vibeocr.classic.recognition_settings import TextBlockOptions

        self._cancel_copy()
        self._invalidate_render_jobs()
        self._source_generation += 1
        self._activate_next_document()
        self._current_result = result
        self._current_snapshot = None
        self._submission_snapshot = None
        self._snapshot_invalidated_by_edit = False
        self._set_export_busy(True)
        options_snapshot = TextBlockOptions.from_dict(options.to_dict())
        generation = self._render_generation
        source_generation = self._source_generation
        document_token = self._active_document_token

        def build(cancel_event, _progress):
            result_snapshot = _capture_stable_result_snapshot(
                result,
                cancel_event,
                include_content_list=False,
                include_images=False,
                include_text_blocks=True,
            )
            result_text_blocks = result_snapshot.text_blocks
            if result_text_blocks:
                body = _build_text_layout_html(
                    result_text_blocks, options_snapshot, cancel_event
                )
            else:
                text = result_snapshot.raw_text
                if text:
                    body = (
                        '<pre style="white-space:pre-wrap;">'
                        f"{html_lib.escape(text)}</pre>"
                    )
                else:
                    body = '<p style="color:#888;">未识别到文字</p>'
            if cancel_event.is_set():
                raise ExportJobCancelled
            resources_dir = _get_resources_dir()
            full_html = _build_full_html(
                body, resources_dir / "katex", resources_dir
            )
            return full_html, str(resources_dir), result_snapshot

        job = ExportSaveJob(build)
        job.setProperty("generation", generation)
        job.setProperty("source_generation", source_generation)
        job.setProperty("document_token", document_token)
        self._render_jobs.add(job)
        job.completed.connect(self._on_render_completed)
        job.failed.connect(self._on_render_failed)
        job.stopped.connect(self._on_render_stopped)
        job.start()

    def update_block_text(self, index: int, text: str) -> None:
        """从外部更新指定块的显示文本（如左侧编辑同步时调用）。

        对 table 块，``text`` 应为新的 ``<table>`` HTML，会重建 ``.ocr-table``
        容器的 innerHTML（替代早期直接 return 不刷新的行为）。
        """
        if not self._web_view:
            return
        escaped = json.dumps(text)
        js = f"""
    (function() {{
        var block = document.getElementById('block-{index}');
        if (!block) return;
        var blockType = block.getAttribute('data-block-type');
        if (blockType === 'table') {{
            // 表格：重建 .ocr-table 容器内容
            var tableBox = block.querySelector('.ocr-table');
            if (tableBox) {{
                tableBox.innerHTML = {escaped};
            }}
        }} else {{
            var contentEl = block.querySelector('p, h1, h2, h3, h4, h5, h6, pre code, ul');
            if (contentEl) {{
                contentEl.innerText = {escaped};
            }} else {{
                block.innerText = {escaped};
            }}
        }}
        block.classList.add('manually-edited');
    }})();
    """
        self._web_view.page().runJavaScript(js)

    def highlight_block(self, index: int) -> None:
        """高亮指定块（-1 取消高亮）"""
        if index == self._highlighted_index:
            return
        self._highlighted_index = index
        if self._web_view:
            js = f"highlightBlock({index})" if index >= 0 else "highlightBlock(-1)"
            self._web_view.page().runJavaScript(js)

    def clear_highlight(self) -> None:
        self.highlight_block(-1)

    def cleanup(self) -> None:
        """显式销毁 QWebEngineView，避免进程退出时 QtWebEngine 崩溃。

        QWebEngineView 的原生渲染进程在 Python 解释器关闭阶段析构会触发
        STATUS_STACK_BUFFER_OVERRUN (0xC0000409)，必须在 Qt 事件循环
        仍在运行时主动销毁。
        """
        self._cancel_copy()
        self._invalidate_render_jobs()
        if self._web_view is not None:
            self._web_view.stop()
            self._web_view.setHtml("")
            self._web_view.setParent(None)

            import shiboken6

            if shiboken6.isValid(self._web_view):
                shiboken6.delete(self._web_view)

            self._web_view = None
            self._channel = None
            self._bridge = None

    def clear(self) -> None:
        self._cancel_copy()
        self._invalidate_render_jobs()
        self._source_generation += 1
        self._activate_next_document()
        self._current_result = None
        self._current_snapshot = None
        self._submission_snapshot = None
        self._snapshot_invalidated_by_edit = False
        self._highlighted_index = -1
        self._copy_btn.hide()
        self._copy_md_btn.hide()
        self._export_docx_btn.hide()
        self._export_xlsx_btn.hide()
        if self._web_view:
            self._web_view.setHtml("")

    def get_result(self) -> Any:
        return self._current_result

    def current_snapshot(self) -> Any:
        """Return the immutable payload backing the currently rendered result."""
        return self._current_snapshot

    def invalidate_snapshot(self) -> None:
        """Freeze export while an incremental model edit is being aggregated."""
        self._cancel_copy()
        self._source_generation += 1
        self._current_snapshot = None
        self._submission_snapshot = None
        self._snapshot_invalidated_by_edit = True
        self._invalidate_render_jobs()
        self._set_export_busy(True)
