"""设置页导航合并回归测试

验证：
- 空白「工具」导航页已删除
- 「推理后端」不再是独立导航项，而是并入「应用设置」页的
  「推理后端与依赖」分组（groupEnvMaintenance）内

防止后续误改回拆分结构。
"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QGroupBox, QListWidget, QPushButton, QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.classic.widgets.backend_options_widget import BackendOptionsWidget


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController（patch 掉重依赖）。

    复用 test_settings_reinstall 的隔离策略：BackendOptionsWidget 构造读
    env_manager / machine_cache，_init_settings_page 读 ConfigManager 等，
    均需 patch 以保证测试隔离与可重复。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        patch("vibeocr.classic.widgets.backend_options_widget.env_manager") as mock_em,
        patch(
            "vibeocr.classic.widgets.backend_options_widget.load_cache",
            return_value=None,
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.classic.managers.config_manager.ConfigManager") as mock_cm,
        patch(
            "vibeocr.backend.core.pipelines.get_preloadable_pipelines",
            return_value=[],
        ),
    ):
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.detect_gpu_info.return_value = {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        }
        mock_cm.instance.return_value = MagicMock(
            get_pipeline_ttls=MagicMock(
                return_value={
                    "OCR": 0,
                    "TABLE_RECOGNITION": 0,
                    "FORMULA_RECOGNITION": 0,
                    "PP-StructureV3": 300,
                    "MinerU": 0,
                    "PaddleOCR-VL": 300,
                }
            ),
        )

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    return ctrl, host


def test_nav_has_no_tools_page(controller):
    """静态导航项应只有「模型管理」「应用设置」两项，无空白「工具」"""
    _ctrl, host = controller
    nav = host.findChild(QListWidget, "settingsNavList")
    assert nav is not None
    texts = [nav.item(i).text() for i in range(nav.count())]
    # 静态两项 + 动态两项（截图选项 / PDF 选项），不应含「工具」
    assert "工具" not in texts
    assert "模型管理" in texts
    assert "应用设置" in texts


def test_nav_has_no_separate_backend_item(controller):
    """「推理后端」不应作为独立导航项存在（已并入分组）"""
    _ctrl, host = controller
    nav = host.findChild(QListWidget, "settingsNavList")
    texts = [nav.item(i).text() for i in range(nav.count())]
    assert "推理后端" not in texts


def test_backend_widget_is_inside_env_group(controller):
    """BackendOptionsWidget 应位于 groupEnvMaintenance（推理后端与依赖）内"""
    _ctrl, host = controller
    group = host.findChild(QGroupBox, "groupEnvMaintenance")
    assert group is not None
    assert group.title() == "推理后端与依赖"

    container = host.findChild(QWidget, "backendOptionsContainer")
    assert container is not None, "backendOptionsContainer 应存在于分组内"

    # 容器内应已放入 BackendOptionsWidget
    backend = container.findChild(BackendOptionsWidget)
    assert backend is not None, "BackendOptionsWidget 应被放入容器"


def test_env_group_still_has_dependency_controls(controller):
    """合并后分组内仍应保留依赖表格与三个重装按钮"""
    _ctrl, host = controller
    assert host.findChild(QPushButton, "btnReinstallPython") is not None
    assert host.findChild(QPushButton, "btnReinstallDeps") is not None
    assert host.findChild(QPushButton, "btnInstallMissing") is not None
