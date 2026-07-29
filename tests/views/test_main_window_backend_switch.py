"""MainWindow 重启时消费 pending_backend 的逻辑测试

不构造完整 MainWindow（UI 重），而是把 _check_pending_backend 作为
未绑定方法在最小 stub 上调用——它只依赖 self._project_root + 模块级
is_cache_valid / update_cache_field。
"""

from pathlib import Path
from unittest.mock import patch

from vibeocr.classic.views import main_window as mw_module
from vibeocr.classic.views.main_window import MainWindow


class _StubWindow:
    """MainWindow 的最小 stub，只提供 _check_pending_backend 需要的属性"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    # 借用 MainWindow 的未绑定方法
    _check_pending_backend = MainWindow._check_pending_backend


def test_pending_backend_triggers_switch(tmp_path):
    """pending_backend 存在且与当前不一致时，应返回需要切换"""
    stub = _StubWindow(tmp_path)
    cached = {
        "version": 1,
        "hardware_info": {"has_gpu": False},  # 当前 CPU
        "pending_backend": "gpu",  # 待切换 GPU
    }
    with (
        patch.object(mw_module, "is_cache_valid", return_value=(True, cached)),
    ):
        needs_switch, target = stub._check_pending_backend()
    assert needs_switch is True
    assert target == "gpu"


def test_no_pending_does_not_switch(tmp_path):
    """无 pending_backend 时不需要切换"""
    stub = _StubWindow(tmp_path)
    cached = {"version": 1, "hardware_info": {"has_gpu": True}}
    with patch.object(mw_module, "is_cache_valid", return_value=(True, cached)):
        needs_switch, target = stub._check_pending_backend()
    assert needs_switch is False
    assert target is None


def test_pending_same_as_current_clears_without_switch(tmp_path):
    """pending 与当前一致时，清除标记但不切换"""
    stub = _StubWindow(tmp_path)
    cached = {
        "version": 1,
        "hardware_info": {"has_gpu": True},  # 当前 GPU
        "pending_backend": "gpu",  # 待切换也是 GPU → 一致
    }
    with (
        patch.object(mw_module, "is_cache_valid", return_value=(True, cached)),
        patch.object(mw_module, "update_cache_field") as mock_update,
    ):
        needs_switch, target = stub._check_pending_backend()
    assert needs_switch is False
    assert target is None
    # 应清除 pending 标记
    mock_update.assert_called_once()
    assert mock_update.call_args[0][1] == "pending_backend"
    assert mock_update.call_args[0][2] is None


def test_cache_invalid_does_not_switch(tmp_path):
    """缓存无效时不切换"""
    stub = _StubWindow(tmp_path)
    with patch.object(mw_module, "is_cache_valid", return_value=(False, None)):
        needs_switch, target = stub._check_pending_backend()
    assert needs_switch is False
    assert target is None


def test_switch_cpu_to_gpu(tmp_path):
    """当前 GPU，pending CPU → 切到 CPU"""
    stub = _StubWindow(tmp_path)
    cached = {
        "version": 1,
        "hardware_info": {"has_gpu": True},
        "pending_backend": "cpu",
    }
    with patch.object(mw_module, "is_cache_valid", return_value=(True, cached)):
        needs_switch, target = stub._check_pending_backend()
    assert needs_switch is True
    assert target == "cpu"
