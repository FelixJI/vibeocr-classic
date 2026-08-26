"""首启 GPU/CPU 选择对话框测试"""

from unittest.mock import MagicMock, patch

import pytest

from vibeocr.classic.runtime_installation import (
    RuntimeComponentDescriptor,
    RuntimeMaintenanceUpdate,
    RuntimeProfileDescriptor,
)
from vibeocr.classic.widgets import backend_choice_dialog as bcd_module


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def _make_dialog(tmp_path, qtbot, has_gpu=True):
    """构造 BackendChoiceDialog，mock Classic 硬件探测。"""
    patch.object(
        bcd_module,
        "detect_gpu_info",
        return_value={
            "has_gpu": has_gpu,
            "name": "NVIDIA GeForce RTX 4090" if has_gpu else "",
            "vram_mb": 24564 if has_gpu else 0,
            "cuda": "cu126" if has_gpu else None,
        },
    ).start()
    dialog = bcd_module.BackendChoiceDialog(tmp_path)
    qtbot.waitUntil(lambda: dialog._gpu_detect_task is None, timeout=3000)
    return dialog


def test_gpu_available_defaults_to_gpu(_cleanup, qtbot, tmp_path):
    """有 GPU 时默认选 GPU，两项启用"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=True)
    qtbot.addWidget(dlg)
    assert dlg._gpu_radio.isChecked()
    assert dlg._gpu_radio.isEnabled()
    assert dlg._cpu_radio.isEnabled()


def test_no_gpu_disables_gpu_defaults_cpu(_cleanup, qtbot, tmp_path):
    """无 GPU 时 GPU 禁用，默认 CPU"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=False)
    qtbot.addWidget(dlg)
    assert not dlg._gpu_radio.isEnabled()
    assert dlg._cpu_radio.isChecked()


def test_selected_backend_returns_choice(_cleanup, qtbot, tmp_path):
    """selected_backend 反映单选"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=True)
    qtbot.addWidget(dlg)
    dlg._cpu_radio.setChecked(True)
    assert dlg.selected_backend() == "cpu"
    dlg._gpu_radio.setChecked(True)
    assert dlg.selected_backend() == "gpu"


def test_install_button_visible_initially(_cleanup, qtbot, tmp_path):
    """初始应显示"开始安装"按钮（未 show 时 isVisible 为 False 属正常，
    改测 button 存在且文本正确 + 未隐藏）"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=True)
    qtbot.addWidget(dlg)
    assert not dlg._install_button.isHidden()
    assert dlg._install_button.isEnabled()
    assert "安装" in dlg._install_button.text()


def test_reinstall_python_passed_to_worker(_cleanup, qtbot, tmp_path):
    """reinstall_python=True 应透传给 InstallWorker"""
    captured = {}

    class FakeWorker:
        def __init__(
            self,
            project_root,
            force_backend=None,
            reinstall_python=False,
            missing_only=False,
            install_component_ids=None,
        ):
            captured["force_backend"] = force_backend
            captured["reinstall_python"] = reinstall_python

        progress = MagicMock()
        profile = MagicMock()
        maintenance = MagicMock()
        completed = MagicMock()
        finished = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

        def wait(self):
            pass

    with patch.object(
        bcd_module,
        "detect_gpu_info",
        return_value={
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        },
    ):
        with patch.object(bcd_module, "InstallWorker", FakeWorker):
            dlg = bcd_module.BackendChoiceDialog(tmp_path, reinstall_python=True)
            qtbot.addWidget(dlg)
            dlg._on_install_clicked()

    assert captured.get("reinstall_python") is True, (
        "reinstall_python 应透传给 InstallWorker"
    )


def test_missing_only_passed_to_install_worker(_cleanup, qtbot, tmp_path):
    """missing_only=True 应透传给 InstallWorker"""
    captured = {}

    class FakeWorker:
        def __init__(
            self,
            project_root,
            force_backend=None,
            reinstall_python=False,
            missing_only=False,
            install_component_ids=None,
        ):
            captured["force_backend"] = force_backend
            captured["reinstall_python"] = reinstall_python
            captured["missing_only"] = missing_only

        progress = MagicMock()
        profile = MagicMock()
        maintenance = MagicMock()
        completed = MagicMock()
        finished = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

        def wait(self):
            pass

    with patch.object(
        bcd_module,
        "detect_gpu_info",
        return_value={
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        },
    ):
        with patch.object(bcd_module, "InstallWorker", FakeWorker):
            dlg = bcd_module.BackendChoiceDialog(tmp_path, missing_only=True)
            qtbot.addWidget(dlg)
            dlg._on_install_clicked()

    assert captured.get("missing_only") is True, "missing_only 应透传给 InstallWorker"


def test_first_run_requests_base_only_instead_of_backend_default_scope(
    _cleanup, qtbot, tmp_path
):
    """首启 RapidOCR/PDF/QR 随 Base 提供，不得省略 scope 触发完整 profile。"""
    captured: dict[str, object] = {}

    class FakeWorker:
        def __init__(
            self,
            project_root,
            force_backend=None,
            reinstall_python=False,
            missing_only=False,
            install_component_ids=None,
            download_source_ids=None,
        ):
            captured["force_backend"] = force_backend
            captured["install_component_ids"] = install_component_ids
            captured["download_source_ids"] = download_source_ids
            self.progress = MagicMock()
            self.profile = MagicMock()
            self.maintenance = MagicMock()
            self.completed = MagicMock()
            self.finished = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

        def wait(self):
            pass

    with (
        patch.object(
            bcd_module,
            "detect_gpu_info",
            return_value={"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None},
        ),
        patch.object(bcd_module, "InstallWorker", FakeWorker),
    ):
        dlg = bcd_module.BackendChoiceDialog(tmp_path)
        qtbot.addWidget(dlg)
        dlg._on_install_clicked()

    assert captured["force_backend"] == "cpu"
    assert captured["install_component_ids"] == ()
    assert captured["download_source_ids"] is None


def test_failure_shows_warning_messagebox(_cleanup, qtbot, tmp_path):
    """安装失败时应弹 QMessageBox.warning"""
    from PySide6.QtWidgets import QMessageBox

    warnings_shown = []
    with (
        patch.object(
            QMessageBox, "warning", lambda *args, **kwargs: warnings_shown.append(args)
        ),
        patch.object(
            bcd_module,
            "detect_gpu_info",
            return_value={"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None},
        ),
    ):
        dlg = bcd_module.BackendChoiceDialog(tmp_path)
        qtbot.addWidget(dlg)
        # 直接调用 _on_finished 模拟失败
        dlg._on_finished(False, "torch 安装失败:\n网络超时")

    assert len(warnings_shown) == 1, "失败时应弹一次 warning"
    all_text = " ".join(str(a) for a in warnings_shown[0])
    assert "torch" in all_text or "失败" in all_text, (
        f"弹窗应含失败信息，实际: {all_text}"
    )


def test_success_does_not_show_warning(_cleanup, qtbot, tmp_path):
    """安装成功时不应弹 warning"""
    from PySide6.QtWidgets import QMessageBox

    warnings_shown = []
    with (
        patch.object(
            QMessageBox, "warning", lambda *args, **kwargs: warnings_shown.append(args)
        ),
        patch.object(
            bcd_module,
            "detect_gpu_info",
            return_value={"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None},
        ),
    ):
        dlg = bcd_module.BackendChoiceDialog(tmp_path)
        qtbot.addWidget(dlg)
        dlg._on_finished(True, "安装成功")

    assert len(warnings_shown) == 0


def _maintenance(**overrides) -> RuntimeMaintenanceUpdate:
    """构造 ensure 运行中的维护事件，字段可按用例覆盖。"""
    fields = dict(
        event_type="progress",
        operation_id="op-1",
        sequence=5,
        operation="ensure",
        operation_state="running",
        phase="prepare_runtime",
        profile_id="win-x64-cpu",
        updated_at="2026-08-14T00:00:00Z",
        component_id="runtime_base",
    )
    fields.update(overrides)
    return RuntimeMaintenanceUpdate(**fields)


def _fill_profile(dlg) -> None:
    dlg._on_profile(
        RuntimeProfileDescriptor(
            "win-x64-cpu",
            "cpu",
            (
                RuntimeComponentDescriptor("runtime_base", "Python 运行时", None),
                RuntimeComponentDescriptor("ocr_engine", "OCR 引擎", "3.7.0"),
            ),
        )
    )


def test_install_connects_maintenance_and_profile_signals(_cleanup, qtbot, tmp_path):
    """回归：首启对话框必须连接 profile/maintenance 进度信号。

    旧实现只连 progress/completed，导致首启安装 Runtime 与重依赖时
    仅显示静态"正在确保绑定的 Runtime profile"文案，无进度条百分比、
    无组件状态（issue 根因）。
    """
    workers = []

    class FakeWorker:
        def __init__(
            self,
            project_root,
            force_backend=None,
            reinstall_python=False,
            missing_only=False,
            install_component_ids=None,
        ):
            workers.append(self)
            self.progress = MagicMock()
            self.profile = MagicMock()
            self.maintenance = MagicMock()
            self.completed = MagicMock()
            self.finished = MagicMock()

        def start(self):
            pass

        def isRunning(self):
            return False

        def wait(self):
            pass

    with (
        patch.object(
            bcd_module,
            "detect_gpu_info",
            return_value={"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None},
        ),
        patch.object(bcd_module, "InstallWorker", FakeWorker),
    ):
        dlg = bcd_module.BackendChoiceDialog(tmp_path)
        qtbot.addWidget(dlg)
        dlg._on_install_clicked()

    assert len(workers) == 1
    worker = workers[0]
    worker.progress.connect.assert_called_once()
    worker.profile.connect.assert_called_once()
    worker.maintenance.connect.assert_called_once()
    worker.completed.connect.assert_called_once()


def test_maintenance_bytes_progress_drives_bar_and_component_rows(
    _cleanup, qtbot, tmp_path
):
    """字节数维护事件应驱动确定进度条与组件行状态。"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=False)
    qtbot.addWidget(dlg)
    _fill_profile(dlg)

    dlg._on_maintenance(
        _maintenance(
            progress_current=50 * 1024 * 1024,
            progress_total=100 * 1024 * 1024,
            progress_unit="bytes",
        )
    )

    assert dlg._progress_bar.maximum() == 100 * 1024 * 1024
    assert dlg._progress_bar.value() == 50 * 1024 * 1024
    assert "50%" in dlg._progress_label.text()
    assert dlg._component_items["runtime_base"].text(1) == "进行中"
    assert dlg._component_items["ocr_engine"].text(1) == "等待中"
    assert "Python 运行时" in dlg._log_text.toPlainText()


def test_profile_rows_show_bundled_and_not_required_truth(_cleanup, qtbot, tmp_path):
    """随包 Base 与未选高级组件不能都被投影成“等待下载”。"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=False)
    qtbot.addWidget(dlg)

    dlg._on_profile(
        RuntimeProfileDescriptor(
            "win-x64-cpu",
            "cpu",
            (
                RuntimeComponentDescriptor(
                    "rapidocr",
                    "RapidOCR",
                    "1.0",
                    desired_state="ready",
                    included_in_base=True,
                ),
                RuntimeComponentDescriptor(
                    "pdf",
                    "PDF",
                    "1.0",
                    desired_state="ready",
                    included_in_base=True,
                ),
                RuntimeComponentDescriptor(
                    "qr",
                    "QR",
                    "1.0",
                    desired_state="ready",
                    included_in_base=True,
                ),
                RuntimeComponentDescriptor(
                    "document_parsing",
                    "文档智能解析",
                    "2.0",
                    "not_required",
                    "2.0",
                    "missing",
                    None,
                ),
            ),
        )
    )

    assert dlg._component_items["rapidocr"].text(1) == "随包提供"
    assert dlg._component_items["pdf"].text(1) == "随包提供"
    assert dlg._component_items["qr"].text(1) == "随包提供"
    assert dlg._component_items["document_parsing"].text(1) == "不需要"


def test_maintenance_steps_progress_keeps_indeterminate_bar(_cleanup, qtbot, tmp_path):
    """steps 进度无确定区间，进度条保持不确定模式但文案显示步数。"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=False)
    qtbot.addWidget(dlg)

    dlg._on_maintenance(
        _maintenance(
            phase="install_profile",
            component_id="ocr_engine",
            progress_current=2,
            progress_total=7,
            progress_unit="steps",
        )
    )

    assert dlg._progress_bar.maximum() == 0
    assert "2/7 步" in dlg._progress_label.text()


def test_success_marks_all_components_ready(_cleanup, qtbot, tmp_path):
    """安装成功后所有组件行应标记为已就绪。"""
    dlg = _make_dialog(tmp_path, qtbot, has_gpu=False)
    qtbot.addWidget(dlg)
    _fill_profile(dlg)
    dlg._on_maintenance(_maintenance())

    dlg._on_finished(True, "Runtime cpu 已验证")

    assert dlg._component_items["runtime_base"].text(1) == "已就绪"
    assert dlg._component_items["ocr_engine"].text(1) == "已就绪"
