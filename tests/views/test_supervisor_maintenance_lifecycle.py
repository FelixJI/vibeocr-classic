"""Install dialogs wait for non-blocking Supervisor invalidation."""

from __future__ import annotations

from unittest.mock import Mock

from PySide6.QtCore import QObject, Signal

from vibeocr.classic.views.main_window import MainWindow
from vibeocr.classic.views.settings_page_controller import SettingsPageController


class _FakeManager(QObject):
    invalidation_finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.invalidate_calls = 0
        self.is_invalidating = False

    def invalidate_supervisor(self) -> bool:
        self.invalidate_calls += 1
        if self.is_invalidating:
            return False
        self.is_invalidating = True
        return True

    def finish(self, success: bool, error: str = "") -> None:
        self.is_invalidating = False
        self.invalidation_finished.emit(success, error)


class _MainWindowHarness:
    _run_after_supervisor_invalidated = MainWindow._run_after_supervisor_invalidated
    _on_supervisor_invalidated_for_maintenance = (
        MainWindow._on_supervisor_invalidated_for_maintenance
    )
    _cancel_pending_maintenance_dialog = (
        MainWindow._cancel_pending_maintenance_dialog
    )
    _show_install_dialog = MainWindow._show_install_dialog

    def __init__(self, manager: _FakeManager) -> None:
        self._closing = False
        self._pending_maintenance_dialog = None
        self._subprocess_manager = manager
        self._statusbar = Mock()
        self._show_install_dialog_after_invalidation = Mock()


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
    _open_install_dialog = SettingsPageController._open_install_dialog
    _open_reinstall_dialog = SettingsPageController._open_reinstall_dialog

    def __init__(self, manager: _FakeManager) -> None:
        self._closing = False
        self._pending_maintenance_dialog = None
        self._subprocess_manager = manager
        self._status_callback = Mock()
        self._show_install_dialog = Mock()
        self._show_reinstall_dialog = Mock()


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


def test_settings_failure_does_not_open_install_dialog(
    qapp, monkeypatch
) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)
    warning = Mock()
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.QMessageBox.warning",
        warning,
    )

    controller._open_install_dialog(force_backend="cpu")
    manager.finish(False, "process still running")

    controller._show_install_dialog.assert_not_called()
    warning.assert_called_once()
    assert "process still running" in warning.call_args.args[2]


def test_settings_repeated_actions_keep_only_first_continuation(qapp) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)

    controller._open_reinstall_dialog(reinstall_python=True)
    controller._open_install_dialog(force_backend="gpu")

    assert manager.invalidate_calls == 1
    manager.finish(True)
    controller._show_reinstall_dialog.assert_called_once_with(
        reinstall_python=True,
        missing_only=False,
    )
    controller._show_install_dialog.assert_not_called()


def test_settings_close_drops_late_invalidation_callback(qapp) -> None:
    manager = _FakeManager()
    controller = _SettingsHarness(manager)

    controller._open_install_dialog(force_backend="cpu")
    controller._closing = True
    controller._cancel_pending_maintenance_dialog()
    manager.finish(True)

    controller._show_install_dialog.assert_not_called()
