"""编辑画布

基于 QGraphicsView + QGraphicsScene 的图像编辑画布。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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
from vibeocr.classic.widgets.editor.selection_decorator import SelectionDecorator


class EditCanvas(QGraphicsView):
    """图像编辑画布"""

    # 当前工具变化（供外部同步）
    tool_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 渲染设置（优化性能）
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        # 使用最小视口更新模式（仅重绘变化的区域）
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        # 启用背景缓存（减少重绘开销）
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))

        # 启用场景索引优化（加速项目查找）
        self._scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)

        # 撤销栈
        self._undo_stack: QUndoStack = create_undo_stack(self)

        # 背景图
        self._background_item: QGraphicsPixmapItem | None = None
        self._background_pixmap: QPixmap | None = None

        # 当前工具和属性
        self._current_tool: EditTool = EditTool.SELECT
        self._pen_color: QColor = QColor(255, 0, 0)
        self._pen_width: int = 2
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor(255, 0, 0, 50)
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

        # 移动跟踪：记录移动开始时各项的位置
        self._move_start_positions: dict[QGraphicsItem, QPointF] = {}

        # 选中装饰器
        self._decorators: dict[int, SelectionDecorator] = {}
        self._scene.selectionChanged.connect(self._on_selection_changed)

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    def _on_selection_changed(self) -> None:
        """选中变化时管理装饰器"""
        selected = {
            id(item)
            for item in self._scene.selectedItems()
            if item != self._background_item
            and not isinstance(item, SelectionDecorator)
        }

        # 移除不再选中的装饰器
        to_remove = [iid for iid in self._decorators if iid not in selected]
        for iid in to_remove:
            dec = self._decorators.pop(iid)
            dec.cleanup()
            self._scene.removeItem(dec)

        # 为新选中的项创建装饰器
        to_add = selected - set(self._decorators.keys())
        for item in self._scene.selectedItems():
            if id(item) in to_add:
                dec = SelectionDecorator(item)
                self._scene.addItem(dec)
                self._decorators[id(item)] = dec

        # 通知外部选中变化
        self.tool_changed.emit(self._current_tool)

    @property
    def selected_annotation(self):
        """获取当前选中的标注项"""
        for item in self._scene.selectedItems():
            if item != self._background_item and not isinstance(
                item, SelectionDecorator
            ):
                return item
        return None

    def set_background(self, pixmap: QPixmap) -> None:
        """设置背景截图"""
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
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_tool(self, tool: EditTool) -> None:
        """切换工具"""
        # 如果切换工具，先完成当前操作
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
        # 更新填充色（同色半透明）
        self._fill_color = QColor(color.red(), color.green(), color.blue(), 50)

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width

    def set_fill_enabled(self, enabled: bool) -> None:
        self._fill_enabled = enabled

    def set_font(self, font: QFont) -> None:
        self._font = font

    def set_font_size(self, size: int) -> None:
        self._font.setPointSize(size)

    def set_bold(self, bold: bool) -> None:
        """设置粗体"""
        self._font.setBold(bold)

    def set_italic(self, italic: bool) -> None:
        """设置斜体"""
        self._font.setItalic(italic)

    def set_mosaic_strength(self, strength: int) -> None:
        self._mosaic_strength = strength

    def set_blur_radius(self, radius: int) -> None:
        self._blur_radius = radius

    def export_image(self) -> QPixmap:
        """导出当前画布内容为 QPixmap"""
        # 取消所有选中
        self._scene.clearSelection()

        # 使用场景范围渲染
        rect = self._scene.sceneRect()
        pixmap = QPixmap(int(rect.width()), int(rect.height()))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._scene.render(painter, QRectF(pixmap.rect()), rect)
        painter.end()
        return pixmap

    # ==================== 鼠标事件处理 ====================

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        scene_pos = self.mapToScene(event.pos())
        tool = self._current_tool

        if tool == EditTool.SELECT:
            # 记录选中项的初始位置（用于移动撤销）
            self._move_start_positions.clear()
            for item in self._scene.selectedItems():
                if item != self._background_item:
                    self._move_start_positions[item] = QPointF(item.pos())
            super().mousePressEvent(event)
            return

        if tool == EditTool.TEXT:
            self._create_text_at(scene_pos)
            return

        # 图形绘制工具
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

        # 绘制模式：更新临时项和显示尺寸提示
        if self._drawing and self._draw_start and self._temp_item:
            self._update_temp_item(scene_pos)
            # 显示实时尺寸提示
            self._show_size_tooltip(event.pos(), scene_pos)
            return

        # SELECT 模式：更新光标以提示可移动
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

    def _show_size_tooltip(self, view_pos, scene_pos: QPointF) -> None:
        """显示绘制时的尺寸提示"""
        if not self._draw_start:
            return

        rect = QRectF(self._draw_start, scene_pos).normalized()
        width = int(rect.width())
        height = int(rect.height())

        # 使用 QToolTip 显示尺寸
        from PySide6.QtWidgets import QToolTip

        QToolTip.showText(
            self.mapToGlobal(view_pos),
            f"{width} × {height}",
            self,
        )

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            scene_pos = self.mapToScene(event.pos())
            self._finish_drawing_at(scene_pos)
            return

        # SELECT 模式：检查移动并创建撤销命令
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._current_tool == EditTool.SELECT
            and self._move_start_positions
        ):
            super().mouseReleaseEvent(event)
            # 检查位置是否有变化
            for item, old_pos in self._move_start_positions.items():
                if item.scene() is not None:  # 确保项仍在场景中
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
        item: QGraphicsRectItem | QGraphicsEllipseItem | ArrowAnnotation

        if tool == EditTool.RECT:
            item = QGraphicsRectItem(rect)
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
            # 箭头临时预览时用虚线
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
        if not self._draw_start or not self._temp_item:
            return

        tool = self._current_tool
        start = self._draw_start

        if tool == EditTool.ARROW:
            if isinstance(self._temp_item, ArrowAnnotation):
                self._temp_item.set_end(current)
        else:
            # 矩形/椭圆/马赛克/模糊/裁剪：更新矩形
            rect = QRectF(start, current).normalized()
            if isinstance(self._temp_item, QGraphicsRectItem) or hasattr(
                self._temp_item, "setRect"
            ):
                self._temp_item.setRect(rect)

    def _finish_drawing_at(self, end: QPointF) -> None:
        """完成绘制，创建正式标注项"""
        self._drawing = False
        if not self._draw_start:
            self._remove_temp()
            return

        start = self._draw_start
        rect = QRectF(start, end).normalized()

        # 最小尺寸检查
        if rect.width() < 3 and rect.height() < 3:
            self._remove_temp()
            return

        tool = self._current_tool

        # 移除临时项
        self._remove_temp()
        self._scene.clearSelection()

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
            )
        elif tool == EditTool.ELLIPSE:
            item = EllipseAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
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

    def resizeEvent(self, event) -> None:
        """调整视图大小时重新适配"""
        super().resizeEvent(event)
        if self._background_item:
            self.fitInView(
                self._scene.sceneRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
