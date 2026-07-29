"""内联编辑画布

基于 QGraphicsView + QGraphicsScene 的轻量级内联编辑画布。
与 EditCanvas 不同的是：
- 透明背景（无深色填充）
- 无滚动条、无边框
- 适配内联编辑场景（嵌入截图覆盖层中）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from vibeocr.classic.widgets.editor.annotation_items import (
    ArrowAnnotation,
    BlurItem,
    EditTool,
    EllipseAnnotation,
    MosaicItem,
    RectAnnotation,
    TextAnnotation,
)
from vibeocr.classic.widgets.editor.command_stack import (
    AddAnnotationCommand,
    MoveAnnotationCommand,
    create_undo_stack,
)

if TYPE_CHECKING:
    from vibeocr.classic.widgets.screen_coordinate_mapper import ScreenCoordinateMapper


class InlineEditCanvas(QGraphicsView):
    """内联编辑画布 — 嵌入截图覆盖层中的轻量级标注画布"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 渲染设置
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )

        # 透明背景、无滚动条、无边框
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background: transparent;")
        self.setBackgroundBrush(QBrush(Qt.GlobalColor.transparent))

        # 场景索引
        self._scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)

        # 撤销栈
        self._undo_stack: QUndoStack = create_undo_stack(self)

        # 背景图
        self._background_item: QGraphicsPixmapItem | None = None
        self._background_pixmap: QPixmap | None = None

        # 坐标映射器
        self._mapper: ScreenCoordinateMapper | None = None

        # 当前工具和属性
        self._current_tool: EditTool = EditTool.SELECT
        self._pen_color: QColor = QColor(255, 0, 0)
        self._pen_width: int = 2
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor(255, 0, 0)
        self._fill_opacity: int = 20
        self._fill_linked: bool = True
        self._font: QFont = QFont("Microsoft YaHei", 14)
        self._mosaic_strength: int = 10
        self._blur_radius: int = 10

        # 绘制状态
        self._drawing: bool = False
        self._draw_start: QPointF | None = None
        self._temp_item: (
            QGraphicsRectItem
            | QGraphicsEllipseItem
            | ArrowAnnotation
            | EllipseAnnotation
            | MosaicItem
            | BlurItem
            | RectAnnotation
            | None
        ) = None

        self._crop_origin: QPointF = QPointF()

        # 移动跟踪
        self._move_start_positions: dict[QGraphicsItem, QPointF] = {}

    # ==================== 属性 ====================

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    @property
    def selected_annotation(self):
        """获取当前选中的标注项"""
        for item in self._scene.selectedItems():
            if item != self._background_item:
                return item
        return None

    # ==================== 公开方法 ====================

    def set_background(
        self,
        pixmap: QPixmap,
        crop_origin: QPointF | None = None,
        mapper: ScreenCoordinateMapper | None = None,
    ) -> None:
        """设置背景截图"""
        self._mapper = mapper
        self._background_pixmap = pixmap
        if self._background_item:
            self._scene.removeItem(self._background_item)

        self._background_item = QGraphicsPixmapItem(pixmap)
        self._background_item.setZValue(0)
        self._background_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False
        )
        self._background_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False
        )
        self._scene.addItem(self._background_item)
        # pixmap.rect() 返回物理尺寸，QGraphicsPixmapItem.boundingRect() 会除以 DPR
        # 返回逻辑尺寸，场景矩形也应使用逻辑尺寸以匹配画布控件
        dpr = pixmap.devicePixelRatio()
        self._scene.setSceneRect(
            QRectF(0, 0, pixmap.width() / dpr, pixmap.height() / dpr)
        )
        if crop_origin is not None:
            self._crop_origin = crop_origin

    def update_crop_region(
        self,
        screen_pixmap: QPixmap,
        new_selection: QRect,
        mapper: ScreenCoordinateMapper,
    ) -> None:
        """更新裁剪区域：用全屏截图 + setPos 偏移显示，消除 DPR 舍入晃动"""
        old_origin = self._crop_origin
        new_origin = QPointF(new_selection.x(), new_selection.y())
        delta = old_origin - new_origin

        self._crop_origin = new_origin

        # 用全屏截图 + 逻辑坐标 setPos 偏移，完全规避 int() 截断 DPR 的问题
        if self._background_item:
            if self._background_item.pixmap().cacheKey() != screen_pixmap.cacheKey():
                self._background_item.setPixmap(screen_pixmap)
            self._background_item.setPos(-new_selection.x(), -new_selection.y())

            new_scene_rect = QRectF(0, 0, new_selection.width(), new_selection.height())
            if self._scene.sceneRect() != new_scene_rect:
                self._scene.setSceneRect(new_scene_rect)
        else:
            self.set_background(screen_pixmap)
            if self._background_item:
                self._background_item.setPos(-new_selection.x(), -new_selection.y())

        if delta.x() != 0 or delta.y() != 0:
            for item in self._scene.items():
                if item != self._background_item:
                    item.moveBy(delta.x(), delta.y())

        # 为 Mosaic/Blur 保留裁剪后的背景（场景坐标系，供像素采样）
        dpr = mapper.screenshot_dpr
        physical_rect = QRect(
            round(new_selection.x() * dpr),
            round(new_selection.y() * dpr),
            round(new_selection.width() * dpr),
            round(new_selection.height() * dpr),
        )
        self._background_pixmap = screen_pixmap.copy(physical_rect)

        for item in self._scene.items():
            if isinstance(item, (MosaicItem, BlurItem)):
                item.update_background(self._background_pixmap)

    def set_tool(self, tool: EditTool) -> None:
        """切换工具"""
        self._finish_drawing()
        self._current_tool = tool

        if tool == EditTool.SELECT:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool == EditTool.TEXT:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        if self._fill_linked:
            self._fill_color = QColor(color.red(), color.green(), color.blue())

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width

    def set_fill_enabled(self, enabled: bool) -> None:
        self._fill_enabled = enabled

    def set_fill_color(self, color: QColor) -> None:
        self._fill_color = QColor(color.red(), color.green(), color.blue())

    def set_fill_opacity(self, opacity: int) -> None:
        self._fill_opacity = opacity

    def set_fill_linked(self, linked: bool) -> None:
        self._fill_linked = linked
        if linked:
            self._fill_color = QColor(
                self._pen_color.red(), self._pen_color.green(), self._pen_color.blue()
            )

    def set_font(self, font: QFont) -> None:
        self._font = font

    def set_font_size(self, size: int) -> None:
        self._font.setPointSize(size)

    def set_bold(self, bold: bool) -> None:
        self._font.setBold(bold)

    def set_italic(self, italic: bool) -> None:
        self._font.setItalic(italic)

    def set_mosaic_strength(self, strength: int) -> None:
        self._mosaic_strength = strength

    def set_blur_radius(self, radius: int) -> None:
        self._blur_radius = radius

    def export_image(self) -> QPixmap:
        """导出当前画布内容为 QPixmap（全物理分辨率）"""
        # 取消所有选中
        self._scene.clearSelection()

        if not self._background_pixmap:
            return QPixmap()

        # 使用场景范围渲染
        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return QPixmap()

        # 以背景图的 DPR 渲染，保持物理像素分辨率
        dpr = self._background_pixmap.devicePixelRatio()
        export_pixmap = QPixmap(int(rect.width() * dpr), int(rect.height() * dpr))
        export_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(export_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(dpr, dpr)
        self._scene.render(painter, rect, rect)
        painter.end()
        return export_pixmap

    # ==================== 鼠标事件处理 ====================

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        tool = self._current_tool
        if tool == EditTool.SELECT:
            self._move_start_positions.clear()
            for item in self._scene.selectedItems():
                if item != self._background_item:
                    self._move_start_positions[item] = QPointF(item.pos())
            super().mousePressEvent(event)
            return

        if tool == EditTool.TEXT:
            self._create_text_at(scene_pos)
            return

        if tool in (
            EditTool.RECT,
            EditTool.ELLIPSE,
            EditTool.ARROW,
            EditTool.MOSAIC,
            EditTool.BLUR,
        ):
            self._drawing = True
            self._draw_start = scene_pos
            self._create_temp_item(scene_pos)
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.pos())

        if self._drawing and self._draw_start is not None and self._temp_item:
            # 用 is not None 而非真值判断：QPointF(0,0) 在 PySide6 中为 falsy
            # （__bool__ 走 isNull()），从场景原点起绘时真值判断会误判为未开始绘制。
            self._update_temp_item(scene_pos)
            return

        if self._current_tool == EditTool.SELECT:
            item = self._scene.itemAt(scene_pos, self.transform())
            if (
                item
                and item != self._background_item
                and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            ):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            scene_pos = self.mapToScene(event.pos())
            self._finish_drawing_at(scene_pos)
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._current_tool == EditTool.SELECT
            and self._move_start_positions
        ):
            super().mouseReleaseEvent(event)
            for item, old_pos in self._move_start_positions.items():
                if item.scene() is not None:
                    new_pos = item.pos()
                    if old_pos != new_pos:
                        cmd = MoveAnnotationCommand(item, old_pos, new_pos)
                        self._undo_stack.push(cmd)
            self._move_start_positions.clear()
            return

        super().mouseReleaseEvent(event)

    # ==================== 绘制辅助方法 ====================

    def _create_temp_item(self, start: QPointF) -> None:
        """创建临时预览项"""
        tool = self._current_tool
        rect = QRectF(start, start)

        if tool == EditTool.RECT:
            item: QGraphicsRectItem | QGraphicsEllipseItem | ArrowAnnotation = (
                QGraphicsRectItem(rect)
            )
            item.setPen(QPen(self._pen_color, self._pen_width, Qt.PenStyle.DashLine))
            item.setBrush(Qt.BrushStyle.NoBrush)
        elif tool == EditTool.ELLIPSE:
            item = QGraphicsEllipseItem(rect)
            item.setPen(QPen(self._pen_color, self._pen_width, Qt.PenStyle.DashLine))
            item.setBrush(Qt.BrushStyle.NoBrush)
        elif tool == EditTool.ARROW:
            item = ArrowAnnotation(
                start,
                start,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
            )
            item.setPen(QPen(self._pen_color, self._pen_width, Qt.PenStyle.DashLine))
        elif tool in (EditTool.MOSAIC, EditTool.BLUR):
            item = QGraphicsRectItem(rect)
            item.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            item.setBrush(QBrush(QColor(0, 120, 215, 30)))
        else:
            return

        item.setZValue(999)
        self._scene.addItem(item)
        self._temp_item = item

    def _update_temp_item(self, current: QPointF) -> None:
        """更新临时预览项"""
        if self._draw_start is None or not self._temp_item:
            # _draw_start 用 is None 判断：QPointF(0,0) 为 falsy，真值判断
            # 会误把已设的原点起点当作未绘制，导致从原点起绘无法更新预览。
            return

        tool = self._current_tool
        start = self._draw_start

        if tool == EditTool.ARROW:
            if isinstance(self._temp_item, ArrowAnnotation):
                self._temp_item.set_end(current)
        else:
            rect = QRectF(start, current).normalized()
            if isinstance(self._temp_item, QGraphicsRectItem) or hasattr(
                self._temp_item, "setRect"
            ):
                self._temp_item.setRect(rect)

    def _finish_drawing_at(self, end: QPointF) -> None:
        """完成绘制，创建正式标注项"""
        self._drawing = False
        if self._draw_start is None:
            # is None 而非真值判断：QPointF(0,0) 为 falsy，原点起绘需被正确识别。
            self._remove_temp()
            return

        start = self._draw_start
        rect = QRectF(start, end).normalized()

        # 最小尺寸检查
        if rect.width() < 3 and rect.height() < 3:
            self._remove_temp()
            return

        tool = self._current_tool
        self._remove_temp()

        item: (
            RectAnnotation
            | EllipseAnnotation
            | ArrowAnnotation
            | MosaicItem
            | BlurItem
            | None
        ) = None

        if tool == EditTool.RECT:
            item = RectAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
                fill_opacity=self._fill_opacity,
            )
        elif tool == EditTool.ELLIPSE:
            item = EllipseAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
                fill_opacity=self._fill_opacity,
            )
        elif tool == EditTool.ARROW:
            item = ArrowAnnotation(
                start,
                end,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
            )
        elif tool == EditTool.MOSAIC:
            if self._background_pixmap:
                item = MosaicItem(
                    rect,
                    self._background_pixmap,
                    strength=self._mosaic_strength,
                )
        elif tool == EditTool.BLUR:
            if self._background_pixmap:
                item = BlurItem(
                    rect,
                    self._background_pixmap,
                    radius=self._blur_radius,
                )
        if item:
            cmd = AddAnnotationCommand(self._scene, item)
            self._undo_stack.push(cmd)

        self._draw_start = None

    def _create_text_at(self, pos: QPointF) -> None:
        """在指定位置创建文字标注"""
        item = TextAnnotation(
            text="文字",
            pos=pos,
            font=QFont(self._font),
            color=self._pen_color,
        )
        cmd = AddAnnotationCommand(self._scene, item, "添加文字")
        self._undo_stack.push(cmd)
        item.enable_editing()

    def _finish_drawing(self) -> None:
        """取消当前绘制操作"""
        self._drawing = False
        self._remove_temp()
        self._draw_start = None

    def _remove_temp(self) -> None:
        """移除临时预览项"""
        if self._temp_item:
            self._scene.removeItem(self._temp_item)
            self._temp_item = None
