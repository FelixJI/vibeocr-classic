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


def test_legacy_pending_backend_does_not_control_startup() -> None:
    window = SimpleNamespace(
        _closing=False,
        _ocr_ready=True,
        _machine_cache_data={"pending_backend": "gpu"},
        _start_supervisor=MagicMock(),
    )

    MainWindow._continue_ready_startup(window)

    window._start_supervisor.assert_called_once_with()
