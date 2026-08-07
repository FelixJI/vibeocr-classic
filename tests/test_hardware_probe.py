"""Classic 自有 NVIDIA 硬件探测契约。"""

import subprocess
import threading
from io import StringIO
from unittest.mock import Mock

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


def _running_process() -> Mock:
    process = Mock()
    process.poll.return_value = None
    process.stdout = StringIO()
    process.stderr = StringIO()
    return process


def test_query_cancellation_after_spawn_terminates_process(monkeypatch) -> None:
    process = _running_process()
    process.wait.return_value = 0
    cancel = Mock()
    cancel.is_set.return_value = False
    cancel.wait.return_value = True
    monkeypatch.setattr(hardware_probe.subprocess, "Popen", lambda *_a, **_k: process)

    assert hardware_probe._query_nvidia_smi(cancel) is None

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=1)
    process.kill.assert_not_called()


def test_query_timeout_kills_and_bounds_unresponsive_process(monkeypatch) -> None:
    process = _running_process()
    process.wait.side_effect = [
        subprocess.TimeoutExpired("nvidia-smi", 1),
        subprocess.TimeoutExpired("nvidia-smi", 1),
    ]
    monkeypatch.setattr(hardware_probe.subprocess, "Popen", lambda *_a, **_k: process)

    assert hardware_probe._query_nvidia_smi(None, timeout_seconds=0) is None

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    assert process.stdout.closed
    assert process.stderr.closed
