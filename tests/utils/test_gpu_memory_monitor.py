"""测试 GPU 显存监控"""

from unittest.mock import patch

import pytest

from vibeocr.backend.utils.gpu_memory_monitor import (
    GPUMemoryInfo,
    GPUMemoryMonitor,
)

_pynvml = pytest.importorskip("pynvml", reason="pynvml not installed")


class TestGPUMemoryMonitor:
    """GPUMemoryMonitor 测试"""

    def test_init_without_gpu(self):
        """测试无 GPU 环境下的初始化"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            monitor = GPUMemoryMonitor()
            assert not monitor.is_available()

    def test_get_status_unavailable(self):
        """测试 GPU 不可用时返回默认值"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            monitor = GPUMemoryMonitor()
            status = monitor.get_status()

            assert status.available is False
            assert status.total == 0
            assert status.free == 0

    def test_estimate_batch_size_no_gpu(self):
        """测试无 GPU 时返回保守 batch_size"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            monitor = GPUMemoryMonitor()
            # 1920x1080 图片
            batch_size = monitor.estimate_batch_size(1920 * 1080)

            # 应该返回默认保守值 4
            assert batch_size == 4

    def test_estimate_batch_size_with_mock_gpu(self):
        """测试模拟 GPU 环境下的 batch_size 估算"""
        monitor = GPUMemoryMonitor()

        # 模拟 get_status 返回
        mock_status = GPUMemoryInfo(total=8192, free=6000, used=2192, available=True)

        with patch.object(monitor, "get_status", return_value=mock_status):
            # 1920x1080 图片约 2M 像素
            batch_size = monitor.estimate_batch_size(1920 * 1080)

            # 6000MB * 0.7 / (2 * 3) ≈ 700
            # 但最大限制为 16
            assert 1 <= batch_size <= 16

    def test_estimate_batch_size_small_image(self):
        """测试小图片的 batch_size 估算"""
        monitor = GPUMemoryMonitor()

        mock_status = GPUMemoryInfo(total=8192, free=4000, used=4192, available=True)

        with patch.object(monitor, "get_status", return_value=mock_status):
            # 640x480 图片约 0.3M 像素
            batch_size = monitor.estimate_batch_size(640 * 480)

            assert batch_size >= 1

    def test_context_manager(self):
        """测试上下文管理器"""
        with (
            patch("pynvml.nvmlInit"),
            patch("pynvml.nvmlShutdown") as shutdown,
            GPUMemoryMonitor() as monitor,
        ):
            assert monitor.is_available()

        shutdown.assert_called_once()


class TestPynvmlStatusPaths:
    """_get_status_pynvml 成功/异常路径与 close 幂等性。"""

    def test_get_status_pynvml_success(self):
        """pynvml 可用时返回真实显存信息（line 62-72）。"""
        from ctypes import Structure, c_ulonglong

        class _FakeMemInfo(Structure):
            _fields_ = [
                ("total", c_ulonglong),
                ("free", c_ulonglong),
                ("used", c_ulonglong),
            ]

        mem = _FakeMemInfo()
        mem.total = 8 * 1024 * 1024 * 1024  # 8GB
        mem.free = 6 * 1024 * 1024 * 1024
        mem.used = 2 * 1024 * 1024 * 1024

        import pynvml

        with (
            patch.object(pynvml, "nvmlInit"),
            patch.object(pynvml, "nvmlDeviceGetHandleByIndex", return_value="handle"),
            patch.object(pynvml, "nvmlDeviceGetMemoryInfo", return_value=mem),
        ):
            monitor = GPUMemoryMonitor()
            status = monitor.get_status()
        assert status.available is True
        assert status.total == 8 * 1024  # 8GB → 8192MB
        assert status.free == 6 * 1024
        assert status.used == 2 * 1024

    def test_get_status_pynvml_exception_returns_unavailable(self):
        """pynvml 调用抛异常时返回 available=False（line 73-75）。"""
        import pynvml

        monitor = GPUMemoryMonitor()
        with patch.object(
            pynvml, "nvmlDeviceGetHandleByIndex", side_effect=RuntimeError("no GPU")
        ):
            status = monitor.get_status()
        assert status.available is False
        assert status.total == 0

    def test_close_when_unavailable_is_noop(self):
        """pynvml 不可用时 close 不调 nvmlShutdown（line 108 分支 False）。"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            monitor = GPUMemoryMonitor()
        # close 不应抛（_pynvml_available=False 分支）
        monitor.close()
