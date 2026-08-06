"""设置页重装入口测试"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel, QTreeWidget, QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.runtime_installation import RuntimeComponentDescriptor
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.runtime_contracts import parse_runtime_status


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
            "vibeocr.classic.views.settings_page_controller.RuntimeInstallerClient"
        ) as runtime_client,
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
        runtime_client.return_value.inspect.return_value = SimpleNamespace(
            ready=True,
            accelerator="cpu",
            backend_version="0.7.0",
            python_version="3.13.12",
            protocol_version="2.1.0",
            profile="win-x64-cpu",
            manifest_sha256="a" * 64,
            integrity="verified",
            components=(
                RuntimeComponentDescriptor("ocr_engine", "OCR engine", "3.7.0"),
                RuntimeComponentDescriptor(
                    "document_parsing", "Document parsing", "3.4.4"
                ),
            ),
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
    assert "CPU" in text
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


def test_backend_row_expands_functional_dependency_groups(controller):
    """Backend 下方应展示 manifest 发布的功能分组，而非逐包解析。"""
    _ctrl, host = controller
    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None
    backend = tree.topLevelItem(1)
    assert backend.text(0) == "Backend Supervisor"
    assert backend.childCount() == 2
    assert [backend.child(index).text(0) for index in range(2)] == [
        "OCR engine",
        "Document parsing",
    ]
    assert backend.child(0).text(2) == "3.7.0"


def test_installer_inspection_preserves_component_actual_drift_before_http_ready(
    controller,
) -> None:
    ctrl, host = controller
    inspection = ctrl._runtime_installer.inspect.return_value
    inspection.ready = False
    inspection.integrity = "not-installed"
    inspection.components = (
        RuntimeComponentDescriptor(
            "ocr_engine",
            "OCR engine",
            "3.7.0",
            desired_state="ready",
            desired_version="3.7.0",
            actual_state="drifted",
            actual_version="3.6.0",
            drift_reason="version_mismatch",
            repairable=True,
        ),
    )
    tree = host.findChild(QTreeWidget, "treeDepsStatus")

    ctrl._populate_deps_tree(tree, {"inspection": inspection})

    component = tree.topLevelItem(1).child(0)
    assert component.text(1) == "⚠ 已漂移 · 版本不一致"
    assert component.text(2) == "3.6.0"


def test_http_runtime_status_overrides_component_state(controller):
    ctrl, host = controller
    inspection = ctrl._runtime_installer.inspect.return_value
    status = parse_runtime_status(
        {
            "schema_version": 2,
            "instance_id": "runtime-1",
            "service_state": "maintenance",
            "backend_version": "0.9.0",
            "source": {
                "backend_version": "0.9.0",
                "backend_source_sha": "a" * 40,
                "runtime_manifest_sha256": "b" * 64,
                "protocol_version": "2.3.0",
                "protocol_manifest_sha256": "c" * 64,
            },
            "profile": {
                "profile_id": "win-x64-cpu",
                "accelerator": "cpu",
                "components": [
                    {
                        "component_id": "ocr_engine",
                        "display_name": "OCR engine",
                        "state": "installing",
                        "version": "3.7.0",
                        "desired_state": "ready",
                        "desired_version": "3.7.0",
                        "actual_state": "missing",
                        "actual_version": None,
                        "drift_reason": "missing",
                        "repairable": True,
                    }
                ],
            },
            "maintenance": {
                "operation_id": "op-1",
                "sequence": 4,
                "operation": "repair",
                "operation_state": "running",
                "phase": "install_profile",
                "profile_id": "win-x64-cpu",
                "component_id": "ocr_engine",
                "updated_at": "2026-08-05T00:00:00Z",
            },
        }
    )
    ctrl._env_refresh_generation += 1

    ctrl._apply_env_maintenance_state(
        ctrl._env_refresh_generation,
        {"mode": "portable", "inspection": inspection, "runtime_status": status},
    )

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree.topLevelItem(1).child(0).text(1) == "… 安装中 · 缺失"
    assert "服务：维护中" in host.findChild(QLabel, "labelEnvStatus").text()
    assert "Source：aaaaaaaaaaaa" in host.findChild(QLabel, "labelEnvStatus").text()
    assert (
        "Runtime manifest：bbbbbbbbbbbb"
        in host.findChild(QLabel, "labelEnvStatus").text()
    )
    assert (
        "install_profile · running" in host.findChild(QLabel, "labelEnvStatus").text()
    )


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
    改为读取 Installer 已验证的当前后端，用 InstallDialog 跑增量补装。
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
    controller[0]._backend_options.current_backend = MagicMock(return_value="cpu")

    btn.click()

    assert len(instances) == 1, f"应打开一个 InstallDialog，实际: {instances}"
    assert instances[0].get("missing_only") is True, "应为 missing_only 模式"
    assert instances[0].get("force_backend") == "cpu", (
        f"应用当前后端 cpu，实际: {instances[0].get('force_backend')}"
    )


def test_backend_change_cancel_does_not_start_install(controller, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    ctrl, _host = controller
    options = MagicMock()
    ctrl._backend_options = options
    open_install = MagicMock()
    monkeypatch.setattr(ctrl, "_open_install_dialog", open_install)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    ctrl._on_backend_change_requested("gpu")

    open_install.assert_not_called()
    options.set_change_in_progress.assert_called_once_with(False)


def test_backend_change_confirmation_opens_visible_install(controller, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    ctrl, _host = controller
    open_install = MagicMock()
    monkeypatch.setattr(ctrl, "_open_install_dialog", open_install)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    ctrl._on_backend_change_requested("gpu")

    open_install.assert_called_once_with(force_backend="gpu")


def test_uninstalled_runtime_is_not_inferred_as_cpu(controller):
    ctrl, _host = controller
    options = MagicMock()
    options.current_backend.return_value = None
    ctrl._backend_options = options
    ctrl._runtime_has_gpu = False

    assert ctrl._runtime_backend_or_none() is None


def test_refresh_fills_bound_runtime_components(controller, qtbot):
    """设置页展示用户可理解的绑定组件，而非只显示 accelerator。"""
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 4, timeout=3000)
    rows = {
        tree.topLevelItem(index).text(0): (
            tree.topLevelItem(index).text(1),
            tree.topLevelItem(index).text(2),
        )
        for index in range(tree.topLevelItemCount())
    }
    assert rows == {
        "Python 运行时": ("✓ 已验证", "3.13.12"),
        "Backend Supervisor": ("✓ 已验证", "0.7.0"),
        "Protocol": ("✓ 已绑定", "2.1.0"),
        "CPU 推理 profile": ("✓ 已选择", "win-x64-cpu"),
    }


def test_runtime_tree_uses_installer_inspect(controller, qtbot):
    ctrl, host = controller
    ctrl._refresh_env_maintenance_state()
    qtbot.waitUntil(
        lambda: host.findChild(QTreeWidget, "treeDepsStatus").topLevelItemCount() == 4,
        timeout=3000,
    )
    assert ctrl._runtime_installer.inspect.call_count >= 1


def test_runtime_tree_exposes_only_backend_functional_groups(controller, qtbot):
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    ctrl._refresh_env_maintenance_state()
    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 4, timeout=3000)
    assert tree.topLevelItem(0).childCount() == 0
    assert tree.topLevelItem(1).childCount() == 2
    assert tree.topLevelItem(2).childCount() == 0
    assert tree.topLevelItem(3).childCount() == 0
    assert all(
        "paddle" not in tree.topLevelItem(1).child(index).text(0).lower()
        for index in range(tree.topLevelItem(1).childCount())
    )


def test_click_reinstall_selected_repairs_whole_profile(controller, monkeypatch, qtbot):
    """兼容按钮请求完整 profile repair，不传真实包名。"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton, QTreeWidget

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() == 4, timeout=3000)

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
