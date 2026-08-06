# tests/widgets/test_text_block_options_widget.py
"""TextBlockOptionsWidget 测试。

覆盖：
- 构造时从 OCRPreferences 回填控件状态
- 控件变化触发持久化（变化即写盘）
- get_text_options() 与控件状态一致
"""

import pytest

from vibeocr.classic.recognition_settings import (
    LINE_MODE_KEEP,
    LINE_MODE_MERGE,
    LINE_MODE_SMART,
    TextBlockOptions,
)
from vibeocr.classic.utils.ocr_preferences import OCRPreferences
from vibeocr.classic.widgets.text_block_options_widget import TextBlockOptionsWidget


@pytest.fixture
def widget(qtbot, tmp_path):
    """创建组件，并用 tmp_path 隔离 OCRPreferences 单例。"""
    OCRPreferences.reset_instance()
    OCRPreferences.instance(tmp_path)
    w = TextBlockOptionsWidget()
    qtbot.addWidget(w)
    yield w
    OCRPreferences.reset_instance()


class TestLoadFromPreferences:
    def test_defaults_loaded(self, widget):
        """空配置 → 控件显示默认值（merge / 全关 / drop_blank 开）。"""
        assert widget._mode_combo.currentData() == LINE_MODE_MERGE
        assert widget._join_space_cb.isChecked() is False
        assert widget._indent_cb.isChecked() is False
        assert widget._drop_blank_cb.isChecked() is True

    def test_custom_values_loaded(self, qtbot, tmp_path):
        """预先写入自定义选项 → 构造时控件回填这些值。"""
        OCRPreferences.reset_instance()
        prefs = OCRPreferences.instance(tmp_path)
        prefs.set_text_options(
            TextBlockOptions(
                line_mode=LINE_MODE_SMART,
                block_join_space=True,
                chinese_indent=True,
                drop_blank_blocks=False,
            )
        )
        OCRPreferences.reset_instance()
        # widget 构造时 load() 调 instance() 无参数，需先以 tmp_path 建好单例
        OCRPreferences.instance(tmp_path)

        w = TextBlockOptionsWidget()
        qtbot.addWidget(w)
        try:
            assert w._mode_combo.currentData() == LINE_MODE_SMART
            assert w._join_space_cb.isChecked() is True
            assert w._indent_cb.isChecked() is True
            assert w._drop_blank_cb.isChecked() is False
        finally:
            OCRPreferences.reset_instance()


class TestPersistOnChange:
    def test_mode_change_persists(self, widget, tmp_path):
        widget._mode_combo.setCurrentIndex(
            widget._mode_combo.findData(LINE_MODE_KEEP)
        )
        loaded = OCRPreferences.instance(tmp_path).get_text_options()
        assert loaded.line_mode == LINE_MODE_KEEP

    def test_checkbox_change_persists(self, widget, tmp_path):
        widget._join_space_cb.setChecked(True)
        widget._indent_cb.setChecked(True)
        widget._drop_blank_cb.setChecked(False)

        loaded = OCRPreferences.instance(tmp_path).get_text_options()
        assert loaded.block_join_space is True
        assert loaded.chinese_indent is True
        assert loaded.drop_blank_blocks is False

    def test_options_changed_signal_emitted(self, widget, qtbot):
        with qtbot.waitSignal(widget.options_changed, timeout=1000) as blocker:
            widget._join_space_cb.setChecked(True)
        assert isinstance(blocker.args[0], TextBlockOptions)
        assert blocker.args[0].block_join_space is True

    def test_load_does_not_trigger_persist(self, qtbot, tmp_path):
        """构造（含 load 回填）期间不应触发写盘 / 信号。

        间接验证：先写入非默认值，构造后这些值应原样保留（load 不写盘覆盖）。
        """
        OCRPreferences.reset_instance()
        OCRPreferences.instance(tmp_path).set_text_options(
            TextBlockOptions(line_mode=LINE_MODE_SMART, block_join_space=True)
        )
        OCRPreferences.reset_instance()
        OCRPreferences.instance(tmp_path)
        w = TextBlockOptionsWidget()
        qtbot.addWidget(w)
        try:
            # 控件回填了 SMART；配置文件内容仍是构造前写入的 SMART（未被覆盖）
            loaded = OCRPreferences.instance(tmp_path).get_text_options()
            assert loaded.line_mode == LINE_MODE_SMART
            assert loaded.block_join_space is True
        finally:
            OCRPreferences.reset_instance()


class TestGetTextOptions:
    def test_get_reflects_widget_state(self, widget):
        widget._mode_combo.setCurrentIndex(
            widget._mode_combo.findData(LINE_MODE_SMART)
        )
        widget._join_space_cb.setChecked(True)
        opts = widget.get_text_options()
        assert opts.line_mode == LINE_MODE_SMART
        assert opts.block_join_space is True
        assert opts.chinese_indent is False
        assert opts.drop_blank_blocks is True


class TestCollapsible:
    def test_is_collapsible(self, widget):
        """改基类后支持折叠 API。"""
        from vibeocr.classic.widgets.collapsible_group_box import CollapsibleGroupBox

        assert isinstance(widget, CollapsibleGroupBox)
        widget.set_collapsed(True)
        assert widget.is_collapsed() is True
