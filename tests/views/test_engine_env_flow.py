"""融合形态（进程内后端 + uv 引擎环境）设置流回归测试

设置页「运行环境」选择组件发出应用请求（设备 + 引擎勾选）后，
SettingsPageController 翻译为引擎环境组合并弹 EngineEnvDialog：
安装所选、移除未选、设备切换整体换 CPU/GPU 版本；未装引擎时仅
持久化设备偏好。下载来源在融合形态下本地渲染与保存。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from vibeocr.classic.engine_envs import ENGINE_ENV_SPECS, EngineEnvState
from vibeocr.classic.mineru_connection import (
    MINERU_CONNECTION_MODE_LOCAL,
    set_active_mineru_connection_mode,
)
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController


def _state(spec_id: str, installed: bool, root: Path) -> EngineEnvState:
    return EngineEnvState(
        spec_id=spec_id,
        installed=installed,
        env_root=root,
        python=root / "Scripts" / "python.exe" if installed else None,
    )


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
    manager.acquire_maintenance.side_effect = (
        lambda on_granted, *, is_active: on_granted()
    )
    return manager


@pytest.fixture
def fused_controller(qtbot, tmp_path, monkeypatch):
    """默认（进程内）形态的控制器；引擎环境管理器全部 mock。"""
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

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
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=_immediate_invalidation_manager(),
        )
        engine_manager = MagicMock()

        def _inspect():
            return {
                spec.id: _state(spec.id, False, tmp_path) for spec in ENGINE_ENV_SPECS
            }

        engine_manager.inspect.side_effect = _inspect
        ctrl._engine_env_manager = lambda: engine_manager  # type: ignore[method-assign]
        ctrl.connect_signals()
        yield ctrl, host, engine_manager
    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)


def _install_engine_env_dialog(monkeypatch) -> list[tuple[list[str], list[str]]]:
    """替换 EngineEnvDialog 为记录 (install_ids, remove_ids) 的假对话框。"""
    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeDialog:
        finished = _SignalStub()

        def __init__(self, manager, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeDialog
    )
    return opened


def _auto_confirm(monkeypatch, answer) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: answer)


def test_apply_installs_selected_engine_cpu(fused_controller, monkeypatch) -> None:
    controller, _host, _manager = fused_controller
    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("cpu", ["paddle"])

    assert opened == [(["paddle-cpu"], [])]


def _yes():
    from PySide6.QtWidgets import QMessageBox

    return QMessageBox.StandardButton.Yes


def _no():
    from PySide6.QtWidgets import QMessageBox

    return QMessageBox.StandardButton.No


def test_apply_installs_gpu_flavor_for_gpu_device(
    fused_controller, monkeypatch
) -> None:
    """设备由应用请求显式给出：勾选 MinerU + GPU 应装 mineru-gpu。"""
    controller, _host, _manager = fused_controller
    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("gpu", ["mineru"])

    assert opened == [(["mineru-gpu"], [])]


def test_apply_declined_does_not_open_dialog(fused_controller, monkeypatch) -> None:
    controller, _host, _manager = fused_controller
    _auto_confirm(monkeypatch, _no())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("cpu", ["paddle"])

    assert opened == []
    assert controller._backend_options._change_in_progress is False


def test_apply_switches_installed_engine_flavor(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """已安装 paddle-cpu 时切 GPU：装 paddle-gpu 并移除 paddle-cpu。"""
    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-cpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("gpu", ["paddle"])

    assert opened == [(["paddle-gpu"], ["paddle-cpu"])]


def test_engine_env_finish_refreshes_widget_state(fused_controller) -> None:
    """引擎环境对话框结束后必须重新探测，而不是用旧状态解除进行中标记。"""
    from unittest.mock import MagicMock

    controller, _host, _manager = fused_controller
    options = MagicMock()
    controller._backend_options = options

    controller._on_engine_env_finished(True)

    options.refresh_runtime_state.assert_called_once_with()
    options.set_change_in_progress.assert_not_called()


def test_fused_apply_survives_preference_write_failure(
    fused_controller, monkeypatch
) -> None:
    """设备偏好写入失败只记日志：应用目标由本次请求显式给出，不阻断安装。"""

    controller, _host, _manager = fused_controller

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("vibeocr.classic.json_storage.write_json_atomic", _boom)
    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("cpu", ["paddle"])

    assert opened == [(["paddle-cpu"], [])]


def test_fused_apply_inspect_failure_recovers_widget(
    fused_controller, monkeypatch
) -> None:
    """应用阶段引擎环境检查抛错：提示用户并恢复控件，异常不逸出槽外。"""
    from PySide6.QtWidgets import QMessageBox

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = RuntimeError("resources unreadable")

    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: warnings.append(a))
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("cpu", ["paddle"])

    assert opened == []
    assert len(warnings) == 1
    assert controller._backend_options._change_in_progress is False


def test_engine_env_finished_invokes_succeeded_callback(fused_controller) -> None:
    controller, _host, _manager = fused_controller
    calls: list[str] = []
    controller._install_succeeded_callback = lambda: calls.append("ok")  # type: ignore[assignment]
    controller._install_abandoned_callback = lambda: calls.append("abandoned")  # type: ignore[assignment]

    controller._on_engine_env_finished(True)
    controller._on_engine_env_finished(False)

    assert calls == ["ok", "abandoned"]


def test_download_sources_render_and_save_fused(fused_controller) -> None:
    """融合形态：下载来源组本地渲染、保存到本地存储并投影模型源环境变量。"""
    import json as json_module
    import os

    from PySide6.QtWidgets import QComboBox, QLabel

    controller, host, _manager = fused_controller
    # connect_signals -> _init_settings_page 已按融合形态渲染下载来源组。

    package_combo = host.findChild(QComboBox, "comboDownloadSource_package_index")
    model_combo = host.findChild(QComboBox, "comboDownloadSource_model_registry")
    assert package_combo is not None and model_combo is not None
    # 默认值与存储默认一致。
    assert package_combo.currentData() == "tuna"
    assert model_combo.currentData() == "huggingface"

    package_combo.setCurrentIndex(package_combo.findData("pypi"))
    model_combo.setCurrentIndex(model_combo.findData("modelscope"))

    saved = controller._save_download_sources_fused()
    assert saved is True

    store_path = controller._project_root / "config" / "download-sources.json"
    data = json_module.loads(store_path.read_text(encoding="utf-8"))
    assert data["package_index_id"] == "pypi"
    assert data["model_source_id"] == "modelscope"
    assert os.environ["MINERU_MODEL_SOURCE"] == "modelscope"

    status = host.findChild(QLabel, "labelDownloadSourceStatus")
    assert status is not None and "已保存" in status.text()


def test_on_save_download_sources_dispatches_to_fused(fused_controller) -> None:
    controller, _host, _manager = fused_controller
    calls: list[bool] = []
    controller._save_download_sources_fused = (  # type: ignore[method-assign]
        lambda: calls.append(True) or True
    )

    controller._on_save_download_sources()

    assert calls == [True]


def test_device_preference_persisted_when_no_engines(
    fused_controller, monkeypatch
) -> None:
    """未装引擎时应用设备选择：持久化偏好并提示不会产生下载。"""
    import json as json_module

    from PySide6.QtWidgets import QMessageBox

    controller, _host, _manager = fused_controller
    infos: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: infos.append(a))
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("gpu", [])

    data = json_module.loads(
        (controller._project_root / "config" / "engine-device.json").read_text(
            encoding="utf-8"
        )
    )
    assert data["device"] == "gpu"
    assert opened == []
    assert len(infos) == 1
    assert controller._backend_options._change_in_progress is False


def test_empty_engine_selection_removes_installed_envs(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """取消勾选全部引擎是明确的移除意图：确认后移除并释放磁盘。"""
    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-cpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("cpu", [])

    assert opened == [([], ["paddle-cpu"])]


def test_apply_persists_feature_intent_for_target_accelerator(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """应用后按目标 accelerator 持久化能力意图（feature id 词汇）。"""

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id in {"paddle-cpu", "mineru-cpu"}, tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.set_offline_component_features.reset_mock()

    _auto_confirm(monkeypatch, _yes())
    opened = _install_engine_env_dialog(monkeypatch)

    controller._on_runtime_change_requested("gpu", ["paddle", "mineru"])

    instance.set_offline_component_features.assert_called_once_with(
        "nvidia_cuda", ["mineru", "paddleocr"]
    )
    assert opened == [(["paddle-gpu", "mineru-gpu"], ["mineru-cpu", "paddle-cpu"])]


def test_apply_noop_when_already_at_target(fused_controller, monkeypatch) -> None:
    """已处于目标形态时不弹对话框、不锁组件。"""
    from PySide6.QtWidgets import QMessageBox

    controller, _host, _manager = fused_controller
    opened = _install_engine_env_dialog(monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **kw: QMessageBox.StandardButton.Ok
    )

    # 全部未装且未勾选：目标即现状。
    controller._on_runtime_change_requested("cpu", [])

    assert opened == []
    assert controller._backend_options._change_in_progress is False


def test_refresh_machine_cache_operation_fused(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """融合形态的“验证 Runtime 状态”：内置后端 + 基础引擎 + 引擎环境摘要。"""
    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "mineru-gpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    # 单测不真载 ONNX 模型；真实探针由 frozen smoke 覆盖。
    engine_probes: list[int] = []
    monkeypatch.setattr(
        "vibeocr.classic.backend_host.verify_base_recognition_engine",
        lambda: engine_probes.append(1),
    )

    success, summary = controller._refresh_machine_cache_operation()

    assert success is True
    assert engine_probes == [1], "必须真实调用基础识别引擎探针后才报已就绪"
    assert "基础识别已内置" in summary
    assert "GPU" in summary
    assert "MinerU 文档解析（GPU 版）" in summary
    controller._runtime_installer.inspect.assert_not_called()


def test_refresh_machine_cache_operation_fused_reports_engine_failure(
    fused_controller, monkeypatch
) -> None:
    """基础识别引擎初始化失败时不得诊断为已就绪。

    本 PR 修复的故障（rapidocr 包数据缺失/onnxruntime 加载失败）只有
    真实初始化才能暴露；仅验证组合根可导入会把损坏闭包报告为已就绪。
    """

    controller, _host, _manager = fused_controller

    def _broken_probe() -> None:
        raise RuntimeError("rapidocr default_models.yaml missing")

    monkeypatch.setattr(
        "vibeocr.classic.backend_host.verify_base_recognition_engine",
        _broken_probe,
    )

    success, summary = controller._refresh_machine_cache_operation()

    assert success is False
    assert "基础识别引擎初始化失败" in summary
    assert "default_models.yaml" in summary
