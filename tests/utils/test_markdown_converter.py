"""测试 Markdown 转换器"""


class TestCSSStyles:
    """测试 CSS 样式"""

    def test_css_contains_chinese_indent_style(self):
        """测试 CSS 包含中文段落缩进样式"""
        from vibeocr.backend.utils.markdown_converter import HTML_STYLE

        assert ".zh-paragraph" in HTML_STYLE
        assert "text-indent" in HTML_STYLE

    def test_css_contains_list_indent_style(self):
        """测试 CSS 包含列表嵌套缩进样式"""
        from vibeocr.backend.utils.markdown_converter import HTML_STYLE

        assert "margin-left" in HTML_STYLE
        assert "li p" in HTML_STYLE


class TestMarkdownToHtmlWithIndent:
    """测试 Markdown 转 HTML 的集成功能"""

    def test_chinese_paragraph_has_indent_class(self):
        """测试中文段落有 zh-paragraph 类"""
        from vibeocr.backend.utils.markdown_converter import markdown_to_html

        html = markdown_to_html("这是中文段落")
        # 检查 body 内容中是否有 zh-paragraph div
        assert '<div class="zh-paragraph">' in html

    def test_english_paragraph_no_indent_class(self):
        """测试英文段落没有 zh-paragraph 类"""
        from vibeocr.backend.utils.markdown_converter import markdown_to_html

        html = markdown_to_html("This is English paragraph")
        # 检查 body 内容中是否有 zh-paragraph div（排除 style 中的 CSS 类定义）
        assert '<div class="zh-paragraph">' not in html

    def test_nested_list_structure(self):
        """测试嵌套列表结构"""
        from vibeocr.backend.utils.markdown_converter import markdown_to_html

        markdown = "- 一级\n  - 二级\n    - 三级"
        html = markdown_to_html(markdown)
        # 验证嵌套结构存在
        assert "<ul>" in html
        assert "</ul>" in html

    def test_latex_not_affected(self):
        """测试 LaTeX 公式不受影响"""
        from vibeocr.backend.utils.markdown_converter import markdown_to_html

        markdown = "$$E=mc^2$$"
        html = markdown_to_html(markdown)
        assert "latex-formula" in html

    def test_table_not_affected(self):
        """测试表格不受影响"""
        from vibeocr.backend.utils.markdown_converter import markdown_to_html

        markdown = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = markdown_to_html(markdown)
        assert "<table>" in html


class TestMarkdownConversionFailure:
    """markdown.markdown 抛异常时的回退路径。"""

    def test_conversion_failure_returns_escaped_text(self, monkeypatch):
        """markdown.markdown 异常 + include_style=False 时返回纯 pre 转义文本（line 158）。"""
        import markdown

        from vibeocr.backend.utils import markdown_converter

        def _boom(*_args, **_kwargs):
            raise RuntimeError("conversion failed")

        monkeypatch.setattr(markdown, "markdown", _boom)
        result = markdown_converter.markdown_to_html("a<b>&c", include_style=False)
        # 不含 body，仅 pre 包裹的转义文本
        assert "<body>" not in result
        assert "<pre>" in result
        assert "&lt;" in result

    def test_conversion_failure_with_style(self, monkeypatch):
        """include_style=True + 异常时返回带 style 的转义文本（line 156-158）。"""
        import markdown

        from vibeocr.backend.utils import markdown_converter

        def _boom(*_args, **_kwargs):
            raise RuntimeError("conversion failed")

        monkeypatch.setattr(markdown, "markdown", _boom)
        result = markdown_converter.markdown_to_html("text", include_style=True)
        assert "body" in result  # 含 HTML_STYLE + body 包裹


def test_markdown_to_html_with_custom_extensions():
    """传入自定义 extensions 时跳过默认扩展（line 127->135 分支）。"""
    from vibeocr.backend.utils.markdown_converter import markdown_to_html

    # 传一个最小 extensions 列表，验证不报错且返回 HTML
    result = markdown_to_html("# Title\n\nParagraph", extensions=["nl2br"], include_style=False)
    assert "<h1>" in result
    assert "Paragraph" in result
