"""首启 GPU/CPU 选择对话框测试"""

from unittest.mock import MagicMock, patch

import pytest

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
        ):
            captured["force_backend"] = force_backend
            captured["reinstall_python"] = reinstall_python

        progress = MagicMock()
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
        ):
            captured["force_backend"] = force_backend
            captured["reinstall_python"] = reinstall_python
            captured["missing_only"] = missing_only

        progress = MagicMock()
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
