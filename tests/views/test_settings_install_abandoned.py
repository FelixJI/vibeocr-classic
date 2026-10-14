"""维护对话框取消/失败后联动恢复 Supervisor 的回归测试。

回归：切换 Runtime 等维护操作会先 invalidate Supervisor；旧逻辑在
InstallDialog 以取消或失败结束（finished(0)）后
只刷新设置页状态，无人重启 Supervisor，导致截图入口永远停在
“OCR Supervisor 子进程正在启动并等待就绪握手，请稍候再试”。

本测试验证取消/失败的 finished 会触发传入的 install_abandoned_callback
（由 MainWindow 提供以重新检测依赖并恢复 Supervisor），成功 finished(1)
不触发（成功路径由 install_succeeded 单独联动）。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QWidget

from vibeocr.classic.runtime_maintenance import RuntimeInstallerClientError
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
    manager.acquire_maintenance.side_effect = lambda on_granted, *, is_active: (
        on_granted()
    )
    return manager


@pytest.fixture
def controller(qtbot, tmp_path, monkeypatch):
    """构造带真实 UI 的 SettingsPageController，patch 掉重依赖保证隔离。

    所有 patch 必须覆盖整个测试生命周期，而非仅构造期：测试体内的
    ``finished(0)`` 联动会同步走到 ``refresh_runtime_state``，构造期的
    ``with patch(...)`` 那时已退出，真实的 ``_start_gpu_detection`` 会拉起
    GPU 探测线程（Popen nvidia-smi 与 Runtime Installer 子进程），
    ``is_cache_valid`` 会经 ``generate_machine_id`` 拉起 wmic 子进程，
    ``inspect`` 也会 Popen Runtime Installer——CI 上由此引入不受测试
    控制的子进程探测。故统一用 monkeypatch 全程挡住，并注入 inspect
    立即失败的 Runtime Installer 桩。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    install_cb = MagicMock(name="install_succeeded_callback")
    abandoned_cb = MagicMock(name="install_abandoned_callback")

    monkeypatch.setattr(
        "vibeocr.classic.widgets.runtime_environment_widget."
        "RuntimeEnvironmentWidget._start_gpu_detection",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.is_cache_valid",
        lambda project_root: (False, None),
    )
    mock_cm = MagicMock(name="ConfigManager")
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
    monkeypatch.setattr(
        "vibeocr.classic.managers.config_manager.ConfigManager", mock_cm
    )

    runtime_installer = MagicMock(name="runtime_installer_client")
    runtime_installer.inspect.side_effect = RuntimeInstallerClientError(
        "测试桩：不探测真实 Runtime"
    )

    ctrl = SettingsPageController(
        ui=host,
        project_root=Path(tmp_path),
        status_callback=lambda msg: None,
        ocr_ready_callback=lambda: True,
        subprocess_manager=_immediate_invalidation_manager(),
        install_succeeded_callback=install_cb,
        install_abandoned_callback=abandoned_cb,
        runtime_installer_client=runtime_installer,
    )
    ctrl.connect_signals()
    return ctrl, host, install_cb, abandoned_cb


class _FakeInstallDialog(QDialog):
    """提供真实 finished / install_succeeded Signal 的轻量安装对话框。"""

    install_completed = Signal(bool, str)
    install_succeeded = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.before_install = kwargs.get(
            "before_install", lambda continuation: continuation()
        )

    def can_start_installation(self):
        return True

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

    dialog.before_install(lambda: None)
    dialog.finished.emit(0)

    abandoned_cb.assert_called_once_with()
    install_cb.assert_not_called()
    assert dialog not in ctrl._active_dialogs


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("cancelled", "本次安装已取消"),
        ("failed", "本次安装失败"),
    ],
)
def test_install_terminal_result_survives_service_recovery(
    controller, monkeypatch, state, expected
):
    from uuid import uuid4

    from vibeocr.classic.runtime_maintenance import InstallationRecord

    ctrl, _host, _installed, abandoned = controller
    result = MagicMock()
    ctrl._maintenance_result_callback = result
    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )

    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]
    dialog.before_install(lambda: None)
    InstallationRecord(str(uuid4()), 2, state).save(ctrl._project_root)
    dialog.install_completed.emit(False, "Runtime Installer 操作已取消")
    dialog.finished.emit(0)

    result.assert_called_once_with(expected)
    abandoned.assert_called_once_with()


def test_prior_cancelled_record_does_not_classify_new_unstarted_dialog(
    controller, monkeypatch
):
    from uuid import uuid4

    from vibeocr.classic.runtime_maintenance import InstallationRecord

    ctrl, _host, _installed, _abandoned = controller
    result = MagicMock()
    ctrl._maintenance_result_callback = result
    InstallationRecord(str(uuid4()), 2, "cancelled").save(ctrl._project_root)
    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )

    ctrl._show_install_dialog()
    ctrl._active_dialogs[-1].install_completed.emit(False, "未开始安装")

    result.assert_called_once_with("本次安装未完成")


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

    dialog.before_install(lambda: None)
    dialog.finished.emit(0)

    abandoned_cb.assert_called_once_with()


def test_abandoned_callback_optional_without_crash(controller, monkeypatch):
    """未提供 install_abandoned_callback 时取消路径不崩溃（仅刷新）。"""
    ctrl, _host, _install_cb, _abandoned_cb = controller
    ctrl._install_abandoned_callback = None

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.before_install(lambda: None)
    dialog.finished.emit(0)

    assert dialog not in ctrl._active_dialogs


def test_maintenance_active_reflects_pending_and_open_dialogs(controller, monkeypatch):
    """is_maintenance_active 覆盖“正在停止”与“对话框打开”两个阶段。"""
    ctrl, _host, _install_cb, _abandoned_cb = controller

    assert ctrl.is_maintenance_active is False

    # “正在停止”阶段：等待 Supervisor 退出的 continuation 挂起。
    ctrl._pending_maintenance_dialog = lambda: None
    assert ctrl.is_maintenance_active is True
    ctrl._cancel_pending_maintenance_dialog()
    assert ctrl.is_maintenance_active is False

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()

    assert ctrl.is_maintenance_active is True

    ctrl._active_dialogs[-1].finished.emit(0)

    assert ctrl.is_maintenance_active is False


@pytest.mark.parametrize("success", [False, True])
def test_close_active_install_waits_for_result_before_runtime_recovery(
    controller, monkeypatch, qtbot, success
):
    import threading
    from uuid import uuid4
    from PySide6.QtCore import Qt
    from vibeocr.classic.runtime_maintenance import InstallationRecord
    from vibeocr.classic.widgets.install_dialog import InstallWorker

    ctrl, _host, installed, abandoned = controller
    release = threading.Event()
    started = threading.Event()
    operation_id = str(uuid4())

    class DelayedTerminalWorker(InstallWorker):
        def run(self):
            self.plan_ready.emit(None)
            self._confirmation.wait(5)
            InstallationRecord(operation_id, 1, "running", None, ()).save(
                self._project_root
            )
            started.set()
            release.wait(5)
            state = "succeeded" if success else "cancelled"
            InstallationRecord(operation_id, 2, state, None, ()).save(
                self._project_root
            )
            self.completed.emit(success, state)

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallWorker", DelayedTerminalWorker
    )
    ctrl._show_install_dialog(missing_only=True)
    dialog = ctrl._active_dialogs[-1]
    try:
        qtbot.waitUntil(lambda: dialog._confirm_button.isVisible())
        dialog._confirm_button.click()
        qtbot.waitUntil(started.is_set)
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog in ctrl._active_dialogs
        installed.assert_not_called()
        abandoned.assert_not_called()
        assert InstallationRecord.read(ctrl._project_root).state == "running"
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
        qtbot.waitUntil(lambda: dialog not in ctrl._active_dialogs)
        if success:
            installed.assert_called_once_with()
            abandoned.assert_not_called()
        else:
            abandoned.assert_called_once_with()
            installed.assert_not_called()
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
        dialog.close()


@pytest.mark.parametrize("operation_missing", [False, True])
def test_replayed_failed_operation_restores_service_without_new_install(
    controller, monkeypatch, qtbot, operation_missing
):
    from types import SimpleNamespace
    from uuid import uuid4
    from vibeocr.classic.runtime_installation import RuntimeMaintenanceUpdate
    from vibeocr.classic.runtime_maintenance import InstallationRecord

    ctrl, _host, installed, abandoned = controller
    record = InstallationRecord(str(uuid4()), 1, "running", "plan", ())
    record.save(ctrl._project_root)
    terminal = RuntimeMaintenanceUpdate(
        "snapshot",
        record.operation_id,
        2,
        "ensure",
        "failed",
        "install_profile",
        "win-x64-cpu",
        "2026-09-21T00:00:00Z",
        message_args={"reason_code": "download_failed", "next_action": "check_network"},
    )
    client = MagicMock()
    client.observe.return_value = SimpleNamespace(
        events=(terminal,), snapshot=terminal, more=False, through_sequence=2
    )
    if operation_missing:
        client.observe.side_effect = RuntimeInstallerClientError(
            "operation not found", canonical_code="RUNTIME_OPERATION_NOT_FOUND"
        )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient",
        lambda *args, **kwargs: client,
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]
    qtbot.waitUntil(lambda: dialog._worker is None)
    try:
        assert InstallationRecord.read(ctrl._project_root).state == "failed"
        abandoned.assert_called_once_with()
        installed.assert_not_called()
        client.ensure.assert_not_called()
        client.repair.assert_not_called()
        ctrl._subprocess_manager.invalidate_supervisor.assert_not_called()
    finally:
        dialog.close()


def test_dialog_closed_while_queued_revokes_maintenance(controller, monkeypatch):
    """等待许可期间用户关闭对话框：撤销排队请求而非停服后走恢复。

    回归：排队请求此前没有撤销路径，前一流程结束后仍会获批并停止
    Supervisor，随后才因 worker 已取消走 abandoned 恢复——用户取消后
    出现不必要的服务中断，等待期间设置页也被 pending 锁住。
    """
    from tests.views.test_supervisor_maintenance_lifecycle import _FakeManager

    ctrl, _host, install_cb, abandoned_cb = controller
    manager = _FakeManager()
    manager.hold_maintenance()
    ctrl._subprocess_manager = manager

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    # 用户点击确认：维护请求进入协调器排队（许可被其他流程持有）。
    dialog.before_install(lambda: None)
    assert len(manager._queued_entries) == 1
    assert ctrl._pending_maintenance_dialog is not None

    # 等待期间用户直接关闭对话框：排队请求被撤销，不再持有 pending。
    dialog.finished.emit(0)
    assert ctrl._pending_maintenance_dialog is None
    assert manager._queued_entries[0]["cancelled"] is True

    # 前一流程结束后被撤销的请求不获批、不失效、不触发恢复回调。
    with pytest.raises(AssertionError):
        manager.grant_pending_maintenance()
    assert manager.invalidate_calls == 0
    abandoned_cb.assert_not_called()
    install_cb.assert_not_called()


def test_dialog_closed_after_grant_keeps_invalidation_subscription(
    controller, monkeypatch
):
    """已获批、失效在途时关闭对话框：保留订阅，结束后走 abandoned 恢复。

    回归：撤销条件此前只看 maintenance_started，无法区分“仍在排队”与
    “已获批并开始失效”——失效在途时关闭对话框会断开订阅，失效仍停服
    但无人继续安装或恢复，识别服务一直停着；探针还可能提前放许可。
    """
    from tests.views.test_supervisor_maintenance_lifecycle import _FakeManager

    ctrl, _host, install_cb, abandoned_cb = controller
    manager = _FakeManager()  # 立即授予；invalidate 启动后不广播（在途）
    ctrl._subprocess_manager = manager

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.before_install(lambda: None)
    assert manager.invalidate_calls == 1
    assert ctrl._pending_maintenance_dialog is not None
    assert ctrl._pending_maintenance_permit.granted is True

    # 失效完成前用户关闭对话框：worker 随之取消，但订阅不得断开。
    dialog.can_start_installation = lambda: False
    dialog.finished.emit(0)
    assert ctrl._pending_maintenance_dialog is not None

    # 失效完成广播：start() 发现 worker 已取消 → abandoned 恢复联动。
    manager.finish(True)
    abandoned_cb.assert_called_once_with()
    install_cb.assert_not_called()


def test_permit_released_on_worker_terminal(controller, monkeypatch):
    """安装失败/取消后详情窗口保留期间（finished 未发），许可随 worker 终态释放。"""
    from tests.views.test_supervisor_maintenance_lifecycle import _FakeManager

    ctrl, _host, install_cb, abandoned_cb = controller
    manager = _FakeManager()
    ctrl._subprocess_manager = manager

    monkeypatch.setattr(
        "vibeocr.classic.widgets.install_dialog.InstallDialog", _FakeInstallDialog
    )
    ctrl._show_install_dialog()
    dialog = ctrl._active_dialogs[-1]

    dialog.before_install(lambda: None)
    manager.finish(True)  # 失效完成 → start() 放行 continuation
    probe = manager.probes[0]
    assert probe() is True  # 安装进行中：对话框带本请求 token

    dialog.install_completed.emit(False, "Runtime Installer 操作已取消")
    assert dialog in ctrl._active_dialogs  # 失败详情窗口仍打开
    assert probe() is False  # worker 终态即释放许可
    abandoned_cb.assert_called_once_with()
