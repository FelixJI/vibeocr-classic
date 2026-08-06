"""Classic 平台默认值 helper 契约。"""

from __future__ import annotations

import pytest

from vibeocr.classic.utils import platform_defaults


def test_cpu_thread_override_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "24")
    monkeypatch.setattr(platform_defaults.os, "cpu_count", lambda: 8)

    assert platform_defaults.get_cpu_thread_count() == 24


@pytest.mark.parametrize("override", ["", "0", "invalid"])
def test_cpu_thread_count_uses_capped_platform_value(
    monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", override)
    monkeypatch.setattr(platform_defaults.os, "cpu_count", lambda: 32)

    assert platform_defaults.get_cpu_thread_count() == 16


def test_cpu_thread_count_falls_back_when_platform_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)

    def _raise_os_error() -> int:
        raise OSError("cpu count unavailable")

    monkeypatch.setattr(platform_defaults.os, "cpu_count", _raise_os_error)

    assert platform_defaults.get_cpu_thread_count() == 4
