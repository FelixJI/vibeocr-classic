"""Settings runtime controls use only the supervisor v2 adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox, QPushButton, QWidget

from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.runtime_contracts import (
    EvictionReason,
    PipelineSpec,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
    SettingsSnapshot,
)


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
        self.refresh_calls = 0
        self.release_calls: list[str | None] = []
        self.update_calls: list[SettingsSnapshot] = []
        self.preload_calls: list[tuple[str, ...]] = []
        self.fetch_health_calls = 0
        self.fetch_settings_calls = 0

    def refresh_residency(self) -> None:
        self.refresh_calls += 1

    def release_idle(self, pipeline: str | None = None) -> None:
        self.release_calls.append(pipeline)

    def update_settings(self, snapshot: SettingsSnapshot) -> None:
        self.update_calls.append(snapshot)

    def preload(self, pipelines: tuple[str, ...]) -> None:
        self.preload_calls.append(pipelines)

    def fetch_health(self) -> None:
        self.fetch_health_calls += 1

    def fetch_settings(self) -> None:
        self.fetch_settings_calls += 1


class _LifecycleOnlyManager:
    @property
    def service(self):
        raise AssertionError("Settings must not access subprocess_manager.service")


@pytest.fixture
def runtime_controller(qtbot, tmp_path, monkeypatch):
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    ttls = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }
    config = MagicMock()
    config.get_pipeline_ttls.side_effect = lambda: dict(ttls)
    config.get_preload_pipelines.return_value = ["PP-StructureV3"]
    config.get_preload_enabled.return_value = True
    config.set_preload_enabled.return_value = True
    config.set_preload_pipelines.return_value = True

    def set_ttl(name: str, value: int) -> bool:
        ttls[name] = value
        return True

    config.set_pipeline_ttl.side_effect = set_ttl
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
        runtime_status = MagicMock()
        controller = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda _message: None,
            runtime_status_callback=runtime_status,
            ocr_ready_callback=lambda: True,
            subprocess_manager=_LifecycleOnlyManager(),
            defer_backend_initialization=True,
            defer_machine_cache_status=True,
        )
        controller.connect_signals()

    yield controller, host, adapter, ttls
    controller.request_shutdown()


def _initial_status() -> ResidencyStatus:
    return ResidencyStatus(
        default_ttl_seconds=900,
        pipelines=(
            PipelineSpec(name="OCR", ttl_seconds=600, pinned=True),
            PipelineSpec(name="PP-StructureV3", ttl_seconds=300),
        ),
        entries=(
            ResidencyEntry(
                pipeline="OCR",
                kind=ResidencyKind.PINNED,
                active_leases=1,
                remaining_ttl_seconds=480,
                estimated_vram_mb=512,
                eviction_reason=EvictionReason.NONE,
            ),
        ),
        vram_used_mb=512,
        vram_total_mb=8192,
    )


def _paddle_status() -> ResidencyStatus:
    """只包含可管理 Paddle 模式的驻留快照。"""

    return ResidencyStatus(
        default_ttl_seconds=900,
        pipelines=(
            PipelineSpec(name="PP-StructureV3", ttl_seconds=300),
            PipelineSpec(name="TABLE_RECOGNITION", ttl_seconds=300),
        ),
        entries=(
            ResidencyEntry(
                pipeline="PP-StructureV3",
                kind=ResidencyKind.PINNED,
                active_leases=1,
                remaining_ttl_seconds=480,
                estimated_vram_mb=512,
                eviction_reason=EvictionReason.NONE,
            ),
        ),
    )


def test_refresh_and_typed_residency_rendering(runtime_controller) -> None:
    _controller, host, adapter, _ttls = runtime_controller
    assert adapter.refresh_calls == 1

    adapter.residency_status.emit(_initial_status())

    text = host.findChild(QLabel, "labelPipelineCacheStatus").text()
    assert "驻留 1 个" in text
    assert "OCR" in text
    assert "已固定" in text
    assert "活动租约 1" in text
    assert "剩余 TTL 480 秒" in text
    assert "显存 512/8192 MB" in text

    host.findChild(QPushButton, "btnRefreshPipelineCache").click()
    assert adapter.refresh_calls == 2


def test_evicted_entries_are_not_reported_as_resident(runtime_controller) -> None:
    _controller, host, adapter, _ttls = runtime_controller
    status = _initial_status()
    adapter.residency_status.emit(
        ResidencyStatus(
            default_ttl_seconds=status.default_ttl_seconds,
            pipelines=status.pipelines,
            entries=(
                *status.entries,
                ResidencyEntry(
                    pipeline="MinerU",
                    kind=ResidencyKind.EVICTED,
                    active_leases=0,
                    eviction_reason=EvictionReason.EXPLICIT_RELEASE,
                ),
            ),
        )
    )

    text = host.findChild(QLabel, "labelPipelineCacheStatus").text()
    assert "驻留 1 个" in text
    assert "MinerU（" not in text


def test_paddle_ttl_debounce_preserves_unmanaged_ocr_policy(
    runtime_controller, qtbot
) -> None:
    _controller, host, adapter, _ttls = runtime_controller
    adapter.residency_status.emit(_initial_status())
    adapter.update_calls.clear()

    combo = host.findChild(QWidget, "comboTtl_PP-StructureV3")
    combo.setCurrentIndex(combo.findData(60))
    combo.setCurrentIndex(combo.findData(180))

    qtbot.waitUntil(lambda: len(adapter.update_calls) == 1, timeout=1500)
    snapshot = adapter.update_calls[0]
    assert snapshot.default_ttl_seconds == 900
    assert len(snapshot.pipelines) == 2
    policies = {spec.name: spec for spec in snapshot.pipelines}
    assert policies["PP-StructureV3"].ttl_seconds == 180
    assert policies["PP-StructureV3"].pinned is False
    assert policies["OCR"].ttl_seconds == 600
    assert policies["OCR"].pinned is True

    adapter.settings_updated.emit(snapshot)
    assert adapter.refresh_calls == 2


def test_release_all_uses_release_idle_and_reenables_on_status(
    runtime_controller, monkeypatch
) -> None:
    _controller, host, adapter, _ttls = runtime_controller
    monkeypatch.setattr(
        "vibeocr.classic.views.settings_page_controller.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    button = host.findChild(QPushButton, "btnReleaseAll")
    button.click()

    assert adapter.release_calls == [None]
    assert not button.isEnabled()

    adapter.residency_status.emit(ResidencyStatus(default_ttl_seconds=300))
    assert button.isEnabled()
    assert (
        "已完成闲置模型释放"
        in host.findChild(QLabel, "labelPipelineCacheStatus").text()
    )


def test_typed_errors_update_feedback(runtime_controller) -> None:
    _controller, host, adapter, _ttls = runtime_controller

    adapter.settings_error.emit("invalid settings")
    assert (
        host.findChild(QLabel, "labelReleaseStatus").text()
        == "TTL 更新失败：invalid settings"
    )

    adapter.residency_error.emit("backend unavailable")
    assert (
        "backend unavailable"
        in host.findChild(QLabel, "labelPipelineCacheStatus").text()
    )


def test_paddle_persistent_residency_option_sends_pinned_policy(
    runtime_controller, qtbot
) -> None:
    _controller, host, adapter, ttls = runtime_controller
    adapter.residency_status.emit(_initial_status())
    adapter.update_calls.clear()

    combo = host.findChild(QWidget, "comboTtl_PP-StructureV3")
    persistent_index = combo.findText("持久驻留")
    assert persistent_index >= 0
    combo.setCurrentIndex(persistent_index)

    qtbot.waitUntil(lambda: len(adapter.update_calls) == 1, timeout=1500)
    snapshot = adapter.update_calls[0]
    policy = next(spec for spec in snapshot.pipelines if spec.name == "PP-StructureV3")
    assert policy.ttl_seconds is None
    assert policy.pinned is True
    assert ttls["PP-StructureV3"] == -1


def test_lifecycle_controls_exclude_ocr_and_mineru_pinning(runtime_controller) -> None:
    _controller, host, _adapter, _ttls = runtime_controller

    assert host.findChild(QWidget, "comboTtl_OCR") is None
    assert host.findChild(QCheckBox, "chkPreload_OCR") is None
    assert host.findChild(QCheckBox, "chkPreload_DOCUMENT_PARSING") is None

    paddle_combo = host.findChild(QWidget, "comboTtl_PP-StructureV3")
    mineru_combo = host.findChild(QWidget, "comboTtl_MinerU")
    assert paddle_combo is not None
    assert paddle_combo.findText("持久驻留") >= 0
    assert mineru_combo is not None
    assert mineru_combo.findText("持久驻留") < 0


def test_preload_selected_pipelines_uses_supervisor_adapter(
    runtime_controller, qtbot
) -> None:
    controller, host, adapter, _ttls = runtime_controller
    runtime_status = controller._runtime_status_callback
    controller._preload_poll_timer.setInterval(10)
    host.findChild(QCheckBox, "chkPreload_PP_STRUCTURE_V3").setChecked(True)
    host.findChild(QCheckBox, "chkPreload_TABLE_RECOGNITION").setChecked(True)
    preload = host.findChild(QPushButton, "btnPreloadNow")
    refresh_calls_before_preload = adapter.refresh_calls
    preload.click()

    assert adapter.preload_calls == [("PP-StructureV3", "TABLE_RECOGNITION")]
    assert not preload.isEnabled()
    runtime_status.assert_called_with(
        "预加载中 · 0/2 驻留 · PP-StructureV3、TABLE_RECOGNITION"
    )
    qtbot.waitUntil(
        lambda: adapter.refresh_calls >= refresh_calls_before_preload + 2,
        timeout=500,
    )

    adapter.residency_status.emit(_paddle_status())
    assert "已驻留 1/2" in host.findChild(QLabel, "labelPreloadStatus").text()
    runtime_status.assert_called_with("预加载中 · 1/2 驻留 · PP-StructureV3")

    adapter.preload_completed.emit(_paddle_status())
    assert preload.isEnabled()
    assert "预加载完成" in host.findChild(QLabel, "labelPreloadStatus").text()
    runtime_status.assert_called_with("已驻留 1 个管道 · PP-StructureV3")
    refresh_calls_after_completion = adapter.refresh_calls
    qtbot.wait(40)
    assert adapter.refresh_calls == refresh_calls_after_completion


def test_preload_failure_stops_polling_and_keeps_partial_residency(
    runtime_controller, qtbot
) -> None:
    controller, host, adapter, _ttls = runtime_controller
    runtime_status = controller._runtime_status_callback
    controller._preload_poll_timer.setInterval(10)
    host.findChild(QCheckBox, "chkPreload_PP_STRUCTURE_V3").setChecked(True)
    host.findChild(QCheckBox, "chkPreload_TABLE_RECOGNITION").setChecked(True)
    preload = host.findChild(QPushButton, "btnPreloadNow")
    preload.click()

    adapter.residency_status.emit(_paddle_status())
    adapter.preload_error.emit("表格识别缺少 PaddleX[ocr] 依赖：beautifulsoup4")

    assert preload.isEnabled()
    assert not controller._preload_poll_timer.isActive()
    assert "beautifulsoup4" in host.findChild(QLabel, "labelPreloadStatus").text()
    cache_text = host.findChild(QLabel, "labelPipelineCacheStatus").text()
    assert "驻留 1 个" in cache_text
    assert "PP-StructureV3" in cache_text
    runtime_status.assert_called_with("驻留未完成 · 1/2 · 其余按需加载")


def test_supervisor_ready_automatically_preloads_persisted_selection(
    runtime_controller,
) -> None:
    controller, _host, adapter, _ttls = runtime_controller

    controller.on_supervisor_ready()

    assert adapter.preload_calls == [("PP-StructureV3",)]


def test_heavy_release_remains_disabled(runtime_controller) -> None:
    _controller, host, _adapter, _ttls = runtime_controller
    heavy_release = host.findChild(QPushButton, "btnReleaseHeavy")
    assert not heavy_release.isEnabled()
    assert "暂不支持" in heavy_release.toolTip()
