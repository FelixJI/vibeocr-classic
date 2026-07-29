"""ExternalShutdownJob 测试（QThread.run 纯逻辑）。"""

from vibeocr.classic.utils.shutdown_jobs import ExternalShutdownJob


def test_run_executes_all_operations_in_order(qapp):
    """run() 顺序执行所有操作（line 27-29）。"""
    calls = []

    job = ExternalShutdownJob(
        (
            ("op1", lambda: calls.append("a")),
            ("op2", lambda: calls.append("b")),
        )
    )
    job.run()  # 直接调 run，不启动线程
    assert calls == ["a", "b"]
    assert job.errors == ()


def test_run_collects_errors(qapp):
    """operation 抛异常时收集到 errors（line 30-31）。"""
    job = ExternalShutdownJob(
        (
            ("ok", lambda: None),
            ("boom", lambda: (_ for _ in ()).throw(RuntimeError("failed"))),
        )
    )
    job.run()
    assert len(job.errors) == 1
    assert job.errors[0][0] == "boom"
    assert "failed" in job.errors[0][1]


def test_run_empty_operations(qapp):
    """空操作列表 run 不报错（line 27 循环不执行）。"""
    job = ExternalShutdownJob(())
    job.run()
    assert job.errors == ()
