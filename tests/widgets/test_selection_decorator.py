"""Tests for SelectionDecorator — handle positions and resize calculations."""

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import QGraphicsScene

from vibeocr.classic.widgets.editor.annotation_items import (
    ArrowAnnotation,
    BlurItem,
    EllipseAnnotation,
    MosaicItem,
    RectAnnotation,
    TextAnnotation,
)
from vibeocr.classic.widgets.editor.selection_decorator import SelectionDecorator


def _make_rect_item(x=0, y=0, w=100, h=80) -> RectAnnotation:
    return RectAnnotation(QRectF(x, y, w, h), pen_color=QColor(255, 0, 0))


def _make_pixmap(w=200, h=200) -> QPixmap:
    px = QPixmap(w, h)
    px.fill(QColor(128, 128, 128))
    return px


def _make_decorator_in_scene(item):
    """把 item 放入 QGraphicsScene 并构造其 SelectionDecorator。

    多数 dispatch 方法依赖 ``sceneBoundingRect``/``pos``，需 item 已入场景。
    SelectionDecorator.__init__ 调用 ``decorated.installSceneEventFilter(self)``，
    要求双方在同一 scene，故把 decorator 也 addItem 到 scene。
    返回 (decorator, scene) ——调用方需保留 scene 引用以避免 GC 提前释放 Qt 对象。
    """
    scene = QGraphicsScene()
    scene.addItem(item)
    dec = SelectionDecorator(item)
    scene.addItem(dec)
    return dec, scene


class TestHandlePositions:
    def test_handle_positions_at_rect_edges(self, qapp):
        item = _make_rect_item(10, 20, 100, 80)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(10, 20, 100, 80))
        assert len(handles) == 8
        assert handles[0] == QPointF(10, 20)
        assert handles[2] == QPointF(110, 20)
        assert handles[5] == QPointF(10, 100)
        assert handles[7] == QPointF(110, 100)
        assert handles[1] == QPointF(60, 20)
        assert handles[3] == QPointF(10, 60)
        assert handles[4] == QPointF(110, 60)
        assert handles[6] == QPointF(60, 100)

    def test_handle_at_origin(self, qapp):
        item = _make_rect_item(0, 0, 50, 50)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(0, 0, 50, 50))
        assert handles[0] == QPointF(0, 0)
        assert handles[7] == QPointF(50, 50)


class TestResizeCalculation:
    def test_top_left_handle_resize(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(0, QPointF(20, 30), original)
        assert new_rect.topLeft() == QPointF(20, 30)
        assert new_rect.bottomRight() == QPointF(110, 90)
        assert new_rect.width() == 90
        assert new_rect.height() == 60

    def test_bottom_right_handle_resize(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(7, QPointF(150, 120), original)
        assert new_rect.topLeft() == QPointF(10, 10)
        assert new_rect.bottomRight() == QPointF(150, 120)

    def test_top_center_handle_only_changes_height(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(1, QPointF(60, 30), original)
        assert new_rect.left() == 10
        assert new_rect.width() == 100
        assert new_rect.top() == 30
        assert new_rect.bottom() == 90

    def test_middle_right_handle_only_changes_width(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(4, QPointF(150, 50), original)
        assert new_rect.top() == 10
        assert new_rect.height() == 80
        assert new_rect.right() == 150
        assert new_rect.left() == 10

    def test_min_size_enforced(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec = SelectionDecorator(item)
        original = QRectF(10, 10, 100, 80)
        new_rect = dec.calculate_resize(7, QPointF(12, 12), original)
        assert new_rect.width() >= 10
        assert new_rect.height() >= 10

    def test_handle_hit_detection(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec = SelectionDecorator(item)
        handles = dec.handle_positions(QRectF(0, 0, 100, 80))
        hit = dec.hit_test(QPointF(0, 0), handles)
        assert hit == 0
        hit = dec.hit_test(QPointF(100, 80), handles)
        assert hit == 7
        hit = dec.hit_test(QPointF(50, 40), handles)
        assert hit == -1


class TestApplyResize:
    """``_apply_resize`` 按 item 类型分派：Rect/Ellipse、Mosaic/Blur、Arrow、Text。"""

    def test_rect_item_resized(self, qapp):
        item = _make_rect_item(10, 10, 100, 80)
        dec, _scene = _make_decorator_in_scene(item)
        dec._apply_resize(QRectF(20, 20, 60, 50))
        # pos 为 0，rect 应直接等于传入矩形
        assert item.rect() == QRectF(20, 20, 60, 50)

    def test_ellipse_item_resized(self, qapp):
        item = EllipseAnnotation(QRectF(0, 0, 100, 80), pen_color=QColor(0, 0, 255))
        dec, _scene = _make_decorator_in_scene(item)
        dec._apply_resize(QRectF(5, 5, 70, 40))
        assert item.rect() == QRectF(5, 5, 70, 40)

    def test_mosaic_item_sets_resizing_and_rect(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(0, 0, 100, 80), bg, strength=10)
        dec, _scene = _make_decorator_in_scene(item)
        dec._apply_resize(QRectF(0, 0, 50, 50))
        # 调整中标记置 True，rect 已更新（不重新生成马赛克，性能优先）
        assert item._resizing is True
        assert item.rect() == QRectF(0, 0, 50, 50)

    def test_blur_item_sets_resizing_and_rect(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(0, 0, 100, 80), bg, radius=10)
        dec, _scene = _make_decorator_in_scene(item)
        dec._apply_resize(QRectF(0, 0, 60, 40))
        assert item._resizing is True
        assert item.rect() == QRectF(0, 0, 60, 40)

    def test_arrow_item_dispatches_resize_arrow(self, qapp):
        item = ArrowAnnotation(QPointF(10, 10), QPointF(100, 80))
        dec, _scene = _make_decorator_in_scene(item)
        # _resize_arrow 依赖 _initial_rect/_initial_start/_initial_end
        dec._initial_rect = QRectF(10, 10, 90, 70)
        dec._initial_start = QPointF(10, 10)
        dec._initial_end = QPointF(100, 80)
        dec._apply_resize(QRectF(10, 10, 180, 140))
        # 缩放后端点应按比例移动：宽度翻倍 → x 差也翻倍
        assert item._end.x() == pytest.approx(190, abs=0.5)
        assert item._end.y() == pytest.approx(150, abs=0.5)

    def test_text_item_dispatches_resize_text(self, qapp):
        item = TextAnnotation("文字", pos=QPointF(0, 0), font=QFont("Arial", 20))
        dec, _scene = _make_decorator_in_scene(item)
        dec._initial_rect = QRectF(0, 0, 100, 40)
        dec._initial_font_size = 20.0
        dec._apply_resize(QRectF(0, 0, 100, 80))
        # 高度翻倍 → 字号翻倍
        assert item.font().pointSizeF() == pytest.approx(40.0, abs=0.5)


class TestResizeArrowAndText:
    """``_resize_arrow``/``_resize_text`` 单独覆盖（含 early-return 守卫）。"""

    def test_resize_arrow_no_initial_state_returns_silently(self, qapp):
        item = ArrowAnnotation(QPointF(0, 0), QPointF(50, 50))
        dec, _scene = _make_decorator_in_scene(item)
        # 未设置 initial 状态 → 早返回，不改 _start/_end
        before_end = QPointF(item._end)
        dec._resize_arrow(item, QRectF(0, 0, 200, 200))
        assert item._end == before_end

    def test_resize_arrow_zero_width_uses_ratio_one(self, qapp):
        item = ArrowAnnotation(QPointF(0, 0), QPointF(50, 50))
        dec, _scene = _make_decorator_in_scene(item)
        dec._initial_rect = QRectF(0, 0, 0, 50)  # 宽 0 → sx=1
        dec._initial_start = QPointF(0, 0)
        dec._initial_end = QPointF(50, 50)
        dec._resize_arrow(item, QRectF(0, 0, 100, 100))
        # old.width()==0 → sx=1，x 方向保持原差值：end.x = new.left + (50-0)*1 = 50
        assert item._end.x() == pytest.approx(50, abs=0.5)

    def test_resize_text_no_initial_state_returns_silently(self, qapp):
        item = TextAnnotation("文字", font=QFont("Arial", 20))
        dec, _scene = _make_decorator_in_scene(item)
        old_size = item.font().pointSizeF()
        dec._resize_text(item, QRectF(0, 0, 999, 999))
        assert item.font().pointSizeF() == old_size

    def test_resize_text_enforces_min_size(self, qapp):
        item = TextAnnotation("文字", font=QFont("Arial", 20))
        dec, _scene = _make_decorator_in_scene(item)
        dec._initial_rect = QRectF(0, 0, 100, 100)
        dec._initial_font_size = 20.0
        # 极小新高度 → ratio 很小，但字号有下限 6
        dec._resize_text(item, QRectF(0, 0, 100, 1))
        assert item.font().pointSizeF() == 6.0


class TestGetCurrentRect:
    """``_get_current_rect``：Arrow/Text 走 boundingRect，其它走 item.rect()。"""

    def test_rect_item_current_rect(self, qapp):
        item = _make_rect_item(10, 20, 100, 80)
        dec, _scene = _make_decorator_in_scene(item)
        cur = dec._get_current_rect()
        # pos=(0,0)，rect 即 RectAnnotation 构造的 QRectF(10,20,100,80)
        assert cur is not None
        assert cur.topLeft() == QPointF(10, 20)
        assert cur.size().width() == 100

    def test_arrow_item_current_rect_via_boundingrect(self, qapp):
        item = ArrowAnnotation(QPointF(5, 5), QPointF(60, 60))
        dec, _scene = _make_decorator_in_scene(item)
        cur = dec._get_current_rect()
        assert cur is not None
        assert cur.width() > 0

    def test_text_item_current_rect_via_boundingrect(self, qapp):
        item = TextAnnotation("文字", pos=QPointF(3, 3), font=QFont("Arial", 14))
        dec, _scene = _make_decorator_in_scene(item)
        cur = dec._get_current_rect()
        assert cur is not None
        assert cur.width() > 0


class TestStoreInitialState:
    """``_store_initial_state``：Arrow 记录端点，Text 记录字号。"""

    def test_arrow_stores_endpoints(self, qapp):
        item = ArrowAnnotation(QPointF(10, 20), QPointF(80, 90))
        dec, _scene = _make_decorator_in_scene(item)
        assert dec._initial_start is None
        dec._store_initial_state(QRectF(10, 20, 70, 70))
        assert dec._initial_start == QPointF(10, 20)
        assert dec._initial_end == QPointF(80, 90)

    def test_text_stores_font_size(self, qapp):
        item = TextAnnotation("文字", font=QFont("Arial", 16))
        dec, _scene = _make_decorator_in_scene(item)
        assert dec._initial_font_size is None
        dec._store_initial_state(QRectF(0, 0, 100, 30))
        assert dec._initial_font_size == 16.0

    def test_rect_item_stores_nothing(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec, _scene = _make_decorator_in_scene(item)
        dec._store_initial_state(QRectF(0, 0, 100, 80))
        # 非 Arrow/Text → 不写任何 initial 字段
        assert dec._initial_start is None
        assert dec._initial_font_size is None


class TestFinalizeResize:
    """``_finalize_resize``：构造 undo 命令推入 canvas.undo_stack；Mosaic/Blur 清 resizing。"""

    def test_finalize_mosaic_clears_resizing_and_pushes_command(self, qapp):
        bg = _make_pixmap()
        item = MosaicItem(QRectF(0, 0, 100, 80), bg, strength=10)
        item.set_resizing(True)
        dec, _scene = _make_decorator_in_scene(item)
        canvas = MagicMock()
        canvas.undo_stack = MagicMock()
        dec._finalize_resize(canvas, QRectF(0, 0, 100, 80), QRectF(0, 0, 50, 50))
        assert item._resizing is False
        canvas.undo_stack.push.assert_called_once()

    def test_finalize_blur_clears_resizing(self, qapp):
        bg = _make_pixmap()
        item = BlurItem(QRectF(0, 0, 100, 80), bg, radius=10)
        item.set_resizing(True)
        dec, _scene = _make_decorator_in_scene(item)
        canvas = MagicMock()
        canvas.undo_stack = MagicMock()
        dec._finalize_resize(canvas, QRectF(0, 0, 100, 80), QRectF(0, 0, 60, 60))
        assert item._resizing is False
        canvas.undo_stack.push.assert_called_once()

    def test_finalize_rect_pushes_command(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec, _scene = _make_decorator_in_scene(item)
        canvas = MagicMock()
        canvas.undo_stack = MagicMock()
        dec._finalize_resize(canvas, QRectF(0, 0, 100, 80), QRectF(0, 0, 40, 40))
        canvas.undo_stack.push.assert_called_once()


class TestFindCanvasAndCleanup:
    """``_find_canvas``（需 EditCanvas 视图）与 ``cleanup``。"""

    def test_find_canvas_returns_editcanvas(self, qapp):
        from vibeocr.classic.widgets.editor.edit_canvas import EditCanvas

        item = _make_rect_item(0, 0, 100, 80)
        scene = QGraphicsScene()
        scene.addItem(item)
        dec = SelectionDecorator(item)
        scene.addItem(dec)  # decorator 需在 scene 中才能 self.scene() 查 views()
        canvas = EditCanvas()
        canvas.setScene(scene)
        assert dec._find_canvas() is canvas

    def test_find_canvas_none_when_no_editcanvas_view(self, qapp):
        # 用普通 QGraphicsView（非 EditCanvas）→ 返回 None
        from PySide6.QtWidgets import QGraphicsView

        item = _make_rect_item(0, 0, 100, 80)
        scene = QGraphicsScene()
        scene.addItem(item)
        dec = SelectionDecorator(item)
        scene.addItem(dec)
        view = QGraphicsView()
        view.setScene(scene)
        assert dec._find_canvas() is None

    def test_find_canvas_none_when_no_scene(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec = SelectionDecorator(item)  # 未入场景
        assert dec._find_canvas() is None

    def test_cleanup_removes_scene_event_filter(self, qapp):
        item = _make_rect_item(0, 0, 100, 80)
        dec = SelectionDecorator(item)
        # cleanup 不抛异常即可；installSceneEventFilter 已在 __init__ 调用。
        dec.cleanup()

