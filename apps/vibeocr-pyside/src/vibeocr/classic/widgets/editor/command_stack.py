"""撤销/重做命令栈

基于 Qt 的 QUndoStack + QUndoCommand 实现。
支持添加、删除、移动、调整大小和属性修改等操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QUndoCommand, QUndoStack

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene


class AddAnnotationCommand(QUndoCommand):
    """添加标注项命令"""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "添加标注",
    ):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        self._scene.addItem(self._item)

    def undo(self) -> None:
        self._scene.removeItem(self._item)


class RemoveAnnotationCommand(QUndoCommand):
    """删除标注项命令"""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "删除标注",
    ):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        self._scene.removeItem(self._item)

    def undo(self) -> None:
        self._scene.addItem(self._item)


class MoveAnnotationCommand(QUndoCommand):
    """移动标注项命令"""

    def __init__(
        self,
        item: QGraphicsItem,
        old_pos: QPointF,
        new_pos: QPointF,
        description: str = "移动标注",
    ):
        super().__init__(description)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos

    def redo(self) -> None:
        self._item.setPos(self._new_pos)

    def undo(self) -> None:
        self._item.setPos(self._old_pos)


class ResizeAnnotationCommand(QUndoCommand):
    """调整标注项大小命令"""

    def __init__(
        self,
        item: QGraphicsItem,
        old_rect: QRectF,
        new_rect: QRectF,
        description: str = "调整大小",
    ):
        super().__init__(description)
        self._item = item
        self._old_rect = QRectF(old_rect)
        self._new_rect = QRectF(new_rect)

    def redo(self) -> None:
        self._apply_rect(self._new_rect)

    def undo(self) -> None:
        self._apply_rect(self._old_rect)

    def _apply_rect(self, rect: QRectF) -> None:
        if hasattr(self._item, "setRect"):
            local_rect = rect.translated(-self._item.pos())
            self._item.setRect(local_rect)

        # 延迟 import 避免循环引用
        from vibeocr.classic.widgets.editor.annotation_items import BlurItem, MosaicItem

        if isinstance(self._item, (MosaicItem, BlurItem)):
            self._item.regenerate()
        self._item.update()


class PropertyChangeCommand(QUndoCommand):
    """属性修改命令

    通用的属性修改命令，支持任意属性的撤销/重做。
    """

    def __init__(
        self,
        item: QGraphicsItem,
        prop_name: str,
        old_value: Any,
        new_value: Any,
        description: str | None = None,
    ):
        super().__init__(description or f"修改{prop_name}")
        self._item = item
        self._prop_name = prop_name
        self._old_value = old_value
        self._new_value = new_value

    def redo(self) -> None:
        self._apply_value(self._new_value)

    def undo(self) -> None:
        self._apply_value(self._old_value)

    def _apply_value(self, value: Any) -> None:
        """应用属性值"""
        # 尝试使用 setter 方法
        setter_name = f"set_{self._prop_name}"
        if hasattr(self._item, setter_name):
            getattr(self._item, setter_name)(value)
        elif hasattr(self._item, self._prop_name):
            setattr(self._item, self._prop_name, value)
        self._item.update()


class TextChangeCommand(QUndoCommand):
    """文字内容修改命令

    专门用于 TextAnnotation 的文字内容修改。
    """

    def __init__(
        self,
        item: QGraphicsItem,
        old_text: str,
        new_text: str,
        description: str = "修改文字",
    ):
        super().__init__(description)
        self._item = item
        self._old_text = old_text
        self._new_text = new_text

    def redo(self) -> None:
        if hasattr(self._item, "setPlainText"):
            self._item.setPlainText(self._new_text)
        elif hasattr(self._item, "setHtml"):
            self._item.setHtml(self._new_text)
        self._item.update()

    def undo(self) -> None:
        if hasattr(self._item, "setPlainText"):
            self._item.setPlainText(self._old_text)
        elif hasattr(self._item, "setHtml"):
            self._item.setHtml(self._old_text)
        self._item.update()


def create_undo_stack(parent=None) -> QUndoStack:
    """创建配置好的 QUndoStack"""
    stack = QUndoStack(parent)
    stack.setUndoLimit(50)
    return stack
