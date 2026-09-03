"""Classic 自有的本机显示硬件探测。

该模块只回答“当前 Windows 主机是否能看到 NVIDIA GPU”并提供 UI 摘要；
实际安装和运行的 accelerator 始终由 Runtime Installer ``inspect`` 决定。
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

_GPU_QUERY = (
    "nvidia-smi",
    "--query-gpu=name,memory.total,driver_version",
    "--format=csv,noheader,nounits",
)
_EMPTY_GPU_INFO: dict[str, object] = {
    "has_gpu": False,
    "name": "",
    "vram_mb": 0,
    "driver_version": "",
    "cuda": None,
}

# NVIDIA 官方「Windows 驱动最低版本 → CUDA」表（驱动向下兼容：满足某 CUDA 的
# 最低驱动即支持该及以下所有版本）。用于 UI 展示「本机驱动支持 CUDA x.y」（向下兼容，不声称“最高”）；
# 实际安装哪个 CUDA 变体仍由 Runtime Installer 按绑定 profile 决定。
_DRIVER_MINIMUM_FOR_CUDA: tuple[tuple[int, int, str], ...] = (
    (560, 76, "12.6"),
    (555, 85, "12.5"),
    (551, 61, "12.4"),
    (545, 84, "12.3"),
    (536, 40, "12.2"),
    (531, 41, "12.1"),
    (527, 41, "12.0"),
    (520, 6, "11.8"),
    (516, 94, "11.7"),
    (511, 65, "11.6"),
    (496, 4, "11.5"),
    (471, 41, "11.4"),
    (465, 89, "11.3"),
    (460, 89, "11.2"),
    (456, 81, "11.1"),
    (451, 48, "11.0"),
)


def max_supported_cuda_from_driver(driver_version: str) -> str | None:
    """按官方最低驱动表推导本机驱动支持的 CUDA 版本（仅展示用途）。

    无法解析或驱动过旧（低于 CUDA 11.0 门槛）时返回 ``None``。
    """

    parts = driver_version.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError:
        return None
    for min_major, min_minor, cuda in _DRIVER_MINIMUM_FOR_CUDA:
        if (major, minor) >= (min_major, min_minor):
            return cuda
    return None


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        logger.debug("[硬件检测] nvidia-smi terminate 失败", exc_info=True)
    try:
        process.wait(timeout=1)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        process.kill()
    except OSError:
        logger.debug("[硬件检测] nvidia-smi kill 失败", exc_info=True)
    try:
        process.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("[硬件检测] nvidia-smi 未在强制终止后退出")
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _query_nvidia_smi(
    cancel_event: threading.Event | None,
    *,
    timeout_seconds: float = 10.0,
) -> subprocess.CompletedProcess[str] | None:
    if cancel_event is not None and cancel_event.is_set():
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            _GPU_QUERY,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except (FileNotFoundError, OSError):
        return None

    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if cancel_event is not None and cancel_event.wait(timeout=0.05):
            _stop_process(process)
            return None
        if time.monotonic() >= deadline:
            _stop_process(process)
            logger.warning("[硬件检测] nvidia-smi 查询超时")
            return None
        if cancel_event is None:
            time.sleep(0.05)

    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(
        _GPU_QUERY,
        process.returncode,
        stdout,
        stderr,
    )


def detect_gpu_info(
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """返回供 Classic UI 展示的 NVIDIA GPU 信息。"""
    result = _query_nvidia_smi(cancel_event)
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return dict(_EMPTY_GPU_INFO)
    if cancel_event is not None and cancel_event.is_set():
        return dict(_EMPTY_GPU_INFO)

    first_line = result.stdout.strip().splitlines()[0]
    parts = [part.strip() for part in first_line.split(",")]
    if not parts or not parts[0]:
        return dict(_EMPTY_GPU_INFO)
    vram_match = re.search(r"(\d+)", parts[1]) if len(parts) > 1 else None
    driver_version = parts[2] if len(parts) > 2 else ""
    return {
        "has_gpu": True,
        "name": parts[0],
        "vram_mb": int(vram_match.group(1)) if vram_match else 0,
        "driver_version": driver_version,
        # 展示用途的支持版本；CUDA wheel 兼容仍由 Runtime Installer 决定。
        "cuda": max_supported_cuda_from_driver(driver_version),
    }
