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
    "cuda": None,
}


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
    return {
        "has_gpu": True,
        "name": parts[0],
        "vram_mb": int(vram_match.group(1)) if vram_match else 0,
        # CUDA/Paddle wheel compatibility belongs to Runtime Installer. Classic
        # does not infer a compatible runtime from the local driver version.
        "cuda": None,
    }
