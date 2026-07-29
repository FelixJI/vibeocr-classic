"""estimate_gpu_batch_size 纯函数单元测试。

独立于 test_gpu_memory_monitor.py（后者有 pynvml importorskip，
会跳过整个文件）。本文件只测纯函数，不需要 GPU/pynvml。
"""

from vibeocr.backend.utils.gpu_memory_monitor import (
    GPU_FALLBACK_BATCH_SIZE,
    estimate_gpu_batch_size,
)


def test_estimate_gpu_batch_size_large_vram_caps_at_10():
    """8G 显存（free 6G）、A4@300（8.7M 像素）→ 5× 放大、0.5 安全 → 夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=6144, avg_pixels=8_700_000)
    assert batch == 10


def test_estimate_gpu_batch_size_small_vram_scales_down():
    """2G 显存（free 1.5G）、A4@300 → 1536*0.5/124.45=6.17 → 6。"""
    batch = estimate_gpu_batch_size(free_mb=1536, avg_pixels=8_700_000)
    assert batch == 6


def test_estimate_gpu_batch_size_minimum_is_1():
    """极小显存也要至少 1。"""
    batch = estimate_gpu_batch_size(free_mb=100, avg_pixels=8_700_000)
    assert batch == 1


def test_estimate_gpu_batch_size_tiny_image():
    """小图（100K 像素）即便显存小也返回较大值，夹到 10。"""
    batch = estimate_gpu_batch_size(free_mb=2000, avg_pixels=100_000)
    assert batch == 10


def test_estimate_gpu_batch_size_zero_free_returns_fallback():
    """显存探测失败（free_mb=0）但 GPU 模式 → 兜底返回 GPU_FALLBACK_BATCH_SIZE。"""
    assert (
        estimate_gpu_batch_size(free_mb=0, avg_pixels=8_700_000)
        == GPU_FALLBACK_BATCH_SIZE
    )


def test_estimate_gpu_batch_size_zero_pixels_returns_1():
    """avg_pixels<=0 时返回 1（line 157-158）。"""
    assert estimate_gpu_batch_size(free_mb=4096, avg_pixels=0) == 1
    assert estimate_gpu_batch_size(free_mb=4096, avg_pixels=-10) == 1


def test_estimate_gpu_batch_size_zero_vram_returns_fallback():
    """free_mb<=0 时返回夹紧的 fallback（line 159-160）。"""
    from vibeocr.backend.utils.gpu_memory_monitor import (
        GPU_BATCH_CAP,
        GPU_FALLBACK_BATCH_SIZE,
        estimate_gpu_batch_size,
    )

    assert estimate_gpu_batch_size(free_mb=0, avg_pixels=1_000_000) == max(
        1, min(GPU_FALLBACK_BATCH_SIZE, GPU_BATCH_CAP)
    )
    assert estimate_gpu_batch_size(free_mb=-5, avg_pixels=1_000_000) == max(
        1, min(GPU_FALLBACK_BATCH_SIZE, GPU_BATCH_CAP)
    )
