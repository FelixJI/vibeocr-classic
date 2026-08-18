"""OCR 引擎 / 离线能力 / 下载源设置组的 catalog 驱动行为契约。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QWidget,
)

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.runtime_contracts import SettingsSnapshot


class _FakeRuntimeAdapter(QObject):
    residency_status = Signal(object)
    residency_error = Signal(str)
    settings_updated = Signal(object)
    settings_loaded = Signal(object)
    settings_error = Signal(str)
    health_loaded = Signal(object)
    health_error = Signal(str)
    preload_completed = Signal(object)
    preload_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.is_started = True
        self.update_calls: list[SettingsSnapshot] = []
        self.fetch_health_calls = 0
        self.fetch_settings_calls = 0

    def refresh_residency(self) -> None:
        pass

    def update_settings(self, snapshot: SettingsSnapshot) -> None:
        self.update_calls.append(snapshot)

    def fetch_health(self) -> None:
        self.fetch_health_calls += 1

    def fetch_settings(self) -> None:
        self.fetch_settings_calls += 1


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


def _health_payload(
    *, with_download_sources: bool = True, with_model_registry: bool = False
) -> dict:
    descriptors = [
        {
            "name": "ocr.engine-selection.v1",
            "lifecycle": "active",
            "introduced_in": "2.6.0",
            "deprecated_in": None,
            "sunset_at": None,
            "replacement": None,
            "ocr_engine_catalog": {
                "engines": [
                    {
                        "id": "rapidocr",
                        "availability": "ready",
                        "included_in_base": True,
                        "reason_code": None,
                        "required_component": None,
                    },
                    {
                        "id": "windows",
                        "availability": "ready",
                        "included_in_base": True,
                        "reason_code": None,
                        "required_component": None,
                    },
                    {
                        "id": "paddleocr",
                        "availability": "preparation_required",
                        "included_in_base": False,
                        "reason_code": "component_missing",
                        "required_component": "win-x64-cpu-document-parsing",
                    },
                ]
            },
        },
        {
            "name": "runtime.component-selection.v1",
            "lifecycle": "active",
            "introduced_in": "2.7.0",
            "deprecated_in": None,
            "sunset_at": None,
            "replacement": None,
            "component_variant_catalog": {
                "variants": [
                    {
                        "feature_id": "document_parsing",
                        "accelerator": "cpu",
                        "component_id": "win-x64-cpu-document-parsing",
                    },
                ]
            },
        },
    ]
    capabilities = [
        "ocr.recognition.v2",
        "ocr.engine-selection.v1",
        "runtime.component-selection.v1",
    ]
    if with_download_sources:
        sources = [
            {
                "kind": "package_index",
                "id": "tuna-pypi",
                "endpoint": "https://mirrors.tuna.tsinghua.edu.cn/pypi",
            },
            {
                "kind": "package_index",
                "id": "pypi",
                "endpoint": "https://pypi.org",
            },
        ]
        if with_model_registry:
            # Backend 0.13 起 catalog 声明 model_registry 源（huggingface/
            # modelscope）；设置页必须 catalog 驱动地按 kind 渲染，而不是
            # 预造 UI 选项。
            sources += [
                {
                    "kind": "model_registry",
                    "id": "huggingface",
                    "endpoint": "https://huggingface.co",
                },
                {
                    "kind": "model_registry",
                    "id": "modelscope",
                    "endpoint": "https://www.modelscope.cn",
                },
            ]
        descriptors.append(
            {
                "name": "runtime.download-sources.v1",
                "lifecycle": "active",
                "introduced_in": "2.7.0",
                "deprecated_in": None,
                "sunset_at": None,
                "replacement": None,
                "download_source_catalog": {"sources": sources},
            }
        )
        capabilities.append("runtime.download-sources.v1")
    return {
        "schema_version": 2,
        "instance_id": "i-1",
        "protocol_version": 2,
        "ready": True,
        "draining": False,
        "capabilities": capabilities,
        "capability_descriptors": descriptors,
    }


@pytest.fixture
def selection_controller(qtbot, tmp_path, monkeypatch):
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    config = MagicMock()
    config.get_pipeline_ttls.return_value = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }
    config.get_ocr_engine_selection.return_value = ("rapidocr", False)
    config.set_ocr_engine.return_value = True
    config.get_offline_component_features.return_value = []
    config.set_offline_component_features.return_value = True
    config.get_preload_pipelines.return_value = []
    config.get_preload_enabled.return_value = False
    config_class = MagicMock()
    config_class.instance.return_value = config
    monkeypatch.setattr(
        "vibeocr.classic.managers.config_manager.ConfigManager",
        config_class,
    )

    adapter = _FakeRuntimeAdapter()
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.get_supervisor_adapter",
        lambda: adapter,
    )

    installer_client = MagicMock()
    installer_client.profile_descriptor.return_value = MagicMock(accelerator="cpu")

    manager = _immediate_invalidation_manager()

    with (
        patch(
            "vibeocr.classic.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch(
            "vibeocr.classic.views.settings_page_controller.SettingsPageController."
            "_refresh_env_maintenance_state"
        ),
    ):
        controller = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda _message: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=manager,
            defer_backend_initialization=True,
            defer_machine_cache_status=True,
            runtime_installer_client=installer_client,
        )
        controller.connect_signals()

    yield controller, host, adapter, config, manager
    controller.request_shutdown()


def test_health_catalog_renders_engines_features_and_sources(
    selection_controller,
) -> None:
    controller, host, _adapter, _config, _manager = selection_controller

    controller._on_health_loaded(_health_payload())

    status = host.findChild(QLabel, "labelOcrEngineStatus").text()
    assert "RapidOCR：可用" in status
    assert "PaddleOCR：需准备组件（component_missing）" in status

    tree = host.findChild(QTreeWidget, "treeOfflineFeatures")
    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).text(0).startswith("文档智能解析")
    assert tree.isEnabled()

    combo = host.findChild(QComboBox, "comboDownloadSource_package_index")
    assert combo is not None
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "tuna-pypi",
        "pypi",
    ]
    assert controller._resolve_download_source_ids() == ("tuna-pypi",)


def test_missing_download_capability_disables_source_ui(selection_controller) -> None:
    controller, host, _adapter, _config, _manager = selection_controller

    controller._on_health_loaded(_health_payload(with_download_sources=False))

    assert host.findChild(QComboBox, "comboDownloadSource_package_index") is None
    assert not host.findChild(QPushButton, "btnSaveDownloadSources").isEnabled()
    assert "不支持下载源选择" in host.findChild(QLabel, "labelDownloadSource").text()


def test_save_download_sources_puts_backend_settings_snapshot(
    selection_controller,
) -> None:
    controller, host, adapter, _config, _manager = selection_controller

    controller._on_settings_loaded(SettingsSnapshot(download_source_ids=("pypi",)))
    controller._on_health_loaded(_health_payload())

    combo = host.findChild(QComboBox, "comboDownloadSource_package_index")
    assert combo.currentData() == "pypi"
    combo.setCurrentIndex(0)

    controller._on_save_download_sources()

    assert adapter.update_calls, "应通过 Backend Settings 保存下载源"
    saved = adapter.update_calls[-1]
    assert saved.download_source_ids == ("tuna-pypi",)
    # 2.7.1 序列化契约：空选择省略字段，不能发送 download_source_ids: []
    assert "download_source_ids" not in SettingsSnapshot().to_payload()


def test_install_offline_features_maps_intent_after_confirmation(
    selection_controller,
) -> None:
    controller, host, _adapter, config, _manager = selection_controller

    controller._on_health_loaded(_health_payload())
    tree = host.findChild(QTreeWidget, "treeOfflineFeatures")
    tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)

    captured: dict[str, object] = {}

    def _fake_show_install_dialog(**kwargs) -> None:
        captured.update(kwargs)

    controller._show_install_dialog = _fake_show_install_dialog  # type: ignore[method-assign]

    with patch(
        "vibeocr.classic.views.settings_page_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as question:
        controller._on_install_offline_features()

    assert question.called
    config.set_offline_component_features.assert_called_once_with(
        "cpu", ["document_parsing"]
    )
    assert captured["install_component_ids"] == ("win-x64-cpu-document-parsing",)
    # 安装启动时快照当前 UI 源选择，不受并发设置修改影响
    assert captured["download_source_ids"] == ("tuna-pypi",)


def test_install_offline_features_declined_does_not_install(
    selection_controller,
) -> None:
    controller, host, _adapter, config, _manager = selection_controller

    controller._on_health_loaded(_health_payload())
    tree = host.findChild(QTreeWidget, "treeOfflineFeatures")
    tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)

    started: list[str] = []

    def _fail(**kwargs) -> None:
        started.append("must-not-run")

    controller._show_install_dialog = _fail  # type: ignore[method-assign]

    with patch(
        "vibeocr.classic.views.settings_page_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        controller._on_install_offline_features()

    assert started == []
    config.set_offline_component_features.assert_not_called()


def test_engine_selection_persists_to_config(selection_controller) -> None:
    controller, host, _adapter, config, _manager = selection_controller

    combo = host.findChild(QComboBox, "comboOcrEngine")
    index = next(i for i in range(combo.count()) if combo.itemData(i) == "windows")
    # currentIndexChanged 已连接 _on_engine_selected；无需手动再调
    combo.setCurrentIndex(index)

    config.set_ocr_engine.assert_called_once_with("windows")


def test_repeated_health_reload_does_not_accumulate_source_rows(
    selection_controller,
    qtbot,
) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    controller, host, _adapter, _config, _manager = selection_controller

    for _ in range(3):
        controller._on_health_loaded(_health_payload())
        # deleteLater 的行容器要等 DeferredDelete 派发后才真正销毁
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert len(host.findChildren(QComboBox, "comboDownloadSource_package_index")) == 1
    assert controller._resolve_download_source_ids() == ("tuna-pypi",)


def test_save_download_sources_waits_for_settings_snapshot(
    selection_controller,
) -> None:
    controller, host, adapter, _config, _manager = selection_controller

    controller._on_health_loaded(_health_payload())
    assert controller._runtime_settings_snapshot is None

    controller._on_save_download_sources()

    # 未读到 Backend 现有设置前不得全量 PUT（避免覆盖 residency 策略）
    assert adapter.update_calls == []
    assert adapter.fetch_settings_calls == 1


def test_model_registry_sources_render_per_kind_from_catalog(
    selection_controller,
) -> None:
    """Backend 0.13 的 catalog 声明 model_registry 源：设置页按 kind 渲染
    Hugging Face / ModelScope 单选，选择经 Backend Settings 持久化。"""
    controller, host, adapter, _config, _manager = selection_controller

    controller._on_health_loaded(_health_payload(with_model_registry=True))

    package_combo = host.findChild(QComboBox, "comboDownloadSource_package_index")
    registry_combo = host.findChild(QComboBox, "comboDownloadSource_model_registry")
    assert package_combo is not None and registry_combo is not None
    assert [package_combo.itemText(i) for i in range(package_combo.count())] == [
        "tuna-pypi",
        "pypi",
    ]
    assert [registry_combo.itemText(i) for i in range(registry_combo.count())] == [
        "huggingface",
        "modelscope",
    ]
    # 每 kind 独立选择：model_registry 选中 modelscope 不影响 package_index
    registry_combo.setCurrentIndex(1)
    # Settings 是全量 PUT：先读到现有快照再保存（C6 守卫语义）
    controller._on_settings_loaded(SettingsSnapshot())

    controller._on_save_download_sources()

    assert adapter.update_calls
    saved = adapter.update_calls[-1]
    # 源集合与顺序无关（每 kind 至多一个）
    assert set(saved.download_source_ids) == {"tuna-pypi", "modelscope"}


def test_model_registry_absent_from_catalog_renders_nothing(
    selection_controller,
) -> None:
    """旧版 Backend（未声明 model_registry）不显示伪选项。"""
    controller, host, _adapter, _config, _manager = selection_controller

    controller._on_health_loaded(_health_payload())

    assert host.findChild(QComboBox, "comboDownloadSource_model_registry") is None
    package_combo = host.findChild(QComboBox, "comboDownloadSource_package_index")
    assert package_combo is not None and package_combo.count() == 2
