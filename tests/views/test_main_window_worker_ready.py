"""Regression coverage for the supervisor-only PySide startup handshake."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vibeocr.classic.views.main_window import MainWindow


class _ReadyWindow:
    """Smallest MainWindow-shaped object needed by the ready callback."""

    def __init__(self) -> None:
        self._closing = False
        self._statusbar = MagicMock()
        self._ensure_ocr_status_callback = MagicMock()
        self._record_supervisor_ready = MagicMock()

    _on_supervisor_ready = MainWindow._on_supervisor_ready
    _on_subprocess_progress = MainWindow._on_subprocess_progress


class _MissingRuntimeWindow:
    def __init__(self) -> None:
        self._closing = False
        self._ocr_ready = False
        self._dependency_check_complete = False
        self._statusbar = MagicMock()
        self._check_pending_sync = MagicMock(return_value=False)
        self._start_install = MagicMock()

    _on_dependency_check_finished = MainWindow._on_dependency_check_finished


class _RapidReadyWindow:
    """Only the readiness fields needed before opening a screenshot overlay."""

    _check_ocr_ready = MainWindow._check_ocr_ready

    def __init__(self) -> None:
        self._dependency_check_complete = True
        self._ocr_ready = True
        self._subprocess_manager = SimpleNamespace(is_ready=True)
        self._start_install = MagicMock()


def test_missing_base_runtime_starts_automatic_base_preparation_after_gui_is_ready() -> (
    None
):
    window = _MissingRuntimeWindow()

    with patch(
        "vibeocr.classic.views.main_window.QTimer.singleShot",
        side_effect=lambda _delay, callback: callback(),
    ) as single_shot:
        window._on_dependency_check_finished(False, ["cpu: not-installed"])

    single_shot.assert_called_once()
    window._start_install.assert_called_once_with()
    window._statusbar.set_result.assert_called_once_with("Runtime 未安装：CPU")


def test_advanced_component_gaps_never_open_a_first_start_dialog() -> None:
    """只要 Base Runtime 可启动，Paddle/MinerU 缺失不是首启故障。"""

    window = _MissingRuntimeWindow()
    window._continue_ready_startup = MagicMock()

    with patch("vibeocr.classic.views.main_window.QTimer.singleShot") as single_shot:
        window._on_dependency_check_finished(True, [])

    single_shot.assert_not_called()
    window._start_install.assert_not_called()
    window._continue_ready_startup.assert_called_once_with()


def test_rapid_screenshot_is_available_after_supervisor_handshake_without_download() -> (
    None
):
    window = _RapidReadyWindow()

    with patch("vibeocr.classic.views.main_window.QMessageBox") as message_box:
        assert window._check_ocr_ready() is True

    message_box.information.assert_not_called()
    message_box.question.assert_not_called()
    window._start_install.assert_not_called()


def test_supervisor_ready_is_not_reported_as_startup_failure() -> None:
    """A started v2 adapter makes the Supervisor handshake ready."""
    window = _ReadyWindow()
    adapter = SimpleNamespace(is_started=True)

    with (
        patch(
            "vibeocr.classic.pyside.supervisor_adapter.get_supervisor_adapter",
            return_value=adapter,
        ),
        patch("vibeocr.classic.views.main_window.QMessageBox.warning") as warning,
        patch("vibeocr.classic.startup_metrics.record_startup"),
    ):
        window._on_supervisor_ready(True)

    warning.assert_not_called()
    window._statusbar.set_service.assert_called_once_with("Supervisor 已连接")
    window._statusbar.set_residency.assert_called_once_with("按需加载 · 尚未确认驻留")
    window._statusbar.clearMessage.assert_called_once_with()
    window._record_supervisor_ready.assert_called_once_with()


def test_t6_smoke_starts_supervisor_without_ocr_dependency_probe() -> None:
    window = _ReadyWindow()
    window._runtime_gpu_capability = None
    window._ocr_ready = False
    window._start_supervisor = MagicMock()

    MainWindow._start_supervisor_self_test(window)

    assert window._runtime_gpu_capability is False
    assert window._ocr_ready is True
    window._start_supervisor.assert_called_once_with()


def test_supervisor_start_does_not_wait_for_gpu_ui_probe() -> None:
    window = _ReadyWindow()
    window._runtime_gpu_capability = None
    window._subprocess_manager = MagicMock()

    MainWindow._start_supervisor(window)

    window._subprocess_manager.start_supervisor.assert_called_once_with()
    window._statusbar.showMessage.assert_called_once_with("Supervisor 启动中")


def test_subprocess_progress_names_process_and_handshake_stage() -> None:
    window = _ReadyWindow()

    window._on_subprocess_progress("正在创建子进程并等待就绪握手")

    window._statusbar.showMessage.assert_called_once_with(
        "正在创建子进程并等待就绪握手"
    )
    window._statusbar.set_service.assert_called_once_with("Supervisor 启动中")


def test_supervisor_failure_does_not_blame_model_download() -> None:
    window = _ReadyWindow()

    with patch("vibeocr.classic.views.main_window.QMessageBox.warning") as warning:
        window._on_supervisor_ready(False)

    window._statusbar.set_service.assert_called_once_with("Supervisor 启动失败")
    window._statusbar.set_residency.assert_called_once_with("不可用")
    window._statusbar.set_result.assert_called_once_with("OCR 暂不可用")
    window._statusbar.clearMessage.assert_called_once_with()
    message = warning.call_args.args[2]
    assert "就绪握手" in message
    assert "当前 Runtime profile 未完成安装或验证" in message
    assert "依赖损坏" not in message
    assert "通常不是模型下载问题" in message


def test_t6_supervisor_failure_exits_instead_of_opening_modal(
    monkeypatch,
) -> None:
    window = _ReadyWindow()
    monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")

    with (
        patch("vibeocr.classic.views.main_window.QMessageBox.warning") as warning,
        patch("vibeocr.classic.startup_metrics.flush_startup") as flush,
        patch("vibeocr.classic.views.main_window.os._exit") as exit_process,
    ):
        window._on_supervisor_ready(False)

    flush.assert_called_once_with()
    exit_process.assert_called_once_with(1)
    warning.assert_not_called()


def test_background_preload_status_does_not_override_active_recognition() -> None:
    window = _ReadyWindow()
    window._single_tab = SimpleNamespace(_is_processing=True)
    window._show_background_runtime_status = (
        MainWindow._show_background_runtime_status.__get__(window)
    )

    window._show_background_runtime_status("模型预加载中 · 已预热 1/2")

    window._statusbar.showMessage.assert_not_called()
    window._statusbar.set_residency.assert_called_once_with("模型预加载中 · 已预热 1/2")

    window._single_tab._is_processing = False
    window._show_background_runtime_status("模型预加载中 · 已预热 2/2")
    window._statusbar.set_residency.assert_called_with("模型预加载中 · 已预热 2/2")
