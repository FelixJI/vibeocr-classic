"""markdown_converter 补充测试 — 覆盖 extract_plain_text、LaTeX、错误回退等分支"""

from vibeocr.backend.utils.markdown_converter import (
    _process_latex_formulas,
    extract_plain_text,
    markdown_to_html,
)


class TestExtractPlainText:
    def test_empty_input(self):
        assert extract_plain_text("") == ""

    def test_none_input(self):
        assert extract_plain_text("") == ""

    def test_strips_style_tag(self):
        html = "<style>body{color:red}</style><p>Hello</p>"
        assert "body{color:red}" not in extract_plain_text(html)
        assert "Hello" in extract_plain_text(html)

    def test_br_to_newline(self):
        assert extract_plain_text("a<br/>b") == "a\nb"

    def test_p_to_newline(self):
        assert extract_plain_text("<p>a</p><p>b</p>") == "a\nb"

    def test_div_to_newline(self):
        assert extract_plain_text("<div>a</div><div>b</div>") == "a\nb"

    def test_table_tags(self):
        html = "<table><tr><td>a</td><td>b</td></tr></table>"
        text = extract_plain_text(html)
        assert "a" in text
        assert "b" in text

    def test_html_entities(self):
        text = extract_plain_text("&amp; &lt; &gt; &nbsp; &quot;")
        assert "&" in text
        assert "<" in text
        assert ">" in text
        assert '"' in text

    def test_strips_all_tags(self):
        assert "<" not in extract_plain_text("<b>bold</b> <i>italic</i>")

    def test_removes_blank_lines(self):
        html = "<p>a</p>\n\n\n<p>b</p>"
        text = extract_plain_text(html)
        assert "\n\n" not in text


class TestMarkdownToHtmlEdgeCases:
    def test_empty_input(self):
        assert markdown_to_html("") == ""

    def test_include_style_false(self):
        html = markdown_to_html("hello", include_style=False)
        assert "<style>" not in html
        assert "<body>" not in html
        assert "hello" in html

    def test_include_style_true_has_body(self):
        html = markdown_to_html("hello", include_style=True)
        assert "<style>" in html
        assert "<body>" in html

    def test_block_latex_formula(self):
        html = markdown_to_html("$$E=mc^2$$", include_style=False)
        assert "latex-formula" in html
        assert "E=mc^2" in html

    def test_inline_latex_formula(self):
        html = markdown_to_html("value $x+y$", include_style=False)
        assert "latex-inline" in html

    def test_block_formula_html_escape(self):
        html = markdown_to_html("$$a<b&c>$$", include_style=False)
        assert "&lt;" in html
        assert "&amp;" in html
        assert "&gt;" in html


class TestProcessLatexFormulas:
    def test_block_formula(self):
        result = _process_latex_formulas("$$E=mc^2$$")
        assert '<div class="latex-formula">' in result

    def test_inline_formula(self):
        result = _process_latex_formulas("see $x+y$ here")
        assert '<span class="latex-inline">' in result

    def test_no_formula(self):
        text = "plain text without formulas"
        assert _process_latex_formulas(text) == text

    def test_table_pipes_ignored(self):
        text = "|$a|"
        result = _process_latex_formulas(text)
        assert "latex-inline" not in result
