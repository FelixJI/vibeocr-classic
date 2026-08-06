"""Classic 自有 NVIDIA 硬件探测契约。"""

import subprocess
import threading

from vibeocr.classic import hardware_probe


def test_detect_gpu_info_parses_first_nvidia_adapter(monkeypatch) -> None:
    monkeypatch.setattr(
        hardware_probe,
        "_query_nvidia_smi",
        lambda _cancel: subprocess.CompletedProcess(
            (),
            0,
            "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n"
            "NVIDIA RTX A4000, 16376 MiB, 560.94\n",
            "",
        ),
    )

    assert hardware_probe.detect_gpu_info() == {
        "has_gpu": True,
        "name": "NVIDIA GeForce RTX 4090",
        "vram_mb": 24564,
        "cuda": None,
    }


def test_detect_gpu_info_falls_back_to_cpu_when_query_fails(monkeypatch) -> None:
    monkeypatch.setattr(hardware_probe, "_query_nvidia_smi", lambda _cancel: None)

    assert hardware_probe.detect_gpu_info() == {
        "has_gpu": False,
        "name": "",
        "vram_mb": 0,
        "cuda": None,
    }


def test_cancelled_probe_discards_late_result(monkeypatch) -> None:
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        hardware_probe,
        "_query_nvidia_smi",
        lambda _cancel: subprocess.CompletedProcess(
            (), 0, "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n", ""
        ),
    )

    assert hardware_probe.detect_gpu_info(cancelled)["has_gpu"] is False
