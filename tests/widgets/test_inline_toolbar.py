# tests/test_inline_toolbar.py
"""Tests for InlineToolbar."""

from PySide6.QtWidgets import QToolButton

from vibeocr.classic.widgets.editor.annotation_items import EditTool
from vibeocr.classic.widgets.inline_toolbar import InlineToolbar


class TestInlineToolbar:
    def test_initial_state(self, qapp):
        toolbar = InlineToolbar()
        assert toolbar._current_tool is None

    def test_tool_buttons_exist(self, qapp):
        toolbar = InlineToolbar()
        assert len(toolbar._tool_buttons) == 7

    def test_tool_buttons_are_qtoolbutton(self, qapp):
        toolbar = InlineToolbar()
        for btn in toolbar._tool_buttons.values():
            assert isinstance(btn, QToolButton)

    def test_action_buttons_are_qtoolbutton(self, qapp):
        toolbar = InlineToolbar()
        assert isinstance(toolbar._btn_undo, QToolButton)
        assert isinstance(toolbar._btn_redo, QToolButton)
        assert isinstance(toolbar._btn_save, QToolButton)
        assert isinstance(toolbar._btn_copy, QToolButton)
        assert isinstance(toolbar._btn_cancel, QToolButton)

    def test_tool_changed_signal(self, qapp):
        toolbar = InlineToolbar()
        received = []
        toolbar.tool_changed.connect(lambda t: received.append(t))
        toolbar._on_tool_clicked(EditTool.RECT)
        assert received == [EditTool.RECT]

    def test_undo_button_initially_disabled(self, qapp):
        toolbar = InlineToolbar()
        assert not toolbar._btn_undo.isEnabled()

    def test_redo_button_initially_disabled(self, qapp):
        toolbar = InlineToolbar()
        assert not toolbar._btn_redo.isEnabled()

    def test_set_undo_enabled(self, qapp):
        toolbar = InlineToolbar()
        toolbar.set_undo_enabled(True)
        assert toolbar._btn_undo.isEnabled()

    def test_set_redo_enabled(self, qapp):
        toolbar = InlineToolbar()
        toolbar.set_redo_enabled(True)
        assert toolbar._btn_redo.isEnabled()

    def test_cancel_requested_signal(self, qapp):
        toolbar = InlineToolbar()
        received = []
        toolbar.cancel_requested.connect(lambda: received.append(True))
        toolbar._btn_cancel.clicked.emit()
        assert len(received) == 1
