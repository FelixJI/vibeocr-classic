"""InlineRecognitionPanel tests"""

from vibeocr.backend.core.pipelines import OCRPipeline
from vibeocr.backend.models.ocr_options import OCROptions
from vibeocr.classic.widgets.inline_recognition_panel import InlineRecognitionPanel


class TestInlineRecognitionPanel:
    def test_initial_pipeline_is_ocr(self, qapp):
        panel = InlineRecognitionPanel()
        options = panel.get_options()
        assert options.pipeline == OCRPipeline.OCR

    def test_pipeline_buttons_exist(self, qapp):
        panel = InlineRecognitionPanel()
        assert len(panel._pipeline_buttons) == len(OCRPipeline)

    def test_get_options_uses_persisted(self, qapp, tmp_path):
        """get_options 返回持久化的选项而非默认值"""
        from vibeocr.classic.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            custom_opts = OCROptions(
                pipeline=OCRPipeline.OCR,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
            )
            prefs.set_pipeline_options("screenshot", OCRPipeline.OCR, custom_opts)

            panel = InlineRecognitionPanel()
            options = panel.get_options()
            assert options.use_doc_orientation_classify is False
            assert options.use_doc_unwarping is False
        finally:
            OCRPreferences.reset_instance()

    def test_click_button_loads_persisted_options(self, qapp, tmp_path):
        """点击按钮加载该管道的持久化选项"""
        from vibeocr.classic.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            custom_opts = OCROptions(
                pipeline=OCRPipeline.PP_STRUCTURE_V3,
                use_table_recognition=False,
            )
            prefs.set_pipeline_options(
                "screenshot", OCRPipeline.PP_STRUCTURE_V3, custom_opts
            )

            panel = InlineRecognitionPanel()
            panel._pipeline_buttons[OCRPipeline.PP_STRUCTURE_V3].click()
            options = panel.get_options()
            assert options.pipeline == OCRPipeline.PP_STRUCTURE_V3
            assert options.use_table_recognition is False
        finally:
            OCRPreferences.reset_instance()

    def test_pipeline_buttons_have_no_tooltip(self, qapp):
        """回归：截图覆盖层内按钮不得带 tooltip（黑底问题难以可靠修复，已移除）。

        背景：覆盖层设置了 WA_TranslucentBackground，QToolTip 顶层窗口在其下呈现
        黑底；尝试过的样式表/event 拦截方案均不可靠。最终移除覆盖层内按钮的 tooltip。
        """
        panel = InlineRecognitionPanel()
        for btn in panel._pipeline_buttons.values():
            assert btn.toolTip() == ""

    def test_set_options(self, qapp):
        panel = InlineRecognitionPanel()
        options = OCROptions(pipeline=OCRPipeline.PADDLEOCR_VL)
        panel.set_options(options)
        assert panel.get_options().pipeline == OCRPipeline.PADDLEOCR_VL

    def test_pipeline_authority_over_corrupted_screenshot_source(self, qapp, tmp_path):
        """加固：即使 screenshot 源存了 .pipeline 不一致的腐烂数据，
        按钮选什么就识别什么——get_options().pipeline 恒等于按钮选择。"""
        from vibeocr.classic.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            # 故意存入腐烂数据：TABLE_RECOGNITION key 下挂一个 pipeline=OCR 的 options
            corrupted = OCROptions(pipeline=OCRPipeline.OCR)
            prefs.set_pipeline_options(
                "screenshot", OCRPipeline.TABLE_RECOGNITION, corrupted
            )

            panel = InlineRecognitionPanel()
            panel._pipeline_buttons[OCRPipeline.TABLE_RECOGNITION].click()
            options = panel.get_options()
            # 按钮选表格 → 必须是表格，绝不被腐烂的 OCR 数据覆盖
            assert options.pipeline == OCRPipeline.TABLE_RECOGNITION
        finally:
            OCRPreferences.reset_instance()
