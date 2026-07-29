"""测试 DependencyManager"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from vibeocr.classic.managers.dependency_manager import (
    DependencyCheckSignals,
    DependencyCheckTask,
    DependencyManager,
)


class TestDependencyCheckSignals:
    """测试依赖检查信号"""

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        signals = DependencyCheckSignals()
        assert hasattr(signals, "finished")


class TestDependencyCheckTask:
    """测试依赖检查任务"""

    def test_task_creation(self, tmp_path):
        """测试任务创建"""
        task = DependencyCheckTask(tmp_path)
        assert task._project_root == tmp_path
        assert hasattr(task, "signals")

    def test_task_run_ready(self, tmp_path, qapp):
        """测试任务运行（依赖就绪）"""
        client = MagicMock()
        client.inspect.return_value = SimpleNamespace(
            ready=True,
            runtime_id="digest/win-x64-cpu",
        )
        task = DependencyCheckTask(tmp_path, client)

        # 连接信号以捕获结果
        finished_mock = Mock()
        task.signals.finished.connect(finished_mock)

        # 运行任务
        task.run()

        # 验证结果
        finished_mock.assert_called_once_with(True, [])
        client.inspect.assert_called_once_with()

    def test_task_run_not_ready(self, tmp_path, qapp):
        """测试任务运行（依赖未就绪）"""
        client = MagicMock()
        client.inspect.return_value = SimpleNamespace(
            ready=False,
            profile="win-x64-cpu",
            integrity="not-installed",
        )
        task = DependencyCheckTask(tmp_path, client)

        finished_mock = Mock()
        task.signals.finished.connect(finished_mock)

        task.run()

        finished_mock.assert_called_once_with(
            False,
            ["win-x64-cpu: not-installed"],
        )


class TestDependencyManager:
    """测试依赖管理器"""

    def test_manager_creation(self, qapp):
        """测试管理器创建"""
        manager = DependencyManager()
        assert manager is not None
        assert not manager.is_checking()
        assert not manager.is_ready()

    def test_manager_with_project_root(self, tmp_path, qapp):
        """测试指定项目根目录"""
        manager = DependencyManager(project_root=tmp_path)
        assert manager._project_root == tmp_path

    @patch("vibeocr.classic.managers.dependency_manager.DependencyCheckTask")
    def test_check_dependencies(self, mock_task_class, tmp_path, qapp):
        """测试检查依赖"""
        manager = DependencyManager(project_root=tmp_path)

        # 模拟任务
        mock_task = MagicMock()
        mock_task_class.return_value = mock_task

        # 连接信号
        started_mock = Mock()
        manager.check_started.connect(started_mock)

        # 检查依赖
        manager.check_dependencies()

        # 验证
        assert manager.is_checking()
        started_mock.assert_called_once()
        mock_task_class.assert_called_once_with(tmp_path, manager._client)

    def test_check_dependencies_prevents_duplicate(self, tmp_path, qapp):
        """测试防止重复检查"""
        manager = DependencyManager(project_root=tmp_path)
        manager._is_checking = True

        started_mock = Mock()
        manager.check_started.connect(started_mock)

        manager.check_dependencies()

        # 不应发出 started 信号
        started_mock.assert_not_called()

    def test_on_check_finished(self, qapp):
        """测试检查完成回调"""
        manager = DependencyManager()

        completed_mock = Mock()
        manager.check_completed.connect(completed_mock)

        # 模拟检查完成
        manager._on_check_finished(True, [])

        assert not manager.is_checking()
        assert manager.is_ready()
        completed_mock.assert_called_once_with(True, [])

    def test_get_missing_dependencies(self, qapp):
        """测试获取缺失依赖"""
        manager = DependencyManager()
        manager._missing_dependencies = ["paddlepaddle", "paddlex"]

        missing = manager.get_missing_dependencies()
        assert missing == ["paddlepaddle", "paddlex"]

        # 验证返回的是副本
        missing.append("other")
        assert manager._missing_dependencies == ["paddlepaddle", "paddlex"]

    def test_reset(self, qapp):
        """测试重置状态"""
        manager = DependencyManager()
        manager._is_checking = True
        manager._is_ready = True
        manager._missing_dependencies = ["paddlepaddle"]

        manager.reset()

        assert not manager.is_checking()
        assert not manager.is_ready()
        assert manager._missing_dependencies == []

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        manager = DependencyManager()
        assert hasattr(manager, "check_completed")
        assert hasattr(manager, "check_started")

    def test_shutdown_keeps_running_task_and_discards_late_result(
        self, qtbot, monkeypatch, tmp_path
    ):
        entered = threading.Event()
        release = threading.Event()

        def slow_check():
            entered.set()
            release.wait(timeout=2)
            return SimpleNamespace(
                ready=True,
                runtime_id="digest/win-x64-cpu",
            )

        manager = DependencyManager(project_root=tmp_path)
        monkeypatch.setattr(manager._client, "inspect", slow_check)
        completed = Mock()
        manager.check_completed.connect(completed)

        manager.check_dependencies()
        qtbot.waitUntil(entered.is_set, timeout=1000)
        assert len(manager._tasks) == 1

        manager.request_shutdown()
        assert len(manager._tasks) == 1
        assert manager.is_drained() is False

        release.set()
        qtbot.waitUntil(manager.is_drained, timeout=2000)
        assert manager._tasks == set()
        assert manager._thread_pool.activeThreadCount() == 0
        assert manager.is_ready() is False
        completed.assert_not_called()
