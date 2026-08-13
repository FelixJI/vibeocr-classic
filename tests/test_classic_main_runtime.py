from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from vibeocr.classic import main
from vibeocr.classic.pyside.update import UpdateService
from vibeocr.classic.runtime_installation import RuntimeInstallerClientError


def test_dependency_failure_tolerates_missing_stdin(monkeypatch) -> None:
    monkeypatch.setattr(main, "check_production_dependencies", lambda: False)
    monkeypatch.setattr(sys, "stdin", None)

    assert main.main() == 1


def test_dependency_check_leaves_missing_runtime_for_gui_consent(
    monkeypatch, tmp_path
) -> None:
    client = Mock()
    client.inspect.return_value = SimpleNamespace(
        ready=False,
        accelerator="cpu",
        integrity="missing",
    )
    client.ensure.return_value = SimpleNamespace()
    monkeypatch.setattr(main, "get_install_root", lambda: tmp_path)
    monkeypatch.setattr(main, "RuntimeInstallerClient", lambda _root: client)

    assert main.check_production_dependencies() is True
    client.ensure.assert_not_called()


def test_dependency_check_fails_when_runtime_inspection_fails(
    monkeypatch, tmp_path
) -> None:
    client = Mock()
    client.inspect.side_effect = RuntimeInstallerClientError("inspect failed")
    monkeypatch.setattr(main, "get_install_root", lambda: tmp_path)
    monkeypatch.setattr(main, "RuntimeInstallerClient", lambda _root: client)

    assert main.check_production_dependencies() is False
    client.ensure.assert_not_called()


def test_update_health_signal_is_confined_to_update_cache(
    monkeypatch, tmp_path
) -> None:
    health_file = tmp_path / "data" / "cache" / "update" / "startup.health"
    monkeypatch.setenv("VIBEOCR_UPDATE_HEALTH_FILE", str(health_file))

    main._publish_update_health(tmp_path)

    assert health_file.read_text(encoding="utf-8") == "ready\n"

    outside = tmp_path.parent / "startup.health"
    outside.unlink(missing_ok=True)
    monkeypatch.setenv("VIBEOCR_UPDATE_HEALTH_FILE", str(outside))
    main._publish_update_health(tmp_path)
    assert not outside.exists()


def test_bridge_health_accepts_retained_legacy_ingress_cache(
    monkeypatch, tmp_path
) -> None:
    install_root = tmp_path / "portable"
    stable_root = tmp_path / "stable"
    legacy_health = install_root / "data" / "cache" / "update" / "startup.health"
    monkeypatch.setattr(main, "get_install_root", lambda: install_root)
    monkeypatch.setenv("VIBEOCR_UPDATE_HEALTH_FILE", str(legacy_health))

    main._publish_update_health(stable_root)

    assert legacy_health.read_text(encoding="utf-8") == "ready\n"


def test_update_health_is_scheduled_after_event_loop_turn() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert (
        "QTimer.singleShot(250, lambda: _publish_update_health(project_root))" in source
    )


def test_updater_timeout_terminates_pre_ready_process(tmp_path) -> None:
    class FakeProcess:
        returncode = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.returncode = 1

        def kill(self):
            raise AssertionError("terminate should be sufficient")

    process = FakeProcess()
    service = object.__new__(UpdateService)

    result = service._poll_ready(
        process,
        tmp_path / "missing.ready",
        "test updater",
        timeout=0,
    )

    assert result == "crashed"
    assert process.terminated
