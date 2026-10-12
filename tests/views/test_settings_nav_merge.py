"""设置页导航分类回归测试

验证：
- 设置导航拆分为三个静态页：通用 / 运行环境 / 模型与性能
  （动态页「截图选项」「PDF 选项」由控制器追加），无空白「工具」页。
- 运行环境页承载 RuntimeEnvironmentWidget（推理设备 + 识别依赖）、
  下载来源与 MinerU 连接；不再有独立的「识别设置」页。
- 已安装/未安装状态表格与逐项维护按钮不再出现在设置界面。

防止后续误改回混排结构。
"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QGroupBox, QListWidget, QPushButton, QTreeWidget, QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.classic.widgets.runtime_environment_widget import RuntimeEnvironmentWidget


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController（patch 掉重依赖）。

    复用 test_settings_reinstall 的隔离策略：禁用 RuntimeEnvironmentWidget
    真后台探测，并隔离 _init_settings_page 的 ConfigManager 等依赖。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        patch(
            "vibeocr.classic.widgets.runtime_environment_widget."
            "RuntimeEnvironmentWidget._start_gpu_detection"
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
    # 静态三项 + 动态两项（截图选项 / PDF 选项），不应含「工具」
    assert "工具" not in texts
    assert "识别设置" not in texts, "识别依赖选择已并入「运行环境」页"
    assert texts[:3] == ["通用", "运行环境", "模型与性能"]
    assert texts[3:] == ["截图选项", "PDF 选项"]


def test_runtime_widget_is_on_runtime_page(controller):
    """RuntimeEnvironmentWidget 应位于「运行环境」页的容器内。"""
    _ctrl, host = controller
    container = host.findChild(QWidget, "backendOptionsContainer")
    assert container is not None, "backendOptionsContainer 应存在于运行环境页"

    widget = container.findChild(RuntimeEnvironmentWidget)
    assert widget is not None, "RuntimeEnvironmentWidget 应被放入容器"


def test_runtime_page_holds_download_sources_and_mineru_connection(controller):
    """下载来源与 MinerU 连接分组应在「运行环境」页上。"""
    _ctrl, host = controller
    page = host.findChild(QWidget, "pageRuntime")
    assert page is not None
    assert page.findChild(QGroupBox, "groupDownloadSources") is not None
    assert page.findChild(QGroupBox, "groupMineruConnection") is not None


def test_runtime_page_has_no_status_tables_or_maintenance_buttons(controller):
    """设置界面不应再保留安装状态表格与逐项维护按钮。"""
    _ctrl, host = controller
    for name in (
        "treeEngineAvailability",
        "treeOfflineFeatures",
        "treeRuntimeStatus",
        "treeDepsStatus",
    ):
        assert host.findChild(QTreeWidget, name) is None, f"{name} 应被移除"
    for name in (
        "btnReinstallPython",
        "btnReinstallDeps",
        "btnReinstallSelected",
        "btnInstallMissing",
        "btnUpdateDeps",
        "btnInstallOfflineFeatures",
    ):
        assert host.findChild(QPushButton, name) is None, f"{name} 应被移除"
