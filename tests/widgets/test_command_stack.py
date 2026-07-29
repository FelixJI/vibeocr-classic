"""command_stack 撤销/重做命令测试"""

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QUndoStack

from vibeocr.classic.widgets.editor.command_stack import (
    AddAnnotationCommand,
    MoveAnnotationCommand,
    PropertyChangeCommand,
    RemoveAnnotationCommand,
    ResizeAnnotationCommand,
    TextChangeCommand,
    create_undo_stack,
)


class TestAddAnnotationCommand:
    def test_redo_adds_item(self, qapp):
        scene = MagicMock()
        item = MagicMock()
        cmd = AddAnnotationCommand(scene, item)
        cmd.redo()
        scene.addItem.assert_called_once_with(item)

    def test_undo_removes_item(self, qapp):
        scene = MagicMock()
        item = MagicMock()
        cmd = AddAnnotationCommand(scene, item)
        cmd.undo()
        scene.removeItem.assert_called_once_with(item)


class TestRemoveAnnotationCommand:
    def test_redo_removes_item(self, qapp):
        scene = MagicMock()
        item = MagicMock()
        cmd = RemoveAnnotationCommand(scene, item)
        cmd.redo()
        scene.removeItem.assert_called_once_with(item)

    def test_undo_adds_item(self, qapp):
        scene = MagicMock()
        item = MagicMock()
        cmd = RemoveAnnotationCommand(scene, item)
        cmd.undo()
        scene.addItem.assert_called_once_with(item)


class TestMoveAnnotationCommand:
    def test_redo_sets_new_pos(self, qapp):
        item = MagicMock()
        old = QPointF(0, 0)
        new = QPointF(10, 20)
        cmd = MoveAnnotationCommand(item, old, new)
        cmd.redo()
        item.setPos.assert_called_once_with(new)

    def test_undo_sets_old_pos(self, qapp):
        item = MagicMock()
        old = QPointF(0, 0)
        new = QPointF(10, 20)
        cmd = MoveAnnotationCommand(item, old, new)
        cmd.undo()
        item.setPos.assert_called_once_with(old)


class TestResizeAnnotationCommand:
    def test_redo_applies_new_rect(self, qapp):
        item = MagicMock()
        item.pos.return_value = QPointF(0, 0)
        old = QRectF(0, 0, 100, 100)
        new = QRectF(0, 0, 200, 200)
        cmd = ResizeAnnotationCommand(item, old, new)
        cmd.redo()
        item.setRect.assert_called_once()
        item.update.assert_called()

    def test_undo_applies_old_rect(self, qapp):
        item = MagicMock()
        item.pos.return_value = QPointF(0, 0)
        old = QRectF(0, 0, 100, 100)
        new = QRectF(0, 0, 200, 200)
        cmd = ResizeAnnotationCommand(item, old, new)
        cmd.undo()
        item.setRect.assert_called_once()

    def test_item_without_set_rect(self, qapp):
        item = MagicMock(spec=["update"])
        old = QRectF(0, 0, 100, 100)
        new = QRectF(0, 0, 200, 200)
        cmd = ResizeAnnotationCommand(item, old, new)
        cmd.redo()
        item.update.assert_called()


class TestPropertyChangeCommand:
    def test_redo_uses_setter(self, qapp):
        item = MagicMock()
        item.set_color = MagicMock()
        cmd = PropertyChangeCommand(item, "color", "red", "blue")
        cmd.redo()
        item.set_color.assert_called_once_with("blue")

    def test_undo_uses_setter(self, qapp):
        item = MagicMock()
        item.set_color = MagicMock()
        cmd = PropertyChangeCommand(item, "color", "red", "blue")
        cmd.undo()
        item.set_color.assert_called_once_with("red")

    def test_fallback_to_setattr(self, qapp):
        item = MagicMock(spec=["update", "opacity"])
        cmd = PropertyChangeCommand(item, "opacity", 0.5, 1.0)
        cmd.redo()
        assert item.opacity == 1.0

    def test_custom_description(self, qapp):
        item = MagicMock()
        cmd = PropertyChangeCommand(item, "x", 1, 2, description="自定义")
        assert cmd.text() == "自定义"

    def test_default_description(self, qapp):
        item = MagicMock()
        cmd = PropertyChangeCommand(item, "x", 1, 2)
        assert "x" in cmd.text()


class TestTextChangeCommand:
    def test_redo_with_setPlainText(self, qapp):
        item = MagicMock()
        cmd = TextChangeCommand(item, "old", "new")
        cmd.redo()
        item.setPlainText.assert_called_once_with("new")

    def test_undo_with_setPlainText(self, qapp):
        item = MagicMock()
        cmd = TextChangeCommand(item, "old", "new")
        cmd.undo()
        item.setPlainText.assert_called_once_with("old")

    def test_fallback_to_setHtml(self, qapp):
        item = MagicMock(spec=["setHtml", "update"])
        cmd = TextChangeCommand(item, "old", "new")
        cmd.redo()
        item.setHtml.assert_called_once_with("new")


class TestCreateUndoStack:
    def test_creates_stack_with_limit(self, qapp):
        stack = create_undo_stack()
        assert isinstance(stack, QUndoStack)
        assert stack.undoLimit() == 50
