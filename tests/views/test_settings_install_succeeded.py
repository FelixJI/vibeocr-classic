"""设置页重装对话框 install_succeeded 联动测试（Bug A 修复）

回归：用户在设置页重装 OCR 依赖（BackendChoiceDialog）成功后，截图界面仍
提示"OCR功能未就绪"。根因：设置页重装路径只连了 dialog.finished（刷新设置页
表格/label），没连 dialog.install_succeeded，也没触发 MainWindow 重新检测依赖
+ 启动子进程 Worker。首启路径（_show_install_dialog）才做了全套联动。

本测试验证 SettingsPageController._open_reinstall_dialog 在对话框 emit
install_succeeded 时调用传入的 install_succeeded_callback，由 MainWindow 提供
该回调触发 dependency_manager.check_dependencies()（联动 Worker 启动）。

不依赖 test_settings_reinstall.py 的重 fixture（那个有预存 BackendOptionsWidget
detect_gpu_info 的 mock 问题），自包含构造 controller 的最小依赖。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

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
        )
        ctrl.connect_signals()
    return ctrl, host, install_cb


def test_open_reinstall_dialog_connects_install_succeeded(controller, monkeypatch):
    """_open_reinstall_dialog 应把 dialog.install_succeeded 连到传入的回调"""
    ctrl, _host, _install_cb = controller

    captured = {}

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            captured["instance"] = self
            self.finished = MagicMock()
            self.install_succeeded = MagicMock()

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    ctrl._open_reinstall_dialog()

    # install_succeeded 信号应被连接（connect 被调用）
    assert "instance" in captured, "应创建对话框"
    captured["instance"].install_succeeded.connect.assert_called_once()
    # 连接的目标应是 install_succeeded_callback
    connected_callable = captured["instance"].install_succeeded.connect.call_args[0][0]
    assert callable(connected_callable), "应连接一个可调用对象"


def test_install_succeeded_emission_invokes_callback(controller, monkeypatch):
    """对话框 emit install_succeeded 时应调用 install_succeeded_callback"""
    ctrl, _host, install_cb = controller

    # 用真实 Signal 驱动回调（验证连接链路，而非仅 connect 被调用）
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QDialog

    class FakeDialog(QDialog):
        install_succeeded = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.finished = MagicMock()

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    ctrl._open_reinstall_dialog()

    # 模拟安装成功 emit
    # _open_reinstall_dialog 持有 dialog 引用在 _active_dialogs
    dialog = ctrl._active_dialogs[-1]
    dialog.install_succeeded.emit()

    (
        install_cb.assert_called_once(),
        ("install_succeeded 信号应触发传入的 install_succeeded_callback"),
    )


def test_cancelled_finished_still_refreshes_env(controller, monkeypatch):
    """取消/失败的 finished 信号仍刷新环境维护状态。

    用 QDialog 子类提供真实的 finished Signal（Qt 要求 Signal 在类级定义），
    emit(0) 驱动 _on_finished 槽，验证它仍调用 _refresh_env_maintenance_state。
    """
    ctrl, _host, _install_cb = controller

    refresh_calls = {"n": 0}
    original_refresh = ctrl._refresh_env_maintenance_state

    def counting_refresh():
        refresh_calls["n"] += 1
        original_refresh()

    ctrl._refresh_env_maintenance_state = counting_refresh

    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QDialog

    class FakeDialog(QDialog):
        # install_succeeded 用真实 Signal（类级定义）
        install_succeeded = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    ctrl._open_reinstall_dialog()
    dialog = ctrl._active_dialogs[-1]

    # 成功 result=1 已由 install_succeeded 刷新；此处验证取消 result=0。
    dialog.finished.emit(0)

    assert refresh_calls["n"] >= 1, "finished 仍应刷新环境维护状态"
    # finished 后对话框应从 _active_dialogs 移除（允许回收）
    assert dialog not in ctrl._active_dialogs, "finished 应移除对话框引用"
