"""批量识别 tab 的文档文件门控按 mode catalog 权威判定。

旧实现直接消费 PreprocessOptionsWidget.gpu_capability 布尔，CPU 计算设备
会拦下已就绪的 MinerU 文档解析；本文件锁定共享 document gate 的行为。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from vibeocr.classic.runtime_selection import (
    ENGINE_AVAILABILITY_UNAVAILABLE,
    RecognitionModeEntry,
    RecognitionModeLifecycle,
    RuntimeSelectionCatalog,
)
from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab


@pytest.fixture
def app(qtbot):
    return QApplication.instance() or QApplication([])


class _DialogRecorder:
    """替身 QMessageBox：记录 warning/information 调用，不弹真实对话框。"""

    def __init__(self):
        self.shown: list[tuple[str, str, str]] = []

    def warning(self, parent, title, text):
        self.shown.append(("warning", title, text))

    def information(self, parent, title, text):
        self.shown.append(("information", title, text))


class _FakeBatchWorker(QObject):
    """替身 BatchRecognitionWorker：不启动任何真实线程。"""

    progress = Signal(int, int, str)
    file_snapshot_ready = Signal(str, object)
    file_completed = Signal(str, str, object)
    terminal = Signal(str)
    error = Signal(str)
    native_stopped = Signal()

    def __init__(self, service, files, options, parent=None):
        super().__init__(parent)
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        """request_shutdown 会调用；替身无需取消任何真实工作。"""

    def isFinished(self):
        return True


@pytest.fixture
def dialogs(monkeypatch):
    recorder = _DialogRecorder()
    monkeypatch.setattr(
        "vibeocr.classic.views.batch_recognition_tab.QMessageBox", recorder
    )
    return recorder


@pytest.fixture
def tab(qtbot, monkeypatch, tmp_path):
    from vibeocr.classic.managers.config_manager import ConfigManager

    monkeypatch.setattr(
        "vibeocr.classic.views.batch_recognition_tab.BatchRecognitionWorker",
        _FakeBatchWorker,
    )
    ConfigManager._instance = None
    ConfigManager.instance(project_root=tmp_path)
    t = BatchRecognitionTab()
    qtbot.addWidget(t)
    yield t
    ConfigManager._instance = None


@pytest.fixture
def doc_file(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return pdf


def _mineru_catalog(availability: str = "ready"):
    return RuntimeSelectionCatalog(
        modes=(
            RecognitionModeEntry(
                "rapid_text",
                "text",
                "OCR",
                "rapidocr",
                "base_runtime",
                "ready",
                RecognitionModeLifecycle("unmanaged", False, False, False, False),
            ),
            RecognitionModeEntry(
                "mineru_document",
                "document",
                "MinerU",
                None,
                "advanced_component",
                availability,
                RecognitionModeLifecycle("process_keep_alive", True, True, False, True),
            ),
        ),
        has_recognition_mode_catalog=True,
    )


class TestFilesChangedGate:
    def test_ready_mode_accepts_document_queue_without_warning(
        self, tab, doc_file, dialogs
    ):
        tab._preprocess_options.set_recognition_catalog(_mineru_catalog("ready"))
        tab._preprocess_options.apply_gpu_gating(False)

        tab._on_files_changed([{"path": str(doc_file)}])

        assert dialogs.shown == []
        assert tab._preprocess_options.is_pipeline_locked


class TestStartGate:
    def test_ready_mode_passes_document_gate(
        self, tab, doc_file, monkeypatch, dialogs
    ):
        tab._preprocess_options.set_recognition_catalog(_mineru_catalog("ready"))
        tab._preprocess_options.apply_gpu_gating(False)
        tab._on_files_changed([{"path": str(doc_file)}])
        monkeypatch.setattr(
            tab._file_list_widget,
            "get_selected_files",
            lambda: [{"path": str(doc_file)}],
        )

        tab._on_start()

        assert dialogs.shown == []
        # 通过文档门控进入提交流程（使用替身 worker，不启动真实识别）。
        assert tab._run_state == BatchRecognitionTab.STATE_RUNNING
        assert tab._worker is not None and tab._worker.started

    def test_unavailable_mode_still_blocks_start(
        self, tab, doc_file, monkeypatch, dialogs
    ):
        tab._preprocess_options.set_recognition_catalog(
            _mineru_catalog("unavailable")
        )
        tab._on_files_changed([{"path": str(doc_file)}])
        # 文件入队已提示过一次；只校验开始门控仍拒绝。
        dialogs.shown.clear()
        monkeypatch.setattr(
            tab._file_list_widget,
            "get_selected_files",
            lambda: [{"path": str(doc_file)}],
        )

        tab._on_start()

        assert len(dialogs.shown) == 1
        kind, _title, _text = dialogs.shown[0]
        assert kind == "warning"
        assert tab._run_state == BatchRecognitionTab.STATE_IDLE
        assert tab._worker is None

    def test_legacy_cpu_keeps_blocking_start(
        self, tab, doc_file, monkeypatch, dialogs
    ):
        tab._preprocess_options.apply_gpu_gating(False)
        tab._on_files_changed([{"path": str(doc_file)}])
        assert (
            tab._preprocess_options.document_parsing_availability()
            == ENGINE_AVAILABILITY_UNAVAILABLE
        )
        # 文件入队已提示过一次；只校验开始门控仍拦截。
        dialogs.shown.clear()
        monkeypatch.setattr(
            tab._file_list_widget,
            "get_selected_files",
            lambda: [{"path": str(doc_file)}],
        )

        tab._on_start()

        assert len(dialogs.shown) == 1
        kind, _title, _text = dialogs.shown[0]
        assert kind == "warning"
        assert tab._run_state == BatchRecognitionTab.STATE_IDLE
        assert tab._worker is None
