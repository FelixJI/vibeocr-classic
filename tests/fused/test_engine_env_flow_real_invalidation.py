"""真实失效链路回归：运行环境变更 → 停止 Supervisor → EngineEnvDialog 弹出。

``tests/views/test_engine_env_flow.py`` 把 SubprocessManager 与
EngineEnvDialog 全部 mock，未覆盖真实链路：进程内后端运行中触发
``invalidate_supervisor``，invalidation 完成后必须构造真实
EngineEnvDialog 并显示。构造路径曾在实机崩溃（QTextEdit 误用
``setMaximumBlockCount``），且任何 invalidation 挂起都会让状态栏
停在“正在停止 OCR Supervisor...”且无任何后续反应。
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from vibeocr.classic.engine_envs import EngineEnvManager
from vibeocr.classic.managers.subprocess_manager import SubprocessManager
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController

_READY_TIMEOUT_S = 180
_DIALOG_TIMEOUT_S = 60


def _process_events_until(qapp, predicate, timeout_s: float, statuses: list[str]):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def real_invalidation_controller(qapp, qtbot, tmp_path, monkeypatch):
    """真实 SubprocessManager（进程内后端）+ 真实 EngineEnvManager。"""
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )

    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    statuses: list[str] = []
    manager = SubprocessManager(tmp_path)
    engine_manager = EngineEnvManager(tmp_path / "envs")

    with (
        patch(
            "vibeocr.classic.widgets.runtime_environment_widget."
            "RuntimeEnvironmentWidget._start_gpu_detection"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.classic.managers.config_manager.ConfigManager") as mock_cm,
        patch("vibeocr.classic.views.settings_page_controller.RuntimeInstallerClient"),
    ):
        mock_cm.instance.return_value = MagicMock(
            get_pipeline_ttls=MagicMock(return_value={}),
        )
        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=statuses.append,
            ocr_ready_callback=lambda: True,
            subprocess_manager=manager,
        )
        ctrl._engine_env_manager = lambda: engine_manager  # type: ignore[method-assign]
        ctrl.connect_signals()
        yield ctrl, manager, statuses

    dialog = getattr(ctrl, "_engine_env_dialog", None)
    if dialog is not None:
        dialog.close()
        qapp.processEvents()
    manager.shutdown(timeout_ms=5000)
    # ExternalShutdownJob 以 deleteLater 回收，线程收尾与 GUI 事件循环
    # 存在微小竞态；排空事件并稍等，避免 teardown 时的 QThread 警告噪音。
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.05)


def test_engine_env_dialog_opens_after_real_invalidation(
    real_invalidation_controller, qapp
):
    ctrl, manager, statuses = real_invalidation_controller

    manager.start_supervisor()
    assert _process_events_until(
        qapp, lambda: manager.is_ready, _READY_TIMEOUT_S, statuses
    ), f"Supervisor 未就绪；状态消息：{statuses}"

    ctrl._on_runtime_change_requested("cpu", ["paddle"])

    assert _process_events_until(
        qapp,
        lambda: getattr(ctrl, "_engine_env_dialog", None) is not None,
        _DIALOG_TIMEOUT_S,
        statuses,
    ), (
        "停止 Supervisor 后 EngineEnvDialog 未出现（invalidation 挂起或 continuation "
        f"未执行）；状态消息：{statuses}"
    )
    assert not manager.is_invalidating
