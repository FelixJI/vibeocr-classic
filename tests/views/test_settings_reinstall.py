"""设置页运行环境应用入口测试（便携 / 子进程形态）

融合形态（uv 引擎环境）的入口在 tests/views/test_engine_env_flow.py；
此处覆盖旧安装器（Runtime Installer）形态：
- 设备/引擎应用请求先确认，再经可见安装对话框执行，不静默变更；
- 未安装 Runtime 时不得把本机无 GPU 推断为 CPU 后端。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.mineru_connection import (
    MINERU_CONNECTION_MODE_LOCAL,
    set_active_mineru_connection_mode,
)
from vibeocr.classic.runtime_installation import RuntimeComponentDescriptor
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
def controller(qtbot, tmp_path, monkeypatch):
    """构造带真实 UI 的 SettingsPageController

    本文件覆盖旧安装器（Runtime Installer）流程；统一置于子进程形态。
    """
    monkeypatch.setenv("VIBEOCR_SUPERVISOR_SUBPROCESS", "1")
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        # GPU worker 由组件专项测试覆盖。禁用真线程，避免 with 退出恢复
        # mock 时 worker 仍跨用例运行。
        patch(
            "vibeocr.classic.widgets.runtime_environment_widget."
            "RuntimeEnvironmentWidget._start_gpu_detection"
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
    yield ctrl, host
    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)


def test_device_only_apply_declined_does_not_stop_service(controller, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    ctrl, _host = controller
    options = MagicMock()
    options.current_backend.return_value = None
    ctrl._backend_options = options
    open_install = MagicMock()
    monkeypatch.setattr(ctrl, "_open_install_dialog", open_install)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    ctrl._on_runtime_change_requested("gpu", [])

    open_install.assert_not_called()
    ctrl._subprocess_manager.invalidate_supervisor.assert_not_called()
    options.set_change_in_progress.assert_called_once_with(False)


def test_device_only_apply_confirmed_opens_visible_install(controller, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    ctrl, _host = controller
    options = MagicMock()
    options.current_backend.return_value = None
    ctrl._backend_options = options
    open_install = MagicMock()
    monkeypatch.setattr(ctrl, "_open_install_dialog", open_install)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    ctrl._on_runtime_change_requested("gpu", [])

    open_install.assert_called_once_with(force_backend="gpu")


def test_uninstalled_runtime_is_not_inferred_as_cpu(controller):
    ctrl, _host = controller
    options = MagicMock()
    options.current_backend.return_value = None
    ctrl._backend_options = options
    ctrl._runtime_has_gpu = False

    assert ctrl._runtime_backend_or_none() is None


def test_invalid_runtime_change_target_releases_widget(controller):
    ctrl, _host = controller
    options = MagicMock()
    ctrl._backend_options = options

    ctrl._on_runtime_change_requested("tpu", ["paddle"])

    options.set_change_in_progress.assert_called_once_with(False)
