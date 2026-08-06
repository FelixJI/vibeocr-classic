"""启动与设置页慢操作的 GUI 响应性及生命周期回归测试。"""

from __future__ import annotations

import subprocess
import threading
from unittest.mock import MagicMock

from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.shortcuts import create_windows_shortcut
from vibeocr.classic.views.main_window import MainWindow
from vibeocr.classic.views.settings_page_controller import (
    SettingsPageController,
)


def _controller(qtbot, tmp_path) -> SettingsPageController:
    host = QWidget()
    qtbot.addWidget(host)
    return SettingsPageController(
        ui=host,
        project_root=tmp_path,
        status_callback=lambda _message: None,
        ocr_ready_callback=lambda: True,
        subprocess_manager=MagicMock(),
    )


def test_settings_drain_waits_for_owned_cache_task(qtbot, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    controller = _controller(qtbot, tmp_path)

    def slow_cache_call():
        entered.set()
        release.wait(timeout=2)
        return "done"

    controller._run_cache_operation(
        slow_cache_call, lambda _result: None, lambda _e: None
    )
    assert entered.wait(timeout=1)
    controller.request_shutdown()

    assert controller.drain(20) is False
    release.set()
    assert controller.drain(2000) is True


def test_refresh_product_binding_only_reinspects_runtime(qtbot, tmp_path) -> None:
    controller = _controller(qtbot, tmp_path)
    controller._show_settings_toast = MagicMock()
    controller._refresh_env_maintenance_state = MagicMock()

    controller._on_update_deps()

    controller._show_settings_toast.assert_called_once_with(
        "Runtime 版本由产品更新统一管理"
    )
    controller._refresh_env_maintenance_state.assert_called_once_with()


def test_machine_cache_validation_does_not_block_gui(qtbot, tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_valid(_root):
        entered.set()
        release.wait(timeout=2)
        return True, {
            "dependencies": {
                "paddlepaddle": True,
                "paddleocr": True,
                "mineru": True,
            }
        }

    monkeypatch.setattr("vibeocr.classic.views.main_window.is_cache_valid", slow_valid)
    window = MagicMock()
    window._project_root = tmp_path
    window._closing = False
    window._machine_cache_running = False
    window._machine_cache_pending_startup = False
    window._machine_cache_generation = 0
    window._machine_cache_tasks = set()
    window._machine_cache_data = None
    window._dependency_check_complete = False
    window._ocr_ready = False
    window._settings_controller = None
    window._request_machine_cache_load = MainWindow._request_machine_cache_load.__get__(
        window
    )
    window._apply_provisional_machine_cache = (
        MainWindow._apply_provisional_machine_cache.__get__(window)
    )
    window._continue_ready_startup = MagicMock()

    MainWindow._try_load_cache(window)
    assert entered.wait(timeout=1)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: not release.is_set())
    release.set()
    qtbot.waitUntil(lambda: not window._machine_cache_running, timeout=1000)

    assert window._ocr_ready is True


def test_shortcut_creation_is_responsive_single_flight_and_restores_buttons(
    qtbot, tmp_path, monkeypatch
):
    controller = _controller(qtbot, tmp_path)
    controller._btn_desktop = QPushButton()
    controller._btn_startmenu = QPushButton()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_create(*_args):
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller._is_bundled", lambda: True
    )
    monkeypatch.setattr(
        "vibeocr.classic.utils.shortcuts.create_windows_shortcut", slow_create
    )
    toast = MagicMock()
    controller._show_settings_toast = toast

    args = ("app.exe", "VibeOCR.lnk", "", str(tmp_path))
    controller._start_shortcut_creation(*args, success_text="完成")
    controller._start_shortcut_creation(*args, success_text="完成")
    assert entered.wait(timeout=1)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: not release.is_set())
    assert calls == 1
    assert not controller._btn_desktop.isEnabled()

    release.set()
    qtbot.waitUntil(lambda: controller._btn_desktop.isEnabled(), timeout=1000)
    toast.assert_called_once_with("完成")


def test_shortcut_timeout_and_failure_return_false(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "vibeocr.classic.utils.shortcuts.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired("powershell", 15)),
    )
    assert not create_windows_shortcut("app.exe", str(tmp_path / "VibeOCR.lnk"))

    monkeypatch.setattr(
        "vibeocr.classic.utils.shortcuts.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1),
    )
    assert not create_windows_shortcut("app.exe", str(tmp_path / "VibeOCR.lnk"))


def test_settings_cache_refresh_is_responsive_and_single_flight(
    qtbot, tmp_path, monkeypatch
):
    controller = _controller(qtbot, tmp_path)
    button = QPushButton(controller._ui)
    button.setObjectName("btnRefreshCache")
    label = QLabel(controller._ui)
    label.setObjectName("labelCacheStatus")
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_refresh():
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)
        return True, "cache-info"

    monkeypatch.setattr(controller, "_refresh_machine_cache_operation", slow_refresh)
    controller._show_settings_toast = MagicMock()

    controller._on_refresh_cache_clicked()
    controller._on_refresh_cache_clicked()
    assert entered.wait(timeout=1)
    assert_qt_event_loop_responsive(qtbot, in_flight=lambda: not release.is_set())
    assert calls == 1
    assert not button.isEnabled()

    release.set()
    qtbot.waitUntil(button.isEnabled, timeout=1000)
    assert label.text() == "缓存已刷新"
