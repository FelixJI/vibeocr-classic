"""ConfigManager 测试（pipeline_ttls 字典 API + 迁移 / max_heavy_pipelines）。"""

import pytest

from vibeocr.classic.managers.config_manager import ConfigManager


@pytest.fixture
def cm(tmp_path):
    """每个测试独立的 ConfigManager 单例（隔离 tmp_path）。"""
    ConfigManager._instance = None
    return ConfigManager.instance(project_root=tmp_path)


def test_get_max_heavy_pipelines_default_none(cm):
    """默认 None（按显存自动分档）。"""
    assert cm.get_max_heavy_pipelines() is None


def test_set_max_heavy_pipelines(cm):
    """设置并读取 max_heavy_pipelines。"""
    assert cm.set_max_heavy_pipelines(2)
    assert cm.get_max_heavy_pipelines() == 2


def test_set_max_heavy_pipelines_none(cm):
    """可以重置回 None。"""
    cm.set_max_heavy_pipelines(3)
    cm.set_max_heavy_pipelines(None)
    assert cm.get_max_heavy_pipelines() is None


def test_log_level_defaults_to_info_and_persists(cm):
    assert cm.get_log_level() == "INFO"
    assert cm.set_log_level("debug")
    assert cm.get_log_level() == "DEBUG"


def test_invalid_log_level_falls_back_to_info(cm):
    assert cm.set_log_level("trace")
    assert cm.get_log_level() == "INFO"


def test_preload_selection_and_enabled_state_persist(cm):
    assert cm.get_preload_pipelines() == []
    assert cm.get_preload_enabled() is False

    assert cm.set_preload_pipelines(["PP-StructureV3", "unknown"])
    assert cm.set_preload_enabled(False)

    assert cm.get_preload_pipelines() == ["PP-StructureV3"]
    assert cm.get_preload_enabled() is False


def test_preload_never_uses_ambiguous_legacy_ocr_pipeline(cm):
    """OCR 可投影到 Rapid/Windows/Paddle，不能伪装成统一模型预加载。"""

    assert cm.set_preload_pipelines(["OCR", "PP-StructureV3"])
    assert cm.get_preload_pipelines() == ["PP-StructureV3"]


# ---------------- per-pipeline TTL dict API + migration ----------------


_DEFAULT_TTLS = {
    "OCR": 0,
    "TABLE_RECOGNITION": 0,
    "FORMULA_RECOGNITION": 0,
    "PP-StructureV3": 300,
    "MinerU": 0,
    "PaddleOCR-VL": 300,
}


def test_migrate_legacy_single_ttl_value(cm, tmp_path):
    """旧 pipeline_ttl_seconds=600 → 重管道 600，轻管道 0，MinerU 0。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"pipeline_ttl_seconds": 600}', encoding="utf-8")

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 0
    assert ttls["TABLE_RECOGNITION"] == 0
    assert ttls["FORMULA_RECOGNITION"] == 0
    assert ttls["PP-StructureV3"] == 600
    assert ttls["PaddleOCR-VL"] == 600
    assert ttls["MinerU"] == 0

    # 旧字段已删除，新字段已写入
    import json

    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" not in data
    assert "pipeline_ttls" in data


def test_default_ttls_for_fresh_user(cm):
    """新用户：轻=0, MinerU=0, paddle 重=300。"""
    ttls = cm.get_pipeline_ttls()
    assert ttls == _DEFAULT_TTLS


def test_partial_dict_filled_with_defaults(cm, tmp_path):
    """只配了部分管道，缺失的补默认。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttls": {"OCR": 100, "PP-StructureV3": 600}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert len(ttls) == 6
    assert ttls["OCR"] == 100
    assert ttls["PP-StructureV3"] == 600
    assert ttls["TABLE_RECOGNITION"] == 0  # 补默认
    assert ttls["MinerU"] == 0
    assert ttls["PaddleOCR-VL"] == 300  # 补默认


def test_set_pipeline_ttl_single(cm):
    """set_pipeline_ttl 改单个管道。"""
    assert cm.set_pipeline_ttl("OCR", 180) is True
    assert cm.get_pipeline_ttls()["OCR"] == 180


def test_set_pipeline_ttl_persists_persistent_sentinel(cm):
    """-1 是 UI 本地配置中的持久驻留哨兵。"""
    assert cm.set_pipeline_ttl("OCR", -1) is True
    assert cm.get_pipeline_ttls()["OCR"] == -1


def test_set_pipeline_ttl_clamps_values_below_persistent_sentinel(cm):
    """小于 -1 的非法值仍回退到默认/继承值 0。"""
    assert cm.set_pipeline_ttl("OCR", -5) is True
    assert cm.get_pipeline_ttls()["OCR"] == 0


def test_set_pipeline_ttls_batch_writes_all(cm):
    """set_pipeline_ttls 批量写入。"""
    ttls = dict(_DEFAULT_TTLS)
    ttls["OCR"] = 60
    ttls["MinerU"] = 200
    assert cm.set_pipeline_ttls(ttls) is True
    result = cm.get_pipeline_ttls()
    assert result["OCR"] == 60
    assert result["MinerU"] == 200
    assert result["PP-StructureV3"] == 300  # 未变


def test_get_pipeline_ttls_rejects_non_int_values(cm, tmp_path):
    """损坏的 TTL 值（字符串/布尔）回退到默认。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttls": {"OCR": "oops", "PP-StructureV3": true, "MinerU": 7}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 0  # 字符串 → 默认
    assert ttls["PP-StructureV3"] == 300  # bool → 默认
    assert ttls["MinerU"] == 7  # 合法 int 保留


def test_legacy_field_not_migrated_when_dict_present(cm, tmp_path):
    """新旧字段并存时，保留 dict，不迁移（dict 优先）。"""
    import json

    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttl_seconds": 600, "pipeline_ttls": {"OCR": 50}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 50  # 来自 dict，不是 legacy
    assert ttls["PP-StructureV3"] == 300  # dict 缺失，补默认（不取 legacy 600）

    # dict 模式下 legacy 字段不被删（避免误删用户手动数据）
    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" in data


def test_migrate_legacy_bool_value_falls_back_to_default(cm, tmp_path):
    """Bug fix: 损坏 legacy 值 ``true``/``false`` 不能被 int() 静默转 1/0。

    应视为损坏，回退到默认（重管道 300）。
    """
    import json

    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"pipeline_ttl_seconds": True}), encoding="utf-8")

    ttls = cm.get_pipeline_ttls()
    # True 不应被当作 1，应回退到默认 300
    assert ttls["PP-StructureV3"] == 300
    assert ttls["PaddleOCR-VL"] == 300
    assert ttls["OCR"] == 0
    assert ttls["MinerU"] == 0

    # 旧字段已删除并迁移为新 dict
    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" not in data
    assert data["pipeline_ttls"]["PP-StructureV3"] == 300


def test_save_json_preserves_existing_file_when_data_is_not_serializable(cm, tmp_path):
    """序列化失败不能先截断用户已有配置。"""
    import json

    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"theme": "light"}', encoding="utf-8")

    assert cm._save_json("app_settings.json", {"invalid": object()}) is False
    assert json.loads(config.read_text(encoding="utf-8")) == {"theme": "light"}


def test_ocr_engine_defaults_to_rapidocr_and_persists(cm):
    """旧配置没有 engine 时按 RapidOCR 迁移；显式设置持久化。"""
    assert cm.get_ocr_engine_selection() == ("rapidocr", False)
    assert cm.get_ocr_engine() == "rapidocr"
    assert cm.set_ocr_engine("windows")
    assert cm.get_ocr_engine_selection() == ("windows", False)


def test_ocr_engine_rejects_unknown_values_without_silent_replacement(cm):
    """未知 engine id 拒绝保存；旧未知值标记需重选，不静默替换。"""
    assert not cm.set_ocr_engine("tesseract")
    assert cm.get_ocr_engine_selection() == ("rapidocr", False)

    import json

    (cm.config_dir / "app_settings.json").write_text(
        json.dumps({"ocr_engine": "legacy-engine"}), encoding="utf-8"
    )
    assert cm.get_ocr_engine_selection() == (None, True)
    # 未初始化 ConfigManager 的读取入口回退默认，请求 seam 对 None 省略字段
    assert cm.get_ocr_engine() == "rapidocr"


def test_offline_component_features_persist_per_accelerator(cm):
    """按 accelerator 保存勾选的离线能力；空列表清空该档位。"""
    assert cm.get_offline_component_features("cpu") == []
    assert cm.set_offline_component_features(
        "cpu", ["document_parsing", "document_parsing", "gpu_runtime"]
    )
    assert cm.get_offline_component_features("cpu") == [
        "document_parsing",
        "gpu_runtime",
    ]
    assert cm.get_offline_component_features("nvidia_cuda") == []
    assert cm.set_offline_component_features("cpu", [])
    assert cm.get_offline_component_features("cpu") == []
