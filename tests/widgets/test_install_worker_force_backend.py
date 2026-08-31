"""Accelerator selection remains opaque to Classic package details.

契约测试直接同步调用 ``worker.run()``，不起真实 QThread，也不用
``qtbot.waitSignal`` 嵌套事件循环。根因（CI required job 自 2026-08-30
起约四成运行偶发 access violation，faulthandler 多线程转储）：主线程在
``QEventLoop.exec`` 内执行循环 GC 时，InstallWorker 线程同时在 ``run()``
里跨线程 emit 信号并停机全局后台循环，两者竞态引发原生崩溃，本地与
GitHub runner 的线程时序差异决定是否触发。线程调度本身由 Qt 保证，
不属于这些 worker→Installer 契约测试的验证范围（与 test_install_dialog.py
禁用真实线程的既有约定一致）。
"""

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
        completed = _run_worker_sync(worker)
        assert completed and completed[-1][0] is True

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
        completed = _run_worker_sync(worker)
        assert completed and completed[-1][0] is True
    client_class.return_value.repair.assert_called_once()
    client_class.return_value.ensure.assert_not_called()
