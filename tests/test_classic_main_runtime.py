from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from vibeocr.classic import main
from vibeocr.classic.runtime_installation import RuntimeInstallerClientError


def test_dependency_check_ensures_missing_bound_runtime(monkeypatch, tmp_path) -> None:
    client = Mock()
    client.inspect.return_value = SimpleNamespace(
        ready=False,
        profile="win-x64-cpu",
        integrity="missing",
    )
    client.ensure.return_value = SimpleNamespace(runtime_id="runtime")
    monkeypatch.setattr(main, "get_install_root", lambda: tmp_path)
    monkeypatch.setattr(main, "RuntimeInstallerClient", lambda _root: client)

    assert main.check_production_dependencies() is True
    client.ensure.assert_called_once_with()


def test_dependency_check_fails_when_runtime_ensure_fails(
    monkeypatch, tmp_path
) -> None:
    client = Mock()
    client.inspect.return_value = SimpleNamespace(
        ready=False,
        profile="win-x64-cpu",
        integrity="missing",
    )
    client.ensure.side_effect = RuntimeInstallerClientError("install failed")
    monkeypatch.setattr(main, "get_install_root", lambda: tmp_path)
    monkeypatch.setattr(main, "RuntimeInstallerClient", lambda _root: client)

    assert main.check_production_dependencies() is False


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


def test_update_health_is_scheduled_after_event_loop_turn() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "QTimer.singleShot(250, lambda: _publish_update_health(project_root))" in source
