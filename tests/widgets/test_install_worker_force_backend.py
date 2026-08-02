"""Accelerator selection remains opaque to Classic package details."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibeocr.classic.widgets.install_dialog import InstallWorker


@pytest.mark.parametrize(
    ("requested", "accelerator"),
    [
        ("cpu", "cpu"),
        ("gpu", "nvidia_cuda"),
        (None, None),
    ],
)
def test_force_backend_selects_only_runtime_accelerator(
    qtbot, tmp_path, requested, accelerator
):
    worker = InstallWorker(tmp_path, force_backend=requested)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace()
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    client_class.assert_called_once_with(tmp_path, accelerator=accelerator)
    client_class.return_value.ensure.assert_called_once()


def test_missing_only_still_ensures_whole_runtime(qtbot, tmp_path):
    worker = InstallWorker(tmp_path, missing_only=True)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace()
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()
    client_class.return_value.ensure.assert_called_once()
    client_class.return_value.repair.assert_not_called()
