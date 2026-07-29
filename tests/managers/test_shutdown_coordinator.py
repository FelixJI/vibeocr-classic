"""测试 ShutdownCoordinator 有序 drain。

按固定顺序 drain 各子系统，避免关闭时后台任务仍在访问已释放的资源。
os._exit 仍保留为 DLL 卸载安全网，但在其之前由本协调器尽力收拢任务。
"""

import threading


class TestShutdownCoordinator:
    def test_coordinate_calls_in_order(self):
        """coordinator 按注册顺序调用各子系统的 shutdown"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []

        coord.register("settings", lambda: order.append("settings"))
        coord.register("pdf", lambda: order.append("pdf"))
        coord.register("subprocess", lambda: order.append("subprocess"))
        coord.register("async_runner", lambda: order.append("async_runner"))

        result = coord.coordinate(timeout_ms=3000)

        assert result is True
        assert order == ["settings", "pdf", "subprocess", "async_runner"]

    def test_coordinate_returns_false_on_timeout(self):
        """步骤超时后可显式允许独立后续步骤继续。"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []
        release = threading.Event()
        coord.register("slow", lambda: release.wait(2), max_timeout_ms=50)
        coord.register("fast", lambda: order.append("fast"))

        result = coord.coordinate(timeout_ms=100)

        # 即使 slow 超时，也返回 False（非完全成功）
        assert result is False
        assert order == ["fast"]
        release.set()

    def test_coordinate_continues_after_exception(self):
        """某子系统抛异常，coordinator 记录但继续后续"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []

        def boom():
            raise RuntimeError("boom")

        coord.register("crash", boom)
        coord.register("after", lambda: order.append("after"))

        result = coord.coordinate(timeout_ms=1000)

        assert result is False  # 有异常
        assert order == ["after"]  # 后续仍执行

    def test_coordinate_empty_returns_true(self):
        """无注册步骤时返回 True"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        assert coord.coordinate(timeout_ms=1000) is True

    def test_fast_step_preserves_remaining_global_budget(self):
        """总预算按绝对截止时间扣减，不再机械均分。"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        coord.register("a", lambda: None, max_timeout_ms=20)
        coord.register("b", lambda: None, max_timeout_ms=250)

        result = coord.coordinate(timeout_ms=300)
        assert result is True
        assert coord.results[0].allowance_ms <= 20
        # a 几乎立即完成，b 获得接近全部剩余预算，而不是固定 150ms。
        assert coord.results[1].allowance_ms > 200

    def test_dependent_steps_stop_after_timeout(self):
        """资源相关步骤可禁止在前一步仍运行时继续，避免并发清理。"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []
        release = threading.Event()
        coord.register(
            "owner",
            lambda: release.wait(1),
            max_timeout_ms=20,
            continue_on_timeout=False,
        )
        coord.register("dependent", lambda: order.append("dependent"))

        assert coord.coordinate(timeout_ms=200) is False
        assert order == []
        assert coord.results[-1].status == "timeout"
        release.set()

    def test_incomplete_drain_stops_dependent_cleanup(self):
        """显式 False 表示 worker 仍活，不能继续销毁其依赖资源。"""
        from vibeocr.classic.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order: list[str] = []
        coord.register(
            "drain",
            lambda: False,
            max_timeout_ms=50,
            continue_on_timeout=False,
        )
        coord.register("dependent", lambda: order.append("dependent"))

        assert coord.coordinate(timeout_ms=100) is False
        assert order == []
        assert coord.results[-1].status == "failed"
