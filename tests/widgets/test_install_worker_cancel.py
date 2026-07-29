"""InstallWorker cooperative cancellation through Runtime Installer."""

import threading
from types import SimpleNamespace
from unittest.mock import patch

from vibeocr.classic.widgets.install_dialog import InstallWorker


def test_request_cancel_sets_cancel_event(qtbot, tmp_path):
    worker = InstallWorker(tmp_path)
    assert not worker.is_cancelled()
    worker.request_cancel()
    assert worker.is_cancelled()


def test_installer_receives_cancel_event(qtbot, tmp_path):
    worker = InstallWorker(tmp_path)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.ensure.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    kwargs = client_class.return_value.ensure.call_args.kwargs
    assert isinstance(kwargs["cancel_event"], threading.Event)
    assert callable(kwargs["progress"])


def test_close_does_not_use_terminate(qtbot, tmp_path):
    worker = InstallWorker(tmp_path)
    worker.request_cancel()
    assert worker.is_cancelled()
    assert worker.isRunning() is False
