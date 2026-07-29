"""Tests for InlineEditCanvas."""

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from vibeocr.classic.widgets.editor.annotation_items import ArrowAnnotation, EditTool
from vibeocr.classic.widgets.inline_edit_canvas import InlineEditCanvas


def _make_pixmap(
    w: int, h: int, color: QColor | None = None, dpr: float = 1.0
) -> QPixmap:
    px = QPixmap(int(w * dpr), int(h * dpr))
    if color:
        px.fill(color)
    else:
        px.fill(QColor(128, 128, 128))
    px.setDevicePixelRatio(dpr)
    return px


def _make_mapper(dpr: float = 1.0, virtual_geometry: QRect | None = None) -> MagicMock:
    mapper = MagicMock()
    mapper.dpr_at.return_value = dpr
    mapper.virtual_geometry = virtual_geometry or QRect(0, 0, 9999, 9999)
    mapper.clip_to_virtual.side_effect = lambda r: r
    return mapper


class TestInlineEditCanvas:
    def test_initial_state(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._background_pixmap is None

    def test_set_background(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        assert canvas._background_pixmap is not None
        assert canvas._background_item is not None

    def test_export_image(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        exported = canvas.export_image()
        assert not exported.isNull()

    def test_export_without_background(self, qapp):
        canvas = InlineEditCanvas()
        exported = canvas.export_image()
        assert exported.isNull()

    def test_undo_stack_exists(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas.undo_stack is not None


class TestUpdateCropRegion:
    def test_background_updates_to_new_region(self, qapp):
        canvas = InlineEditCanvas()

        # 原始屏幕 1000x800
        screen_pxm = _make_pixmap(1000, 800, QColor(100, 100, 100), dpr=1.0)

        # 初始裁剪区域 (100, 100, 300x200)
        initial_sel = QRect(100, 100, 300, 200)
        cropped = screen_pxm.copy(initial_sel)
        canvas.set_background(cropped)

        old_scene_rect = canvas._scene.sceneRect()
        assert old_scene_rect == QRectF(0, 0, 300, 200)

        # 更新到新裁剪区域 (50, 50, 400x300)
        new_sel = QRect(50, 50, 400, 300)
        canvas.update_crop_region(screen_pxm, new_sel, _make_mapper())

        new_scene_rect = canvas._scene.sceneRect()
        assert new_scene_rect == QRectF(0, 0, 400, 300)

    def test_annotations_translated_by_delta(self, qapp):
        canvas = InlineEditCanvas()
        screen_pxm = _make_pixmap(1000, 800, dpr=1.0)

        initial_sel = QRect(100, 100, 300, 200)
        cropped = screen_pxm.copy(initial_sel)
        canvas.set_background(cropped, crop_origin=QPointF(100, 100))

        # 在场景坐标 (50, 30) 添加一个矩形标注
        annotation = QGraphicsRectItem(QRectF(50, 30, 60, 40))
        canvas._scene.addItem(annotation)

        # 裁剪区域移动了 (20, 10)，即 new_sel 左上角从 (100,100) 变为 (120,110)
        # 标注应该平移 -(20, 10) = (-20, -10) 以保持屏幕绝对位置
        new_sel = QRect(120, 110, 300, 200)
        canvas.update_crop_region(screen_pxm, new_sel, _make_mapper())

        # 原场景坐标 (50, 30) → 新场景坐标 (30, 20)
        assert annotation.pos().x() == -20.0
        assert annotation.pos().y() == -10.0


class TestFillProperties:
    def test_default_fill_linked(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_linked is True

    def test_default_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_opacity == 20

    def test_default_fill_color_follows_pen(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_color.red() == canvas._pen_color.red()
        assert canvas._fill_color.green() == canvas._pen_color.green()
        assert canvas._fill_color.blue() == canvas._pen_color.blue()

    def test_set_pen_color_syncs_fill_when_linked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_pen_color_no_sync_when_unlinked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 0
        assert canvas._fill_color.blue() == 255

    def test_set_fill_linked_syncs_to_pen_color(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        canvas.set_fill_linked(True)
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_opacity(80)
        assert canvas._fill_opacity == 80


class TestTrivialSetters:
    """10 个 trivial setter：写入对应内部属性（draw 管线读取这些值）。"""

    def test_set_pen_width(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_pen_width(7)
        assert canvas._pen_width == 7

    def test_set_fill_enabled(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_enabled(True)
        assert canvas._fill_enabled is True

    def test_set_fill_color(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_color(QColor(10, 20, 30))
        assert canvas._fill_color.red() == 10
        assert canvas._fill_color.green() == 20
        assert canvas._fill_color.blue() == 30

    def test_set_font(self, qapp):
        from PySide6.QtGui import QFont

        canvas = InlineEditCanvas()
        f = QFont("Courier", 22)
        canvas.set_font(f)
        assert canvas._font.family() == "Courier"

    def test_set_font_size(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_font_size(33)
        assert canvas._font.pointSize() == 33

    def test_set_bold(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_bold(True)
        assert canvas._font.bold() is True

    def test_set_italic(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_italic(True)
        assert canvas._font.italic() is True

    def test_set_mosaic_strength(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_mosaic_strength(25)
        assert canvas._mosaic_strength == 25

    def test_set_blur_radius(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_blur_radius(18)
        assert canvas._blur_radius == 18


class TestSetToolCursorAndDrag:
    """``set_tool``：SELECT/TEXT/其它三档 cursor 与 drag 模式。"""

    def test_select_tool_sets_rubberband_and_arrow_cursor(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGraphicsView

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.SELECT)
        assert canvas._current_tool == EditTool.SELECT
        assert canvas.dragMode() == QGraphicsView.DragMode.RubberBandDrag
        assert canvas.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor

    def test_text_tool_sets_nodrag_and_ibeam_cursor(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGraphicsView

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.TEXT)
        assert canvas.dragMode() == QGraphicsView.DragMode.NoDrag
        assert canvas.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor

    def test_rect_tool_sets_cross_cursor(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGraphicsView

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        assert canvas.dragMode() == QGraphicsView.DragMode.NoDrag
        assert canvas.viewport().cursor().shape() == Qt.CursorShape.CrossCursor

    def test_set_tool_finishes_previous_drawing(self, qapp):
        canvas = InlineEditCanvas()
        canvas._drawing = True
        canvas._draw_start = QPointF(1, 1)
        canvas._temp_item = QGraphicsRectItem(QRectF(0, 0, 5, 5))
        canvas._scene.addItem(canvas._temp_item)
        canvas.set_tool(EditTool.SELECT)
        # 切工具应取消当前绘制
        assert canvas._drawing is False
        assert canvas._draw_start is None
        assert canvas._temp_item is None


class TestSelectedAnnotationProperty:
    def test_returns_none_when_no_selection(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas.selected_annotation is None

    def test_returns_selected_non_background_item(self, qapp):
        canvas = InlineEditCanvas()
        item = QGraphicsRectItem(QRectF(0, 0, 10, 10))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        canvas._scene.addItem(item)
        item.setSelected(True)
        assert canvas.selected_annotation is item

    def test_skips_background_item(self, qapp):
        canvas = InlineEditCanvas()
        bg = QPixmap(50, 50)
        bg.fill()
        canvas.set_background(bg)
        # 即便背景被选中也不应返回它
        canvas._background_item.setSelected(True)
        assert canvas.selected_annotation is None


class TestCreateTempItem:
    """``_create_temp_item``：5 工具分支（RECT/ELLIPSE/ARROW/MOSAIC+BLUR/其它）。"""

    def test_rect_temp_item_created(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._create_temp_item(QPointF(10, 10))
        assert isinstance(canvas._temp_item, QGraphicsRectItem)
        assert canvas._temp_item in canvas._scene.items()

    def test_ellipse_temp_item_created(self, qapp):
        from PySide6.QtWidgets import QGraphicsEllipseItem

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ELLIPSE)
        canvas._create_temp_item(QPointF(5, 5))
        assert isinstance(canvas._temp_item, QGraphicsEllipseItem)

    def test_arrow_temp_item_created(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ARROW)
        canvas._create_temp_item(QPointF(0, 0))
        assert isinstance(canvas._temp_item, ArrowAnnotation)

    def test_mosaic_temp_item_is_rect_placeholder(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.MOSAIC)
        canvas._create_temp_item(QPointF(0, 0))
        # MOSAIC/BLUR 用蓝色虚线占位矩形（非 MosaicItem）
        assert isinstance(canvas._temp_item, QGraphicsRectItem)

    def test_blur_temp_item_is_rect_placeholder(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.BLUR)
        canvas._create_temp_item(QPointF(0, 0))
        assert isinstance(canvas._temp_item, QGraphicsRectItem)

    def test_select_tool_creates_no_temp(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.SELECT)
        canvas._create_temp_item(QPointF(0, 0))
        assert canvas._temp_item is None


class TestUpdateTempItem:
    def test_arrow_temp_updates_end(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ARROW)
        # 注意：QPointF(0,0) 在 PySide6 中为 falsy（__bool__ 走 isNull），
        # _update_temp_item 的 `if not self._draw_start` 守卫会早返回，故用非原点。
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before_end = QPointF(canvas._temp_item._end)
        canvas._update_temp_item(QPointF(80, 60))
        assert canvas._temp_item._end != before_end
        assert canvas._temp_item._end.x() == 80

    def test_rect_temp_updates_rect(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        canvas._update_temp_item(QPointF(60, 50))
        r = canvas._temp_item.rect()
        assert r.width() == 50
        assert r.height() == 40

    def test_update_without_draw_start_is_noop(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._temp_item = QGraphicsRectItem(QRectF(0, 0, 5, 5))
        canvas._draw_start = None
        # 不抛异常、不修改 temp_item
        canvas._update_temp_item(QPointF(99, 99))

    def test_arrow_temp_updates_from_origin(self, qapp):
        """回归：QPointF(0,0) 在 PySide6 为 falsy；修复后从原点起绘也应能更新预览。"""
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ARROW)
        canvas._draw_start = QPointF(0, 0)
        canvas._create_temp_item(QPointF(0, 0))
        canvas._update_temp_item(QPointF(70, 50))
        # 从原点(0,0)起绘，end 应被更新到 (70,50)——修复前会因 falsy 早返回而不更新。
        assert canvas._temp_item._end.x() == 70
        assert canvas._temp_item._end.y() == 50

    def test_rect_temp_updates_from_origin(self, qapp):
        """回归：从原点起绘的矩形预览也应正常更新。"""
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._draw_start = QPointF(0, 0)
        canvas._create_temp_item(QPointF(0, 0))
        canvas._update_temp_item(QPointF(60, 40))
        r = canvas._temp_item.rect()
        assert r.width() == 60
        assert r.height() == 40



class TestFinishDrawingAt:
    """``_finish_drawing_at``：min-size 检查 + 5 标注创建分支。"""

    def test_too_small_removes_temp_no_command(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._drawing = True
        canvas._draw_start = QPointF(20, 20)
        canvas._create_temp_item(QPointF(20, 20))
        undo_before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(21, 21))  # < 3x3
        assert canvas._drawing is False
        assert canvas._temp_item is None
        assert canvas._undo_stack.count() == undo_before  # 无命令

    def test_no_draw_start_removes_temp(self, qapp):
        canvas = InlineEditCanvas()
        canvas._drawing = True
        canvas._draw_start = None
        canvas._finish_drawing_at(QPointF(50, 50))
        assert canvas._drawing is False

    def test_rect_creates_annotation_and_command(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(40, 30))
        assert canvas._undo_stack.count() == before + 1
        # 场景中应有 1 个 RectAnnotation
        from vibeocr.classic.widgets.editor.annotation_items import RectAnnotation as RA

        rect_anns = [i for i in canvas._scene.items() if isinstance(i, RA)]
        assert len(rect_anns) == 1

    def test_rect_from_origin_creates_annotation(self, qapp):
        """回归：从场景原点(0,0)起绘也应创建标注——QPointF(0,0) 为 falsy，
        修复前 `_finish_drawing_at` 的 `if not self._draw_start` 会误判为未绘制。"""
        from vibeocr.classic.widgets.editor.annotation_items import RectAnnotation as RA

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.RECT)
        canvas._drawing = True
        canvas._draw_start = QPointF(0, 0)
        canvas._create_temp_item(QPointF(0, 0))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(40, 30))
        # 从原点起绘应同样创建标注 + 命令（修复前会被 falsy 守卫吞掉）。
        assert canvas._undo_stack.count() == before + 1
        rect_anns = [i for i in canvas._scene.items() if isinstance(i, RA)]
        assert len(rect_anns) == 1


    def test_ellipse_creates_annotation(self, qapp):
        from vibeocr.classic.widgets.editor.annotation_items import EllipseAnnotation

        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ELLIPSE)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(50, 40))
        assert canvas._undo_stack.count() == before + 1
        ellipses = [i for i in canvas._scene.items() if isinstance(i, EllipseAnnotation)]
        assert len(ellipses) == 1

    def test_arrow_creates_annotation(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_tool(EditTool.ARROW)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(60, 40))
        assert canvas._undo_stack.count() == before + 1
        arrows = [i for i in canvas._scene.items() if isinstance(i, ArrowAnnotation)]
        assert len(arrows) == 1

    def test_mosaic_creates_item_only_with_background(self, qapp):
        from vibeocr.classic.widgets.editor.annotation_items import MosaicItem

        canvas = InlineEditCanvas()
        canvas.set_background(_make_pixmap(200, 200))  # Mosaic 需背景
        canvas.set_tool(EditTool.MOSAIC)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(50, 50))
        assert canvas._undo_stack.count() == before + 1
        mosaics = [i for i in canvas._scene.items() if isinstance(i, MosaicItem)]
        assert len(mosaics) == 1

    def test_mosaic_without_background_creates_nothing(self, qapp):
        canvas = InlineEditCanvas()  # 无背景
        canvas.set_tool(EditTool.MOSAIC)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(50, 50))
        # 无 background_pixmap → 不创建 MosaicItem，无命令
        assert canvas._undo_stack.count() == before

    def test_blur_creates_item_only_with_background(self, qapp):
        from vibeocr.classic.widgets.editor.annotation_items import BlurItem

        canvas = InlineEditCanvas()
        canvas.set_background(_make_pixmap(200, 200))
        canvas.set_tool(EditTool.BLUR)
        canvas._drawing = True
        canvas._draw_start = QPointF(10, 10)
        canvas._create_temp_item(QPointF(10, 10))
        before = canvas._undo_stack.count()
        canvas._finish_drawing_at(QPointF(50, 50))
        assert canvas._undo_stack.count() == before + 1
        blurs = [i for i in canvas._scene.items() if isinstance(i, BlurItem)]
        assert len(blurs) == 1


class TestCreateTextAt:
    def test_creates_text_annotation_and_command(self, qapp):
        from vibeocr.classic.widgets.editor.annotation_items import TextAnnotation

        canvas = InlineEditCanvas()
        before = canvas._undo_stack.count()
        canvas._create_text_at(QPointF(20, 30))
        assert canvas._undo_stack.count() == before + 1
        texts = [i for i in canvas._scene.items() if isinstance(i, TextAnnotation)]
        assert len(texts) == 1
        assert texts[0].toPlainText() == "文字"

