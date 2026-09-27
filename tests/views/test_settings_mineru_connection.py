"""MinerU 连接设置组的行为契约（设置页控制器 + 假 Supervisor 适配器）。

覆盖：
- capability 门控：未声明 ocr.mineru-remote-api.v1 的旧 Backend 整组禁用；
- 保存链：沿既有 /v2/settings 全量 PUT，保留 TTL/其他 extra/下载源；
- 凭据交互：Key 永不回显、留空保留、显式清除才删除；
- 校验：空白/userinfo/query/fragment URL 与含换行 Key 拒绝；
- 准备链：远程模式复用 preload（pipelines+recognition_modes），完成刷新
  health/tier；
- 本地 TTL/释放提示不得声称管理远程服务。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from vibeocr.classic.mineru_connection import (
    MINERU_CONNECTION_EXTRA_KEY,
    MINERU_CONNECTION_MODE_LOCAL,
    MINERU_CONNECTION_MODE_REMOTE,
    set_active_mineru_connection_mode,
)
from vibeocr.classic.runtime_selection import (
    MINERU_REMOTE_API_CAPABILITY,
    RecognitionModeEntry,
    RecognitionModeLifecycle,
    RuntimeSelectionCatalog,
)
from vibeocr.classic.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.classic.views.settings_page_controller import SettingsPageController
from vibeocr.runtime_contracts import (
    PipelineSpec,
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
        self.update_calls: list[SettingsSnapshot] = []
        self.preload_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.fetch_health_calls = 0
        self.fetch_settings_calls = 0

    def refresh_residency(self) -> None:
        self.refresh_calls += 1

    def update_settings(self, snapshot: SettingsSnapshot) -> None:
        self.update_calls.append(snapshot)

    def preload(
        self,
        pipelines: tuple[str, ...],
        *,
        recognition_modes: tuple[str, ...] = (),
    ) -> None:
        self.preload_calls.append((tuple(pipelines), tuple(recognition_modes)))

    def fetch_health(self) -> None:
        self.fetch_health_calls += 1

    def fetch_settings(self) -> None:
        self.fetch_settings_calls += 1


def _health_payload(*, with_mineru_remote: bool) -> dict:
    capabilities = ["ocr.recognition.v2"]
    if with_mineru_remote:
        capabilities.append(MINERU_REMOTE_API_CAPABILITY)
    return {
        "schema_version": 2,
        "instance_id": "i-1",
        "protocol_version": 2,
        "ready": True,
        "draining": False,
        "capabilities": capabilities,
        "capability_descriptors": [],
    }


def _ready_catalog() -> RuntimeSelectionCatalog:
    """含 mineru_document（process_keep_alive，支持 TTL）的协商目录。"""

    process = RecognitionModeLifecycle("process_keep_alive", False, True, False, True)
    residency = RecognitionModeLifecycle("model_residency", True, True, True, True)
    unmanaged = RecognitionModeLifecycle("unmanaged", False, False, False, False)
    return RuntimeSelectionCatalog(
        modes=(
            RecognitionModeEntry(
                "rapid_text",
                "text",
                "OCR",
                "rapidocr",
                "base_runtime",
                "ready",
                unmanaged,
            ),
            RecognitionModeEntry(
                "mineru_document",
                "document",
                "MinerU",
                None,
                "advanced_component",
                "ready",
                process,
            ),
            RecognitionModeEntry(
                "paddle_structure",
                "document",
                "PP-StructureV3",
                None,
                "advanced_component",
                "ready",
                residency,
            ),
        ),
        has_recognition_mode_catalog=True,
    )


def _snapshot(extra: dict | None = None) -> SettingsSnapshot:
    return SettingsSnapshot(
        default_ttl_seconds=900,
        pipelines=(PipelineSpec(name="PP-StructureV3", ttl_seconds=300),),
        extra=extra if extra is not None else {"unrelated": "keep"},
        download_source_ids=("tuna-pypi",),
    )


@pytest.fixture
def mineru_controller(qtbot, tmp_path, monkeypatch):
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

    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)
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
            subprocess_manager=MagicMock(),
            defer_backend_initialization=True,
            defer_machine_cache_status=True,
            runtime_installer_client=installer_client,
        )
        controller.connect_signals()

    yield controller, host, adapter
    controller.request_shutdown()
    set_active_mineru_connection_mode(MINERU_CONNECTION_MODE_LOCAL)


def _status_text(host: QWidget) -> str:
    return host.findChild(QLabel, "labelMineruConnectionStatus").text()


def _recognition_page(host: QWidget) -> QWidget:
    """识别设置页（QStackedWidget 会隐藏非当前页，可见性断言以页为锚点）。"""

    stacked = host.findChild(QStackedWidget, "settingsStackedWidget")
    assert stacked is not None
    page = host.findChild(QWidget, "pageRecognition")
    assert page is not None
    return page


def _select_mode(host: QWidget, mode: str) -> None:
    combo = host.findChild(QComboBox, "comboMineruConnectionMode")
    combo.setCurrentIndex(combo.findData(mode))


def test_group_gated_until_capability_confirmed(mineru_controller) -> None:
    """未声明 capability 的旧 Backend：整组禁用并明确不可用。"""
    controller, host, adapter = mineru_controller

    group = host.findChild(QGroupBox, "groupMineruConnection")
    assert group is not None
    assert not group.isEnabled()
    assert "等待 Backend 能力目录" in _status_text(host)

    controller._on_health_loaded(_health_payload(with_mineru_remote=False))
    assert not group.isEnabled()
    assert "ocr.mineru-remote-api.v1" in _status_text(host)
    assert "不支持" in _status_text(host)

    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    assert group.isEnabled()
    assert adapter.fetch_health_calls == 0  # 门控只依赖已有 health，不发请求


def test_old_backend_save_is_rejected_without_request(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=False))
    controller._on_settings_loaded(_snapshot())

    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert adapter.update_calls == []


def test_save_remote_puts_settings_preserving_other_state(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())

    _select_mode(host, MINERU_CONNECTION_MODE_REMOTE)
    host.findChild(QLineEdit, "editMineruApiUrl").setText(
        "https://mineru.example.com/proxy/"
    )
    host.findChild(QLineEdit, "editMineruApiKey").setText("sk-first")
    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert len(adapter.update_calls) == 1
    saved = adapter.update_calls[0]
    assert saved.default_ttl_seconds == 900
    assert saved.pipelines == (PipelineSpec(name="PP-StructureV3", ttl_seconds=300),)
    assert saved.download_source_ids == ("tuna-pypi",)
    assert saved.extra == {
        "unrelated": "keep",
        MINERU_CONNECTION_EXTRA_KEY: {
            "mode": MINERU_CONNECTION_MODE_REMOTE,
            "api_url": "https://mineru.example.com/proxy/",
            "api_key": "sk-first",
        },
    }

    # 成功回读：刷新能力目录，且不把未验证连接说成就绪。
    health_before = adapter.fetch_health_calls
    adapter.settings_updated.emit(saved)
    assert adapter.fetch_health_calls == health_before + 1
    assert "已保存并回读成功" in _status_text(host)
    assert "未验证" in _status_text(host)
    assert "验证并准备" in _status_text(host)


def test_save_error_keeps_effective_config_message(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())

    _select_mode(host, MINERU_CONNECTION_MODE_REMOTE)
    host.findChild(QLineEdit, "editMineruApiUrl").setText("https://m.example.com/")
    host.findChild(QPushButton, "btnSaveMineruConnection").click()
    adapter.settings_error.emit("invalid mineru_connection")

    assert "保存失败" in _status_text(host)
    assert "原配置保持生效" in _status_text(host)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "mineru.example.com",
        "ftp://mineru.example.com/",
        "https://user:pass@example.com/",
        "https://example.com/?q=1",
        "https://example.com/#frag",
        "https://example.com/ a",
    ],
)
def test_invalid_remote_input_is_rejected_locally(mineru_controller, url) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())

    _select_mode(host, MINERU_CONNECTION_MODE_REMOTE)
    host.findChild(QLineEdit, "editMineruApiUrl").setText(url)
    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert adapter.update_calls == []
    assert "未保存" in _status_text(host)


def test_key_with_crlf_is_rejected(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())

    _select_mode(host, MINERU_CONNECTION_MODE_REMOTE)
    host.findChild(QLineEdit, "editMineruApiUrl").setText("https://m.example.com/")
    host.findChild(QLineEdit, "editMineruApiKey").setText("bad\nkey")
    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert adapter.update_calls == []
    assert "换行" in _status_text(host)


def test_saved_key_is_never_echoed_and_blank_input_keeps_it(
    mineru_controller,
) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(
        _snapshot(
            {
                "unrelated": "keep",
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": MINERU_CONNECTION_MODE_REMOTE,
                    "api_url": "https://m.example.com/",
                    "api_key": "stored-secret",
                },
            }
        )
    )

    key_edit = host.findChild(QLineEdit, "editMineruApiKey")
    assert key_edit.text() == ""
    assert key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert "已保存 API Key" in key_edit.placeholderText()
    assert "stored-secret" not in _status_text(host)

    # 仅修改 URL：Key 输入为空 → 保留已保存 Key。
    host.findChild(QLineEdit, "editMineruApiUrl").setText("https://new.example.com/")
    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    saved = adapter.update_calls[-1].extra[MINERU_CONNECTION_EXTRA_KEY]
    assert saved["api_key"] == "stored-secret"
    assert saved["api_url"] == "https://new.example.com/"


def test_explicit_clear_button_removes_saved_key_and_edit_restores_it(
    mineru_controller,
) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    stored = {
        MINERU_CONNECTION_EXTRA_KEY: {
            "mode": MINERU_CONNECTION_MODE_REMOTE,
            "api_url": "https://m.example.com/",
            "api_key": "stored-secret",
        }
    }
    controller._on_settings_loaded(_snapshot(stored))

    clear_button = host.findChild(QPushButton, "btnClearMineruApiKey")
    assert clear_button.isVisibleTo(host.findChild(QGroupBox, "groupMineruConnection"))
    clear_button.click()
    assert (
        "已请求清除" in host.findChild(QLineEdit, "editMineruApiKey").placeholderText()
    )

    host.findChild(QPushButton, "btnSaveMineruConnection").click()
    saved = adapter.update_calls[-1].extra[MINERU_CONNECTION_EXTRA_KEY]
    assert "api_key" not in saved

    # 重新回读后清除按钮隐藏；输入新 Key 撤销清除意图。
    controller._on_settings_loaded(_snapshot(stored))
    clear_button.click()
    key_edit = host.findChild(QLineEdit, "editMineruApiKey")
    key_edit.setText("sk-replacement")
    key_edit.textEdited.emit("sk-replacement")  # 模拟用户输入撤销清除
    host.findChild(QPushButton, "btnSaveMineruConnection").click()
    saved = adapter.update_calls[-1].extra[MINERU_CONNECTION_EXTRA_KEY]
    assert saved["api_key"] == "sk-replacement"


def test_local_mode_saves_mode_only_and_disables_remote_inputs(
    mineru_controller,
) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(
        _snapshot(
            {
                "unrelated": "keep",
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": MINERU_CONNECTION_MODE_REMOTE,
                    "api_url": "https://m.example.com/",
                    "api_key": "stored-secret",
                },
            }
        )
    )

    _select_mode(host, MINERU_CONNECTION_MODE_LOCAL)
    assert not host.findChild(QWidget, "mineruConnectionUrlRow").isEnabled()
    assert not host.findChild(QWidget, "mineruConnectionKeyRow").isEnabled()
    assert not host.findChild(QPushButton, "btnPrepareMineruRemote").isVisibleTo(
        _recognition_page(host)
    )

    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    saved = adapter.update_calls[-1].extra
    assert saved[MINERU_CONNECTION_EXTRA_KEY] == {"mode": MINERU_CONNECTION_MODE_LOCAL}
    assert saved["unrelated"] == "keep"


def test_save_without_snapshot_reads_settings_first(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    assert controller._runtime_settings_snapshot is None

    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert adapter.update_calls == []
    assert adapter.fetch_settings_calls == 1
    assert "正在读取现有设置" in _status_text(host)


def test_save_with_disconnected_supervisor_reports_and_skips(
    mineru_controller,
) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())
    adapter.is_started = False

    host.findChild(QPushButton, "btnSaveMineruConnection").click()

    assert adapter.update_calls == []
    assert "未连接" in _status_text(host)


def test_remote_mode_ttl_tooltip_does_not_claim_remote_control(
    mineru_controller,
) -> None:
    controller, host, _adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._selection_catalog = _ready_catalog()
    controller._refresh_lifecycle_controls()

    ttl_combo = host.findChild(QComboBox, "comboTtl_MinerU")
    assert ttl_combo is not None
    assert "停止 API 进程" in ttl_combo.toolTip()

    controller._on_settings_loaded(
        _snapshot(
            {
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": MINERU_CONNECTION_MODE_REMOTE,
                    "api_url": "https://m.example.com/",
                }
            }
        )
    )
    assert "不管理远程服务" in ttl_combo.toolTip()

    controller._on_settings_loaded(_snapshot())
    assert "停止 API 进程" in ttl_combo.toolTip()


def test_prepare_remote_reuses_preload_and_refreshes_health(
    mineru_controller,
) -> None:
    """远程准备：复用既有 preload 链路（pipelines+recognition_modes）。"""
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(
        _snapshot(
            {
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": MINERU_CONNECTION_MODE_REMOTE,
                    "api_url": "https://m.example.com/",
                }
            }
        )
    )

    prepare_button = host.findChild(QPushButton, "btnPrepareMineruRemote")
    assert prepare_button.isVisibleTo(_recognition_page(host))
    health_before = adapter.fetch_health_calls
    prepare_button.click()

    assert adapter.preload_calls == [(("MinerU",), ("mineru_document",))]
    assert not prepare_button.isEnabled()
    assert "正在验证并准备" in _status_text(host)
    assert adapter.fetch_health_calls == health_before

    adapter.preload_completed.emit(ResidencyStatus(default_ttl_seconds=300))
    assert prepare_button.isEnabled()
    assert "准备完成" in _status_text(host)
    assert adapter.fetch_health_calls == health_before + 1
    # 准备成功后，状态文案如实区分"已通过 Backend 准备"与"未验证"。
    controller._refresh_mineru_connection_controls()
    assert "已通过 Backend 准备" in _status_text(host)

    prepare_button.click()  # 再次进入准备中，验证失败路径
    assert not prepare_button.isEnabled()
    adapter.preload_error.emit("connection refused")
    assert "准备失败" in _status_text(host)
    assert "connection refused" in _status_text(host)
    assert prepare_button.isEnabled()


def test_prepare_button_hidden_until_remote_mode_saved(mineru_controller) -> None:
    controller, host, adapter = mineru_controller
    controller._on_health_loaded(_health_payload(with_mineru_remote=True))
    controller._on_settings_loaded(_snapshot())

    prepare_button = host.findChild(QPushButton, "btnPrepareMineruRemote")
    assert not prepare_button.isVisibleTo(_recognition_page(host))
    prepare_button.click()
    assert adapter.preload_calls == []
    assert "请先保存远程连接配置" in _status_text(host)


def test_supervisor_ready_also_reads_settings_for_connection_mode(
    mineru_controller,
) -> None:
    """就绪即回读 settings：已保存的远程模式在首次提交前生效。"""
    controller, _host, adapter = mineru_controller

    controller.on_supervisor_ready()

    assert adapter.fetch_health_calls == 1
    assert adapter.fetch_settings_calls == 1
    from vibeocr.classic.recognition_settings import OCROptions
    from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

    with pytest.raises(ValueError, match="正在读取 MinerU 连接设置"):
        OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING).to_pipeline_selection()
    controller._on_settings_loaded(
        _snapshot(
            {
                MINERU_CONNECTION_EXTRA_KEY: {
                    "mode": MINERU_CONNECTION_MODE_REMOTE,
                    "api_url": "https://m.example.com/",
                }
            }
        )
    )
    assert (
        OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING).to_pipeline_selection().mineru
        is not None
    )
