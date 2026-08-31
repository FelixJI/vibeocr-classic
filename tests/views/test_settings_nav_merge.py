"""设置页导航分类回归测试

验证：
- 设置导航按用户任务拆分为四个静态页：通用 / 识别设置 / 运行时与组件 / 模型缓存
  （动态页「截图选项」「PDF 选项」由控制器追加），无空白「工具」页。
- 「推理后端」不是独立导航项：BackendOptionsWidget 位于「运行时与组件」页的
  Runtime 分组（groupEnvMaintenance）内。

防止后续误改回混排结构。
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

    复用 test_settings_reinstall 的隔离策略：禁用 BackendOptionsWidget 真后台
    探测，并隔离 _init_settings_page 的 ConfigManager 等依赖。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        patch(
            "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.SettingsPageController._refresh_env_maintenance_state"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.classic.managers.config_manager.ConfigManager") as mock_cm,
    ):
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


def test_nav_static_pages_match_responsibility_split(controller):
    """静态导航应按用户任务使用清晰、非实现导向的名称。"""
    _ctrl, host = controller
    nav = host.findChild(QListWidget, "settingsNavList")
    assert nav is not None
    texts = [nav.item(i).text() for i in range(nav.count())]
    # 静态四项 + 动态两项（截图选项 / PDF 选项），不应含「工具」
    assert "工具" not in texts
    assert texts[:4] == ["通用", "识别设置", "运行时与组件", "模型缓存"]
    assert texts[4:] == ["截图选项", "PDF 选项"]


def test_nav_has_no_separate_backend_item(controller):
    """「推理后端」不应作为独立导航项存在（位于运行时与组件页内）。"""
    _ctrl, host = controller
    nav = host.findChild(QListWidget, "settingsNavList")
    texts = [nav.item(i).text() for i in range(nav.count())]
    assert "推理后端" not in texts


def test_backend_widget_is_inside_env_group(controller):
    """BackendOptionsWidget 应位于 groupEnvMaintenance（Runtime 与加速）内"""
    _ctrl, host = controller
    group = host.findChild(QGroupBox, "groupEnvMaintenance")
    assert group is not None
    assert group.title() == "运行时与组件"

    container = host.findChild(QWidget, "backendOptionsContainer")
    assert container is not None, "backendOptionsContainer 应存在于分组内"

    # 容器内应已放入 BackendOptionsWidget
    backend = container.findChild(BackendOptionsWidget)
    assert backend is not None, "BackendOptionsWidget 应被放入容器"


def test_env_group_still_has_dependency_controls(controller):
    """重排后 Runtime 页仍应保留依赖表格与维护按钮"""
    _ctrl, host = controller
    assert host.findChild(QPushButton, "btnReinstallPython") is not None
    assert host.findChild(QPushButton, "btnReinstallDeps") is not None
    assert host.findChild(QPushButton, "btnInstallMissing") is not None
