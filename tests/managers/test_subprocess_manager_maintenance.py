"""跨入口维护协调器测试：串行化、重新失效与探针兜底。

多个 owner（首启自动安装 vs 设置页运行环境变更）的维护请求必须由
SubprocessManager 串行化：同一时刻只有一个流程持有许可；持有者结束后
下一个请求重新执行自己的失效，而不是消费别人的完成广播。
"""

import pytest

from vibeocr.classic.managers.subprocess_manager import SubprocessManager


@pytest.fixture
def coordinator(qapp, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(SubprocessManager, "_MAINTENANCE_PROBE_INTERVAL_MS", 10)
    manager = SubprocessManager(tmp_path)
    state = {"grants": [], "invalidates": 0, "a_active": True, "b_active": True}

    def _invalidate() -> bool:
        state["invalidates"] += 1
        return True

    monkeypatch.setattr(manager, "invalidate_supervisor", _invalidate)

    def _owner(name: str, active_key: str):
        # 模拟真实 owner：获得许可后执行自己的失效。
        return lambda: (
            state["grants"].append(name),
            manager.invalidate_supervisor(),
        ), (lambda: state[active_key])

    state["make_owner"] = _owner
    return manager, state


def _wait_until(qtbot, predicate, timeout_ms: int = 5000) -> None:
    qtbot.waitUntil(predicate, timeout=timeout_ms)


def test_maintenance_requests_serialize_with_fresh_invalidation(
    coordinator, qtbot
) -> None:
    manager, state = coordinator
    make_owner = state["make_owner"]

    grant_a, active_a = make_owner("A", "a_active")
    grant_b, active_b = make_owner("B", "b_active")
    manager.acquire_maintenance(grant_a, is_active=active_a)
    manager.acquire_maintenance(grant_b, is_active=active_b)

    # A 立即获得许可并失效；B 在 A 结束前保持排队。
    assert state["grants"] == ["A"]
    assert state["invalidates"] == 1

    state["a_active"] = False
    _wait_until(qtbot, lambda: state["grants"] == ["A", "B"])

    # B 获得许可后必须重新执行自己的失效（A 的流程可能已重启 Supervisor）。
    assert state["invalidates"] == 2

    state["b_active"] = False
    _wait_until(qtbot, lambda: not manager._maintenance_busy)
    assert manager._maintenance_queue == []


def test_maintenance_probe_failure_releases_permit(coordinator, qtbot) -> None:
    """探针抛异常时按已结束处理，许可不能永久滞留。"""
    manager, state = coordinator
    make_owner = state["make_owner"]

    def _broken_probe() -> bool:
        raise RuntimeError("probe boom")

    grant_a, _ = make_owner("A", "a_active")
    grant_b, active_b = make_owner("B", "b_active")
    manager.acquire_maintenance(grant_a, is_active=_broken_probe)
    manager.acquire_maintenance(grant_b, is_active=active_b)

    assert state["grants"] == ["A"]

    _wait_until(qtbot, lambda: state["grants"] == ["A", "B"])
    assert state["invalidates"] == 2

    state["b_active"] = False
    _wait_until(qtbot, lambda: not manager._maintenance_busy)


def test_maintenance_permit_released_when_request_never_becomes_active(
    coordinator, qtbot
) -> None:
    """获得许可但请求终止（未开任何维护 UI）时，许可随探针回落释放。"""
    manager, state = coordinator
    make_owner = state["make_owner"]

    grant_a, _ = make_owner("A", "a_active")
    grant_b, active_b = make_owner("B", "b_active")
    manager.acquire_maintenance(grant_a, is_active=lambda: False)
    manager.acquire_maintenance(grant_b, is_active=active_b)

    assert state["grants"] == ["A"]
    _wait_until(qtbot, lambda: state["grants"] == ["A", "B"])
    assert state["invalidates"] == 2
