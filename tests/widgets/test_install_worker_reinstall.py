"""Repair operations replace legacy Python/package reinstall paths.

契约测试直接同步调用 ``worker.run()``，不起真实 QThread、不用
``qtbot.waitSignal`` 嵌套事件循环；根因说明见
test_install_worker_force_backend.py 模块 docstring。
"""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibeocr.classic.widgets.install_dialog import InstallWorker
from vibeocr.classic.runtime_installation import RuntimeProfileDescriptor


def _run_worker_sync(worker: InstallWorker) -> list[tuple[bool, str]]:
    """在当前线程同步执行 run() 并捕获 completed 信号参数。"""
    completed: list[tuple[bool, str]] = []
    worker.completed.connect(lambda *args: completed.append(args))
    worker.run()
    return completed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reinstall_python": True},
        {"single_pkg": "numpy"},
        {"packages": ["numpy", "pillow"]},
    ],
)
def test_legacy_reinstall_requests_repair_of_whole_profile(qtbot, tmp_path, kwargs):
    worker = InstallWorker(tmp_path, **kwargs)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as client_class:
        client_class.return_value.profile_descriptor.return_value = (
            RuntimeProfileDescriptor("win-x64-cpu", "cpu")
        )
        client_class.return_value.repair.return_value = SimpleNamespace(
            profile="win-x64-cpu",
            runtime_id="digest/win-x64-cpu",
        )
        completed = _run_worker_sync(worker)
        assert completed and completed[-1][0] is True
    client_class.return_value.repair.assert_called_once()
    client_class.return_value.ensure.assert_not_called()


def test_progress_signal_also_logged(qtbot, tmp_path, caplog):
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
        with caplog.at_level(
            logging.INFO,
            logger="vibeocr.classic.widgets.install_dialog",
        ):
            completed = _run_worker_sync(worker)
            assert completed and completed[-1][0] is True
    assert "运行时" in " ".join(record.message for record in caplog.records)
