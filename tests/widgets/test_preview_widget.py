"""PreviewWidget 统一预览组件测试"""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QScrollArea

from vibeocr.backend.models.ocr_result import TextBlock
from vibeocr.classic.widgets.preview_widget import PreviewWidget


class TestPreviewWidgetBasic:
    def test_creation(self, qapp):
        widget = PreviewWidget()
        assert widget._pixmap is None

    def test_set_pixmap(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        assert widget._pixmap is not None

    def test_original_pixmap_after_set(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        pix = widget.original_pixmap()
        assert pix is not None
        assert not pix.isNull()

    def test_original_pixmap_none_after_clear(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()
        assert widget.original_pixmap() is None

    def test_clear(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()
        assert widget._pixmap is None
        assert widget._text_blocks == []

    def test_custom_empty_text(self, qapp):
        widget = PreviewWidget(empty_text="自定义文案")
        assert widget._empty_text == "自定义文案"


class TestPreviewWidgetTextBlocks:
    def test_set_text_blocks(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        blocks = [
            TextBlock(text="Hello", score=0.95, bbox=(10, 20, 200, 50)),
            TextBlock(text="World", score=0.60, bbox=(10, 60, 200, 90)),
        ]
        widget.set_text_blocks(blocks)
        assert widget._text_blocks == blocks

    def test_set_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
            {"type": "table", "text": "data", "bbox": [10, 60, 200, 90]},
        ]
        widget.set_content_list(content)
        assert widget._content_list == content


class TestPreviewWidgetFileLoading:
    def test_load_image_file(self, qapp, qtbot, temp_image_file):
        widget = PreviewWidget()
        widget.load_file(str(temp_image_file))
        qtbot.waitUntil(lambda: not widget._image_load_jobs.is_running, timeout=2000)
        assert widget._original_pixmap is not None
        assert widget._total_pages == 1

    def test_has_scroll_area(self, qapp):
        widget = PreviewWidget()
        scroll_areas = widget.findChildren(QScrollArea)
        assert len(scroll_areas) >= 1


class TestPreviewWidgetHighlights:
    def test_highlight_block_no_crash(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.highlight_block(0)
        widget.highlight_block(-1)

    def test_clear_highlight(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear_highlight()
        assert widget._highlight_block_index == -1

    def test_highlight_block_with_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
        ]
        widget.set_content_list(content)
        widget.highlight_block(0)
        assert widget._highlight_block_index == 0


class TestPreviewWidgetSignals:
    """信号相关测试（原 tests/test_preview_widget.py）"""

    def test_click_without_pixmap_emits_signal(self, qapp, qtbot):
        """无图片时点击触发 screenshot_requested 信号。"""
        widget = PreviewWidget()
        widget.show()
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.screenshot_requested, timeout=1000):

            class MockEvent:
                def button(self):
                    return Qt.MouseButton.LeftButton

            widget._on_label_click(MockEvent())

    def test_click_with_pixmap_no_signal(self, qapp, sample_pixmap, qtbot):
        """有图片时点击不触发信号。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.show()
        qtbot.addWidget(widget)

        with qtbot.assertNotEmitted(widget.screenshot_requested, wait=100):

            class MockEvent:
                def button(self):
                    return Qt.MouseButton.LeftButton

            widget._on_label_click(MockEvent())

    def test_image_changed_signal_on_set(self, qapp, sample_pixmap, qtbot):
        """设置图片时发送 image_changed 信号。"""
        widget = PreviewWidget()
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.image_changed, timeout=1000):
            widget.set_pixmap(sample_pixmap)

    def test_image_changed_signal_on_clear(self, qapp, sample_pixmap, qtbot):
        """清除图片时发送 image_changed 信号。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        qtbot.addWidget(widget)

        with qtbot.waitSignal(widget.image_changed, timeout=1000):
            widget.clear()


class TestPreviewWidgetTableHitTest:
    """块类型模式下的命中测试与反查逻辑。"""

    def test_hit_test_type_block_hits_table(self, qapp, sample_pixmap):
        """_hit_test_type_block 应命中预设的表格矩形并返回 block_type。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # 直接构造命中矩形，避免依赖布局时序（_update_type_overlay 需有效尺寸）
        widget._type_screen_rects = [
            (0, QRectF(10, 10, 100, 80), "table"),
            (1, QRectF(200, 10, 100, 50), "text"),
        ]
        # 命中表格区域
        cl_idx, block_type = widget._hit_test_type_block(50, 40)
        assert cl_idx == 0
        assert block_type == "table"
        # 命中文本区域
        cl_idx, block_type = widget._hit_test_type_block(230, 30)
        assert cl_idx == 1
        assert block_type == "text"

    def test_hit_test_type_block_miss(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._type_screen_rects = [(0, QRectF(10, 10, 50, 50), "table")]
        cl_idx, block_type = widget._hit_test_type_block(500, 500)
        assert cl_idx == -1
        assert block_type == ""

    def test_find_text_block_by_content_index(self, qapp, sample_pixmap):
        """_find_text_block_by_content_index 按 content_index 反查 text_blocks。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        blocks = [
            TextBlock(text="A", score=0.9, bbox=None, content_index=0),
            TextBlock(text="B", score=0.9, bbox=None, content_index=2),
        ]
        widget.set_text_blocks(blocks)
        assert widget._find_text_block_by_content_index(2) == 1
        assert widget._find_text_block_by_content_index(0) == 0
        assert widget._find_text_block_by_content_index(99) == -1
        assert widget._find_text_block_by_content_index(-1) == -1

    def test_type_screen_rects_cleared_on_set_pixmap(self, qapp, sample_pixmap):
        """切换图片时 _type_screen_rects 应被重置，避免残留命中数据。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget._type_screen_rects = [(0, QRectF(0, 0, 10, 10), "table")]
        widget.set_pixmap(sample_pixmap)
        assert widget._type_screen_rects == []


class TestConfidenceModeTableDoubleClick:
    """双击行为：普通文本块走内联编辑；表格块不弹内联编辑器
    （表格 text 是原始 HTML，内联显示会暴露标签，故表格块双击不做处理，
    请在右侧结果视图编辑表格）。
    """

    @staticmethod
    def _pos(x: int, y: int):
        """构造带 x()/y() 方法的 pos 桩（_on_label_double_click 调用 pos.x()）。"""

        class _P:
            def x(self):
                return x

            def y(self):
                return y

        return _P()

    def test_table_block_skips_inline_edit(self, qapp, sample_pixmap):
        """label=='table' 的置信度块双击不应触发内联编辑。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(
                text="<table><tr><td>x</td></tr></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        called: list = []
        widget._start_inline_edit = lambda idx: called.append(idx)

        # 双击落在表格 bbox 内（置信度模式命中）
        widget._on_label_double_click(self._pos(50, 40))
        assert called == [], "表格块双击不应触发内联编辑"

    def test_text_block_routes_to_inline_edit(self, qapp, sample_pixmap):
        """label!='table' 的置信度块双击仍走 _start_inline_edit。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="普通文本", score=0.9, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        called: list = []
        widget._start_inline_edit = lambda idx: called.append(idx)

        widget._on_label_double_click(self._pos(50, 40))
        assert called == [0]


class TestPreviewWidgetZoom:
    """阶段 B：缩放 + 框对齐 + 漂移修复的回归测试。

    overlay 挂在 image_label 上（随 label 滚动），框坐标是 label-relative，
    故滚动无漂移；缩放后 _compute_scale_factor 与命中矩形同步重算。
    """

    def test_overlay_parented_to_image_label(self, qapp, sample_pixmap):
        """overlay 必须挂在 image_label 下，否则滚动时会与 label 错位漂移。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        assert widget._overlay.parent() is widget._image_label

    def test_widget_resizable_false(self, qapp):
        """setWidgetResizable(False) 让 label 可超出 viewport 触发滚动。"""
        widget = PreviewWidget()
        assert widget._scroll_area.widgetResizable() is False

    def test_zoom_controls_disabled_without_image(self, qapp):
        widget = PreviewWidget()
        assert widget._zoom_in_btn.isEnabled() is False
        assert widget._zoom_out_btn.isEnabled() is False
        assert widget._fit_btn.isEnabled() is False

    def test_scale_reset_on_new_pixmap(self, qapp, sample_pixmap):
        """设置新图时用户缩放倍数重置为 1.0（=fit）。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._scale = 3.0
        widget.set_pixmap(sample_pixmap)
        assert widget._scale == 1.0

    def test_zoom_by_changes_user_scale(self, qapp, sample_pixmap, qtbot):
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        # 让布局完成，fit_scale 被算出
        qtbot.wait(50)
        before = widget._scale
        widget._zoom_by(2.0)
        assert widget._scale == before * 2.0

    def test_zoom_clamped_to_range(self, qapp, sample_pixmap, qtbot):
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        qtbot.wait(50)
        widget._zoom_by(1000.0)
        assert widget._scale == PreviewWidget._MAX_USER_SCALE
        widget._zoom_fit()
        widget._zoom_by(0.0001)
        assert widget._scale == PreviewWidget._MIN_USER_SCALE

    def test_compute_scale_factor_reflects_zoom(self, qapp, sample_pixmap, qtbot):
        """缩放后 _compute_scale_factor 返回的 disp 尺寸应按总缩放放大。"""
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        qtbot.wait(50)
        img_w = widget._original_pixmap.width()
        widget._scale = 2.0
        disp_w, disp_h, _, _ = widget._compute_scale_factor()
        total = widget._current_total_scale()
        assert abs(disp_w - img_w * total) < 1.0
        assert disp_h > 0

    def test_block_screen_rects_rescaled_on_zoom(self, qapp, sample_pixmap, qtbot):
        """缩放后 _update_block_overlay 应重算命中矩形到新的像素坐标。"""
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        widget.set_text_blocks([TextBlock(text="A", score=0.9, bbox=(0, 0, 500, 500))])
        qtbot.wait(50)
        # fit 后的矩形宽度
        rect0 = widget._block_screen_rects[0]
        widget._scale = 2.0
        widget._update_block_overlay()
        rect2 = widget._block_screen_rects[0]
        # 缩放 2x 后宽度应约为原来的 2 倍
        assert rect2[2] > rect0[2] * 1.8

    def test_zoom_label_shows_percentage(self, qapp, sample_pixmap, qtbot):
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        qtbot.wait(50)
        widget._update_zoom_controls()
        # fit 时显示 100%（fit_scale × scale=1.0）；离屏可能 fit<1，故只校验非空
        assert widget._zoom_label.text().endswith("%")


class TestPreviewWidgetPolygon:
    """阶段 C：多边形（旋转/倾斜文字框）渲染与命中测试。

    TextBlock.polygon 透传 4 点检测多边形；有 polygon 时 overlay 画贴合的
    平行四边形，命中测试用 QPolygonF.containsPoint；无 polygon 回退 AABB。
    """

    def test_polygon_to_screen_basic(self, qapp):
        """_polygon_to_screen 把归一化多边形映射到屏幕坐标。"""
        widget = PreviewWidget()
        # [0,1000] 归一化的 4 点正方形 → 在 100×100 区域内应映射到 (0,0)~(100,100)
        poly = widget._polygon_to_screen(
            (0, 0, 1000, 0, 1000, 1000, 0, 1000), 100.0, 100.0, 0.0, 0.0
        )
        assert poly is not None
        assert len(poly) == 4
        assert poly[0] == QPointF(0, 0)
        assert poly[2] == QPointF(100, 100)

    def test_polygon_to_screen_none_for_short(self, qapp):
        """点数不足 3 的多边形返回 None（回退 AABB）。"""
        widget = PreviewWidget()
        assert widget._polygon_to_screen((1, 2, 3, 4), 100, 100, 0, 0) is None
        assert widget._polygon_to_screen(None, 100, 100, 0, 0) is None

    def test_hit_test_prefers_polygon(self, qapp, sample_pixmap):
        """有多边形时用多边形命中：点落在 AABB 外但 polygon 内的应命中。"""
        from PySide6.QtGui import QPolygonF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # AABB 是 (10,10)-(20,20) 的小矩形，但多边形是覆盖 (10,10)-(80,80) 的大平行四边形
        widget._block_screen_rects = [(10, 10, 10, 10)]  # w=h=10
        widget._block_screen_polys = [
            QPolygonF(
                [QPointF(10, 10), QPointF(80, 10), QPointF(80, 80), QPointF(10, 80)]
            )
        ]
        # (50,50) 落在 AABB 外、polygon 内 → 应命中（证明用了 polygon 而非 AABB）
        assert widget._hit_test_block(50, 50) == 0

    def test_hit_test_falls_back_to_rect_without_polygon(self, qapp, sample_pixmap):
        """无多边形时回退 AABB 命中。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._block_screen_rects = [(10, 10, 50, 50)]
        widget._block_screen_polys = [None]
        assert widget._hit_test_block(30, 30) == 0
        assert widget._hit_test_block(100, 100) == -1

    def test_hit_test_polygon_excludes_aabb_outside(self, qapp, sample_pixmap):
        """有多边形的块，点落在 AABB 内但 polygon 外 → 不命中该块。"""
        from PySide6.QtGui import QPolygonF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # AABB 大 (0,0,100,100)，但 polygon 小 (10,10)-(20,20)
        widget._block_screen_rects = [(0, 0, 100, 100)]
        widget._block_screen_polys = [
            QPolygonF(
                [QPointF(10, 10), QPointF(20, 10), QPointF(20, 20), QPointF(10, 20)]
            )
        ]
        # (5,5) 在 AABB 内、polygon 外 → 不命中
        assert widget._hit_test_block(5, 5) == -1
        # (15,15) 在 polygon 内 → 命中
        assert widget._hit_test_block(15, 15) == 0

    def test_update_block_overlay_populates_polys(self, qapp, sample_pixmap, qtbot):
        """set_text_blocks 后，_update_block_overlay 把 polygon 填入屏幕多边形列表。"""
        widget = PreviewWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.set_pixmap(sample_pixmap)
        widget.set_text_blocks(
            [
                TextBlock(
                    text="A",
                    score=0.9,
                    bbox=(0, 0, 500, 500),
                    polygon=(0, 0, 500, 0, 500, 500, 0, 500),
                )
            ]
        )
        qtbot.wait(50)
        assert len(widget._block_screen_polys) == 1
        assert widget._block_screen_polys[0] is not None
        assert len(widget._block_screen_polys[0]) == 4


class TestDoubleClickOriginalImage:
    """双击空白区域（未命中任何 bbox）应打开原图查看器，而非静默忽略。"""

    @staticmethod
    def _pos(x: int, y: int):
        class _P:
            def x(self):
                return x

            def y(self):
                return y

        return _P()

    def test_empty_area_opens_viewer(self, qapp, sample_pixmap, monkeypatch):
        """无任何 text_block / content_list 时，双击图片区域应调用 _show_original_image。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)

        called = []
        widget._show_original_image = lambda: called.append(True)

        widget._on_label_double_click(self._pos(50, 40))
        assert called == [True]

    def test_bbox_hit_does_not_open_viewer(self, qapp, sample_pixmap):
        """双击命中 bbox 时不应打开原图查看器。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="Hello", score=0.9, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        viewer_called = []
        widget._show_original_image = lambda: viewer_called.append(True)
        widget._start_inline_edit = lambda idx: None

        widget._on_label_double_click(self._pos(50, 40))
        assert viewer_called == [], "命中 bbox 时不应打开原图查看器"

    def test_content_list_hit_does_not_open_viewer(self, qapp, sample_pixmap):
        """双击命中 content_list 块时不应打开原图查看器。"""
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [{"type": "text", "text": "Hello"}]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "text")]

        viewer_called = []
        widget._show_original_image = lambda: viewer_called.append(True)
        widget._start_inline_edit = lambda idx: None

        widget._on_label_double_click(self._pos(50, 40))
        assert viewer_called == [], "命中 content_list 块时不应打开原图查看器"


def _pos(x: int, y: int):
    """构造带 x()/y() 方法的 pos 桩（_on_mouse_move / _on_block_*_click 调用 pos.x()）。"""

    class _P:
        def x(self):
            return x

        def y(self):
            return y

    return _P()


class TestTooltipConfidenceDisplay:
    """左侧置信度模式 tooltip：表格/图片/公式等占位 score 块应显示"无置信度"，
    而非误导性的百分比（如表格 score=0.9 显示"90%"）。普通文本块保留真实百分比。
    """

    def test_table_block_shows_no_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(
                text="<table></table>", score=0.9, bbox=(10, 10, 200, 80), label="table"
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        assert "无置信度" in widget._image_label.toolTip()
        assert "90%" not in widget._image_label.toolTip()

    def test_formula_block_shows_no_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="E=mc^2", score=1.0, bbox=(10, 10, 200, 80), label="formula")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        assert "无置信度" in widget._image_label.toolTip()
        assert "100%" not in widget._image_label.toolTip()

    def test_text_block_shows_real_confidence(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(text="普通文本", score=0.92, bbox=(10, 10, 200, 80), label="text")
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "92.0%" in tip
        assert "无置信度" not in tip

    def test_edited_flag_still_appended(self, qapp, sample_pixmap):
        """手动修改标记 [手动修改] 应继续追加在 tooltip 末尾。"""
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._text_blocks = [
            TextBlock(
                text="<table></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                is_manually_edited=True,
            )
        ]
        widget._block_screen_rects = [(10, 10, 190, 70)]

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "[手动修改]" in tip


class TestTooltipBlockTypeMode:
    """块类型模式（表格/公式管道实际渲染模式）下悬停 tooltip 应能命中并显示。

    回归：表格管道左侧在块类型模式渲染，置信度命中测试（_hit_test_block）恒返回
    -1，导致 tooltip 完全不出现。_on_mouse_move 现增加块类型模式回退。
    """

    def test_table_block_tooltip_in_block_type_mode(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        # 块类型模式：表格管道的数据
        widget._content_list = [
            {"type": "table", "table_body": "<table></table>", "bbox": [0, 0, 1, 1]}
        ]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "table")]
        widget._text_blocks = [
            TextBlock(
                text="<table></table>",
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ]
        widget._block_screen_rects = []  # 块类型模式：置信度命中矩形为空

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "90%" not in tip

    def test_formula_block_tooltip_in_block_type_mode(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [
            {"type": "formula", "text": "E=mc^2", "bbox": [0, 0, 1, 1]}
        ]
        widget._type_screen_rects = [(0, QRectF(10, 10, 190, 70), "formula")]
        widget._text_blocks = [
            TextBlock(
                text="E=mc^2",
                score=1.0,
                bbox=(10, 10, 200, 80),
                label="formula",
                content_index=0,
            )
        ]
        widget._block_screen_rects = []

        widget._on_mouse_move(_pos(50, 40))
        tip = widget._image_label.toolTip()
        assert "无置信度" in tip
        assert "100%" not in tip

    def test_no_tooltip_when_miss(self, qapp, sample_pixmap):
        from PySide6.QtCore import QRectF

        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget._content_list = [{"type": "table", "table_body": "<table></table>"}]
        widget._type_screen_rects = [(0, QRectF(10, 10, 50, 50), "table")]
        widget._text_blocks = []
        widget._block_screen_rects = []

        # 鼠标在矩形外
        widget._on_mouse_move(_pos(500, 500))
        assert widget._image_label.toolTip() == ""


class TestLegendModifiedEntry:
    """右上角图例：存在手动修改块时追加"修改后"橙色项；无则不追加。"""

    def test_legend_includes_modified_when_edited_present(self, qapp):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.classic.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            EDIT_BORDER,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "text",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["text"],
                None,
            )
        ]
        # 置信度模式数据：第 7 项（index 6）is_manually_edited = True
        overlay._conf_rects = [(0.0, 0.0, 10.0, 10.0, 0.9, "x", True)]

        labels = [lbl for lbl, _ in overlay._legend_entries()]
        assert "文本" in labels
        assert "修改后" in labels
        # "修改后"对应橙色 EDIT_BORDER
        edited = [c for lbl, c in overlay._legend_entries() if lbl == "修改后"]
        assert edited and edited[0].red() == EDIT_BORDER.red()
        assert edited[0].green() == EDIT_BORDER.green()

    def test_legend_excludes_modified_when_no_edit(self, qapp):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.classic.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "table",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["table"],
                None,
            )
        ]
        overlay._conf_rects = []  # 无修改块

        labels = [lbl for lbl, _ in overlay._legend_entries()]
        assert "表格" in labels
        assert "修改后" not in labels

    def test_formula_legend_uses_orange(self, qapp):
        """PaddleX 公式管道（type=formula）应在图例中显示橙色"公式"。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor

        from vibeocr.classic.widgets.preview_widget import (
            BLOCK_BORDER_COLORS,
            UnifiedBBoxOverlay,
        )

        overlay = UnifiedBBoxOverlay()
        overlay._mode = "block_type"
        overlay._type_rects = [
            (
                0,
                QRectF(0, 0, 10, 10),
                "formula",
                QColor(0, 0, 0),
                BLOCK_BORDER_COLORS["formula"],
                None,
            )
        ]
        entries = overlay._legend_entries()
        labels = [lbl for lbl, _ in entries]
        assert "公式" in labels
        formula_color = next(c for lbl, c in entries if lbl == "公式")
        # 橙色 ~ (249, 115, 22)，而非文本蓝 (59, 130, 246)
        assert formula_color.red() > 200
        assert formula_color.green() < 150


def test_fifty_thousand_blocks_use_bounded_overlay_working_set(
    qapp, qtbot, sample_pixmap
):
    """大结果保留完整模型，但单帧只创建有界数量的 Qt 绘制对象。"""
    import time

    from vibeocr.classic.widgets.preview_widget import MAX_INTERACTIVE_OVERLAY_BLOCKS

    widget = PreviewWidget()
    qtbot.addWidget(widget)
    widget.set_pixmap(sample_pixmap)
    blocks = [
        TextBlock(text=str(index), score=0.9, bbox=(0, 0, 10, 10))
        for index in range(50_000)
    ]

    before = time.perf_counter()
    widget.set_text_blocks(blocks)
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 150
    assert len(widget._text_blocks) == 50_000
    assert len(widget._overlay._conf_rects) <= MAX_INTERACTIVE_OVERLAY_BLOCKS
    assert len(widget._confidence_overlay_indices) <= MAX_INTERACTIVE_OVERLAY_BLOCKS
    assert len(widget._block_screen_rects) <= MAX_INTERACTIVE_OVERLAY_BLOCKS
    assert len(widget._block_screen_polys) <= MAX_INTERACTIVE_OVERLAY_BLOCKS


def test_repeated_large_block_update_reuses_indexes_and_bounded_overlay(
    qapp, qtbot, sample_pixmap
):
    """Editing the same large result must not rescan all blocks on the GUI thread."""
    from vibeocr.classic.widgets.preview_widget import MAX_INTERACTIVE_OVERLAY_BLOCKS

    class ObservedBlocks(list):
        iterations = 0

        def __iter__(self):
            type(self).iterations += 1
            return super().__iter__()

    widget = PreviewWidget()
    qtbot.addWidget(widget)
    widget.set_pixmap(sample_pixmap)
    blocks = ObservedBlocks(
        TextBlock(
            text=str(index),
            score=0.9,
            bbox=(0, 0, 10, 10),
            content_index=index,
        )
        for index in range(50_000)
    )
    widget.set_text_blocks(blocks)
    ObservedBlocks.iterations = 0

    blocks[0].text = "changed"
    widget.set_text_blocks(blocks)

    assert ObservedBlocks.iterations == 0
    assert len(widget._block_screen_rects) <= MAX_INTERACTIVE_OVERLAY_BLOCKS
    assert len(widget._block_screen_polys) <= MAX_INTERACTIVE_OVERLAY_BLOCKS


def test_rapid_large_edits_and_resizes_keep_qt_heartbeat(
    qapp, qtbot, sample_pixmap
):
    """A burst of edits and resize notifications must yield before 150 ms."""
    import time

    from PySide6.QtCore import QTimer

    widget = PreviewWidget()
    qtbot.addWidget(widget)
    widget.set_pixmap(sample_pixmap)
    blocks = [
        TextBlock(
            text=str(index),
            score=0.9,
            bbox=(0, 0, 10, 10),
            content_index=index,
        )
        for index in range(50_000)
    ]
    widget.set_text_blocks(blocks)
    qtbot.waitUntil(lambda: not widget._block_index_jobs.is_running, timeout=2000)

    fired_at = []
    started = time.perf_counter()
    QTimer.singleShot(0, lambda: fired_at.append(time.perf_counter()))
    for index in range(12):
        blocks[index].text = f"changed-{index}"
        widget.set_text_blocks(blocks)
        widget.resize(640 + index, 480 + index)

    qtbot.waitUntil(lambda: bool(fired_at), timeout=150)
    assert (fired_at[0] - started) * 1000 <= 150
