"""设置页重装入口测试"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QTreeWidget, QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController


class _ImmediateInvalidationEmitter(QObject):
    invalidation_finished = Signal(bool, str)


def _immediate_invalidation_manager() -> MagicMock:
    manager = MagicMock()
    emitter = _ImmediateInvalidationEmitter()
    manager._test_invalidation_emitter = emitter
    manager.invalidation_finished = emitter.invalidation_finished

    def invalidate_supervisor() -> bool:
        emitter.invalidation_finished.emit(True, "")
        return True

    manager.invalidate_supervisor.side_effect = invalidate_supervisor
    return manager


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController

    connect_signals 会触发 _init_backend_options / _init_settings_page，
    这些会访问 ConfigManager、machine_cache、pipelines、BackendOptionsWidget。
    为保证测试隔离，patch 掉这些重依赖。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        # BackendOptionsWidget 构造读 env_manager / machine_cache
        patch("vibeocr.classic.widgets.backend_options_widget.env_manager") as mock_em,
        patch(
            "vibeocr.classic.widgets.backend_options_widget.load_cache",
            return_value=None,
        ),
        # 本文件测试依赖表/按钮，GPU worker 由组件专项测试覆盖。
        # 禁用真线程，避免 with 退出恢复 mock 时 worker 仍跨用例运行。
        patch(
            "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection"
        ),
        # _init_settings_page 读 ConfigManager / machine_cache / pipelines
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.classic.managers.config_manager.ConfigManager") as mock_cm,
        patch(
            "vibeocr.backend.core.pipelines.get_preloadable_pipelines",
            return_value=[],
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.RuntimeInstallerClient"
        ) as runtime_client,
    ):
        mock_em.detect_gpu.return_value = (False, None)
        # BackendOptionsWidget._load_state 读 detect_gpu_info()（含 vram/cuda
        # 等字段），必须返回真实结构而非默认 MagicMock，否则 vram >= 1024
        # 会因 MagicMock 与 int 比较抛 TypeError。此处配置无 GPU 的回退值。
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
        runtime_client.return_value.inspect.return_value = SimpleNamespace(
            ready=True,
            accelerator="cpu",
            backend_version="0.7.0",
            manifest_sha256="a" * 64,
            integrity="verified",
        )

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=_immediate_invalidation_manager(),
        )
        ctrl.connect_signals()
    return ctrl, host


def test_reinstall_python_button_exists(controller):
    """重装 Python 按钮应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnReinstallPython")
    assert btn is not None, "btnReinstallPython 应存在"


def test_click_reinstall_python_confirms_then_opens_dialog(controller, monkeypatch):
    """点重装 Python：确认 Yes 后应弹 BackendChoiceDialog(reinstall_python=True)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    # tmp_path 无 python/ → 按钮被禁用；测试模拟 portable 场景启用按钮
    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.setEnabled(True)

    # 模拟用户点"是"
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    # mock BackendChoiceDialog 避免真弹窗
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)
            self.reinstall_python = kwargs.get("reinstall_python", False)

        def exec(self):
            return 1

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1, "应弹出一次对话框"
    assert instances[0].get("reinstall_python") is True


def test_click_reinstall_python_cancel_does_nothing(controller, monkeypatch):
    """点重装 Python：确认 No 后不应弹对话框"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    opened = []
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog",
        lambda *a, **kw: opened.append(kw),
    )

    btn.click()

    assert len(opened) == 0, "取消时不应弹对话框"


def test_click_reinstall_deps_opens_dialog_without_reinstall(controller, monkeypatch):
    """点重装 OCR 依赖：应弹 BackendChoiceDialog(reinstall_python=False)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnReinstallDeps")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def exec(self):
            return 1

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1
    assert instances[0].get("reinstall_python") is False


def test_runtime_maintenance_buttons_enabled_for_bound_product(controller, qtbot):
    """产品有 component-lock 时维护按钮映射到 ensure/repair。"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    controller[0]._refresh_env_maintenance_state()

    btn_py = host.findChild(QPushButton, "btnReinstallPython")
    btn_deps = host.findChild(QPushButton, "btnReinstallDeps")
    qtbot.waitUntil(btn_py.isEnabled, timeout=3000)
    assert btn_deps.isEnabled()


def test_env_status_label_shows_runtime_binding(controller, qtbot):
    """labelEnvStatus 显示 accelerator、Backend 版本与 manifest 摘要。"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QLabel

    controller[0]._refresh_env_maintenance_state()

    label = host.findChild(QLabel, "labelEnvStatus")
    qtbot.waitUntil(lambda: "Backend：0.7.0" in label.text(), timeout=3000)
    text = label.text()
    assert "cpu" in text
    assert "已验证" in text


def test_install_missing_button_exists(controller):
    """补充安装缺失依赖按钮应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
    assert btn is not None, "btnInstallMissing 应存在"


def test_deps_status_tree_exists(controller):
    """依赖状态树（QTreeWidget）应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None, "treeDepsStatus 应存在"


def test_reinstall_selected_button_exists(controller):
    """旧按钮保留为整个 Runtime 修复入口。"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnReinstallSelected")
    assert btn is not None, "btnReinstallSelected 应存在"
    assert btn.isEnabled()
    assert "修复 Runtime" in btn.text()


def test_click_install_missing_opens_dialog_with_missing_only(controller, monkeypatch):
    """点补充安装缺失依赖：走当前后端，弹 InstallDialog(missing_only=True)

    回归（问题4）：补装不再二次提示选择 GPU/CPU（旧逻辑弹 BackendChoiceDialog）。
    改为直接读 resolve_use_gpu 当前后端，用 InstallDialog 跑增量补装。
    """
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    # 补装现在走 InstallDialog（非 BackendChoiceDialog），用当前后端
    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", FakeDialog
    )
    # resolve_use_gpu 返回 False（CPU），验证 force_backend 被透传
    monkeypatch.setattr(
        "vibeocr.backend.env_manager.resolve_use_gpu", lambda root: False
    )

    btn.click()

    assert len(instances) == 1, f"应打开一个 InstallDialog，实际: {instances}"
    assert instances[0].get("missing_only") is True, "应为 missing_only 模式"
    assert instances[0].get("force_backend") == "cpu", (
        f"应用当前后端 cpu，实际: {instances[0].get('force_backend')}"
    )


def test_refresh_fills_runtime_accelerator_tree(controller, qtbot):
    """设置页只展示当前 Runtime accelerator，不展示包清单。"""
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 1, timeout=3000)
    top0 = tree.topLevelItem(0)
    assert top0.text(0) == "cpu"
    assert "已验证" in top0.text(1)
    assert top0.text(2) == "0.7.0"


def test_runtime_tree_uses_installer_inspect(controller, qtbot):
    ctrl, host = controller
    ctrl._refresh_env_maintenance_state()
    qtbot.waitUntil(
        lambda: host.findChild(QTreeWidget, "treeDepsStatus").topLevelItemCount() == 1,
        timeout=3000,
    )
    assert ctrl._runtime_installer.inspect.call_count >= 1


def test_runtime_tree_does_not_expose_python_dependency_children(controller, qtbot):
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    ctrl._refresh_env_maintenance_state()
    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 1, timeout=3000)
    assert tree.topLevelItem(0).childCount() == 0


def test_click_reinstall_selected_repairs_whole_profile(controller, monkeypatch, qtbot):
    """兼容按钮请求完整 profile repair，不传真实包名。"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton, QTreeWidget

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 1, timeout=3000)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", FakeDialog
    )

    btn = host.findChild(QPushButton, "btnReinstallSelected")
    btn.click()

    assert len(instances) == 1
    assert instances[0].get("packages") == ["runtime-profile"]
