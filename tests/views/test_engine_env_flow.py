"""融合形态（进程内后端 + uv 引擎环境）的设置页安装入口测试。"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from vibeocr.classic.engine_envs import (
    ENGINE_ENV_SPECS,
    EngineEnvState,
)
from vibeocr.classic.mineru_connection import (
    MINERU_CONNECTION_MODE_LOCAL,
    set_active_mineru_connection_mode,
)
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController


class _ImmediateInvalidationEmitter(QObject):
    invalidation_finished = Signal(bool, str)


def _immediate_invalidation_manager() -> MagicMock:
    manager = MagicMock()
    emitter = _ImmediateInvalidationEmitter()
    manager.invalidation_finished = emitter.invalidation_finished

    def invalidate_supervisor() -> bool:
        emitter.invalidation_finished.emit(True, "")
        return True

    manager.invalidate_supervisor.side_effect = invalidate_supervisor
    return manager


def _state(spec_id: str, installed: bool, tmp_path) -> EngineEnvState:
    root = tmp_path / "envs" / spec_id
    return EngineEnvState(
        spec_id=spec_id,
        installed=installed,
        env_root=root,
        python=root / "Scripts" / "python.exe" if installed else None,
    )


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
            "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection"
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch(
            "vibeocr.classic.managers.config_manager.ConfigManager"
        ) as mock_cm,
        patch(
            "vibeocr.classic.views.settings_page_controller.RuntimeInstallerClient"
        ),
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


def _check_feature(host, feature_id: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidget

    tree = host.findChild(QTreeWidget, "treeOfflineFeatures")
    assert tree is not None
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.data(0, Qt.ItemDataRole.UserRole) == feature_id:
            item.setCheckState(0, Qt.CheckState.Checked)
            return
    # 目录未加载（无后端状态）时动态补一行，验证纯前端翻译逻辑。
    from PySide6.QtWidgets import QTreeWidgetItem

    item = QTreeWidgetItem([feature_id])
    item.setData(0, Qt.ItemDataRole.UserRole, feature_id)
    item.setCheckState(0, Qt.CheckState.Checked)
    tree.addTopLevelItem(item)


def test_install_offline_features_opens_engine_env_dialog(
    fused_controller, monkeypatch
) -> None:
    controller, host, _manager = fused_controller
    _check_feature(host, "paddleocr")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
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

    controller._on_install_offline_features()

    assert opened == [(["paddle-cpu"], [])]


def test_install_offline_features_keeps_gpu_when_gpu_runtime_checked(
    fused_controller, monkeypatch
) -> None:
    controller, host, _manager = fused_controller
    _check_feature(host, "mineru")
    _check_feature(host, "gpu_runtime")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
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

    controller._on_install_offline_features()

    assert opened == [(["mineru-gpu"], [])]


def test_install_offline_features_declined_does_not_open_dialog(
    fused_controller, monkeypatch
) -> None:
    controller, host, _manager = fused_controller
    _check_feature(host, "paddleocr")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    opened: list = []

    class _SignalStub2:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeDialog:
        finished = _SignalStub2()

        def __init__(self, *args, **kwargs):
            opened.append(kwargs)

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeDialog
    )

    controller._on_install_offline_features()
    assert opened == []


def test_backend_change_switches_installed_engine_flavor(
    fused_controller, monkeypatch, tmp_path
) -> None:
    controller, _host, manager = fused_controller
    # 已安装 paddle-cpu；切换到 GPU 应装 paddle-gpu 并移除 paddle-cpu。
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-cpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub3:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeDialog:
        finished = _SignalStub3()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeDialog
    )

    controller._on_backend_change_requested("gpu")

    assert opened == [(["paddle-gpu"], ["paddle-cpu"])]


def test_engine_env_finished_invokes_succeeded_callback(fused_controller) -> None:
    controller, _host, _manager = fused_controller
    calls: list[str] = []
    controller._install_succeeded_callback = lambda: calls.append("ok")  # type: ignore[assignment]
    controller._install_abandoned_callback = lambda: calls.append("abandoned")  # type: ignore[assignment]

    controller._on_engine_env_finished(True)
    controller._on_engine_env_finished(False)

    assert calls == ["ok", "abandoned"]


def test_reinstall_python_in_fused_mode_shows_info(
    fused_controller, monkeypatch
) -> None:
    controller, _host, _manager = fused_controller
    from PySide6.QtWidgets import QMessageBox

    shown: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: shown.append(args) or QMessageBox.StandardButton.Ok,
    )

    controller._on_reinstall_python()

    assert len(shown) == 1
    controller._subprocess_manager.invalidate_supervisor.assert_not_called()


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


def test_device_preference_persisted_and_used_by_install(
    fused_controller, monkeypatch
) -> None:
    """未装引擎时切换设备要持久化；安装流程读取该偏好。"""
    import json as json_module

    from PySide6.QtWidgets import QMessageBox

    controller, host, _manager = fused_controller
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )
    controller._switch_engine_device_fused("gpu")

    data = json_module.loads(
        (controller._project_root / "config" / "engine-device.json").read_text(
            encoding="utf-8"
        )
    )
    assert data["device"] == "gpu"
    assert controller._engine_device_preference() == "gpu"

    # 只勾选 PaddleOCR（不勾 GPU 运行时）也应安装 GPU 版。
    _check_feature(host, "paddleocr")
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
        QMessageBox,
        "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeDialog
    )
    controller._on_install_offline_features()
    assert opened == [(["paddle-gpu"], [])]


def test_empty_selection_with_installed_envs_offers_removal(
    fused_controller, monkeypatch, tmp_path
) -> None:
    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-cpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from PySide6.QtWidgets import QMessageBox

    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub3:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeDialog:
        finished = _SignalStub3()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeDialog
    )

    # 不勾选任何能力：确认后应弹出“移除全部”计划。
    controller._on_install_offline_features()

    assert opened == [([], ["paddle-cpu"])]


def test_env_maintenance_state_fused_skips_runtime_installer(
    fused_controller, qtbot
) -> None:
    """融合形态：环境维护状态来自引擎环境检查，不调用 Runtime Installer。"""
    from PySide6.QtWidgets import QLabel, QPushButton, QTreeWidget

    controller, host, _manager = fused_controller
    label = host.findChild(QLabel, "labelEnvStatus")
    assert label is not None

    def _refreshed() -> bool:
        return "基础识别已内置" in label.text()

    qtbot.waitUntil(_refreshed, timeout=5000)
    # connect_signals 阶段的后台刷新不得触碰 Installer。
    controller._runtime_installer.inspect.assert_not_called()

    status_tree = host.findChild(QTreeWidget, "treeRuntimeStatus")
    assert status_tree is not None and status_tree.topLevelItemCount() >= 4
    deps_tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert deps_tree is not None
    assert deps_tree.topLevelItemCount() == 1 + len(ENGINE_ENV_SPECS)
    for button_name in ("btnReinstallPython", "btnReinstallDeps", "btnUpdateDeps"):
        button = host.findChild(QPushButton, button_name)
        assert button is not None and button.isEnabled(), button_name


def test_refresh_machine_cache_operation_fused(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """融合形态的“验证 Runtime 状态”：内置后端 + 引擎环境摘要。"""
    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "mineru-gpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    success, summary = controller._refresh_machine_cache_operation()

    assert success is True
    assert "基础识别已内置" in summary
    assert "GPU" in summary
    assert "MinerU 文档解析（GPU 版）" in summary
    controller._runtime_installer.inspect.assert_not_called()


def test_init_ocr_runtime_group_fused_sets_accelerator_from_envs(
    fused_controller, tmp_path
) -> None:
    """融合形态：可选能力目录的加速方案来自已装引擎环境，而非 manifest。"""
    controller, _host, manager = fused_controller
    # fixture 构造时四个环境均未安装 → 缺省 cpu。
    assert controller._selection_accelerator == "cpu"

    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-gpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    controller._init_ocr_runtime_group()

    assert controller._selection_accelerator == "nvidia_cuda"
    controller._runtime_installer.profile_descriptor.assert_not_called()


def test_env_refresh_recomputes_selection_accelerator(
    fused_controller, qtbot, tmp_path
) -> None:
    """引擎安装/设备切换后的环境刷新必须重算能力目录的 accelerator。

    旧实现只在设置页初始化时计算一次：基础态安装 GPU 引擎后，能力树
    仍按 CPU accelerator 渲染，GPU 能力显示为未勾选，空选择还会被解释
    为移除已安装环境。
    """

    controller, host, manager = fused_controller
    assert controller._selection_accelerator == "cpu"

    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-gpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    controller._refresh_env_maintenance_state()

    qtbot.waitUntil(
        lambda: controller._selection_accelerator == "nvidia_cuda",
        timeout=5000,
    )
    # 完整投影：未装组件是明确的 missing，GPU 引擎就绪。
    states = controller._runtime_component_states
    assert states["paddleocr-cuda"] == "ready"
    assert states["paddleocr-cpu"] == "missing"
    assert states["mineru-cpu"] == "missing"
    assert states["mineru-cuda"] == "missing"
    # Paddle GPU 清单不含 torch：gpu_runtime 不因 paddle-gpu 就绪。
    assert states["gpu_runtime"] == "missing"


def test_env_refresh_projects_gpu_runtime_from_mineru_gpu(
    fused_controller, qtbot, tmp_path
) -> None:
    """mineru-gpu 闭包含 torch/CUDA：其安装态必须把 gpu_runtime 标为就绪。"""

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "mineru-gpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }
    controller._refresh_env_maintenance_state()

    qtbot.waitUntil(
        lambda: controller._runtime_component_states.get("gpu_runtime") == "ready",
        timeout=5000,
    )
    assert controller._runtime_component_states["mineru-cuda"] == "ready"
    assert controller._runtime_component_states["paddleocr-cpu"] == "missing"
    assert controller._selection_accelerator == "nvidia_cuda"


def test_switch_engine_device_migrates_feature_selection(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """设备切换后能力勾选迁移到目标 accelerator。

    ConfigManager 按 accelerator 分键保存勾选：切换已装 CPU 引擎到 GPU
    时若不迁移，能力树按 nvidia_cuda 渲染后全为未勾选，空选择会被
    安装入口解释为移除全部引擎。
    """

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id in {"paddle-cpu", "mineru-cpu"}, tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.get_offline_component_features.return_value = ["paddleocr"]
    instance.set_offline_component_features.reset_mock()

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub4:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeSwitchDialog:
        finished = _SignalStub4()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeSwitchDialog
    )

    controller._switch_engine_device_fused("gpu")

    # 已装家族（paddle+mineru）∪ 源 accelerator 意图（paddleocr）；
    # gpu_runtime 不可独立勾选（融合能力树已隐藏），不迁移。
    instance.set_offline_component_features.assert_called_once_with(
        "nvidia_cuda", ["mineru", "paddleocr"]
    )
    assert opened == [(["paddle-gpu", "mineru-gpu"], ["mineru-cpu", "paddle-cpu"])]


def test_switch_engine_device_to_cpu_drops_gpu_runtime(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """GPU→CPU 切换：迁移保留家族意图，丢弃 CPU 目录不存在的 gpu_runtime。"""

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id in {"paddle-gpu", "mineru-gpu"}, tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.get_offline_component_features.return_value = [
        "paddleocr",
        "mineru",
        "gpu_runtime",
    ]
    instance.set_offline_component_features.reset_mock()

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )

    class _SignalStub5:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeSwitchDialog2:
        finished = _SignalStub5()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            pass

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeSwitchDialog2
    )

    controller._switch_engine_device_fused("cpu")

    instance.set_offline_component_features.assert_called_once_with(
        "cpu", ["mineru", "paddleocr"]
    )


def test_render_offline_features_hides_gpu_runtime_in_fused_mode(
    fused_controller,
) -> None:
    """融合能力树不展示 gpu_runtime：它只是 mineru-gpu 的闭包组件。

    若仍可勾选，仅勾它会得到空安装集（describe_selection 无对应目标），
    已装引擎会被全部解释为待移除；勾选也无法真正安装该运行时。
    """

    controller, host, _manager = fused_controller
    from vibeocr.classic.runtime_selection import (
        ComponentVariantEntry,
        RuntimeSelectionCatalog,
    )

    controller._selection_catalog = RuntimeSelectionCatalog(
        variants=(
            ComponentVariantEntry("paddleocr", "cpu", "paddleocr-cpu"),
            ComponentVariantEntry("mineru", "cpu", "mineru-cpu"),
            ComponentVariantEntry("paddleocr", "nvidia_cuda", "paddleocr-cuda"),
            ComponentVariantEntry("mineru", "nvidia_cuda", "mineru-cuda"),
            ComponentVariantEntry("gpu_runtime", "nvidia_cuda", "gpu_runtime"),
        )
    )
    controller._selection_accelerator = "nvidia_cuda"
    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.get_offline_component_features.return_value = ["mineru"]

    controller._render_offline_features()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidget

    tree = host.findChild(QTreeWidget, "treeOfflineFeatures")
    assert tree is not None
    rows = {
        tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole): (
            tree.topLevelItem(index).checkState(0)
        )
        for index in range(tree.topLevelItemCount())
    }
    assert set(rows) == {"paddleocr", "mineru"}
    assert rows["mineru"] == Qt.CheckState.Checked
    assert rows["paddleocr"] == Qt.CheckState.Unchecked


def test_switch_engine_device_aborts_when_config_write_fails(
    fused_controller, monkeypatch, tmp_path
) -> None:
    """配置写入失败（返回 False）时中止设备切换并告知用户。

    若忽略结果继续安装，切换后的能力树会读到目标 accelerator 的空
    选择，把已装引擎显示为未勾选，下次安装入口把空选择解释为全部移除。
    """

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = lambda: {
        spec.id: _state(spec.id, spec.id == "paddle-cpu", tmp_path)
        for spec in ENGINE_ENV_SPECS
    }

    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.get_offline_component_features.return_value = []
    instance.set_offline_component_features.return_value = False

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    warnings: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **kw: warnings.append(a)
    )
    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub6:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeAbortDialog:
        finished = _SignalStub6()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeAbortDialog
    )

    controller._switch_engine_device_fused("gpu")

    assert opened == [], "配置写入失败必须中止切换，不得弹出安装对话框"
    assert warnings, "应向用户报告保存失败"


def test_install_offline_features_aborts_when_config_write_fails(
    fused_controller, monkeypatch
) -> None:
    """配置写入失败时中止引擎安装并告知用户（与切换路径同模式）。"""

    controller, host, _manager = fused_controller
    _check_feature(host, "paddleocr")

    from vibeocr.classic.managers import config_manager as cm_module

    instance = cm_module.ConfigManager.instance.return_value
    instance.set_offline_component_features.return_value = False

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    warnings: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **kw: warnings.append(a)
    )
    opened: list[tuple[list[str], list[str]]] = []

    class _SignalStub7:
        def connect(self, *_args, **_kwargs):
            pass

    class FakeAbortDialog2:
        finished = _SignalStub7()

        def __init__(self, m, install_ids, remove_ids, **kwargs):
            opened.append((list(install_ids), list(remove_ids)))

        def show(self):
            pass

    monkeypatch.setattr(
        "vibeocr.classic.widgets.engine_env_dialog.EngineEnvDialog", FakeAbortDialog2
    )

    controller._on_install_offline_features()

    assert opened == [], "配置写入失败必须中止安装"
    assert warnings, "应向用户报告保存失败"


def test_init_ocr_runtime_group_survives_inspect_failure(
    fused_controller,
) -> None:
    """初始化路径的引擎环境检查失败不得中断设置页构造。

    _init_ocr_runtime_group 在 GUI 构造链里同步调用 inspect()；随包
    wheel/清单不可读时应按缺省 cpu 渲染，错误由环境状态区异步呈现。
    """

    controller, _host, manager = fused_controller
    manager.inspect.side_effect = RuntimeError("resources unreadable")

    controller._init_ocr_runtime_group()

    assert controller._selection_accelerator == "cpu"
