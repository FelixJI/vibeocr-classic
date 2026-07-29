"""ExportSettingsWidget 按钮状态测试。

「导出当前」按钮在有结果前应禁用，避免点击后静默无反应。
"""

from types import SimpleNamespace

from vibeocr.classic.widgets.export_settings_widget import ExportSettingsWidget


class TestExportCurrentButtonState:
    def test_export_current_disabled_initially(self, qapp):
        widget = ExportSettingsWidget()
        assert widget._export_btn.isEnabled() is False

    def test_export_current_enabled_after_set_result(self, qapp):
        widget = ExportSettingsWidget()
        widget.set_current_result(SimpleNamespace(raw_text="x"))
        assert widget._export_btn.isEnabled() is True

    def test_export_current_disabled_when_result_cleared(self, qapp):
        widget = ExportSettingsWidget()
        widget.set_current_result(SimpleNamespace(raw_text="x"))
        widget.set_current_result(None)
        assert widget._export_btn.isEnabled() is False

    def test_export_all_stays_enabled_regardless_of_result(self, qapp):
        widget = ExportSettingsWidget()
        # 「导出全部」不依赖单个结果，初始即可用
        assert widget._export_all_btn.isEnabled() is True
        widget.set_current_result(None)
        assert widget._export_all_btn.isEnabled() is True
