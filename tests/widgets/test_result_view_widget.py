"""Tests for result_view_widget block rendering functions."""

import re
import sys
import time
import types
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.classic.widgets.result_view_widget import (
    BLOCK_BORDER_COLORS,
    BLOCK_TYPE_LABELS,
    _build_full_html,
    _render_block,
    _render_code,
    _render_equation,
    _render_fallback,
    _render_list,
    _render_table,
    _render_text,
    _render_title,
)


class _ImmediateExportClient:
    def export_ocr_sync(self, _payload, *, output_path, **_kwargs):
        path = Path(output_path)
        path.write_bytes(b"PK-test")
        return {"output_path": str(path)}


class _FailingExportClient:
    def export_ocr_sync(self, _payload, **_kwargs):
        return {}


class TestRenderBlockTitleAttribute:
    """测试 _render_block 生成的 title 属性。"""

    def test_title_attribute_with_type_and_confidence(self):
        """有类型和置信度时，title 包含两者。"""
        block = {"type": "text", "text": "hello", "confidence": 0.92}
        html = _render_block(block, 0)
        assert 'title="类型: 文本 | 置信度: 92%"' in html

    def test_title_attribute_type_only(self):
        """只有类型没有置信度时，title 只显示类型。"""
        block = {"type": "title", "text": "Chapter 1", "level": 1}
        html = _render_block(block, 1)
        assert "类型: 标题" in html

    def test_title_attribute_confidence_only(self):
        """text 块有置信度时，title 包含置信度。"""
        block = {"text": "no type", "confidence": 0.75}
        html = _render_block(block, 2)
        assert "置信度: 75%" in html

    def test_title_always_present(self):
        """所有块都有 title 属性（至少包含类型）。"""
        block = {"text": "plain text"}
        html = _render_block(block, 3)
        assert "title=" in html

    def test_no_inline_confidence(self):
        """置信度信息只出现在 title 属性中，不内嵌显示。"""
        block = {"type": "text", "text": "hello", "confidence": 0.60}
        html = _render_block(block, 0)
        without_title = re.sub(r' title="[^"]*"', "", html)
        assert "置信度" not in without_title

    def test_table_block_title(self):
        """表格块显示类型标签。"""
        block = {"type": "table", "table_body": "<table><tr><td>data</td></tr></table>"}
        html = _render_block(block, 4)
        assert "类型: 表格" in html

    def test_equation_block_title(self):
        """公式块显示类型标签。"""
        block = {"type": "equation", "text": "E=mc^2"}
        html = _render_block(block, 5)
        assert "类型: 公式" in html

    def test_high_confidence_still_shows_in_title(self):
        """高置信度（>=0.95）也显示在 title 中。"""
        block = {"type": "text", "text": "confident", "confidence": 0.98}
        html = _render_block(block, 6)
        assert "置信度: 98%" in html

    def test_page_idx_in_title(self):
        """有 page_idx 时 title 包含页码信息。"""
        block = {"type": "text", "text": "hello", "page_idx": 3}
        html = _render_block(block, 0)
        assert "页码: 3" in html


class TestRenderBlockAttributes:
    """测试 _render_block 生成的 div 属性。"""

    def test_data_block_index(self):
        """块有正确的 data-block-index 属性。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 42)
        assert 'data-block-index="42"' in html

    def test_id_attribute(self):
        """块有正确的 id 属性。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 7)
        assert 'id="block-7"' in html

    def test_border_color_per_type(self):
        """不同类型有不同边框颜色。"""
        block = {"type": "table", "text": "t"}
        html = _render_block(block, 0)
        assert "#22c55e" in html

    def test_ocr_block_class(self):
        """块有 ocr-block CSS 类。"""
        block = {"type": "text", "text": "hello"}
        html = _render_block(block, 0)
        assert 'class="ocr-block"' in html


class TestRenderTextBlock:
    """测试 _render_text 函数。"""

    def test_plain_text(self):
        block = {"text": "hello world"}
        html = _render_text(block, 0)
        assert html == "<p>hello world</p>"

    def test_html_escaped(self):
        block = {"text": "<script>alert('xss')</script>"}
        html = _render_text(block, 0)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestRenderTitleBlock:
    """测试 _render_title 函数和 text 块 text_level 提升为标题。"""

    def test_title_via_text_level(self):
        """text 块有 text_level 字段时被渲染为标题。"""
        block = {"type": "text", "text_level": 2, "text": "Heading"}
        html = _render_block(block, 0)
        assert "<h2>Heading</h2>" in html

    def test_title_via_level(self):
        """title 块使用 level 字段。"""
        block = {"type": "title", "level": 1, "text": "Chapter"}
        html = _render_block(block, 0)
        assert "<h1>Chapter</h1>" in html

    def test_title_level_capped_at_6(self):
        """标题级别不超过 h6。"""
        block = {"type": "text", "text_level": 99, "text": "Deep"}
        html = _render_title(block, 0)
        assert "<h6>Deep</h6>" in html


class TestRenderTable:
    """测试 _render_table 函数。"""

    def test_table_body(self):
        block = {"table_body": "<table><tr><td>data</td></tr></table>"}
        html = _render_table(block, 0)
        assert 'class="ocr-table"' in html

    def test_table_with_caption_and_footnote(self):
        block = {
            "table_body": "<table><tr><td>d</td></tr></table>",
            "table_caption": ["My Table"],
            "table_footnote": ["Note 1"],
        }
        html = _render_table(block, 0)
        assert "My Table" in html
        assert "Note 1" in html

    def test_table_fallback_html_field(self):
        """兼容旧数据的 html 字段。"""
        block = {"type": "table", "html": "<table><tr><td>data</td></tr></table>"}
        html = _render_table(block, 0)
        assert 'class="ocr-table"' in html

    def test_canonical_table_wins_over_stale_legacy_projection(self):
        from vibeocr.runtime_contracts.contracts.tables import TableCellV1, TableModelV1

        table = TableModelV1(
            table_id="render-table",
            row_count=1,
            column_count=1,
            cells=(
                TableCellV1(
                    cell_id="render-cell",
                    row=0,
                    column=0,
                    text="fresh",
                ),
            ),
        )
        html = _render_table(
            {
                "type": "table",
                "table": table.to_payload(),
                "table_body": "<table><tr><td>stale</td></tr></table>",
            },
            0,
        )

        assert "fresh" in html
        assert "stale" not in html
        assert 'data-table-id="render-table"' in html
        assert 'data-cell-id="render-cell"' in html


class TestRenderEquation:
    """测试 _render_equation 函数。"""

    def test_equation_rendering(self):
        block = {"text": "E=mc^2"}
        html = _render_equation(block, 0)
        assert 'class="math-block"' in html
        assert "data-latex=" in html

    def test_latex_escaped_in_attribute(self):
        block = {"text": "a < b"}
        html = _render_equation(block, 0)
        assert 'data-latex="a &lt; b"' in html

    def test_no_hardcoded_blue_border(self):
        """_render_equation 不应再自带蓝色左边框（#0078d4）。

        外层 _render_block 已为公式块加了橙色左边框作为类型标识；
        此处再叠加会形成"双条色标"，且蓝色与文本蓝混淆。
        """
        block = {"text": "E=mc^2"}
        html = _render_equation(block, 0)
        assert "#0078d4" not in html.lower()
        assert "border-left" not in html

    def test_retains_background_and_styles(self):
        """移除蓝边框后，背景/圆角/字体样式应保留。"""
        block = {"text": "x^2"}
        html = _render_equation(block, 0)
        assert "background:#f8f9fa" in html
        assert "border-radius:4px" in html
        assert "Consolas" in html


class TestFormulaBlockColor:
    """公式块（type=equation/formula）外层边框应为橙色，避免与文字蓝混淆。"""

    def test_equation_block_uses_orange_border(self):
        block = {"type": "equation", "text": "E=mc^2"}
        html = _render_block(block, 0)
        orange = BLOCK_BORDER_COLORS["equation"]
        assert f"border-left:3px solid {orange}" in html

    def test_formula_type_uses_orange_border(self):
        """PaddleX 公式管道输出 type=formula，同样映射到橙色边框。"""
        block = {"type": "formula", "text": "a+b"}
        html = _render_block(block, 0)
        orange = BLOCK_BORDER_COLORS["formula"]
        assert orange == "#f97316"
        assert f"border-left:3px solid {orange}" in html


class TestRenderList:
    """测试 _render_list 函数。"""

    def test_list_items(self):
        block = {"list_items": ["one", "two", "three"]}
        html = _render_list(block, 0)
        assert "<ul" in html
        assert "<li>one</li>" in html
        assert "<li>two</li>" in html
        assert "<li>three</li>" in html


class TestRenderCode:
    """测试 _render_code 函数。"""

    def test_code_with_sub_type(self):
        block = {"code_body": "print('hello')", "sub_type": "python"}
        html = _render_code(block, 0)
        assert "[python]" in html
        assert "print(&#x27;hello&#x27;)" in html or "print(&#39;hello&#39;)" in html

    def test_code_without_sub_type(self):
        block = {"code_body": "echo hi"}
        html = _render_code(block, 0)
        assert "[" not in html
        assert "echo hi" in html


class TestRenderFallback:
    """测试 _render_fallback 函数。"""

    def test_unknown_type_uses_fallback(self):
        block = {"type": "unknown_type", "text": "some text"}
        html = _render_block(block, 0)
        assert "some text" in html

    def test_empty_text_returns_empty(self):
        block = {}
        html = _render_fallback(block, 0)
        assert html == ""


def test_unknown_canonical_table_schema_has_visible_legacy_warning():
    rendered = _render_table(
        {
            "type": "table",
            "table": {"schema_version": 999},
            "table_body": "<table><tr><td>legacy</td></tr></table>",
        },
        0,
    )

    assert 'class="table-schema-warning"' in rendered
    assert "不受支持" in rendered
    assert "legacy" in rendered


class TestBorderColorLookup:
    """测试边框颜色和类型标签查找。"""

    def test_all_known_types_have_colors(self):
        for t in [
            "text",
            "title",
            "table",
            "image",
            "figure",
            "chart",
            "equation",
            "interline_equation",
            "inline_equation",
            "list",
            "code",
            "seal",
        ]:
            assert t in BLOCK_BORDER_COLORS

    def test_all_known_types_have_labels(self):
        for t in [
            "text",
            "title",
            "table",
            "image",
            "figure",
            "chart",
            "equation",
            "interline_equation",
            "inline_equation",
            "list",
            "code",
            "seal",
        ]:
            assert t in BLOCK_TYPE_LABELS


class TestTableNormalizationInRender:
    """_render_table 应规整化表格：剥离 inline style、补齐空单元格。"""

    def test_strips_inline_style_on_render(self):
        """渲染时 PaddleX 自带的 style 属性应被剥离。"""
        block = {
            "table_body": (
                '<table><tr><td style="background:#eee">A</td>'
                '<th style="color:red">B</th></tr></table>'
            )
        }
        html = _render_table(block, 0)
        assert "style" not in html
        assert "<td>A</td>" in html

    def test_fills_missing_cells_on_render(self):
        """不规则行应在渲染时补齐为矩形，避免 Excel 粘贴错位。"""
        block = {
            "table_body": (
                "<table><tr><th>H1</th><th>H2</th></tr><tr><td>only</td></tr></table>"
            )
        }
        html = _render_table(block, 0)
        assert "<td>only</td><td></td>" in html

    def test_no_zebra_stripe_in_css(self):
        """CSS 中不应有斑马纹/底纹（避免原生 copy 带样式）。"""
        from pathlib import Path

        html = _build_full_html("<p>x</p>", Path("resources/katex"))
        assert "nth-child(even)" not in html
        # th 不应有 background
        th_rule = re.search(r"\.ocr-table th\s*\{[^}]*\}", html)
        assert th_rule is None or "background" not in th_rule.group(0)


class TestTableCopyAndSelectionJS:
    """验证表格 copy 拦截与单元格拖选的 JS 已注入页面。"""

    def _full_html(self) -> str:
        from pathlib import Path

        return _build_full_html("<p>x</p>", Path("resources/katex"))

    def test_table_edit_js_emits_stable_table_and_cell_ids(self):
        html = self._full_html()

        assert "onTableCellEditedForDocument" in html
        assert "table[data-table-id]" in html
        assert "data-cell-id" in html

    def test_copy_interceptor_present(self):
        html = self._full_html()
        assert "addEventListener('copy'" in html
        assert "_tableSelToOutput" in html

    def test_cell_selection_js_present(self):
        html = self._full_html()
        assert "_startCellSelect" in html
        assert "_applyTableSelHighlight" in html
        assert "sel-cell" in html

    def test_copy_outputs_clean_html_marker(self):
        """copy 拦截器应输出无属性的 <table>/<td>（setData text/html）。"""
        html = self._full_html()
        assert "setData('text/html'" in html
        assert "setData('text/plain'" in html


class TestFormulaTypeRendering:
    """PaddleX 公式管道输出 type='formula'，应在渲染层归一到公式渲染（KaTeX）。"""

    def test_formula_type_uses_equation_renderer(self):
        """type='formula' 的块应渲染出 .math-block + data-latex（同 equation）。"""
        block = {"type": "formula", "text": "E=mc^2"}
        html = _render_block(block, 0)
        assert 'class="math-block"' in html
        assert "data-latex=" in html

    def test_formula_has_equation_border_color(self):
        block = {"type": "formula", "text": "x^2"}
        html = _render_block(block, 0)
        assert "#f97316" in html  # 与 equation 同色

    def test_formula_has_type_label(self):
        block = {"type": "formula", "text": "a+b"}
        html = _render_block(block, 0)
        assert "类型: 公式" in html


class TestCursorStyling:
    """光标样式：.ocr-block 不再有内联 cursor:pointer；编辑态用 !important。"""

    def test_no_inline_cursor_pointer(self):
        block = {"type": "text", "text": "hi"}
        html = _render_block(block, 0)
        assert "cursor:pointer" not in html

    def test_text_cursor_in_stylesheet(self):
        """样式表应给文本元素设 cursor:text。"""
        from pathlib import Path

        html = _build_full_html("<p>x</p>", Path("resources/katex"))
        assert "cursor: text" in html

    def test_editable_cursor_important(self):
        """[contenteditable=true] 的 cursor:text 必须带 !important 压过内联。"""
        from pathlib import Path

        html = _build_full_html("<p>x</p>", Path("resources/katex"))
        assert "cursor: text !important" in html


class TestEventBindingDecoupledFromQWebChannel:
    """事件监听器必须在顶层绑定，不能依赖 QWebChannel 回调（qwebchannel.js 未加载）。"""

    def _full_html(self) -> str:
        from pathlib import Path

        return _build_full_html("<p>x</p>", Path("resources/katex"))

    def test_dblclick_bound_outside_qwebchannel_callback(self):
        """事件绑定块必须出现在 QWebChannel 守卫之前（顶层绑定，不依赖回调）。

        用 'document.querySelectorAll(\\'.ocr-block\\').forEach' 标记事件绑定块的
        起点，用 'typeof QWebChannel' 标记 QWebChannel 调用块的起点（避免匹配到
        注释中提到的 'new QWebChannel' 字样）。
        """
        html = self._full_html()
        binding_pos = html.find("document.querySelectorAll('.ocr-block').forEach")
        guard_pos = html.find("typeof QWebChannel !== 'undefined'")
        assert binding_pos > 0, "顶层事件绑定块缺失"
        assert guard_pos > 0, "QWebChannel 守卫块缺失"
        assert binding_pos < guard_pos, "事件绑定必须在 QWebChannel 之前（顶层绑定）"
        # dblclick 监听器应在绑定块内（在 QWebChannel 守卫之前）
        dblclick_pos = html.find("addEventListener('dblclick'")
        assert binding_pos < dblclick_pos < guard_pos

    def test_qwebchannel_guarded_by_typeof(self):
        """QWebChannel 调用必须用 typeof 守卫，避免未定义时抛错。"""
        html = self._full_html()
        assert "typeof QWebChannel !== 'undefined'" in html

    def test_bridge_calls_guarded(self):
        """所有 _bridge.* 调用应有 if(_bridge) 守卫（bridge 不可用时不影响编辑）。"""
        html = self._full_html()
        # click 处理器里的 _bridge.onBlockClick 必须有守卫
        assert "if (_bridge) _bridge.onBlockClick" in html

    def test_edit_callback_carries_document_identity(self):
        html = self._full_html()
        assert "onBlockEditedForDocument(_documentToken" in html
        assert "var _documentToken" in html

    def test_formula_in_equation_edit_branch(self):
        """dblclick 的公式编辑分支应包含 'formula' 类型。"""
        html = self._full_html()
        assert "'formula'" in html


class TestKaTeXLoading:
    """KaTeX 外部脚本加载与公式渲染触发。"""

    def _full_html(self) -> str:
        from pathlib import Path

        return _build_full_html("<p>x</p>", Path("resources/katex"))

    def test_katex_uses_absolute_url(self):
        """KaTeX 脚本 URL 必须是 file:/// 绝对路径，而非 file:resources/... 畸形 URL。

        早期版本传相对路径给 QUrl.fromLocalFile 生成畸形 URL，WebEngine 无法加载，
        导致公式显示为原始 LaTeX。
        """
        html = self._full_html()
        assert "file:///" in html, "KaTeX 应使用 file:/// 绝对路径"
        assert "file:resources/" not in html, "不应出现畸形 file:resources/ URL"

    def test_katex_onload_triggers_render(self):
        """KaTeX <script> 应带 onload=renderAllMath()，加载完成即触发渲染。"""
        html = self._full_html()
        assert 'onload="renderAllMath()"' in html

    def test_render_all_math_defined(self):
        """应定义 renderAllMath() 函数供 onload 调用。"""
        html = self._full_html()
        assert "function renderAllMath()" in html

    def test_inline_script_before_katex_script(self):
        """内联 <script>（编辑逻辑）应在 KaTeX 外部 <script> 之前，
        确保 KaTeX 加载失败时不阻塞编辑/光标逻辑。"""
        html = self._full_html()
        inline_pos = html.find("function renderAllMath()")
        # 内联脚本结束 </script> 后才出现 KaTeX 外部 script 标签
        katex_script_pos = html.find('onload="renderAllMath()"')
        assert inline_pos > 0
        assert katex_script_pos > inline_pos


class _InlineScriptParser(HTMLParser):
    """按 HTML 规范提取内联 <script> 文本（不含带 src 的外链脚本）。

    用解析器替代正则过滤：大小写与属性形式均按规范处理，
    规避 CodeQL "Bad HTML filtering regexp" 告警。
    """

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <script> 是 raw text 元素，标签体经由 handle_data 原样送达
        if tag == "script" and not any(name == "src" for name, _ in attrs):
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)


class TestInlineJsSyntaxRegression:
    """回归测试：内联 <script> 必须是合法 JS，否则整个脚本不执行
    （表现为无法编辑 + 公式不渲染）。

    历史根因：_build_full_html 是个 f-string，里面写了 ``parts.join('\\n\\n')``
    但 Python 把 ``\\n`` 当转义符处理成真换行，导致 JS 单引号字符串字面量
    跨行 → SyntaxError → 整个内联脚本不执行。
    """

    def _inline_script(self) -> str:
        from pathlib import Path

        html = _build_full_html("<p>x</p>", Path("resources/katex"))
        parser = _InlineScriptParser()
        parser.feed(html)
        parser.close()
        assert parser.scripts, "应存在内联 <script>"
        return parser.scripts[0]

    def test_no_raw_newline_in_single_quoted_js_strings(self):
        """JS 单引号字符串字面量内不得出现裸换行（会破坏整个脚本解析）。

        检查 join('\\n') 等：在生成的 JS 里应是 ``join('\\n')``（反斜杠+n），
        而非跨真实行。
        """
        js = self._inline_script()
        # join 调用应为 join('\\n...') —— 反斜杠 + 字母 n，不是真换行
        for m in re.finditer(r"\.join\('([^']*)'\)", js):
            inner = m.group(1)
            # 不应含真实换行符（\n 字节）
            assert "\n" not in inner, (
                f"join() 字符串含真实换行会破坏 JS 语法: {inner!r}"
            )
        # texts.join('\t') 同理
        for m in re.finditer(r"join\('(\\\\t|[^']*)'\)", js):
            assert "\t" not in m.group(1), "join() 字符串含真实制表符"

    def test_node_syntax_check(self):
        """若有 node，做真正的 JS 语法检查（最严格）。"""
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if node is None:
            pytest.skip("node 不可用，跳过 JS 语法检查")
        js = self._inline_script()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, (
                f"内联 JS 语法错误（会导致整个脚本不执行，无法编辑+公式不渲染）:\n"
                f"{r.stderr}"
            )
        finally:
            from pathlib import Path

            Path(path).unlink(missing_ok=True)


class TestResultViewExportButtons:
    """结果区工具栏导出/复制按钮测试。"""

    @pytest.fixture
    def app(self, qtbot):
        return QApplication.instance() or QApplication([])

    @pytest.fixture
    def widget(self, app, qtbot):
        from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

        w = ResultViewWidget(utility_client=_ImmediateExportClient())
        qtbot.addWidget(w)
        return w

    def _make_result(
        self, markdown_text="# 标题\n\n正文段落", raw_text="标题\n正文段落"
    ):
        return SimpleNamespace(
            content_list=[],
            markdown_text=markdown_text,
            raw_text=raw_text,
            html_text="",
            text_with_scores=[],
            images={},
        )

    @staticmethod
    def _fake_clipboard(monkeypatch):
        """注入一个可读写的假剪贴板（避免依赖 Windows COM 剪贴板可用性）。"""

        class FakeClipboard:
            def __init__(self):
                self._text = ""
                self.mime = None

            def setText(self, text):
                self._text = text
                self.mime = None

            def text(self):
                return self._text

            def setMimeData(self, mime):
                self.mime = mime
                self._text = mime.text() if mime is not None else ""

        fake = FakeClipboard()

        from PySide6.QtGui import QGuiApplication

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: fake)
        return fake

    def test_copy_markdown_to_clipboard(self, widget, qtbot, monkeypatch):
        """复制为 Markdown：剪贴板内容 == markdown_text。"""
        result = self._make_result(markdown_text="# H1\n内容")
        # 绕过 WebEngine 渲染，直接设 _current_result
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_markdown()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        assert fake.text() == "# H1\n内容"

    def test_copy_markdown_falls_back_to_raw(self, widget, qtbot, monkeypatch):
        """无 markdown_text 时回退到 raw_text。"""
        result = self._make_result(markdown_text="", raw_text="纯文本")
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_markdown()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        assert fake.text() == "纯文本"

    def test_copy_markdown_no_result_is_noop(self, widget, qtbot, monkeypatch):
        """无结果时不报错、不写剪贴板。"""
        widget._current_result = None
        fake = self._fake_clipboard(monkeypatch)

        fake.setText("SENTINEL")
        widget._on_copy_markdown()
        assert fake.text() == "SENTINEL"

    def test_copy_after_snapshot_invalidation_rebuilds_latest_plain_and_markdown(
        self, widget, qtbot, monkeypatch
    ):
        """A pending aggregate rebuild must not expose the pre-edit copy payload."""
        from vibeocr.classic.recognition_result import TextBlock
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        result = self._make_result(
            markdown_text="old aggregate", raw_text="old aggregate"
        )
        result.text_blocks = [TextBlock("old block", 1.0, None)]
        result.content_list = [{"type": "text", "text": "old block"}]
        widget._current_result = result
        widget._current_snapshot = snapshot_ocr_result(
            result, include_content_list=True, include_text_blocks=True
        )
        result.text_blocks[0].text = "latest accepted edit"
        result.content_list[0]["text"] = "latest accepted edit"
        widget.invalidate_snapshot()
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_markdown()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        assert fake.text() == "latest accepted edit"

        fake.setText("SENTINEL")
        widget._on_copy_text()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        assert fake.text() == "latest accepted edit"

    def test_copy_after_snapshot_invalidation_uses_latest_table_html(
        self, widget, qtbot, monkeypatch
    ):
        """Rich HTML and plain table payloads both reflect the accepted edit."""
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        result = self._make_result(
            markdown_text="old aggregate", raw_text="old aggregate"
        )
        result.content_list = [
            {
                "type": "table",
                "table_body": "<table><tr><td>old</td></tr></table>",
            }
        ]
        result.text_blocks = []
        widget._current_result = result
        widget._current_snapshot = snapshot_ocr_result(result)
        result.content_list[0]["table_body"] = (
            "<table><tr><td>latest accepted edit</td></tr></table>"
        )
        widget.invalidate_snapshot()
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_text()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)

        assert fake.mime is not None
        assert "latest accepted edit" in fake.mime.text()
        assert "latest accepted edit" in fake.mime.html()
        assert "old" not in fake.mime.html()

    def test_50k_invalid_snapshot_copy_is_async_and_eventually_latest(
        self, widget, qtbot, monkeypatch
    ):
        from tests.qt_responsiveness import assert_qt_event_loop_responsive
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        result = self._make_result(
            markdown_text="old aggregate", raw_text="old aggregate"
        )
        result.content_list = [
            {"type": "text", "text": f"line-{index}"} for index in range(50_000)
        ]
        result.text_blocks = []
        widget._current_result = result
        widget._current_snapshot = snapshot_ocr_result(result)
        result.content_list[-1]["text"] = "latest accepted edit"
        widget.invalidate_snapshot()
        fake = self._fake_clipboard(monkeypatch)

        started = time.perf_counter()
        widget._on_copy_markdown()
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms < 150
        assert widget._copy_md_btn.isEnabled() is False
        assert_qt_event_loop_responsive(
            qtbot, in_flight=lambda: widget._copy_job is not None
        )
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=15_000)
        assert widget._copy_md_btn.isEnabled() is True
        assert "latest accepted edit" in fake.text()
        assert "old aggregate" not in fake.text()

    def test_late_javascript_copy_callback_is_dropped_after_document_switch(
        self, widget, monkeypatch
    ):
        class FakePage:
            def __init__(self):
                self.callbacks = []

            def runJavaScript(self, _script, callback):
                self.callbacks.append(callback)

        class FakeWebView:
            def __init__(self):
                self.fake_page = FakePage()

            def page(self):
                return self.fake_page

        web = FakeWebView()
        widget._web_view = web
        widget._current_result = self._make_result(raw_text="current")
        widget._active_document_token = "document-1"
        widget._rendered_document_token = "document-1"
        fake = self._fake_clipboard(monkeypatch)
        fake.setText("SENTINEL")

        widget._on_copy_text()
        callback = web.fake_page.callbacks.pop()
        widget._cancel_copy()
        widget._activate_next_document()
        callback(
            {
                "documentToken": "document-1",
                "html": "",
                "text": "late old document text",
            }
        )

        assert fake.text() == "SENTINEL"

    def test_copy_text_token_mismatch_shows_retry_toast(self, widget, monkeypatch):
        """token 失配时（结果在点击后被刷新），「复制文本」不写剪贴板但
        给出重试提示 toast，避免用户点击后毫无反馈。"""

        class FakePage:
            def __init__(self):
                self.callbacks = []

            def runJavaScript(self, _script, callback):
                self.callbacks.append(callback)

        class FakeWebView:
            def __init__(self):
                self.fake_page = FakePage()

            def page(self):
                return self.fake_page

        web = FakeWebView()
        widget._web_view = web
        widget._current_result = self._make_result(raw_text="stale")
        # 点击时 token 匹配（按钮可点）
        widget._active_document_token = "doc-1"
        widget._rendered_document_token = "doc-1"
        fake = self._fake_clipboard(monkeypatch)
        fake.setText("SENTINEL")
        toasts: list[str] = []
        monkeypatch.setattr(
            widget, "_show_copy_toast", lambda msg="x": toasts.append(msg)
        )

        widget._on_copy_text()
        callback = web.fake_page.callbacks.pop()
        # 点击后结果被刷新：active token 变了，回调带的还是旧 token
        widget._active_document_token = "doc-2"
        callback({"documentToken": "doc-1", "html": "", "text": "old"})

        # 不写剪贴板
        assert fake.text() == "SENTINEL"
        # 但给出重试提示
        assert any("重新复制" in t for t in toasts)

    def test_copy_text_empty_payload_shows_no_content_toast(self, widget, monkeypatch):
        """结果无文本无表格时（如纯图片），「复制文本」给出「无可复制内容」提示。"""

        class FakePage:
            def __init__(self):
                self.callbacks = []

            def runJavaScript(self, _script, callback):
                self.callbacks.append(callback)

        class FakeWebView:
            def __init__(self):
                self.fake_page = FakePage()

            def page(self):
                return self.fake_page

        web = FakeWebView()
        widget._web_view = web
        widget._current_result = self._make_result(raw_text="")
        widget._active_document_token = "doc-1"
        widget._rendered_document_token = "doc-1"
        self._fake_clipboard(monkeypatch)
        toasts: list[str] = []
        monkeypatch.setattr(
            widget, "_show_copy_toast", lambda msg="x": toasts.append(msg)
        )

        widget._on_copy_text()
        callback = web.fake_page.callbacks.pop()
        callback({"documentToken": "doc-1", "html": "", "text": ""})

        assert any("无可复制内容" in t for t in toasts)

    def test_copy_markdown_empty_result_shows_toast(self, widget, qtbot, monkeypatch):
        """结果 markdown_text 与 raw_text 均为空时，复制MD 提示「无可复制内容」。"""
        result = self._make_result(markdown_text="", raw_text="")
        widget._current_result = result
        self._fake_clipboard(monkeypatch)
        toasts: list[str] = []
        monkeypatch.setattr(
            widget, "_show_copy_toast", lambda msg="x": toasts.append(msg)
        )

        widget._on_copy_markdown()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        assert any("无可复制内容" in t for t in toasts)

    def test_copy_text_with_table_writes_rich_mime(self, widget, qtbot, monkeypatch):
        """结果含表格时，「复制文本」写入 HTML + Tab 文本 + CF_HTML。

        验证：剪贴板 plain 文本含 Tab 分隔（Excel 粘贴行列对齐），HTML 含
        <table>（Word 粘贴原生表格），CF_HTML 含头部标记。
        """
        result = self._make_result(
            markdown_text="",
            raw_text="",
        )
        # 注入表格：content_list 里带 table 块（覆盖前后端分离下表格来源）
        result.content_list = [
            {
                "type": "table",
                "table_body": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>",
            },
        ]
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)

        widget._on_copy_text()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)

        # 走富剪贴板分支：mime 非空
        assert fake.mime is not None
        # Tab 分隔纯文本：Excel 粘贴行列对齐
        plain = fake.mime.text()
        assert "A\tB" in plain
        assert "1\t2" in plain
        # HTML 含 <table>：Word 粘贴原生表格
        html = fake.mime.html()
        assert "<table>" in html
        assert "<td>A</td>" in html
        # CF_HTML 头部（Word/Excel 专用格式）
        cf_html = fake.mime.data("HTML Format").data().decode("utf-8")
        assert "Version:0.9" in cf_html
        assert "StartFragment" in cf_html

    def test_copy_text_no_table_falls_back_to_plain(self, widget, qtbot, monkeypatch):
        """无表格时「复制文本」不写 mime（回退到 WebEngine JS 选区文本）。

        这里 _current_result 无表格、web_view 未创建，_on_copy_text 应走
        `if not self._web_view: return` 分支，不写剪贴板、不抛异常。
        """
        result = self._make_result(raw_text="普通文本，无表格")
        widget._current_result = result
        fake = self._fake_clipboard(monkeypatch)
        fake.setText("SENTINEL")

        widget._on_copy_text()
        qtbot.waitUntil(lambda: widget._copy_job is None, timeout=2000)
        # WebEngine 尚未加载时由后台稳定快照提供纯文本。
        assert fake.mime is None
        assert fake.text() == "普通文本，无表格"

    def test_plain_text_render_once_copy_does_not_hit_refresh_toast(
        self, widget, qtbot, monkeypatch
    ):
        """纯文本结果经 display_text_layout 单次渲染完成后，立即点「复制文本」，
        异步 JS 回调返回的 token 与当前活动 token 匹配 → 不应出现「结果已刷新」。

        本测试模拟修复后的端到端流程：display_text_layout（仅渲染一次）→
        loadFinished 回填 _rendered_document_token → 复制 → 回调匹配 → 写入剪贴板。
        修复前会先 display_result（token A）再 display_text_layout（token B），
        回调带旧 token A 触发 toast。
        """
        from vibeocr.classic.recognition_result import TextBlock
        from vibeocr.classic.recognition_settings import TextBlockOptions

        class FakePage:
            def __init__(self, widget_ref):
                self._widget_ref = widget_ref
                self.copy_callbacks = []

            def runJavaScript(self, script, callback):
                if "_documentToken" in script:
                    # 加载完成后的 token 探测：返回 setHtml 写入的活动 token。
                    # 该 token == display_text_layout 调用时的 _active_document_token。
                    callback(self._widget_ref._active_document_token)
                else:
                    # 复制载荷查询（getCopyPayload）：由测试显式触发回调。
                    self.copy_callbacks.append(callback)

        class FakeWebView:
            def __init__(self, widget_ref):
                self._widget_ref = widget_ref
                self.fake_page = FakePage(widget_ref)
                self.set_html_calls = []

            def page(self):
                return self.fake_page

            def setHtml(self, html, base_url):
                # 记录 setHtml（占位符已被替换为活动 token），并同步触发 loadFinished。
                self.set_html_calls.append(html)
                # _on_render_completed 已设置 _pending_document_token；
                # 模拟 WebEngine 加载完成 → JS 读取 _documentToken 回填 rendered token。
                self._widget_ref._on_web_load_finished(True)

        result = self._make_result(raw_text="第一行\n第二行")
        result.text_blocks = [
            TextBlock("第一行", 0.95, (0, 0, 10, 10)),
            TextBlock("第二行", 0.93, (0, 10, 10, 20)),
        ]
        options = TextBlockOptions()

        fake_view = FakeWebView(widget)
        # 桩掉惰性 WebEngine 创建，直接返回我们的 fake view，并同步写入 _web_view
        # （_on_web_load_finished 用 self._web_view 做非空守卫）。
        widget._web_view = fake_view
        monkeypatch.setattr(widget, "_ensure_web_view", lambda: fake_view)

        widget.display_text_layout(result, options)

        # 等待渲染作业完成并触发 setHtml（进而触发 loadFinished 回填 rendered token）。
        qtbot.waitUntil(lambda: len(fake_view.set_html_calls) > 0, timeout=3000)
        # loadFinished 已在 setHtml 内同步触发，此时 _rendered_document_token 应已回填。
        assert widget._rendered_document_token == widget._active_document_token, (
            "单次渲染后 rendered token 应等于 active token（无双重 bump）"
        )
        assert widget._rendered_document_token != ""
        # parent 从未 show()，isVisible() 恒为 False 无法反映“显示状态”；
        # isHidden() 才可靠：show() 后为 False、hide() 后为 True。
        assert not widget._copy_btn.isHidden(), (
            "单次渲染并回填 rendered token 后，复制按钮应已 show()"
        )

        toasts: list[str] = []
        monkeypatch.setattr(
            widget, "_show_copy_toast", lambda msg="x": toasts.append(msg)
        )
        fake_clip = self._fake_clipboard(monkeypatch)
        fake_clip.setText("SENTINEL")

        widget._on_copy_text()
        # _on_copy_text 调 getCopyPayload()，回调挂在 fake_page.copy_callbacks 上。
        assert fake_view.fake_page.copy_callbacks, "应发起一次 JS 复制载荷查询"
        callback = fake_view.fake_page.copy_callbacks.pop()
        # 回调携带当前活动 token（匹配）。
        callback(
            {
                "documentToken": widget._active_document_token,
                "html": "",
                "text": "第一行\n第二行",
            }
        )

        assert fake_clip.text() == "第一行\n第二行", "匹配 token 应写入剪贴板"
        assert not any("重新复制" in t for t in toasts), (
            "单次渲染后复制不应出现「结果已刷新」toast"
        )

    def test_buttons_hidden_initially(self, widget):
        """初始（无结果）三个新按钮隐藏。

        用 isHidden() 而非 isVisible()：父窗口从未 show()，isVisible() 恒为
        False（弱断言）；isHidden() 仅在显式 hide() 后为 True，能真正验证
        _setup_ui 里的 btn.hide() 生效。
        """
        assert widget._copy_md_btn.isHidden() is True
        assert widget._export_docx_btn.isHidden() is True
        assert widget._export_xlsx_btn.isHidden() is True

    def test_export_docx_creates_file(self, widget, qtbot, monkeypatch, tmp_path):
        """导出 Word：mock 另存为对话框，断言生成 .docx 文件。"""
        result = self._make_result(raw_text="导出测试内容")
        widget._current_result = result
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        widget._current_snapshot = snapshot_ocr_result(result)

        out = tmp_path / "out.docx"
        # mock QFileDialog.getSaveFileName 返回 (路径, 过滤)
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QFileDialog",
            type(
                "F",
                (),
                {"getSaveFileName": staticmethod(lambda *a, **k: (str(out), ""))},
            ),
            raising=False,
        )
        # mock QMessageBox 避免弹窗阻塞
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QMessageBox",
            type(
                "M",
                (),
                {
                    "information": staticmethod(lambda *a, **k: None),
                    "warning": staticmethod(lambda *a, **k: None),
                },
            ),
            raising=False,
        )
        widget._on_export_file("docx")
        qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)
        assert out.exists()
        # docx 是 zip 包，文件头 PK
        assert out.read_bytes()[:2] == b"PK"

    def test_export_xlsx_creates_file(self, widget, qtbot, monkeypatch, tmp_path):
        """导出 Excel：断言生成 .xlsx 文件。"""
        result = self._make_result(raw_text="表格导出测试")
        widget._current_result = result
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        widget._current_snapshot = snapshot_ocr_result(result)

        out = tmp_path / "out.xlsx"
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QFileDialog",
            type(
                "F",
                (),
                {"getSaveFileName": staticmethod(lambda *a, **k: (str(out), ""))},
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QMessageBox",
            type(
                "M",
                (),
                {
                    "information": staticmethod(lambda *a, **k: None),
                    "warning": staticmethod(lambda *a, **k: None),
                },
            ),
            raising=False,
        )
        widget._on_export_file("xlsx")
        qtbot.waitUntil(lambda: widget._export_job is None, timeout=2000)
        assert out.exists()
        # xlsx 也是 zip 包
        assert out.read_bytes()[:2] == b"PK"

    def test_export_cancel_is_noop(self, widget, qtbot, monkeypatch, tmp_path):
        """用户取消对话框（返回空路径）不报错、不生成文件。"""
        result = self._make_result(raw_text="取消测试")
        widget._current_result = result
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        widget._current_snapshot = snapshot_ocr_result(result)

        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QFileDialog",
            type("F", (), {"getSaveFileName": staticmethod(lambda *a, **k: ("", ""))}),
            raising=False,
        )
        out = tmp_path / "should_not_exist.docx"
        widget._on_export_file("docx")
        assert not out.exists()

    def test_export_no_result_is_noop(self, widget, qtbot):
        """无结果时导出不报错。"""
        widget._current_result = None
        widget._on_export_file("docx")  # 不应抛异常

    def test_export_failure_shows_warning(self, widget, qtbot, monkeypatch, tmp_path):
        """ExportService.export 返回 False 时走 warning 分支，不抛异常。"""
        result = self._make_result(raw_text="失败测试")
        widget._current_result = result
        from vibeocr.classic.utils.export_jobs import snapshot_ocr_result

        widget._current_snapshot = snapshot_ocr_result(result)

        out = tmp_path / "fail.docx"
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QFileDialog",
            type(
                "F",
                (),
                {"getSaveFileName": staticmethod(lambda *a, **k: (str(out), ""))},
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "vibeocr.classic.widgets.result_view_widget.QMessageBox",
            type(
                "M",
                (),
                {
                    "information": staticmethod(lambda *a, **k: None),
                    "warning": staticmethod(lambda *a, **k: None),
                },
            ),
            raising=False,
        )
        # 让注入的 v2 utility client 返回失败。
        widget._utility_client = _FailingExportClient()
        # 不应抛异常
        widget._on_export_file("docx")
        qtbot.waitUntil(lambda: widget._export_job is None, timeout=3000)
        # 失败时不应写出文件
        assert not out.exists()


class TestResultViewPrewarmWebEngine:
    """prewarm_webengine：窗口显示后延迟预热 WebEngine，避免首次截图结果前闪烁。

    见 .superpowers/sdd/fix-task2-brief.md：QWebEngineView 惰性创建于首次结果渲染
    的 GUI 线程（_ensure_web_view 内 QWebEngineView(self) + layout.addWidget），
    触发 Chromium 冷启动 + 父级重排。prewarm_webengine 把该成本前移到启动空闲片段。
    """

    @pytest.fixture
    def app(self, qtbot):
        return QApplication.instance() or QApplication([])

    @pytest.fixture
    def widget(self, app, qtbot):
        from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

        w = ResultViewWidget(utility_client=_ImmediateExportClient())
        qtbot.addWidget(w)
        return w

    def test_prewarm_webengine_invokes_ensure_web_view_once_and_is_idempotent(
        self, widget, monkeypatch
    ):
        """prewarm_webengine 第一次创建 _web_view 并 addWidget；第二次幂等不重复创建。

        桩掉 _ensure_web_view 内部延迟 import 的 QWebEngineView/QWebChannel（避免
        在无显示环境真实拉起 Chromium），让真实 _ensure_web_view 逻辑跑通：首次
        构造视图 + layout.addWidget，二次命中 ``if self._web_view is not None: return``
        幂等守卫。用 addWidget 计数 + 视图对象引用验证幂等性。
        """
        add_widget_calls = []

        class _FakePage:
            def setWebChannel(self, channel):
                pass

        class _FakeSignal:
            def connect(self, fn):
                pass

        class _FakeWebView:
            # _ensure_web_view 以属性方式访问 loadFinished（Qt 信号），故需为类属性。
            loadFinished = _FakeSignal()

            def __init__(self, parent):
                self._parent = parent

            def page(self):
                return _FakePage()

        class _FakeWebChannel:
            def __init__(self, parent):
                pass

            def registerObject(self, name, obj):
                pass

        # 桩掉 _ensure_web_view 内 ``from PySide6.QtWebChannel import QWebChannel``
        # 与 ``from PySide6.QtWebEngineWidgets import QWebEngineView`` 的延迟 import。
        webchannel_mod = types.ModuleType("PySide6.QtWebChannel")
        webchannel_mod.QWebChannel = _FakeWebChannel
        monkeypatch.setitem(sys.modules, "PySide6.QtWebChannel", webchannel_mod)
        webengine_mod = types.ModuleType("PySide6.QtWebEngineWidgets")
        webengine_mod.QWebEngineView = _FakeWebView
        monkeypatch.setitem(sys.modules, "PySide6.QtWebEngineWidgets", webengine_mod)
        # 桩掉 layout.addWidget 以计数「加入布局」次数（即触发父级重排的次数）。
        # 不调用真实 addWidget：_FakeWebView 非 QWidget 子类，真实调用会类型失败；
        # 这里只需计数 _ensure_web_view 是否走到 addWidget 这一步。
        real_layout = widget.layout()

        def counting_add_widget(child):
            add_widget_calls.append(child)

        monkeypatch.setattr(real_layout, "addWidget", counting_add_widget)

        # 第一次预热：应创建视图并 addWidget 一次。
        widget.prewarm_webengine()
        assert widget._web_view is not None, "首次预热后应已创建 _web_view"
        assert len(add_widget_calls) == 1, "首次预热应仅 addWidget 一次（一次重排）"
        first_view = widget._web_view

        # 第二次预热：_ensure_web_view 幂等守卫应直接 return，不重复创建/加入。
        widget.prewarm_webengine()
        assert widget._web_view is first_view, "二次预热不应替换已存在的 _web_view"
        assert len(add_widget_calls) == 1, (
            "二次预热应幂等：_web_view 已存在，不应再次 addWidget"
        )

    def test_prewarm_webengine_respects_closing_guard(self, widget, monkeypatch):
        """_closing 为真时 prewarm_webengine 不应创建 _web_view。

        桩掉 _ensure_web_view（closing 守卫应在调用 _ensure_web_view 之前生效），
        验证 prewarm 在关闭期间根本不触发创建路径。
        """
        calls = []
        monkeypatch.setattr(widget, "_ensure_web_view", lambda: calls.append(1) or None)

        widget.set_closing(True)
        widget.prewarm_webengine()

        assert calls == [], "_closing 为真时不应调用 _ensure_web_view"
        assert widget._web_view is None, "closing 期间不应创建 _web_view"
