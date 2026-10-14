"""Install dialogs wait for non-blocking Supervisor invalidation."""

from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

import pytest

from vibeocr.classic.views.main_window import MainWindow
from vibeocr.classic.views.settings_page_controller import SettingsPageController


class _FakePermit:
    """真实 MaintenancePermit 的测试桩：撤销排队中的请求。"""

    def __init__(self, entry: dict) -> None:
        self._entry = entry

    @property
    def granted(self) -> bool:
        return self._entry["granted"]

    def cancel(self) -> None:
        self._entry["cancelled"] = True


class _FakeManager(QObject):
    invalidation_finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.invalidate_calls = 0
        self.is_invalidating = False
        self.maintenance_in_progress = False
        self.is_starting = False
        self.holds_runtime_process = False
        self.probes: list = []
        self._maintenance_busy = False
        self._queued_entries: list[dict] = []

    def invalidate_supervisor(self) -> bool:
        self.invalidate_calls += 1
        if self.is_invalidating:
            return False
        self.is_invalidating = True
        return True

    def finish(self, success: bool, error: str = "") -> None:
        self.is_invalidating = False
        self.invalidation_finished.emit(success, error)

    def acquire_maintenance(self, on_granted, *, is_active) -> _FakePermit:
        # 立即授予（未竞争场景）；测试可用 hold_maintenance 模拟许可被占。
        self.probes.append(is_active)
        entry = {"on_granted": on_granted, "cancelled": False, "granted": False}
        if self._maintenance_busy:
            self._queued_entries.append(entry)
            return _FakePermit(entry)
        entry["granted"] = True
        self._maintenance_busy = True
        on_granted()
        return _FakePermit(entry)

    def hold_maintenance(self) -> None:
        self._maintenance_busy = True

    def grant_pending_maintenance(self) -> None:
        while self._queued_entries:
            entry = self._queued_entries.pop(0)
            if entry["cancelled"]:
                continue
            entry["granted"] = True
            entry["on_granted"]()
            return
        raise AssertionError("没有可授予的排队维护请求")


class _MainWindowHarness(QWidget):
    _run_after_supervisor_invalidated = MainWindow._run_after_supervisor_invalidated
    _on_supervisor_invalidated_for_maintenance = (
        MainWindow._on_supervisor_invalidated_for_maintenance
    )
    _cancel_pending_maintenance_dialog = MainWindow._cancel_pending_maintenance_dialog
    _show_install_dialog = MainWindow._show_install_dialog
    _continue_ready_startup = MainWindow._continue_ready_startup
    _start_supervisor_when_maintenance_done = (
        MainWindow._start_supervisor_when_maintenance_done
    )
    _stop_supervisor_start_retry = MainWindow._stop_supervisor_start_retry
    _ensure_supervisor_start_retry = MainWindow._ensure_supervisor_start_retry
    _SUPERVISOR_START_RETRY_INTERVAL_MS = MainWindow._SUPERVISOR_START_RETRY_INTERVAL_MS

    def __init__(self, manager: _FakeManager) -> None:
        super().__init__()
        self._closing = False
        self._pending_maintenance_dialog = None
        self._subprocess_manager = manager
        self._statusbar = Mock()
        self._show_install_dialog_after_invalidation = Mock()
        self._ocr_ready = True
        self._install_dialog_open = False
        self._supervisor_start_retry_timer = None
        self._start_supervisor = Mock()
        self._refresh_settings_env_state = Mock()


class _SettingsHarness:
    _run_after_supervisor_invalidated = (
        SettingsPageController._run_after_supervisor_invalidated
    )
    _on_supervisor_invalidated_for_maintenance = (
        SettingsPageController._on_supervisor_invalidated_for_maintenance
    )
    _cancel_pending_maintenance_dialog = (
        SettingsPageController._cancel_pending_maintenance_dialog
    )

    def __init__(self, manager: _FakeManager) -> None:
        self._closing = False
        self._pending_maintenance_dialog = None
        self._subprocess_manager = manager
        self._status_callback = Mock()
        self._show_install_dialog = Mock()


def test_main_window_waits_and_coalesces_repeated_install_requests(qapp) -> None:
    manager = _FakeManager()
    window = _MainWindowHarness(manager)

    window._show_install_dialog([])
    window._show_install_dialog([])

    assert manager.invalidate_calls == 1
    window._show_install_dialog_after_invalidation.assert_not_called()

    manager.finish(True)
    window._show_install_dialog_after_invalidation.assert_called_once_with([])


def test_main_window_close_drops_late_invalidation_callback(qapp) -> None:
    manager = _FakeManager()
    window = _MainWindowHarness(manager)

    window._show_install_dialog([])
    window._closing = True
    window._cancel_pending_maintenance_dialog()
    manager.finish(True)

    window._show_install_dialog_after_invalidation.assert_not_called()


def test_settings_failure_does_not_open_install_dialog(qapp, monkeypatch) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)
    warning = Mock()
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.QMessageBox.warning",
        warning,
    )

    controller._run_after_supervisor_invalidated(controller._show_install_dialog)
    manager.finish(False, "process still running")

    controller._show_install_dialog.assert_not_called()
    warning.assert_called_once()
    assert "process still running" in warning.call_args.args[2]


def test_settings_repeated_actions_keep_only_first_continuation(qapp) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)

    controller._run_after_supervisor_invalidated(controller._show_install_dialog)
    controller._run_after_supervisor_invalidated(controller._show_install_dialog)

    assert manager.invalidate_calls == 1
    manager.finish(True)
    controller._show_install_dialog.assert_called_once()


def test_settings_request_queues_behind_other_maintenance(qapp) -> None:
    """其他维护持有许可时请求必须排队，获得许可后执行自己的失效。

    回归：旧逻辑在共享广播上等待/丢弃 continuation，会带着失效的
    “旧 Supervisor 已退出”前置执行（持有者可能已重启 Supervisor）。
    """
    manager = _FakeManager()
    manager.hold_maintenance()
    controller = _SettingsHarness(manager)
    continuation = Mock()

    controller._run_after_supervisor_invalidated(continuation)

    assert manager.invalidate_calls == 0
    continuation.assert_not_called()

    manager.grant_pending_maintenance()
    assert manager.invalidate_calls == 1
    manager.finish(True)
    continuation.assert_called_once_with()


def test_main_window_request_queues_behind_other_maintenance(qapp) -> None:
    manager = _FakeManager()
    manager.hold_maintenance()
    window = _MainWindowHarness(manager)
    continuation = Mock()

    window._run_after_supervisor_invalidated(continuation)

    assert manager.invalidate_calls == 0
    continuation.assert_not_called()

    manager.grant_pending_maintenance()
    assert manager.invalidate_calls == 1
    manager.finish(True)
    continuation.assert_called_once_with()


def test_queued_settings_request_ignores_broadcast_until_granted(qapp) -> None:
    """排队中的请求不得消费其他 owner 的失效完成广播。

    回归：旧实现在排队前就订阅全局 ``invalidation_finished``，首启安装
    的失效完成会绕过许可与自己的失效直接执行排队的 continuation。
    """
    manager = _FakeManager()
    manager.hold_maintenance()
    controller = _SettingsHarness(manager)
    continuation = Mock()

    controller._run_after_supervisor_invalidated(continuation)

    # 其他 owner 的失效完成广播：排队中的请求必须无动于衷。
    manager.finish(True)
    continuation.assert_not_called()
    assert manager.invalidate_calls == 0

    manager.grant_pending_maintenance()
    assert manager.invalidate_calls == 1
    manager.finish(True)
    continuation.assert_called_once_with()


def test_queued_main_window_request_ignores_broadcast_until_granted(qapp) -> None:
    manager = _FakeManager()
    manager.hold_maintenance()
    window = _MainWindowHarness(manager)
    continuation = Mock()

    window._run_after_supervisor_invalidated(continuation)

    manager.finish(True)
    continuation.assert_not_called()
    assert manager.invalidate_calls == 0

    manager.grant_pending_maintenance()
    assert manager.invalidate_calls == 1
    manager.finish(True)
    continuation.assert_called_once_with()


def test_settings_close_drops_late_invalidation_callback(qapp) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)

    controller._run_after_supervisor_invalidated(controller._show_install_dialog)
    controller._closing = True
    controller._cancel_pending_maintenance_dialog()
    manager.finish(True)

    controller._show_install_dialog.assert_not_called()


def test_supervisor_recovery_deferred_while_maintenance_active(
    qapp, qtbot, monkeypatch
) -> None:
    """维护（含排队）期间迟到的 Supervisor 恢复不得启动，维护结束后再启动。

    回归：前一维护完成后的依赖检查是异步的，其恢复路径曾直接
    ``_start_supervisor()``，会抢占下一个维护流程正在安装/删除的
    Runtime 与引擎环境。
    """
    monkeypatch.setattr(MainWindow, "_SUPERVISOR_START_RETRY_INTERVAL_MS", 10)
    manager = _FakeManager()
    manager.maintenance_in_progress = True
    window = _MainWindowHarness(manager)

    window._continue_ready_startup()
    window._start_supervisor.assert_not_called()

    # 维护结束（许可释放且队列清空）后，重试定时器接管启动。
    manager.maintenance_in_progress = False
    qtbot.waitUntil(lambda: window._start_supervisor.called, timeout=5000)
    window._start_supervisor.assert_called_once_with()


def test_install_finished_startup_deferred_while_maintenance_active(
    qapp, qtbot, monkeypatch
) -> None:
    monkeypatch.setattr(MainWindow, "_SUPERVISOR_START_RETRY_INTERVAL_MS", 10)
    manager = _FakeManager()
    manager.maintenance_in_progress = True
    window = _MainWindowHarness(manager)
    install_finished = MainWindow._on_install_finished

    install_finished(window, 1)
    window._start_supervisor.assert_not_called()

    manager.maintenance_in_progress = False
    qtbot.waitUntil(lambda: window._start_supervisor.called, timeout=5000)


def test_main_window_holder_probe_ignores_queued_request(qapp) -> None:
    """主窗口持有者探针只描述本请求；排队请求的 pending 不得拖住许可。

    回归：首启自动安装对话框 exec() 期间再次触发安装（如 300ms 定时
    弹窗与用户手动安装竞争）时，探针曾读共享 pending 字段，首个对话框
    关闭后许可仍因排队请求保持 active，永远无法释放。
    """
    manager = _FakeManager()
    window = _MainWindowHarness(manager)

    window._run_after_supervisor_invalidated(Mock())
    manager.finish(True)
    # 模拟获批 continuation 打开的首启安装对话框 exec()。
    window._install_dialog_open = True
    probe1 = manager.probes[0]
    assert probe1() is True

    window._run_after_supervisor_invalidated(Mock())

    window._install_dialog_open = False
    assert probe1() is False


def test_deferred_startup_blocked_by_residual_process(qapp, qtbot, monkeypatch) -> None:
    """维护结束但失效失败留下残留进程 owner 时，延迟恢复不得启动。

    回归：重试定时器曾直接 _start_supervisor()，该底层方法不检查
    holds_runtime_process，会叠加第二个 Supervisor 并覆盖旧句柄。
    """
    monkeypatch.setattr(MainWindow, "_SUPERVISOR_START_RETRY_INTERVAL_MS", 10)
    manager = _FakeManager()
    manager.maintenance_in_progress = True
    window = _MainWindowHarness(manager)

    window._continue_ready_startup()
    window._start_supervisor.assert_not_called()

    manager.maintenance_in_progress = False
    manager.holds_runtime_process = True
    qtbot.wait(60)
    window._start_supervisor.assert_not_called()

    manager.holds_runtime_process = False
    window._start_supervisor_when_maintenance_done()
    window._start_supervisor.assert_called_once_with()


def test_deferred_startup_rechecks_ocr_ready(qapp, monkeypatch) -> None:
    monkeypatch.setattr(MainWindow, "_SUPERVISOR_START_RETRY_INTERVAL_MS", 10)
    manager = _FakeManager()
    manager.maintenance_in_progress = True
    window = _MainWindowHarness(manager)

    window._continue_ready_startup()
    window._ocr_ready = False  # 等待期间后续检测把就绪状态改回 false
    manager.maintenance_in_progress = False
    window._start_supervisor_when_maintenance_done()

    window._start_supervisor.assert_not_called()


def test_cancelled_request_revokes_queued_maintenance(qapp) -> None:
    """用户关闭等待中的对话框等撤销路径必须撤掉排队中的维护请求。"""
    manager = _FakeManager()
    manager.hold_maintenance()
    controller = _SettingsHarness(manager)
    continuation = Mock()

    controller._run_after_supervisor_invalidated(continuation)
    assert len(manager._queued_entries) == 1
    permit = controller._pending_maintenance_permit
    assert permit is not None

    controller._cancel_pending_maintenance_dialog()

    with pytest.raises(AssertionError):
        manager.grant_pending_maintenance()
    continuation.assert_not_called()
