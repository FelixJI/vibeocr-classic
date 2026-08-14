from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

from vibeocr.classic import main
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
