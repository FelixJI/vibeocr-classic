"""Repair operations replace legacy Python/package reinstall paths."""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibeocr.classic.widgets.install_dialog import InstallWorker


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reinstall_python": True},
        {"single_pkg": "numpy"},
        {"packages": ["numpy", "pillow"]},
    ],
)
def test_legacy_reinstall_requests_repair_of_whole_profile(
    qtbot, tmp_path, kwargs
):
    worker = InstallWorker(tmp_path, **kwargs)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.repair.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()
    client_class.return_value.repair.assert_called_once()
    client_class.return_value.ensure.assert_not_called()


def test_progress_signal_also_logged(qtbot, tmp_path, caplog):
    worker = InstallWorker(tmp_path)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        with (
            caplog.at_level(
                logging.INFO,
                logger="vibeocr.classic.widgets.install_dialog",
            ),
            qtbot.waitSignal(worker.completed, timeout=5000),
        ):
            worker.start()
    assert "运行时" in " ".join(record.message for record in caplog.records)
