"""Legacy per-package sync markers are retired by component-lock updates."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from vibeocr.classic.views.main_window import MainWindow


class _Stub:
    _check_pending_sync = MainWindow._check_pending_sync
    _delete_pending_sync = MainWindow._delete_pending_sync

    def __init__(self, root: Path) -> None:
        self._project_root = root


def test_legacy_pending_sync_is_removed_without_package_install(tmp_path: Path) -> None:
    marker = tmp_path / "data" / "cache" / "update" / "pending_sync.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"dep_versions":{"torch":"9.9"}}', encoding="utf-8")
    stub = _Stub(tmp_path)
    assert stub._check_pending_sync() is False
    assert not marker.exists()


def test_settings_directory_pending_sync_is_also_removed(tmp_path: Path) -> None:
    marker = tmp_path / "data" / "settings" / "pending_sync.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"dep_versions":{"torch":"9.9"}}', encoding="utf-8")

    assert _Stub(tmp_path)._check_pending_sync() is False
    assert not marker.exists()


def test_missing_legacy_marker_is_noop(tmp_path: Path) -> None:
    assert _Stub(tmp_path)._check_pending_sync() is False


def test_successful_component_update_rechecks_runtime_without_backend_cache() -> None:
    window = SimpleNamespace(
        _refresh_settings_env_state=MagicMock(),
        _dependency_manager=SimpleNamespace(check_dependencies=MagicMock()),
        _startup_update_check_complete=False,
    )

    MainWindow._on_deps_update_finished(window, 1)

    window._refresh_settings_env_state.assert_called_once_with()
    window._dependency_manager.check_dependencies.assert_called_once_with()
    assert window._startup_update_check_complete is True
