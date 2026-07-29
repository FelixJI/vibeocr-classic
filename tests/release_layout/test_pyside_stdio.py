"""PySide 冻结入口标准流编码门禁。"""

from __future__ import annotations

from vibeocr.classic import main as main_module


class _FakeStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def test_configure_standard_streams_forces_utf8(monkeypatch) -> None:
    stdout = _FakeStream()
    stderr = _FakeStream()
    monkeypatch.setattr(main_module.sys, "stdout", stdout)
    monkeypatch.setattr(main_module.sys, "stderr", stderr)

    main_module._configure_standard_streams()

    expected = [{"encoding": "utf-8", "errors": "replace"}]
    assert stdout.calls == expected
    assert stderr.calls == expected


def test_configure_standard_streams_tolerates_missing_stream(monkeypatch) -> None:
    monkeypatch.setattr(main_module.sys, "stdout", None)
    monkeypatch.setattr(main_module.sys, "stderr", object())

    main_module._configure_standard_streams()


def test_finish_t3_smoke_flushes_before_immediate_exit(monkeypatch) -> None:
    events: list[object] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append("paint")

    monkeypatch.setattr(main_module, "flush_startup", lambda: events.append("flush"))
    monkeypatch.setattr(
        main_module.os, "_exit", lambda code: events.append(("exit", code))
    )

    main_module._finish_t3_smoke(_FakeApp())

    assert events == ["paint", "flush", ("exit", 0)]


def test_t6_smoke_uses_process_unique_startup_locks(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")
    monkeypatch.setattr(main_module.os, "getpid", lambda: 4321)

    single_instance, exclusive_mutex = main_module._startup_lock_names()

    assert single_instance == "VibeOCR-SelfTest-4321"
    assert exclusive_mutex == r"Local\VibeOCR.Frontend.Exclusive.SelfTest.4321"


def test_production_uses_stable_startup_locks(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)

    single_instance, exclusive_mutex = main_module._startup_lock_names()

    assert single_instance == "VibeOCR"
    assert exclusive_mutex is None
