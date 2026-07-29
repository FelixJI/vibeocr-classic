"""选中态装饰器

绘制选中边框和 8 个缩放手柄，处理手柄拖拽交互。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from vibeocr.classic.widgets.editor.annotation_items import (
    ArrowAnnotation,
    BlurItem,
    EllipseAnnotation,
    MosaicItem,
    RectAnnotation,
    TextAnnotation,
)

HANDLE_SIZE = 6
MIN_SIZE = 10

SELECTION_COLOR = QColor(0, 120, 215)

# 手柄索引
TOP_LEFT = 0
TOP_CENTER = 1
TOP_RIGHT = 2
MIDDLE_LEFT = 3
MIDDLE_RIGHT = 4
BOTTOM_LEFT = 5
BOTTOM_CENTER = 6
BOTTOM_RIGHT = 7


class SelectionDecorator(QGraphicsItem):
    """选中态装饰器，绘制边框和缩放手柄"""

    def __init__(self, decorated: QGraphicsItem):
        super().__init__()
        self._decorated = decorated
        self._active_handle: int = -1
        self._drag_start_scene: QPointF | None = None
        self._initial_rect: QRectF | None = None
        self._initial_start: QPointF | None = None
        self._initial_end: QPointF | None = None
        self._initial_font_size: float | None = None

        self.setZValue(1000)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setAcceptHoverEvents(True)

        decorated.installSceneEventFilter(self)

    @property
    def decorated_item(self) -> QGraphicsItem:
        return self._decorated

    def handle_positions(self, rect: QRectF) -> list[QPointF]:
        cx = rect.left() + rect.width() / 2
        cy = rect.top() + rect.height() / 2
        return [
            QPointF(rect.left(), rect.top()),  # 0: top-left
            QPointF(cx, rect.top()),  # 1: top-center
            QPointF(rect.right(), rect.top()),  # 2: top-right
            QPointF(rect.left(), cy),  # 3: middle-left
            QPointF(rect.right(), cy),  # 4: middle-right
            QPointF(rect.left(), rect.bottom()),  # 5: bottom-left
            QPointF(cx, rect.bottom()),  # 6: bottom-center
            QPointF(rect.right(), rect.bottom()),  # 7: bottom-right
        ]

    def hit_test(self, pos: QPointF, handles: list[QPointF]) -> int:
        threshold = HANDLE_SIZE
        for i, hp in enumerate(handles):
            dx = abs(pos.x() - hp.x())
            dy = abs(pos.y() - hp.y())
            if dx <= threshold and dy <= threshold:
                return i
        return -1

    def calculate_resize(self, handle: int, pos: QPointF, original: QRectF) -> QRectF:
        left = original.left()
        top = original.top()
        right = original.right()
        bottom = original.bottom()

        if handle in (TOP_LEFT, MIDDLE_LEFT, BOTTOM_LEFT):
            left = pos.x()
        if handle in (TOP_RIGHT, MIDDLE_RIGHT, BOTTOM_RIGHT):
            right = pos.x()
        if handle in (TOP_LEFT, TOP_CENTER, TOP_RIGHT):
            top = pos.y()
        if handle in (BOTTOM_LEFT, BOTTOM_CENTER, BOTTOM_RIGHT):
            bottom = pos.y()

        if right - left < MIN_SIZE:
            if handle in (TOP_LEFT, MIDDLE_LEFT, BOTTOM_LEFT):
                left = right - MIN_SIZE
            else:
                right = left + MIN_SIZE
        if bottom - top < MIN_SIZE:
            if handle in (TOP_LEFT, TOP_CENTER, TOP_RIGHT):
                top = bottom - MIN_SIZE
            else:
                bottom = top + MIN_SIZE

        return QRectF(left, top, right - left, bottom - top)

    def boundingRect(self) -> QRectF:
        margin = HANDLE_SIZE + 2
        r = self._decorated.sceneBoundingRect()
        return r.adjusted(-margin, -margin, margin, margin)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,  # type: ignore[override]
    ) -> None:
        rect = self._decorated.sceneBoundingRect()
        handles = self.handle_positions(rect)

        painter.setPen(QPen(SELECTION_COLOR, 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        half = HANDLE_SIZE / 2
        painter.setPen(QPen(SELECTION_COLOR, 1))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        for hp in handles:
            painter.drawRect(
                QRectF(hp.x() - half, hp.y() - half, HANDLE_SIZE, HANDLE_SIZE)
            )

    def sceneEventFilter(self, watched, event):
        if event.type() == QEvent.Type.GraphicsSceneMouseMove:
            self.prepareGeometryChange()
            self.update()
        return False

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        rect = self._decorated.sceneBoundingRect()
        handles = self.handle_positions(rect)
        scene_pos = event.scenePos()
        hit = self.hit_test(scene_pos, handles)

        if hit >= 0:
            self._active_handle = hit
            self._drag_start_scene = scene_pos
            self._initial_rect = rect
            self._store_initial_state(rect)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._active_handle < 0 or not self._initial_rect:
            super().mouseMoveEvent(event)
            return

        scene_pos = event.scenePos()
        new_rect = self.calculate_resize(
            self._active_handle, scene_pos, self._initial_rect
        )
        self._apply_resize(new_rect)
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._active_handle >= 0 and event.button() == Qt.MouseButton.LeftButton:
            canvas = self._find_canvas()
            if canvas and self._initial_rect:
                new_rect = self._get_current_rect()
                if new_rect and self._initial_rect != new_rect:
                    self._finalize_resize(canvas, self._initial_rect, new_rect)
            self._active_handle = -1
            self._drag_start_scene = None
            self._initial_rect = None
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _store_initial_state(self, rect: QRectF) -> None:
        if isinstance(self._decorated, ArrowAnnotation):
            self._initial_start = QPointF(self._decorated._start)
            self._initial_end = QPointF(self._decorated._end)
        elif isinstance(self._decorated, TextAnnotation):
            self._initial_font_size = self._decorated.font().pointSizeF()

    def _apply_resize(self, new_rect: QRectF) -> None:
        item = self._decorated

        if isinstance(item, (RectAnnotation, EllipseAnnotation)):
            item.setRect(new_rect.translated(-item.pos()))
        elif isinstance(item, (MosaicItem, BlurItem)):
            item.set_resizing(True)
            item.setRect(new_rect.translated(-item.pos()))
        elif isinstance(item, ArrowAnnotation):
            self._resize_arrow(item, new_rect)
        elif isinstance(item, TextAnnotation):
            self._resize_text(item, new_rect)

        self.prepareGeometryChange()
        self.update()

    def _resize_arrow(self, item: ArrowAnnotation, new_rect: QRectF) -> None:
        if not self._initial_rect or not self._initial_start or not self._initial_end:
            return
        old = self._initial_rect
        sx = new_rect.width() / old.width() if old.width() > 0 else 1
        sy = new_rect.height() / old.height() if old.height() > 0 else 1
        new_start = QPointF(
            new_rect.left() + (self._initial_start.x() - old.left()) * sx,
            new_rect.top() + (self._initial_start.y() - old.top()) * sy,
        )
        new_end = QPointF(
            new_rect.left() + (self._initial_end.x() - old.left()) * sx,
            new_rect.top() + (self._initial_end.y() - old.top()) * sy,
        )
        item._start = new_start
        item._end = new_end
        item._update_path()

    def _resize_text(self, item: TextAnnotation, new_rect: QRectF) -> None:
        if not self._initial_rect or not self._initial_font_size:
            return
        old = self._initial_rect
        ratio = new_rect.height() / old.height() if old.height() > 0 else 1
        new_size = max(6, self._initial_font_size * ratio)
        font = item.font()
        font.setPointSizeF(new_size)
        item.setFont(font)

    def _get_current_rect(self) -> QRectF | None:
        item = self._decorated
        if isinstance(item, (ArrowAnnotation, TextAnnotation)):
            return item.boundingRect().translated(item.pos())
        if hasattr(item, "rect"):
            return item.rect().translated(item.pos())
        return None

    def _finalize_resize(self, canvas, old_rect: QRectF, new_rect: QRectF) -> None:
        item = self._decorated
        if isinstance(item, (MosaicItem, BlurItem)):
            item.set_resizing(False)
        from vibeocr.classic.widgets.editor.command_stack import ResizeAnnotationCommand

        cmd = ResizeAnnotationCommand(item, old_rect, new_rect)
        canvas.undo_stack.push(cmd)

    def _find_canvas(self):
        from vibeocr.classic.widgets.editor.edit_canvas import EditCanvas

        scene = self.scene()
        if scene:
            for view in scene.views():
                if isinstance(view, EditCanvas):
                    return view
        return None

    def cleanup(self) -> None:
        self._decorated.removeSceneEventFilter(self)
