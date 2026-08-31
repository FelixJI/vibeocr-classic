"""InstallWorker cooperative cancellation through Runtime Installer.

契约测试直接同步调用 ``worker.run()``，不起真实 QThread、不用
``qtbot.waitSignal`` 嵌套事件循环；根因说明见
test_install_worker_force_backend.py 模块 docstring。
"""

import threading
from types import SimpleNamespace
from unittest.mock import patch

from vibeocr.classic.runtime_installation import RuntimeProfileDescriptor
from vibeocr.classic.widgets.install_dialog import InstallWorker


def _run_worker_sync(worker: InstallWorker) -> list[tuple[bool, str]]:
    """在当前线程同步执行 run() 并捕获 completed 信号参数。"""
    completed: list[tuple[bool, str]] = []
    worker.completed.connect(lambda *args: completed.append(args))
    worker.run()
    return completed


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
        client_class.return_value.profile_descriptor.return_value = (
            RuntimeProfileDescriptor("win-x64-cpu", "cpu")
        )
        client_class.return_value.ensure.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        completed = _run_worker_sync(worker)
        assert completed and completed[-1][0] is True

    kwargs = client_class.return_value.ensure.call_args.kwargs
    assert isinstance(kwargs["cancel_event"], threading.Event)
    assert callable(kwargs["progress"])


def test_close_does_not_use_terminate(qtbot, tmp_path):
    worker = InstallWorker(tmp_path)
    worker.request_cancel()
    assert worker.is_cancelled()
    assert worker.isRunning() is False
