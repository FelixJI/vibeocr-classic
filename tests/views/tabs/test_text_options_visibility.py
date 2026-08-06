# tests/views/tabs/test_text_options_visibility.py
"""验证「文本块处理」选项对纯文本结果的显示既可见又保留逐块可编辑性。

根因：TextBlockProcessor 只重写 result.raw_text，但 display_result 从
content_list 逐块渲染成独立 <div class="ocr-block">，永远不走 raw_text 分支，
导致换行模式/空格/缩进等选项在结果区看不到任何效果（只有复制MD/导出才有效）。

设计要求（用户反馈）：排版只是展示形式变化，块身份（data-block-index）必须
保留——右侧双击编辑、左右高亮联动按 index 仍可命中，编辑回写按 content_index
反查 text_block 的契约不变。因此排版后的 HTML 中每个文本块仍应是独立的
``.ocr-block``，只是视觉分组（keep 逐行 / merge 段内横排 / smart 段落）。
"""

from vibeocr.classic.recognition_result import OCRResult, TextBlock
from vibeocr.classic.recognition_settings import (
    LINE_MODE_KEEP,
    LINE_MODE_MERGE,
    LINE_MODE_SMART,
    TextBlockOptions,
)
from vibeocr.classic.widgets.result_view_widget import _build_text_layout_html


def _plain_text_result() -> OCRResult:
    """通用 OCR（纯文本）风格的 OCRResult：两行文本块。"""
    return OCRResult(
        text_blocks=[
            TextBlock(text="第一行", score=0.95, bbox=(10, 10, 200, 40)),
            TextBlock(text="第二行", score=0.93, bbox=(10, 50, 200, 80)),
        ],
        text_with_scores=[("第一行", 0.95), ("第二行", 0.93)],
        raw_text="第一行\n第二行",
        markdown_text="第一行\n第二行",
        image_height=100,
    )


class TestTextLayoutPreservesBlockIdentity:
    """排版后每个文本块仍是独立的 .ocr-block，可按 index 编辑/联动。"""

    def test_each_block_remains_ocr_block_after_merge(self):
        """merge 模式：两块视觉合并，但 DOM 仍是两个 .ocr-block。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 两个独立 .ocr-block（带各自的 data-block-index）
        assert html.count('class="ocr-block"') == 2
        assert 'data-block-index="0"' in html
        assert 'data-block-index="1"' in html

    def test_each_block_remains_ocr_block_after_keep(self):
        """keep 模式：两块各自成行，仍是两个 .ocr-block。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_KEEP)
        html = _build_text_layout_html(result.text_blocks, opts)

        assert html.count('class="ocr-block"') == 2
        assert 'data-block-index="0"' in html
        assert 'data-block-index="1"' in html

    def test_block_id_is_original_text_blocks_index(self):
        """data-block-index 对应 text_blocks 中的原始下标（编辑回写契约）。

        即使 drop_blank / 排序改变了遍历顺序，index 仍指向原始 text_blocks
        列表的位置（_abs_index），保证 _on_result_block_edited 按 index 反查
        content_list[index] → text_block 的链路不变。
        """
        result = OCRResult(
            text_blocks=[
                TextBlock(text="甲", score=0.9, bbox=(0, 100, 100, 200)),
                TextBlock(text="   ", score=0.9, bbox=(0, 200, 100, 300)),  # 空白
                TextBlock(text="乙", score=0.9, bbox=(0, 300, 100, 400)),
            ],
            text_with_scores=[("甲", 0.9), ("   ", 0.9), ("乙", 0.9)],
        )
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE, drop_blank_blocks=True)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 空白块被过滤，但保留下来的两块 index 仍是原始的 0 和 2
        assert 'data-block-index="0"' in html  # 甲
        assert 'data-block-index="2"' in html  # 乙
        assert 'data-block-index="1"' not in html  # 空白块不渲染


class TestTextLayoutVisualModes:
    """不同换行模式产生不同的视觉分组。"""

    def test_keep_mode_blocks_on_separate_lines(self):
        """keep：每块 block 级（display:block），各自成行。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_KEEP)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 不应出现段内横排分组（ocr-segment 仅 merge/smart 用）
        assert "ocr-segment" not in html
        # 两块各自独立（block 级）
        assert html.count("display:block") == 2

    def test_merge_mode_inline_blocks_in_one_segment(self):
        """merge：所有块横排（inline-block）在单个段内。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 单段，段内块横排
        assert html.count('class="ocr-segment"') == 1
        assert html.count("display:inline-block") == 2

    def test_smart_mode_splits_into_segments_by_gap(self):
        """smart：垂直间距大的块分到不同段（多段）。"""
        result = OCRResult(
            text_blocks=[
                TextBlock(text="段1行1", score=0.9, bbox=(0, 100, 100, 200)),
                TextBlock(text="段1行2", score=0.9, bbox=(0, 210, 100, 310)),
                TextBlock(text="段2行1", score=0.9, bbox=(0, 511, 100, 611)),
            ],
            text_with_scores=[("段1行1", 0.9), ("段1行2", 0.9), ("段2行1", 0.9)],
            image_height=800,
        )
        opts = TextBlockOptions(line_mode=LINE_MODE_SMART)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 两段（段1两块 + 段2一块）
        assert html.count('class="ocr-segment"') == 2
        assert html.count('class="ocr-block"') == 3

    def test_block_join_space_inserts_separator(self):
        """块间加空格：段内块之间插入空格文本节点。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE, block_join_space=True)
        html = _build_text_layout_html(result.text_blocks, opts)

        # 段内两块之间应有空格（</p></div> 后跟空格再 <div）
        assert "</div> <div" in html

    def test_block_no_space_no_separator(self):
        """不加空格：段内块直接相邻（无空格文本节点）。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE, block_join_space=False)
        html = _build_text_layout_html(result.text_blocks, opts)

        assert "</div> <div" not in html

    def test_chinese_indent_at_first_segment_first_block(self):
        """中文缩进：首段首块前置两个全角空格。"""
        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE, chinese_indent=True)
        html = _build_text_layout_html(result.text_blocks, opts)

        assert "\u3000\u3000第一行" in html
        # 第二块不应有缩进
        assert "\u3000\u3000第二行" not in html

    def test_chinese_indent_each_segment_in_smart(self):
        """smart + 缩进：每段首块都加缩进。"""
        result = OCRResult(
            text_blocks=[
                TextBlock(text="段1", score=0.9, bbox=(0, 100, 100, 200)),
                TextBlock(text="段2", score=0.9, bbox=(0, 511, 100, 611)),
            ],
            text_with_scores=[("段1", 0.9), ("段2", 0.9)],
            image_height=800,
        )
        opts = TextBlockOptions(line_mode=LINE_MODE_SMART, chinese_indent=True)
        html = _build_text_layout_html(result.text_blocks, opts)

        assert "\u3000\u3000段1" in html
        assert "\u3000\u3000段2" in html


class TestTextLayoutIntegration:
    """通过 ResultViewWidget.display_text_layout 端到端验证。"""

    def test_display_text_layout_renders_to_webview(self, qapp, qtbot, monkeypatch):
        from vibeocr.classic.widgets.result_view_widget import ResultViewWidget

        widget = ResultViewWidget()
        web_view = type("_V", (), {"setHtml": lambda self, html, url=None: None})()
        monkeypatch.setattr(widget, "_ensure_web_view", lambda: web_view)

        captured: dict = {}

        def _capture(html, url=None):
            captured["html"] = html

        monkeypatch.setattr(web_view, "setHtml", _capture, raising=False)

        result = _plain_text_result()
        opts = TextBlockOptions(line_mode=LINE_MODE_MERGE)
        widget.display_text_layout(result, opts)
        qtbot.waitUntil(lambda: not widget._render_jobs, timeout=2000)

        html = captured.get("html", "")
        # 块身份保留 + 视觉合并
        assert 'data-block-index="0"' in html
        assert 'data-block-index="1"' in html
        assert "ocr-segment" in html


class TestEditPathInLayoutMode:
    """排版模式下双击编辑仍能正确反查到对应的 text_block。

    核心契约：display_text_layout 渲染的 data-block-index 对应原始 text_blocks
    下标；_on_result_block_edited(index) 按 content_index==index 反查 text_block。
    即使 drop_blank 过滤掉中间块、或 merge 让块横排，编辑「块2」应更新块2，
    不应错位到块0。
    """

    def test_edit_block_2_updates_correct_text_block(self, qapp, monkeypatch):
        """drop_blank 过滤掉中间空白块后，编辑保留下来的「乙」（原始 index=2）
        应更新 text_blocks[2]，而非 text_blocks[0]。"""
        from vibeocr.classic.recognition_result import OCRResult, TextBlock
        from vibeocr.classic.views.tabs.single_recognition_tab import (
            SingleRecognitionTab,
        )

        tab = SingleRecognitionTab()
        result = OCRResult(
            text_blocks=[
                TextBlock(text="甲", score=0.9, bbox=(0, 100, 100, 200)),
                TextBlock(text="   ", score=0.9, bbox=(0, 200, 100, 300)),
                TextBlock(text="乙", score=0.9, bbox=(0, 300, 100, 400)),
            ],
            text_with_scores=[("甲", 0.9), ("   ", 0.9), ("乙", 0.9)],
            raw_text="甲\n   \n乙",
        )
        tab._current_ocr_result = result
        tab._plain_text_at_recognition = True
        # 模拟 _on_ocr_finished 的完整流程：_display_result 回填 content_list
        # 并为 text_blocks 补建 content_index（编辑回调按 content_index 反查）。
        tab._display_result(result)
        # update_block_text 触发 WebEngine JS，stub 掉
        monkeypatch.setattr(
            tab._result_widget, "update_block_text", lambda *a, **k: None
        )
        monkeypatch.setattr(
            tab._preview_widget, "set_text_blocks", lambda *a, **k: None
        )

        # 用户在排版视图双击「乙」（原始 index=2）改为「乙改」
        tab._on_result_block_edited(2, "乙改")

        assert result.text_blocks[2].text == "乙改"
        assert result.text_blocks[2].is_manually_edited is True
        # 甲（index=0）不应被误改
        assert result.text_blocks[0].text == "甲"
