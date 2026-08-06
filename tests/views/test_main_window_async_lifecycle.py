"""MainWindow 懒加载与两阶段退出的异步边界测试。"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from vibeocr.classic.views.main_window import MainWindow


@pytest.fixture
def main_window(qapp, qtbot, tmp_path, monkeypatch):
    from vibeocr.classic.managers.config_manager import ConfigManager

    ConfigManager.reset_instance()
    ConfigManager.instance(tmp_path)
    monkeypatch.setattr(
        "vibeocr.classic.managers.subprocess_manager.SubprocessManager.start_supervisor",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibeocr.classic.managers.dependency_manager.DependencyManager.check_dependencies",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection",
        lambda self: None,
    )
    window = MainWindow()
    window.show()
    qtbot.addWidget(window)
    yield window
    if isValid(window):
        window._force_quit = True
        if window._shutdown_phase == "idle":
            window._begin_shutdown_requests()
            probes = window._collect_shutdown_gui_probes()
            qtbot.waitUntil(
                lambda: all(bool(probe()) for _name, probe in probes), timeout=7000
            )
            window._shutdown_phase = "ready"
        elif window._shutdown_phase == "draining":
            qtbot.waitUntil(lambda: window._shutdown_phase == "ready", timeout=7000)
        window.close()
    ConfigManager.reset_instance()


def _configure_gui_poll_shutdown(window, monkeypatch, *, settings_is_drained):
    calls: list[tuple[str, QThread]] = []

    def record(name: str) -> None:
        calls.append((name, QThread.currentThread()))

    window._force_quit = True
    window._tray_icon = None
    window._app_settings = SimpleNamespace(minimize_to_tray=False, save=lambda: None)
    window._single_tab = SimpleNamespace(
        set_closing=lambda _value: record("single:request"),
        is_drained=lambda: True,
        _result_widget=SimpleNamespace(cleanup=lambda: None),
    )
    window._settings_controller = SimpleNamespace(
        request_shutdown=lambda: record("settings:request"),
        is_drained=lambda: settings_is_drained(record),
        initialize_deferred_backend_options=lambda: None,
        apply_deferred_machine_cache_status=lambda _valid: None,
    )
    window._pdf_tab = SimpleNamespace(
        request_shutdown=lambda: record("pdf:request"), is_drained=lambda: True
    )
    window._batch_tab = SimpleNamespace(
        request_shutdown=lambda: record("batch:request"),
        shutdown=lambda **_kwargs: record("batch:legacy-request") or True,
        is_drained=lambda: True,
        _result_widget=SimpleNamespace(cleanup=lambda: None),
    )
    window._qrcode_tab = SimpleNamespace(
        set_closing=lambda _value: record("qr:request"), is_drained=lambda: True
    )
    window._subprocess_manager = SimpleNamespace(
        request_shutdown=lambda: record("subprocess:request"),
        is_drained=lambda: True,
        take_shutdown_callable=lambda: record("subprocess:take") or (lambda: None),
        shutdown=lambda _timeout_ms: True,
    )
    window._edge_toolbar = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(window, "_save_layout", lambda: None)
    monkeypatch.setattr("vibeocr.classic.client.shutdown_backend_client", lambda: None)
    return calls


def test_shutdown_request_and_poll_stay_on_gui_owner_thread(
    main_window, qtbot, monkeypatch
):
    polls = 0

    def settings_is_drained(record):
        nonlocal polls
        polls += 1
        record("settings:poll")
        return polls >= 2

    calls = _configure_gui_poll_shutdown(
        main_window, monkeypatch, settings_is_drained=settings_is_drained
    )

    main_window.close()
    qtbot.waitUntil(lambda: main_window._shutdown_phase == "ready", timeout=1000)

    assert polls >= 2
    assert calls
    assert all(thread is main_window.thread() for _name, thread in calls)


def test_shutdown_budget_expiry_keeps_owner_alive_until_native_drain(
    main_window, qtbot, monkeypatch
):
    drained = threading.Event()
    cleanup = MagicMock()
    _configure_gui_poll_shutdown(
        main_window,
        monkeypatch,
        settings_is_drained=lambda _record: drained.is_set(),
    )
    monkeypatch.setattr(main_window, "_cleanup_webengine_widgets", cleanup)
    monkeypatch.setattr(main_window, "_SHUTDOWN_UX_BUDGET_MS", 50, raising=False)

    main_window.close()
    qtbot.wait(150)

    assert main_window._shutdown_phase == "draining"
    assert main_window._shutdown_timed_out is True
    cleanup.assert_not_called()

    drained.set()
    qtbot.waitUntil(lambda: main_window._shutdown_phase == "ready", timeout=1000)
    cleanup.assert_called_once_with()


def test_repeated_close_is_single_flight(main_window, qtbot, monkeypatch):
    drained = threading.Event()
    calls = _configure_gui_poll_shutdown(
        main_window,
        monkeypatch,
        settings_is_drained=lambda _record: drained.is_set(),
    )
    main_window.close()
    qtbot.waitUntil(lambda: main_window._shutdown_phase == "draining")
    request_count = sum(name.endswith(":request") for name, _thread in calls)
    main_window.close()

    assert sum(name.endswith(":request") for name, _thread in calls) == request_count
    drained.set()
    qtbot.waitUntil(lambda: main_window._shutdown_phase == "ready")


def test_shutdown_requests_delayed_startup_update(main_window, qtbot, monkeypatch):
    lifecycle = SimpleNamespace(request_shutdown=MagicMock(), is_drained=lambda: True)
    main_window._startup_update_task = lifecycle
    _configure_gui_poll_shutdown(
        main_window, monkeypatch, settings_is_drained=lambda _record: True
    )

    main_window._begin_shutdown_requests()

    lifecycle.request_shutdown.assert_called_once_with()
    probes = main_window._collect_shutdown_gui_probes()
    qtbot.waitUntil(lambda: all(bool(probe()) for _name, probe in probes), timeout=7000)
    main_window._shutdown_phase = "ready"


def test_closing_discards_late_lazy_prewarm(main_window, qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    builder = MagicMock(return_value=QWidget())
    index = next(iter(main_window._lazy_tab_builders))
    role, _old_builder = main_window._lazy_tab_builders[index]
    main_window._lazy_tab_builders[index] = (role, builder)

    def slow_prewarm(_role):
        entered.set()
        release.wait(2)

    monkeypatch.setattr(main_window, "_prewarm_lazy_tab", slow_prewarm)
    _configure_gui_poll_shutdown(
        main_window, monkeypatch, settings_is_drained=lambda _record: True
    )

    main_window._ui.tabWidget.setCurrentIndex(index)
    qtbot.waitUntil(entered.is_set)
    main_window.close()
    release.set()
    qtbot.waitUntil(lambda: not main_window._lazy_tab_tasks)
    qtbot.waitUntil(lambda: main_window._shutdown_phase == "ready", timeout=2000)

    builder.assert_not_called()


@pytest.mark.parametrize("has_gpu", [False, True])
def test_settings_gpu_callback_updates_main_window_state(qtbot, has_gpu):
    """设置页解析结果经 MainWindow 保存并广播，不读取共享缓存。"""
    from vibeocr.classic.views.settings_page_controller import (
        SettingsPageController,
    )

    host = QWidget()
    qtbot.addWidget(host)
    host._closing = False
    host._runtime_gpu_capability = None
    host._worker_start_pending = False
    host._apply_gpu_gating_to_all = MagicMock()
    controller = SimpleNamespace(
        _closing=False,
        _runtime_has_gpu=None,
        _gpu_capability_callback=lambda resolved: (
            MainWindow._on_gpu_capability_resolved(host, resolved)
        ),
    )

    SettingsPageController._on_gpu_capability_resolved(controller, has_gpu)

    assert controller._runtime_has_gpu is has_gpu
    assert host._runtime_gpu_capability is has_gpu
    host._apply_gpu_gating_to_all.assert_called_once_with(has_gpu)


@pytest.mark.parametrize("has_gpu", [False, True])
def test_lazy_tab_rebroadcasts_resolved_gpu_state(qapp, qtbot, has_gpu):
    """懒加载控件构造后由 MainWindow 重放已解析的 GPU 状态。"""
    from PySide6.QtWidgets import QTabWidget, QVBoxLayout

    from vibeocr.classic.widgets.preprocess_options_widget import (
        PreprocessOptionsWidget,
    )
    from vibeocr.classic.widgets.screenshot_options_widget import (
        ScreenshotOptionsWidget,
    )
    from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

    host = QWidget()
    qtbot.addWidget(host)
    tab_widget = QTabWidget(host)
    tab_widget.addTab(QWidget(), "占位")

    container = QWidget(host)
    layout = QVBoxLayout(container)
    preprocess = PreprocessOptionsWidget(container)
    screenshot = ScreenshotOptionsWidget(container)
    layout.addWidget(preprocess)
    layout.addWidget(screenshot)
    assert preprocess.gpu_capability is None

    index = 0
    generation = 1
    host._lazy_tab_build_scheduled = None
    host._closing = False
    host._lazy_tab_generation = generation
    host._lazy_tab_builders = {index: ("about", lambda: container)}
    host._runtime_gpu_capability = has_gpu
    host._ui = SimpleNamespace(tabWidget=tab_widget)
    host._restore_lazy_tab_layout = lambda _role, _widget: None
    host._apply_gpu_gating_to_all = lambda resolved: (
        MainWindow._apply_gpu_gating_to_all(host, resolved)
    )

    MainWindow._build_lazy_tab_on_gui(host, index, generation)

    assert preprocess.gpu_capability is has_gpu
    assert screenshot._groups[OCRPipeline.PADDLEOCR_VL].box.isEnabled() is has_gpu


def test_restored_heavy_tab_builds_after_first_show_on_gui_thread(
    qapp, qtbot, tmp_path, monkeypatch
):
    from vibeocr.classic.managers.config_manager import ConfigManager

    ConfigManager.reset_instance()
    ConfigManager.instance(tmp_path)
    monkeypatch.setattr(
        "vibeocr.classic.managers.subprocess_manager.SubprocessManager.start_supervisor",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibeocr.classic.managers.layout_manager.LayoutManager.get_tab_index",
        lambda self: 3,
    )
    monkeypatch.setattr(
        MainWindow, "_prewarm_lazy_tab", staticmethod(lambda _role: None)
    )
    observations: list[tuple[bool, object]] = []

    def build_pdf(window):
        observations.append((window.isVisible(), QThread.currentThread()))
        return QWidget()

    monkeypatch.setattr(MainWindow, "_build_pdf_tab", build_pdf)
    window = MainWindow()
    qtbot.addWidget(window)
    assert not observations
    assert window._ui.tabWidget.currentWidget().objectName() == "lazySkeleton_pdf"

    window.show()
    qtbot.waitUntil(lambda: bool(observations))

    assert observations == [(True, window.thread())]
    window._shutdown_phase = "ready"
    window.close()
    ConfigManager.reset_instance()


def test_about_to_quit_cleanup_has_no_thread_wait() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "apps/vibeocr-pyside/src/vibeocr/classic/main.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cleanup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_cleanup_install_workers_on_quit"
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait"
        for node in ast.walk(cleanup)
    )
