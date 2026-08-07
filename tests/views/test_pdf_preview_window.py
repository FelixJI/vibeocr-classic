# tests/views/test_pdf_preview_window.py
"""PdfPreviewWindow / _PreviewCanvas 测试"""

import fitz
import pytest

from vibeocr.classic.views.pdf_preview_window import PdfPreviewWindow, _PreviewCanvas


@pytest.fixture
def canvas(qtbot):
    c = _PreviewCanvas()
    qtbot.addWidget(c)
    return c


@pytest.fixture
def window(qtbot):
    w = PdfPreviewWindow()
    qtbot.addWidget(w)
    return w


class _FakeLayer:
    """模拟 TextLayerInfo。"""

    def __init__(self, bbox, text_preview="hello", color_id=0):
        self.bbox = bbox
        self.text_preview = text_preview
        self.color_id = color_id


class TestPreviewCanvasState:
    def test_set_highlight_layers_stores_params(self, canvas):
        """set_highlight_layers 应存储 render_dpi/page_rect/source。"""
        page_rect = fitz.Rect(0, 0, 612, 792)
        layers = [_FakeLayer((72.0, 72.0, 200.0, 100.0))]
        canvas.set_highlight_layers(
            layers, render_dpi=144, page_rect=page_rect, source="pdf"
        )
        assert canvas._render_dpi == 144
        assert canvas._page_rect == page_rect
        assert canvas._source == "pdf"
        assert canvas._highlight_layers is layers

    def test_paint_event_no_crash_without_pixmap(self, canvas):
        """无 pixmap 时 paintEvent 不应崩溃。"""
        # 触发一次重绘
        canvas.update()
        # 无异常即通过

    def test_paint_event_no_crash_with_pixmap_and_highlights(self, canvas, qapp):
        """有 pixmap 和高亮层时 paintEvent 不应崩溃。"""
        from PySide6.QtGui import QPixmap

        pm = QPixmap(100, 100)
        pm.fill()
        canvas.set_pixmap(pm)
        page_rect = fitz.Rect(0, 0, 612, 792)
        canvas.set_highlight_layers(
            [_FakeLayer((72.0, 72.0, 200.0, 100.0))], render_dpi=72, page_rect=page_rect
        )
        # 强制同步重绘以触发 paintEvent
        canvas.repaint()
        # 无异常即通过


class TestPdfPreviewWindowPublicApi:
    def test_set_highlight_forwards_to_canvas(self, window):
        """set_highlight 应转发参数到 _canvas。"""
        from PySide6.QtGui import QPixmap

        pm = QPixmap(100, 100)
        pm.fill()
        page_rect = fitz.Rect(0, 0, 612, 792)
        layers = [_FakeLayer((0.0, 0.0, 100.0, 100.0))]

        window.set_highlight(
            pm, layers, render_dpi=200, page_rect=page_rect, source="pdf"
        )

        assert window._canvas._pixmap is not None
        assert window._canvas._render_dpi == 200
        assert window._canvas._page_rect == page_rect
        assert window._canvas._highlight_layers is layers


class TestPreviewCanvasPublicName:
    def test_public_preview_canvas_class_exists(self, qtbot):
        """PreviewCanvas 作为公开类可被实例化。"""
        from vibeocr.classic.views.pdf_preview_window import PreviewCanvas

        canvas = PreviewCanvas()
        qtbot.addWidget(canvas)
        assert canvas is not None

    def test_pdf_preview_window_uses_preview_canvas(self, window):
        """PdfPreviewWindow 内部应使用公开的 PreviewCanvas。"""
        from vibeocr.classic.views.pdf_preview_window import PreviewCanvas

        assert isinstance(window._canvas, PreviewCanvas)

    def test_underscore_alias_still_works(self):
        """向后兼容别名 _PreviewCanvas 仍可导入（旧代码不破坏）。"""
        from vibeocr.classic.views.pdf_preview_window import (
            PreviewCanvas,
            _PreviewCanvas,
        )

        assert _PreviewCanvas is PreviewCanvas


class TestPreviewCanvasOcrBlocks:
    """OCR 原始块渲染（set_ocr_blocks）—— 与单次识别预览同款逻辑。"""

    def test_set_ocr_blocks_stores_blocks_and_pixmap(self, canvas, qapp):
        """set_ocr_blocks 存储 OCR 块列表和 pixmap。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        blocks = [
            TextBlock(text="Hello", score=0.95, bbox=(50.0, 50.0, 300.0, 100.0)),
            TextBlock(text="World", score=0.60, bbox=(50.0, 150.0, 300.0, 200.0)),
        ]
        canvas.set_ocr_blocks(0, blocks, pm)

        assert canvas._ocr_blocks is blocks
        assert canvas._ocr_page_index == 0
        assert canvas._pixmap is pm
        # 应计算出块屏幕矩形（2 个块）
        assert len(canvas._ocr_block_rects) == 2

    def test_set_ocr_blocks_paint_no_crash(self, canvas, qapp):
        """有 pixmap + OCR 块时 paintEvent 不崩溃。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0,
            [TextBlock(text="Hi", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))],
            pm,
        )
        canvas.repaint()
        # 无异常即通过

    def test_set_ocr_blocks_clears_old_highlight_layers(self, canvas, qapp):
        """设置 OCR 块时清除旧的 text_layers 高亮（避免两种高亮叠加）。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        page_rect = fitz.Rect(0, 0, 612, 792)
        canvas.set_highlight_layers(
            [_FakeLayer((72.0, 72.0, 200.0, 100.0))], render_dpi=72, page_rect=page_rect
        )
        assert len(canvas._highlight_layers) == 1

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0, [TextBlock(text="Hi", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))], pm
        )
        # OCR 块优先，旧的 highlight_layers 不再渲染
        assert canvas._ocr_blocks is not None
        assert len(canvas._ocr_blocks) >= 1


class TestPreviewCanvasSetPixmapClears:
    """set_pixmap（裸 pixmap 入口）应清除所有高亮数据源，避免 bbox 残留（Bug B）。"""

    def test_set_pixmap_clears_ocr_blocks(self, canvas, qapp):
        """先设 OCR 块，再 set_pixmap 裸 pixmap → OCR 块应被清除。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm1 = QPixmap(200, 200)
        pm1.fill()
        canvas.set_ocr_blocks(
            0, [TextBlock(text="Hi", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))], pm1
        )
        assert canvas._ocr_blocks is not None

        pm2 = QPixmap(300, 300)
        pm2.fill()
        canvas.set_pixmap(pm2)

        assert canvas._ocr_blocks is None
        assert canvas._ocr_block_rects == []
        assert canvas._pixmap is pm2

    def test_set_pixmap_after_highlight_clears(self, canvas):
        """先设 highlight_layers，再 set_pixmap 裸 pixmap → highlight 应被清除。"""
        from PySide6.QtGui import QPixmap

        page_rect = fitz.Rect(0, 0, 612, 792)
        canvas.set_highlight_layers(
            [_FakeLayer((72.0, 72.0, 200.0, 100.0))], render_dpi=72, page_rect=page_rect
        )
        assert len(canvas._highlight_layers) == 1
        assert canvas._page_rect is not None

        pm = QPixmap(100, 100)
        pm.fill()
        canvas.set_pixmap(pm)

        assert canvas._highlight_layers == []
        assert canvas._page_rect is None


class TestPreviewCanvasBlockEdit:
    """双击改字 → emit block_text_edited 信号。"""

    def test_block_text_edited_signal_exists(self, canvas):
        """PreviewCanvas 应有 block_text_edited 信号。"""
        assert hasattr(canvas, "block_text_edited")

    def test_finish_block_edit_emits_signal(self, canvas, qapp):
        """结束内联编辑时 emit block_text_edited(page_index, block_index, text)。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        blocks = [
            TextBlock(text="签回联", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0)),
        ]
        canvas.set_ocr_blocks(3, blocks, pm)

        emitted = []
        canvas.block_text_edited.connect(
            lambda pg, idx, txt: emitted.append((pg, idx, txt))
        )

        # 模拟编辑第 0 块为 "签收联"
        canvas._apply_block_edit(0, "签收联")

        assert len(emitted) == 1
        assert emitted[0] == (3, 0, "签收联")

    def test_finish_block_edit_noop_when_unchanged(self, canvas, qapp):
        """文字未变时不 emit 信号。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0,
            [TextBlock(text="Hello", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))],
            pm,
        )

        emitted = []
        canvas.block_text_edited.connect(
            lambda pg, idx, txt: emitted.append((pg, idx, txt))
        )
        canvas._apply_block_edit(0, "Hello")
        assert len(emitted) == 0


def _make_mouse_event(position, button=None):
    """构造带 .position()/.button() stub 的伪鼠标事件（直调 handler 用）。

    本文件画布用 event.position()（返回 QPointF）、event.button()。
    """
    from unittest.mock import MagicMock

    ev = MagicMock()
    ev.position.return_value = position
    if button is not None:
        ev.button.return_value = button
    return ev


class TestPreviewCanvasScaleBehavior:
    """缩放后 bbox 坐标空间一致性（防双重缩放回归）。"""

    def test_ocr_block_rects_unscaled_after_zoom(self, canvas, qapp):
        """放大后 _ocr_block_rects 应是未缩放的 pixmap 像素坐标（不含 scale）。

        旧 bug：rects 预乘 scale，paintEvent 又 painter.scale → 缩放两次（scale²）。
        """
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        block = TextBlock(text="Hi", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0))
        canvas.set_ocr_blocks(0, [block], pm)
        # 模拟滚轮放大到 2x
        canvas._scale = 2.0
        canvas._compute_ocr_block_rects()

        sx, sy, sw, sh = canvas._ocr_block_rects[0]
        # 期望：bbox/BBOX_NORM * pixmap_size（无 scale）
        assert sx == 50.0 / 1000.0 * 1000
        assert sy == 50.0 / 1000.0 * 800
        assert sw == (300.0 - 50.0) / 1000.0 * 1000
        assert sh == (100.0 - 50.0) / 1000.0 * 800

    def test_inline_edit_geometry_uses_scaled_coords(self, canvas, qapp):
        """缩放后 editor.setGeometry 应 × scale（child widget 走 widget 像素空间）。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        block = TextBlock(text="Hi", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0))
        canvas.set_ocr_blocks(0, [block], pm)
        canvas._scale = 2.0
        canvas._compute_ocr_block_rects()

        canvas._start_inline_edit(0)
        editor = canvas._inline_editor
        assert editor is not None
        geo = editor.geometry()
        bx, by, _bw, _bh = canvas._ocr_block_rects[0]
        assert geo.x() == int(bx * 2.0)
        assert geo.y() == int(by * 2.0)

    def test_hover_after_scale_sets_tooltip(self, canvas, qapp):
        """缩放后 hover hit-test 应 / scale（防命中回归）。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        block = TextBlock(text="Hi", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0))
        canvas.set_ocr_blocks(0, [block], pm)
        canvas._scale = 2.0
        canvas._compute_ocr_block_rects()
        canvas._drag_last = None  # 确保非拖拽态走 hover

        # rect 中心（未缩放）= (175, 60)；widget 像素 = × scale = (350, 120)
        ev = _make_mouse_event(QPointF(350.0, 120.0))
        canvas.mouseMoveEvent(ev)
        assert canvas.toolTip() != ""

        # 移出块外
        ev2 = _make_mouse_event(QPointF(900.0, 900.0))
        canvas.mouseMoveEvent(ev2)
        assert canvas.toolTip() == ""

    def test_double_click_hit_uses_scaled_mouse(self, canvas, qapp):
        """双击命中需 / scale 后再 hit-test。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtCore import Qt as QtConst
        from PySide6.QtGui import QPixmap

        from vibeocr.classic.recognition_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        block = TextBlock(text="Hi", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0))
        canvas.set_ocr_blocks(0, [block], pm)
        canvas._scale = 2.0
        canvas._compute_ocr_block_rects()

        # rect 中心（未缩放）= (175, 60)；widget 像素 × scale = (350, 120)
        ev = _make_mouse_event(
            QPointF(350.0, 120.0), button=QtConst.MouseButton.LeftButton
        )
        canvas.mouseDoubleClickEvent(ev)
        assert canvas._editing_index == 0


class TestPreviewCanvasDragPan:
    """左键拖拽平移：驱动外层 QScrollArea 的滚动条。"""

    def test_drag_pan_moves_scrollbars(self, window, qapp):
        """拖拽应平移滚动条（hbar/vbar value 改变）。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtCore import Qt as QtConst

        canvas = window._canvas
        assert hasattr(window, "_scroll"), "PdfPreviewWindow 应暴露 _scroll 引用"

        # 不调 window.show()（会触发 Windows 下销毁期崩溃）；
        # 直接给滚动条设范围，让拖拽平移逻辑有可移动空间。
        hbar = window._scroll.horizontalScrollBar()
        vbar = window._scroll.verticalScrollBar()
        hbar.setRange(0, 1000)
        vbar.setRange(0, 1000)
        hbar.setValue(500)
        vbar.setValue(500)
        h_before, v_before = hbar.value(), vbar.value()

        # 按下 → 移动（向右下拖 50px，视口应向左上滚 → value 减小）
        canvas.mousePressEvent(
            _make_mouse_event(
                QPointF(100.0, 100.0), button=QtConst.MouseButton.LeftButton
            )
        )
        canvas.mouseMoveEvent(_make_mouse_event(QPointF(150.0, 150.0)))
        assert hbar.value() == h_before - 50
        assert vbar.value() == v_before - 50

        # 释放：拖拽状态清除
        canvas.mouseReleaseEvent(
            _make_mouse_event(
                QPointF(150.0, 150.0), button=QtConst.MouseButton.LeftButton
            )
        )
        assert canvas._drag_last is None

    def test_drag_default_hand_cursor(self, canvas, qapp):
        """默认应为 OpenHandCursor（提示可拖）。"""
        from PySide6.QtCore import Qt as QtConst

        assert canvas.cursor().shape() == QtConst.CursorShape.OpenHandCursor

    def test_drag_sets_closed_hand_cursor(self, canvas, qapp):
        """按下左键应切到 ClosedHandCursor。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtCore import Qt as QtConst

        canvas.mousePressEvent(
            _make_mouse_event(
                QPointF(10.0, 10.0), button=QtConst.MouseButton.LeftButton
            )
        )
        assert canvas.cursor().shape() == QtConst.CursorShape.ClosedHandCursor


class TestPdfPreviewWindowPaging:
    """翻页工具栏 + 键盘 + page_change_requested 信号。"""

    def test_window_has_paging_controls(self, window):
        """窗口应有上一页/下一页按钮 + 页码 Label。"""
        assert hasattr(window, "_btn_prev")
        assert hasattr(window, "_btn_next")
        assert hasattr(window, "_page_label")

    def test_page_change_requested_signal_exists(self, window):
        assert hasattr(window, "page_change_requested")

    def test_prev_button_emits_signal(self, window, qtbot):
        """按上一页应 emit page_change_requested(目标页)。"""
        window._page_indices = [0, 5, 10]
        window._current_pos = 1  # 当前 page_index=5
        with qtbot.waitSignal(window.page_change_requested, timeout=1000) as blocker:
            window._go_prev()
        # pos 1→0，page_indices[0]=0
        assert blocker.args == [0]
        assert window._current_pos == 0

    def test_next_button_emits_signal(self, window, qtbot):
        """按下一页应 emit page_change_requested(目标页)。"""
        window._page_indices = [0, 5, 10]
        window._current_pos = 0  # 当前 page_index=0
        with qtbot.waitSignal(window.page_change_requested, timeout=1000) as blocker:
            window._go_next()
        # pos 0→1，page_indices[1]=5
        assert blocker.args == [5]
        assert window._current_pos == 1

    def test_prev_disabled_at_first(self, window):
        """在首页时上一页按钮应 disabled。"""
        window._page_indices = [0, 5, 10]
        window._current_pos = 0
        window._update_paging_buttons()
        assert not window._btn_prev.isEnabled()
        assert window._btn_next.isEnabled()

    def test_next_disabled_at_last(self, window):
        """在末页时下一页按钮应 disabled。"""
        window._page_indices = [0, 5, 10]
        window._current_pos = 2
        window._update_paging_buttons()
        assert window._btn_prev.isEnabled()
        assert not window._btn_next.isEnabled()

    def test_page_label_shows_position(self, window):
        """页码 Label 应显示"第 X / Y 页"。"""
        window._page_indices = [0, 5, 10]
        window._current_pos = 1
        window._update_paging_buttons()
        assert "2 / 3" in window._page_label.text()

    def test_set_page_indices_clamps_current(self, window):
        """set_page_indices 应把 current 钳制到合法范围。"""
        window.set_page_indices([0, 5, 10], current=99)
        assert window._current_pos == 2  # 钳到最后

    def test_empty_page_indices_disables_both(self, window):
        """无可浏览页时两个翻页按钮都应 disabled。"""
        window._page_indices = []
        window._current_pos = 0
        window._update_paging_buttons()
        assert not window._btn_prev.isEnabled()
        assert not window._btn_next.isEnabled()
        assert window._page_label.text() == "—"

    def test_keyboard_right_triggers_next(self, window, qtbot):
        """→ 键应触发下一页并 emit page_change_requested。"""
        from PySide6.QtCore import Qt as QtConst

        window._page_indices = [0, 5, 10]
        window._current_pos = 0
        with qtbot.waitSignal(window.page_change_requested, timeout=1000) as blocker:
            qtbot.keyClick(window, QtConst.Key.Key_Right)
        assert blocker.args == [5]
        assert window._current_pos == 1

    def test_keyboard_left_triggers_prev(self, window, qtbot):
        """← 键应触发上一页并 emit page_change_requested。"""
        from PySide6.QtCore import Qt as QtConst

        window._page_indices = [0, 5, 10]
        window._current_pos = 1
        with qtbot.waitSignal(window.page_change_requested, timeout=1000) as blocker:
            qtbot.keyClick(window, QtConst.Key.Key_Left)
        assert blocker.args == [0]
        assert window._current_pos == 0

    def test_keyboard_escape_closes_window(self, window, qtbot):
        """Esc 应关闭窗口。"""
        from PySide6.QtCore import Qt as QtConst

        window.show()
        qtbot.waitExposed(window)
        qtbot.keyClick(window, QtConst.Key.Key_Escape)
        assert not window.isVisible()
