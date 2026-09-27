"""设置页每管道 TTL ComboBox 三态契约。

覆盖（任务既定缺口）：
- 空：无 catalog 时渲染等待占位，不生成任何 TTL 行
- 加载：catalog 到达后仅为已就绪且声明 TTL 管理的模式建行，并从
  ConfigManager 恢复选中档位
- 错误：能力目录读取失败时状态行展示错误；非法持久化值回退「继承默认」
- 保存回读：切换档位写入 ConfigManager 并可读回
- 两次刷新无重复：_refresh_lifecycle_controls 重建不残留旧行
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from vibeocr.classic.managers.config_manager import ConfigManager
from vibeocr.classic.runtime_selection import (
    RecognitionModeEntry,
    RecognitionModeLifecycle,
    RuntimeSelectionCatalog,
)
from vibeocr.classic.runtime_selection import ENGINE_AVAILABILITY_READY
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline


def _mode(mode_id, family, pipeline_id, availability, lifecycle):
    return RecognitionModeEntry(
        mode_id,
        family,
        pipeline_id,
        None,
        "advanced_component",
        availability,
        RecognitionModeLifecycle(*lifecycle),
    )


def _ttl_catalog():
    """ready+ttl：表格与 MinerU；OCR 共享投影含 unmanaged 模式被排除；公式未就绪。"""
    return RuntimeSelectionCatalog(
        modes=(
            _mode(
                "rapid_text",
                "text",
                "OCR",
                ENGINE_AVAILABILITY_READY,
                ("unmanaged", False, False, False, False),
            ),
            _mode(
                "paddle_table",
                "specialized",
                "TABLE_RECOGNITION",
                ENGINE_AVAILABILITY_READY,
                ("model_residency", True, True, True, True),
            ),
            _mode(
                "mineru_document",
                "document",
                "MinerU",
                ENGINE_AVAILABILITY_READY,
                ("process_keep_alive", True, True, False, True),
            ),
            _mode(
                "paddle_formula",
                "specialized",
                "FORMULA_RECOGNITION",
                "preparation_required",
                ("model_residency", True, True, True, True),
            ),
        ),
        has_recognition_mode_catalog=True,
    )


def _ttl_rows(host: QWidget) -> set[str]:
    from PySide6.QtWidgets import QVBoxLayout

    layout = host.findChild(QVBoxLayout, "runtimeCacheLayout")
    assert layout is not None
    return {
        layout.itemAt(i).widget().objectName()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not None
        and layout.itemAt(i).widget().objectName().startswith("ttlRow_")
    }


@pytest.fixture
def env(qtbot, tmp_path):
    """带真实 UI 与真实（tmp 隔离）ConfigManager 的控制器；重依赖被 patch。"""
    ConfigManager._instance = None
    ConfigManager.instance(project_root=tmp_path)
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)
    with (
        patch(
            "vibeocr.classic.widgets.backend_options_widget."
            "BackendOptionsWidget._start_gpu_detection"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller."
            "SettingsPageController._refresh_env_maintenance_state"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
    ):
        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    yield ctrl, host
    ctrl._ttl_sync_timer.stop()
    ConfigManager._instance = None


def test_empty_catalog_renders_waiting_placeholder(env):
    """空：catalog 未达时显示等待文案且没有 TTL 行。"""
    _ctrl, host = env
    status = host.findChild(QLabel, "labelLifecycleAvailability")
    assert status is not None
    assert status.text() == "正在等待 Backend 模型管理能力目录…"
    assert _ttl_rows(host) == set()


def test_catalog_load_creates_managed_rows_and_restores_selection(env):
    """加载：仅 ready+ttl 模式建行；档位从 ConfigManager 恢复。"""
    ctrl, host = env
    ConfigManager.instance().set_pipeline_ttl("TABLE_RECOGNITION", 180)
    ctrl._selection_catalog = _ttl_catalog()
    ctrl._refresh_lifecycle_controls()

    managed = {OCRPipeline.TABLE_RECOGNITION, OCRPipeline.DOCUMENT_PARSING}
    assert _ttl_rows(host) == {f"ttlRow_{p.value}" for p in managed}
    table_combo = host.findChild(QComboBox, "comboTtl_TABLE_RECOGNITION")
    assert table_combo is not None and table_combo.currentData() == 180
    mineru_combo = host.findChild(
        QComboBox, f"comboTtl_{OCRPipeline.DOCUMENT_PARSING.value}"
    )
    assert mineru_combo is not None and mineru_combo.currentData() == 0
    # MinerU 不支持固定驻留，预设去掉「持久驻留」档。
    assert mineru_combo.findText("持久驻留") < 0
    assert table_combo.findText("持久驻留") >= 0


def test_load_error_and_invalid_value_fall_back(env):
    """错误：目录读取失败状态行展示错误；非法持久化档回退继承默认。"""
    ctrl, host = env
    ConfigManager.instance().set_pipeline_ttl("TABLE_RECOGNITION", 123)
    ctrl._selection_catalog = _ttl_catalog()
    ctrl._selection_load_error = "health 超时"
    ctrl._refresh_lifecycle_controls()

    status = host.findChild(QLabel, "labelLifecycleAvailability")
    assert "模型管理能力读取失败：health 超时" in status.text()
    table_combo = host.findChild(QComboBox, "comboTtl_TABLE_RECOGNITION")
    assert table_combo.currentData() == 0  # 123 不在预设档，回退「继承默认 TTL」


def test_combo_change_persists_and_reads_back(env):
    """保存回读：切档写入 ConfigManager 并可读回。"""
    ctrl, host = env
    ctrl._selection_catalog = _ttl_catalog()
    ctrl._refresh_lifecycle_controls()
    table_combo = host.findChild(QComboBox, "comboTtl_TABLE_RECOGNITION")

    table_combo.setCurrentIndex(table_combo.findData(300))

    assert ConfigManager.instance().get_pipeline_ttls()["TABLE_RECOGNITION"] == 300
    assert table_combo.currentData() == 300


def test_two_refreshes_do_not_duplicate_rows(env):
    """两次刷新无重复：重建后行集合保持不变。"""
    ctrl, host = env
    ctrl._selection_catalog = _ttl_catalog()
    ctrl._refresh_lifecycle_controls()
    first = _ttl_rows(host)
    assert first

    ctrl._refresh_lifecycle_controls()

    assert _ttl_rows(host) == first
    # 行内控件也只保留一份（无重复 combo）。
    for name in first:
        pipeline_value = name.removeprefix("ttlRow_")
        assert len(host.findChildren(QComboBox, f"comboTtl_{pipeline_value}")) == 1
