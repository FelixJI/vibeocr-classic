from __future__ import annotations

import subprocess

from scripts.verify_velopack_portable_e2e import _stop_process


class _Process:
    def __init__(self, *, running: bool, terminate_times_out: bool = False) -> None:
        self.running = running
        self.terminate_times_out = terminate_times_out
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def wait(self, timeout: float) -> int:
        del timeout
        self.waits += 1
        if self.terminate_times_out and self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("VibeOCR.exe", 15)
        self.running = False
        return 0


def test_stop_process_terminates_a_running_packaged_app() -> None:
    process = _Process(running=True)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is False
    assert process.waits == 1


def test_stop_process_kills_after_terminate_timeout() -> None:
    process = _Process(running=True, terminate_times_out=True)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is True
    assert process.waits == 2


def test_stop_process_only_reaps_an_exited_app() -> None:
    process = _Process(running=False)

    _stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is False
    assert process.killed is False
    assert process.waits == 1
