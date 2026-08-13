"""Pytest configuration and fixtures for VibeOCR tests."""

import asyncio
import os

# ---------------------------------------------------------------------------
# Windows + PaddlePaddle + Torch OpenMP 冲突兜底（必须在任何可能 import
# paddle/torch 的测试之前设置）。
#
# 全量 pytest 会依次触发 paddle（含 libiomp5md.dll）与 torch（含另一份
# libiomp5md.dll）的 DLL 加载。Windows 加载器遇到第二份冲突的 OpenMP 时抛
# ENTRYPOINT_NOT_FOUND (0xc0000139) 致命异常（在 torch._load_dll_libraries 处），
# 可能杀死整个 pytest 进程或留下后台线程崩溃的诊断噪音（行为非确定）。
#
# 生产环境 vibeocr.classic.main 顶部已设置同样的兜底；测试进程不经过 main.py，
# 故在此复制。KMP_DUPLICATE_LIB_OK=TRUE 让 Intel OpenMP 运行时容忍重复加载，
# 减少致命冲突。另见 test_ocr_service.py 的 PADDLE_TORCH_CONFLICT 跳过（治本）。
# ---------------------------------------------------------------------------
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import io
import sys
from pathlib import Path

import pytest

# Add all physical workspace source roots to this process and spawned workers.
repo_root = Path(__file__).parent.parent
source_paths = [
    repo_root / "packages/vibeocr-contracts-py/src",
    repo_root / "packages/vibeocr-runtime-client-py/src",
    repo_root / "apps/vibeocr-pyside/src",
]
for source_path in reversed(source_paths):
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))
existing_pythonpath = os.environ.get("PYTHONPATH", "")
pythonpath_parts = [str(path) for path in source_paths]
if existing_pythonpath:
    pythonpath_parts.append(existing_pythonpath)
os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)


@pytest.fixture(scope="session", autouse=True)
def isolate_application_log_root(tmp_path_factory):
    """Route process-wide application logs outside the repository during tests."""
    from types import SimpleNamespace

    from vibeocr.classic.services import log_service

    original_get_active_app_paths = log_service.get_active_app_paths
    test_install_root = tmp_path_factory.mktemp("vibeocr-test-install-root")
    log_service.get_active_app_paths = lambda: SimpleNamespace(
        data_root=test_install_root / "data"
    )
    try:
        yield
    finally:
        log_service.get_active_app_paths = original_get_active_app_paths


@pytest.fixture(scope="session")
def qapp():
    """提供 QApplication 实例（GUI 测试必需）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def pytest_sessionfinish(session, exitstatus):
    """Close the process-wide WorkerHost before pytest joins executor threads."""
    from vibeocr.classic.client import shutdown_backend_client

    shutdown_backend_client()


@pytest.fixture
def sample_pixmap():
    """提供测试用 QPixmap（100x50 白色图片）。"""
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(100, 50)
    pixmap.fill()
    return pixmap


@pytest.fixture
def sample_image_bytes():
    """提供测试图片的字节数据。"""
    from PIL import Image

    img = Image.new("RGB", (100, 50), color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def sample_image_with_text_bytes():
    """提供包含简单文字的测试图片字节数据。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 100), color="white")
    draw = ImageDraw.Draw(img)
    # 使用默认字体绘制文字
    draw.text((10, 30), "Test OCR", fill="black")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def temp_image_file(tmp_path, sample_image_with_text_bytes):
    """提供临时图片文件路径。"""
    img_path = tmp_path / "test_image.png"
    img_path.write_bytes(sample_image_with_text_bytes)
    return img_path


@pytest.fixture
def wait_worker():
    """返回一个等待 QThread worker 完成的辅助函数。"""
    import time

    from PySide6.QtCore import QCoreApplication

    def _wait(worker, timeout=10000):
        start = time.monotonic()
        while not worker.isFinished():
            QCoreApplication.processEvents()
            worker.wait(50)
            if time.monotonic() - start > timeout / 1000:
                break
        QCoreApplication.processEvents()

    return _wait


# ---------------------------------------------------------------------------
# qasync 测试范式
#
# 仓库首个「Qt slot 异步 + qtbot 断言」组合。生产环境由 main.py 的
# create_qasync_event_loop 提前安装并 run_forever 一个 qasync.QEventLoop；
# 测试进程不经过 main.py，故需要本 fixture 在 qapp 上安装一个 qasync loop。
#
# qasync.QEventLoop 把 asyncio 与 Qt 事件循环融合。但 qasync.run_forever 内部
# 调 QApplication.exec（必须在主线程），无法放独立线程；而在主线程 run_forever
# 会阻塞测试。故采用「set loop + 显式推进」模式：loop 仅 set 不 running，
# 通过 wait_until_done 辅助反复调 loop.run_until_complete(asyncio.sleep(0))
# 单步推进协程。生产代码用 AsyncTaskRunner（_get_running_or_set_loop 兜底，
# set 即可），不依赖 running loop。
# ---------------------------------------------------------------------------


@pytest.fixture
def qasync_loop(qapp):
    """为当前 qapp 安装一个 qasync 事件循环（function 级，隔离干净）。

    loop 仅 set 不 run_forever（避免阻塞测试或触发「QApplication.exec 必须
    主线程」警告）。被测代码通过 AsyncTaskRunner 派发的任务用 wait_until_done
    辅助显式推进。每步 loop.run_until_complete(asyncio.sleep(0)) 既推进
    asyncio 任务又经 qasync 触发 Qt 事件处理。
    """
    import asyncio

    from vibeocr.classic.utils.qt_async import create_qasync_event_loop

    loop = create_qasync_event_loop(qapp)
    asyncio.set_event_loop(loop)

    try:
        yield loop
    finally:
        # 全局 AsyncTaskRunner 是模块级单例，测试间共享。本测试派发的 task 绑定
        # 在当前 loop 上，若残留会污染后续测试（尤其 test_qt_async 复用该单例）。
        # close loop 前先取消所有未完成 task，避免跨 loop 残留。
        try:
            from vibeocr.classic.utils.qt_async import get_async_runner

            runner = get_async_runner()
            runner.cancel_all()
            # ``Task.cancel()`` only schedules cancellation callbacks.  Drain
            # them before closing the function-scoped loop so wrapper and
            # caller coroutines both reach terminal state.
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            runner._tasks.clear()
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        # 避免关闭的 loop 污染后续测试的线程当前 loop
        asyncio.set_event_loop(None)


def wait_until_done(qtbot, loop, condition, *, timeout_ms: int = 2000):
    """反复推进 qasync loop + Qt 事件，直到 condition() 为真或超时。

    单步用 loop.run_until_complete(asyncio.sleep(0)) 推进 asyncio 任务一步，
    再让 qtbot.waitUntil 的小窗口泵 Qt 事件。两者交替直到 condition 满足。

    Args:
        qtbot: pytest-qt 的 qtbot fixture。
        loop: qasync_loop fixture yield 的 qasync 事件循环。
        condition: 无参 callable，返回 bool；为 True 时结束。
        timeout_ms: 总超时毫秒。
    """
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    while not condition():
        if time.monotonic() > deadline:
            break
        # 推进 asyncio 任务一步（qasync 内部会处理 Qt 事件）
        loop.run_until_complete(asyncio.sleep(0))
        # 再让 qtbot 泵一小段 Qt 事件（处理 QTimer 等）
        qtbot.waitUntil(lambda: condition() or time.monotonic() > deadline, timeout=50)
    assert condition(), f"等待条件在 {timeout_ms}ms 内未满足"
