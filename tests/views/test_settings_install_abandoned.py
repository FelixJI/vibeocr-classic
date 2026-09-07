"""维护对话框取消/失败后联动恢复 Supervisor 的回归测试。

回归：切换 Runtime 等维护操作会先 invalidate Supervisor；旧逻辑在
InstallDialog / BackendChoiceDialog 以取消或失败结束（finished(0)）后
只刷新设置页状态，无人重启 Supervisor，导致截图入口永远停在
“OCR Supervisor 子进程正在启动并等待就绪握手，请稍候再试”。

本测试验证取消/失败的 finished 会触发传入的 install_abandoned_callback
（由 MainWindow 提供以重新检测依赖并恢复 Supervisor），成功 finished(1)
不触发（成功路径由 install_succeeded 单独联动）。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QWidget

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
    """构造带真实 UI 的 SettingsPageController，patch 掉重依赖保证隔离"""
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    install_cb = MagicMock(name="install_succeeded_callback")
    abandoned_cb = MagicMock(name="install_abandoned_callback")

    with (
        patch(
            "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection"
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
            project_root=Path(tmp_path),
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=_immediate_invalidation_manager(),
            install_succeeded_callback=install_cb,
            install_abandoned_callback=abandoned_cb,
        )
        ctrl.connect_signals()
    return ctrl, host, install_cb, abandoned_cb


class _FakeBackendChoiceDialog(QDialog):
    """提供真实 finished / install_succeeded Signal 的轻量对话框。"""

    install_succeeded = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def show(self):
        pass


class _FakeInstallDialog(QDialog):
    """提供真实 finished / install_succeeded Signal 的轻量安装对话框。"""

    install_succeeded = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def show(self):
        pass


def test_install_dialog_failure_invokes_abandoned_callback(controller, monkeypatch):
    """InstallDialog 失败（done(0)）应触发 install_abandoned_callback。"""
    ctrl, _host, install_cb, abandoned_cb = controller

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(0)

    abandoned_cb.assert_called_once_with()
    install_cb.assert_not_called()
    assert dialog not in ctrl._active_dialogs


def test_install_dialog_success_does_not_invoke_abandoned_callback(
    controller, monkeypatch
):
    """InstallDialog 成功（done(1)）只走 install_succeeded 联动。"""
    ctrl, _host, install_cb, abandoned_cb = controller

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(1)

    abandoned_cb.assert_not_called()
    install_cb.assert_not_called()


def test_install_dialog_user_close_invokes_abandoned_callback(controller, monkeypatch):
    """用户直接关闭安装对话框（等价 finished(0)）也应恢复 Supervisor。"""
    ctrl, _host, _install_cb, abandoned_cb = controller

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(0)

    abandoned_cb.assert_called_once_with()


def test_reinstall_dialog_cancel_invokes_abandoned_callback(controller, monkeypatch):
    """BackendChoiceDialog 取消（finished(0)）应触发 install_abandoned_callback。"""
    ctrl, _host, _install_cb, abandoned_cb = controller

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog",
        _FakeBackendChoiceDialog,
    )
    ctrl._open_reinstall_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(0)

    abandoned_cb.assert_called_once_with()


def test_reinstall_dialog_success_does_not_invoke_abandoned_callback(
    controller, monkeypatch
):
    """BackendChoiceDialog 成功（finished(1)）不触发恢复回调。"""
    ctrl, _host, _install_cb, abandoned_cb = controller

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog",
        _FakeBackendChoiceDialog,
    )
    ctrl._open_reinstall_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(1)

    abandoned_cb.assert_not_called()


def test_abandoned_callback_optional_without_crash(controller, monkeypatch):
    """未提供 install_abandoned_callback 时取消路径保持旧行为（仅刷新）。"""
    ctrl, _host, _install_cb, _abandoned_cb = controller
    ctrl._install_abandoned_callback = None

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog",
        _FakeBackendChoiceDialog,
    )
    ctrl._open_reinstall_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.finished.emit(0)

    assert dialog not in ctrl._active_dialogs


def test_maintenance_active_reflects_pending_and_open_dialogs(controller, monkeypatch):
    """is_maintenance_active 覆盖“正在停止”与“对话框打开”两个阶段。"""
    ctrl, _host, _install_cb, _abandoned_cb = controller

    assert ctrl.is_maintenance_active is False

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog",
        _FakeBackendChoiceDialog,
    )
    ctrl._open_reinstall_dialog()

    assert ctrl.is_maintenance_active is True

    ctrl._active_dialogs[-1].finished.emit(0)

    assert ctrl.is_maintenance_active is False
