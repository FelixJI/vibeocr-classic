# tests/test_indent_processor.py
import pytest

from vibeocr.backend.utils.indent_processor import IndentConfig, IndentProcessor


class TestIndentConfig:
    def test_default_values(self):
        config = IndentConfig()
        assert config.chinese_indent == "2em"
        assert config.chinese_threshold == 0.05


class TestIsChineseText:
    @pytest.fixture
    def processor(self):
        return IndentProcessor()

    def test_pure_chinese(self, processor):
        assert processor.is_chinese_text("这是中文段落") is True

    def test_pure_english(self, processor):
        assert processor.is_chinese_text("This is English paragraph") is False

    def test_mixed_above_threshold(self, processor):
        # "中文内容" 4个中文字符，总长度约20，占比20%>5%
        assert processor.is_chinese_text("这是一些中文和 some English") is True

    def test_mixed_below_threshold(self, processor):
        # 1个中文字符，总长度约30，占比约3%<5%
        assert (
            processor.is_chinese_text("This is a long English paragraph 中 end")
            is False
        )

    def test_empty_string(self, processor):
        assert processor.is_chinese_text("") is False

    def test_whitespace_only(self, processor):
        assert processor.is_chinese_text("   ") is False

    def test_boundary_exactly_5_percent(self, processor):
        # 5个中文字符，总长度100，占比正好5%
        text = "一二三四五" + "a" * 95
        assert processor.is_chinese_text(text) is True


class TestProcessMarkdown:
    @pytest.fixture
    def processor(self):
        return IndentProcessor()

    def test_chinese_paragraph_wrapped(self, processor):
        markdown = "这是中文段落"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' in result
        assert "这是中文段落" in result

    def test_english_paragraph_not_wrapped(self, processor):
        markdown = "This is English"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' not in result
        assert result == "This is English"

    def test_multiple_paragraphs(self, processor):
        markdown = "中文段落\n\nEnglish paragraph"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">中文段落</div>' in result
        assert "English paragraph" in result

    def test_preserves_code_blocks(self, processor):
        markdown = "```\ncode here\n```"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">' not in result

    def test_preserves_tables(self, processor):
        markdown = "| 列1 | 列2 |\n|---|---|\n| 值1 | 值2 |"
        result = processor.process_markdown(markdown)
        # 表格不应被包装
        assert result == markdown

    def test_empty_input(self, processor):
        assert processor.process_markdown("") == ""

    def test_list_items_not_wrapped(self, processor):
        markdown = "- 列表项1\n- 列表项2"
        result = processor.process_markdown(markdown)
        # 列表项不应被包装为段落
        assert '<div class="zh-paragraph">' not in result

    def test_mixed_code_and_chinese(self, processor):
        """代码块和中文段落混合"""
        markdown = "这是中文段落\n\n```\ncode\n```\n\n另一个中文段落"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">这是中文段落</div>' in result
        assert '<div class="zh-paragraph">另一个中文段落</div>' in result
        assert "```\ncode\n```" in result  # 代码块保持不变

    def test_mixed_table_and_chinese(self, processor):
        """表格和中文段落混合"""
        markdown = "中文段落\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">中文段落</div>' in result
        assert "| A | B |" in result  # 表格保持不变

    def test_mixed_list_and_chinese(self, processor):
        """列表和中文段落混合"""
        markdown = "中文段落\n\n- 列表项1\n- 列表项2\n\n另一个中文段落"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">中文段落</div>' in result
        assert '<div class="zh-paragraph">另一个中文段落</div>' in result
        assert "- 列表项1" in result  # 列表保持不变

    def test_multiple_code_blocks_with_chinese(self, processor):
        """多个代码块和中文段落混合"""
        markdown = "开始段落\n\n```python\nprint(1)\n```\n\n中间段落\n\n```bash\necho hello\n```\n\n结束段落"
        result = processor.process_markdown(markdown)
        assert '<div class="zh-paragraph">开始段落</div>' in result
        assert '<div class="zh-paragraph">中间段落</div>' in result
        assert '<div class="zh-paragraph">结束段落</div>' in result
        assert "```python\nprint(1)\n```" in result
        assert "```bash\necho hello\n```" in result


class TestIndentProcessorEdgeCases:
    """HTML 块密度跳过、代码块标记行跳过边界。"""

    @pytest.fixture
    def processor(self):
        from vibeocr.backend.utils.indent_processor import IndentProcessor

        return IndentProcessor()

    def test_many_html_blocks_skipped(self, processor):
        """含 >=3 个 HTML 块级标签时直接返回原文（line 55）。"""
        md = "<div>a</div>\n<table>b</table>\n<p>c</p>\n中文段落"
        assert processor.process_markdown(md) == md

    def test_code_fence_marker_line_preserved(self, processor):
        """单行代码块标记（以```开头但无闭合）原样保留（line 96-97）。"""
        # 不成对的代码围栏标记 + 中文段：整段以 ``` 开头被当作代码块标记跳过
        md = "```python\n中文段落"
        result = processor.process_markdown(md)
        assert result == md  # 原样返回，不包装
