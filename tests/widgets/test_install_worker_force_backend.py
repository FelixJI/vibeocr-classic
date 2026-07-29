"""Runtime profile selection remains opaque to Classic package details."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibeocr.classic.widgets.install_dialog import InstallWorker


@pytest.mark.parametrize(
    ("requested", "profile"),
    [
        ("cpu", "win-x64-cpu"),
        ("gpu", "win-x64-cu126"),
        (None, "auto"),
    ],
)
def test_force_backend_selects_only_runtime_profile(
    qtbot, tmp_path, requested, profile
):
    worker = InstallWorker(tmp_path, force_backend=requested)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace(
            profile=profile,
            runtime_id=f"digest/{profile}",
        )
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    client_class.assert_called_once_with(tmp_path, profile=profile)
    client_class.return_value.ensure.assert_called_once()


def test_missing_only_still_ensures_whole_runtime(qtbot, tmp_path):
    worker = InstallWorker(tmp_path, missing_only=True)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()
    client_class.return_value.ensure.assert_called_once()
    client_class.return_value.repair.assert_not_called()
