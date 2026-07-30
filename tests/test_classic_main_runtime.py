from __future__ import annotations

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
