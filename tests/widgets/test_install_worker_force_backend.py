"""Accelerator selection remains opaque to Classic package details."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibeocr.classic.widgets.install_dialog import InstallWorker
from vibeocr.classic.runtime_installation import RuntimeProfileDescriptor


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
        client_class.return_value.profile_descriptor.return_value = (
            RuntimeProfileDescriptor("win-x64-cpu", "cpu")
        )
        client_class.return_value.ensure.return_value = SimpleNamespace()
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()
        assert worker.wait(1000)

    client_class.assert_called_once_with(tmp_path, accelerator=accelerator)
    client_class.return_value.ensure.assert_called_once()


def test_missing_only_repairs_installed_scope_without_expanding_profile(
    qtbot, tmp_path
):
    worker = InstallWorker(tmp_path, missing_only=True)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.profile_descriptor.return_value = (
            RuntimeProfileDescriptor("win-x64-cpu", "cpu")
        )
        client_class.return_value.repair.return_value = SimpleNamespace()
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()
        assert worker.wait(1000)
    client_class.return_value.repair.assert_called_once()
    client_class.return_value.ensure.assert_not_called()
