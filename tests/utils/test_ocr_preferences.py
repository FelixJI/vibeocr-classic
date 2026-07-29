# tests/utils/test_ocr_preferences.py
"""OCRPreferences 持久化测试"""

import json

import pytest

from vibeocr.backend.core.pipelines import OCRPipeline
from vibeocr.backend.models.ocr_options import OCROptions
from vibeocr.classic.utils.ocr_preferences import OCRPreferences


@pytest.fixture
def tmp_config_dir(tmp_path):
    """提供临时配置目录"""
    return tmp_path


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前后重置单例"""
    OCRPreferences.reset_instance()
    yield
    OCRPreferences.reset_instance()


class TestOCRPreferencesNewFields:
    """验证新字段（表格识别、公式识别）通过持久化正确保存和加载"""

    def test_table_recognition_round_trip(self, tmp_config_dir):
        """表格识别选项保存再加载应保持一致"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=False,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
        )
        prefs.set_options(options)

        # 重置单例并重新加载
        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is False
        assert loaded.use_table_orientation_classify is False
        assert loaded.use_ocr_results_with_table_cells is False

    def test_formula_recognition_round_trip(self, tmp_config_dir):
        """公式识别选项保存再加载应保持一致"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=8,
        )
        prefs.set_options(options)

        # 重置单例并重新加载
        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.pipeline == OCRPipeline.FORMULA_RECOGNITION
        assert loaded.formula_recognition_batch_size == 8

    def test_batch_options_new_fields(self, tmp_config_dir):
        """批量选项也应正确保存新字段"""
        prefs = OCRPreferences(tmp_config_dir)

        batch_opts = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=True,
        )
        prefs.set_batch_options(batch_opts)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_batch_options()

        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is True

    def test_all_pipelines_persist(self, tmp_config_dir):
        """所有管道类型都应能正确保存和恢复"""
        prefs = OCRPreferences(tmp_config_dir)

        for pipeline in OCRPipeline:
            options = OCROptions(pipeline=pipeline)
            prefs.set_options(options)

            OCRPreferences.reset_instance()
            prefs2 = OCRPreferences(tmp_config_dir)
            loaded = prefs2.get_options()
            assert loaded.pipeline == pipeline, f"管道 {pipeline} 持久化失败"

            # 重置以准备下一次迭代
            OCRPreferences.reset_instance()
            prefs = OCRPreferences(tmp_config_dir)

    def test_json_file_contains_new_fields(self, tmp_config_dir):
        """验证 JSON 文件中确实包含新字段"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=True,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=True,
        )
        prefs.set_options(options)

        config_path = tmp_config_dir / "ocr_preferences.json"
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["version"] == 4
        main_data = data["main"]["TABLE_RECOGNITION"]
        assert main_data["use_wireless_table"] is True
        assert main_data["use_table_orientation_classify"] is False
        assert main_data["use_ocr_results_with_table_cells"] is True
        assert main_data["formula_recognition_batch_size"] == 1  # default

    def test_missing_new_fields_get_defaults(self, tmp_config_dir):
        """旧格式 JSON（缺少新字段）加载时应使用默认值"""
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "OCR",
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "use_textline_orientation": False,
            "batch_options": {"pipeline": "OCR"},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        loaded = prefs.get_options()

        # 新字段应使用默认值
        assert loaded.use_wireless_table is True
        assert loaded.use_table_orientation_classify is True
        assert loaded.use_ocr_results_with_table_cells is True
        assert loaded.formula_recognition_batch_size == 1

    def test_table_new_fields_round_trip(self, tmp_config_dir):
        """表格识别新增字段持久化往返"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_e2e_wired_table_rec_model=True,
            text_det_limit_side_len=960,
            text_det_thresh=0.3,
        )
        prefs.set_options(options)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.use_e2e_wired_table_rec_model is True
        assert loaded.text_det_limit_side_len == 960
        assert loaded.text_det_thresh == 0.3

    def test_formula_new_fields_round_trip(self, tmp_config_dir):
        """公式识别新增字段持久化往返"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_model_name="LaTeX-OCR",
            formula_recognition_model_dir="/models/formula",
        )
        prefs.set_options(options)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.formula_recognition_model_name == "LaTeX-OCR"
        assert loaded.formula_recognition_model_dir == "/models/formula"


class TestPerPipelineStorage:
    """Per-pipeline options storage tests"""

    def test_set_and_get_pipeline_options(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.OCR,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        prefs.set_pipeline_options("main", OCRPipeline.OCR, options)

        loaded = prefs.get_pipeline_options("main", OCRPipeline.OCR)
        assert loaded.use_doc_orientation_classify is False
        assert loaded.use_doc_unwarping is False

    def test_different_sources_independent(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        main_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=False)
        screenshot_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=True)

        prefs.set_pipeline_options("main", OCRPipeline.OCR, main_opts)
        prefs.set_pipeline_options("screenshot", OCRPipeline.OCR, screenshot_opts)

        assert (
            prefs.get_pipeline_options("main", OCRPipeline.OCR).use_doc_unwarping
            is False
        )
        assert (
            prefs.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping
            is True
        )

    def test_different_pipelines_independent(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        ocr_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=False)
        struct_opts = OCROptions(
            pipeline=OCRPipeline.PP_STRUCTURE_V3, use_doc_unwarping=True
        )

        prefs.set_pipeline_options("main", OCRPipeline.OCR, ocr_opts)
        prefs.set_pipeline_options("main", OCRPipeline.PP_STRUCTURE_V3, struct_opts)

        assert (
            prefs.get_pipeline_options("main", OCRPipeline.OCR).use_doc_unwarping
            is False
        )
        assert (
            prefs.get_pipeline_options(
                "main", OCRPipeline.PP_STRUCTURE_V3
            ).use_doc_unwarping
            is True
        )

    def test_get_unsaved_pipeline_returns_default(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        loaded = prefs.get_pipeline_options("main", OCRPipeline.TABLE_RECOGNITION)
        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is True  # default

    def test_persist_and_reload(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        prefs.set_pipeline_options(
            "screenshot",
            OCRPipeline.OCR,
            OCROptions(
                pipeline=OCRPipeline.OCR,
                use_doc_unwarping=False,
            ),
        )
        prefs.set_pipeline_options(
            "screenshot",
            OCRPipeline.TABLE_RECOGNITION,
            OCROptions(
                pipeline=OCRPipeline.TABLE_RECOGNITION,
                use_wireless_table=False,
            ),
        )

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)

        assert (
            prefs2.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping
            is False
        )
        assert (
            prefs2.get_pipeline_options(
                "screenshot", OCRPipeline.TABLE_RECOGNITION
            ).use_wireless_table
            is False
        )

    def test_pipeline_options_changed_signal(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        received = []
        prefs.pipeline_options_changed.connect(
            lambda s, o: received.append((s, o.pipeline))
        )

        prefs.set_pipeline_options(
            "main", OCRPipeline.OCR, OCROptions(pipeline=OCRPipeline.OCR)
        )

        assert len(received) == 1
        assert received[0] == ("main", OCRPipeline.OCR)


class TestVersionMigration:
    """v1 → v2 migration tests"""

    def test_v1_migrates_to_v2(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "PP-StructureV3",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": True,
            "batch_options": {"pipeline": "OCR"},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)

        # Old options migrated to "main" under PP-StructureV3
        loaded = prefs.get_pipeline_options("main", OCRPipeline.PP_STRUCTURE_V3)
        assert loaded.pipeline == OCRPipeline.PP_STRUCTURE_V3
        assert loaded.use_doc_orientation_classify is False
        assert loaded.use_doc_unwarping is True

        # legacy get_options returns the migrated pipeline
        assert prefs.get_options().pipeline == OCRPipeline.PP_STRUCTURE_V3

    def test_v1_migration_preserves_batch_options(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "OCR",
            "batch_options": {"pipeline": "MinerU", "enable_formula": False},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        assert prefs.get_batch_options().enable_formula is False

    def test_v2_loads_correctly(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        v2_data = {
            "version": 2,
            "last_main_pipeline": "TABLE_RECOGNITION",
            "main": {
                "TABLE_RECOGNITION": {
                    "pipeline": "TABLE_RECOGNITION",
                    "use_wireless_table": False,
                },
            },
            "screenshot": {
                "OCR": {
                    "pipeline": "OCR",
                    "use_doc_unwarping": False,
                },
            },
            "batch_options": {"pipeline": "OCR"},
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(v2_data, f)

        prefs = OCRPreferences(tmp_config_dir)

        assert (
            prefs.get_pipeline_options(
                "main", OCRPipeline.TABLE_RECOGNITION
            ).use_wireless_table
            is False
        )
        assert (
            prefs.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping
            is False
        )
        # legacy get_options uses last_main_pipeline
        assert prefs.get_options().pipeline == OCRPipeline.TABLE_RECOGNITION


class TestPdfSettings:
    """PDF 全局设置（PdfGlobalSettings）持久化与公共 API 测试"""

    def test_pdf_settings_round_trip(self, tmp_config_dir):
        """PdfGlobalSettings 保存后重新加载应保持一致"""
        from vibeocr.backend.models.pdf_ocr_options import PdfGlobalSettings

        prefs = OCRPreferences(tmp_config_dir)
        settings = PdfGlobalSettings(
            render_dpi=200, font_size_ratio=0.6, text_layer_visible=True
        )
        prefs.set_pdf_settings(settings)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_pdf_settings()
        assert loaded.render_dpi == 200
        assert loaded.font_size_ratio == 0.6
        assert loaded.text_layer_visible is True

    def test_pdf_settings_default_when_absent(self, tmp_config_dir):
        """未保存过 pdf_settings 时返回默认值"""
        prefs = OCRPreferences(tmp_config_dir)
        loaded = prefs.get_pdf_settings()
        assert loaded.render_dpi == 300
        assert loaded.font_size_ratio == 0.8

    def test_pdf_pipeline_options_round_trip(self, tmp_config_dir):
        """set/get_pdf_pipeline_options 应往返并更新 _last_pdf_pipeline"""
        prefs = OCRPreferences(tmp_config_dir)
        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING, enable_formula=False
        )
        prefs.set_pdf_pipeline_options(options)

        loaded = prefs.get_pdf_pipeline_options()
        assert loaded.pipeline == OCRPipeline.DOCUMENT_PARSING
        assert loaded.enable_formula is False

    def test_last_pdf_pipeline_persists(self, tmp_config_dir):
        """_last_pdf_pipeline 应跨实例持久化"""
        prefs = OCRPreferences(tmp_config_dir)
        prefs.set_pdf_pipeline_options(OCROptions(pipeline=OCRPipeline.OCR))

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        # get_pdf_pipeline_options 默认走 _last_pdf_pipeline（恢复后应仍是 OCR）
        loaded = prefs2.get_pdf_pipeline_options()
        assert loaded.pipeline == OCRPipeline.OCR

    def test_default_last_pdf_pipeline_is_ocr(self, tmp_config_dir):
        """空配置时 _last_pdf_pipeline 默认为 OCR（匹配 PDF 允许管道集）。"""
        prefs = OCRPreferences(tmp_config_dir)
        assert prefs.get_pdf_pipeline_options().pipeline == OCRPipeline.OCR

    def test_v2_loads_without_pdf_fields(self, tmp_config_dir):
        """v2 配置（无 pdf / pdf_settings / last_pdf_pipeline）加载后应使用默认"""
        config_path = tmp_config_dir / "ocr_preferences.json"
        v2_data = {
            "version": 2,
            "last_main_pipeline": "OCR",
            "main": {"OCR": {"pipeline": "OCR"}},
            "screenshot": {},
            "batch_options": {"pipeline": "OCR"},
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(v2_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        # PDF 设置走默认
        assert prefs.get_pdf_settings().render_dpi == 300
        # _last_pdf_pipeline 默认 OCR
        assert prefs.get_pdf_pipeline_options().pipeline == OCRPipeline.OCR


class TestPdfSplitterState:
    """PDF splitter 布局状态的持久化（base64 入 JSON）"""

    def test_round_trip(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        # 模拟 QSplitter.saveState().data() 返回的 bytes
        state = b"\x00\x00\x00\xc8\x00\xff\x01\x02"
        prefs.set_pdf_splitter_state(state)
        prefs.save()

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        assert prefs2.get_pdf_splitter_state() == state

    def test_default_is_none(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        assert prefs.get_pdf_splitter_state() is None

    def test_none_round_trips_as_none(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        prefs.set_pdf_splitter_state(b"abc")
        prefs.set_pdf_splitter_state(None)
        prefs.save()

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        assert prefs2.get_pdf_splitter_state() is None


class TestPdfRightSplitterState:
    """PDF 右侧（纵向）splitter 布局状态的持久化"""

    def test_round_trip(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        state = b"\x01\x02\x03\x04\x05"
        prefs.set_pdf_right_splitter_state(state)
        prefs.save()

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        assert prefs2.get_pdf_right_splitter_state() == state

    def test_default_is_none(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        assert prefs.get_pdf_right_splitter_state() is None

    def test_none_round_trips_as_none(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        prefs.set_pdf_right_splitter_state(b"abc")
        prefs.set_pdf_right_splitter_state(None)
        prefs.save()

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        assert prefs2.get_pdf_right_splitter_state() is None

    def test_set_both_states_single_save(self, tmp_config_dir):
        """set_pdf_splitter_states 一次性写入两个状态，均能回读。"""
        prefs = OCRPreferences(tmp_config_dir)
        main = b"\xaa\xbb"
        right = b"\xcc\xdd\xee"
        prefs.set_pdf_splitter_states(main, right)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        assert prefs2.get_pdf_splitter_state() == main
        assert prefs2.get_pdf_right_splitter_state() == right


class TestTextBlockOptions:
    """文本块处理选项（TextBlockOptions）持久化测试"""

    def test_round_trip(self, tmp_config_dir):
        from vibeocr.backend.models.text_block_options import (
            LINE_MODE_SMART,
            TextBlockOptions,
        )

        prefs = OCRPreferences(tmp_config_dir)
        opts = TextBlockOptions(
            line_mode=LINE_MODE_SMART,
            block_join_space=True,
            chinese_indent=True,
            drop_blank_blocks=False,
        )
        prefs.set_text_options(opts)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_text_options()
        assert loaded.line_mode == LINE_MODE_SMART
        assert loaded.block_join_space is True
        assert loaded.chinese_indent is True
        assert loaded.drop_blank_blocks is False

    def test_default_when_absent(self, tmp_config_dir):
        """空配置时返回默认值。"""
        from vibeocr.backend.models.text_block_options import LINE_MODE_MERGE

        prefs = OCRPreferences(tmp_config_dir)
        loaded = prefs.get_text_options()
        assert loaded.line_mode == LINE_MODE_MERGE
        assert loaded.drop_blank_blocks is True

    def test_old_config_without_field_uses_defaults(self, tmp_config_dir):
        """v3 配置（无 text_block_options 字段）加载后走默认值。"""
        from vibeocr.backend.models.text_block_options import LINE_MODE_MERGE

        config_path = tmp_config_dir / "ocr_preferences.json"
        v3_data = {
            "version": 3,
            "last_main_pipeline": "OCR",
            "main": {"OCR": {"pipeline": "OCR"}},
            "screenshot": {},
            "batch_options": {"pipeline": "OCR"},
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(v3_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        loaded = prefs.get_text_options()
        assert loaded.line_mode == LINE_MODE_MERGE
        assert loaded.drop_blank_blocks is True

    def test_json_file_contains_field(self, tmp_config_dir):
        """JSON 文件确实包含 text_block_options 字段。"""
        config_path = tmp_config_dir / "ocr_preferences.json"
        prefs = OCRPreferences(tmp_config_dir)
        prefs.save()

        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "text_block_options" in data
        assert data["text_block_options"]["line_mode"] == "merge"
        assert data["text_block_options"]["drop_blank_blocks"] is True
