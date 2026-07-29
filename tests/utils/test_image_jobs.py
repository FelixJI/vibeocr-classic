"""``vibeocr.classic.utils.image_jobs`` 纯 worker 函数测试。

这些函数都在调用方线程中执行（不启动 QThread），输入是 QImage / 路径 / bytes，
配合 cancel_event 验证取消与错误路径。小真 PNG 用 Qt 直接写到 tmp_path。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QImage

from vibeocr.classic.utils.image_jobs import (
    ClipboardPngResult,
    GenerationImageJobs,
    _discard_result_async,
    compose_screen_images,
    decode_image_bytes,
    decode_image_file,
    delete_files,
    save_image_file,
    write_clipboard_png,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_png(path: Path, width: int = 4, height: int = 4, color: str = "red") -> None:
    """用 Qt 写一个合法的小 PNG 到 path。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")


def _png_bytes(width: int = 4, height: int = 4, color: str = "blue") -> bytes:
    """返回一个合法小 PNG 的 bytes。"""
    from PySide6.QtCore import QBuffer, QByteArray

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


# -----------------------------------------------------------------------------
# decode_image_file
# -----------------------------------------------------------------------------


def test_decode_image_file_reads_valid_png(tmp_path):
    png = tmp_path / "img.png"
    _write_png(png, 5, 3)
    event = threading.Event()

    image = decode_image_file(str(png), event)

    assert not image.isNull()
    assert image.width() == 5
    assert image.height() == 3


def test_decode_image_file_bad_path_raises(tmp_path):
    event = threading.Event()
    with pytest.raises(ValueError, match="无法读取图片"):
        decode_image_file(str(tmp_path / "missing.png"), event)


def test_decode_image_file_cancelled_before_returns_empty(tmp_path):
    png = tmp_path / "img.png"
    _write_png(png)
    event = threading.Event()
    event.set()

    image = decode_image_file(str(png), event)
    assert image.isNull()


def test_decode_image_file_cancelled_after_read_returns_empty(tmp_path):
    png = tmp_path / "img.png"
    _write_png(png)
    event = threading.Event()

    # 通过 monkeypatch QImage.copy 让「读后取消」分支生效：在读之后 set event
    original_copy = QImage.copy

    def patched_copy(self, *args, **kwargs):
        event.set()
        return original_copy(self, *args, **kwargs)

    QImage.copy = patched_copy  # type: ignore[method-assign]
    try:
        image = decode_image_file(str(png), event)
    finally:
        QImage.copy = original_copy  # type: ignore[method-assign]

    assert image.isNull()


# -----------------------------------------------------------------------------
# decode_image_bytes
# -----------------------------------------------------------------------------


def test_decode_image_bytes_valid():
    event = threading.Event()
    data = _png_bytes(6, 2)

    image = decode_image_bytes(data, event)

    assert not image.isNull()
    assert image.width() == 6
    assert image.height() == 2


def test_decode_image_bytes_bad_raises():
    event = threading.Event()
    with pytest.raises(ValueError, match="无法解码预处理图片"):
        decode_image_bytes(b"not an image", event)


def test_decode_image_bytes_cancelled_before_returns_empty():
    event = threading.Event()
    event.set()
    image = decode_image_bytes(_png_bytes(), event)
    assert image.isNull()


# -----------------------------------------------------------------------------
# compose_screen_images
# -----------------------------------------------------------------------------


def test_compose_screen_images_draws_into_black_canvas():
    event = threading.Event()
    img1 = QImage(2, 2, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white"))
    img2 = QImage(3, 3, QImage.Format.Format_ARGB32)
    img2.fill(QColor("white"))
    images = [(QPoint(0, 0), img1), (QPoint(5, 5), img2)]
    physical = QSize(10, 10)

    result = compose_screen_images(images, physical, dpr=1.0, cancel_event=event)

    assert not result.isNull()
    assert result.size() == physical
    assert result.devicePixelRatio() == 1.0
    # 左上角 (0,0) 来自 img1 白色；画布其余部分为黑色
    assert result.pixelColor(0, 0) == QColor("white")
    assert result.pixelColor(9, 9) == QColor("black")


def test_compose_screen_images_cancelled_returns_empty():
    event = threading.Event()
    event.set()
    result = compose_screen_images([], QSize(5, 5), dpr=1.0, cancel_event=event)
    assert result.isNull()


def test_compose_screen_images_empty_physical_size_returns_empty():
    event = threading.Event()
    result = compose_screen_images([], QSize(0, 0), dpr=1.0, cancel_event=event)
    assert result.isNull()


def test_compose_screen_images_midway_cancel_returns_empty():
    event = threading.Event()

    class CancelAfterFirstDrawImage(QImage):
        """第一张画完后触发取消。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

    img1 = QImage(2, 2, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white"))

    # 第二张绘制前设 cancel
    img2 = QImage(2, 2, QImage.Format.Format_ARGB32)
    img2.fill(QColor("white"))
    images = [(QPoint(0, 0), img1), (QPoint(3, 0), img2)]

    # 包裹 drawImage：画完第一张后 set cancel
    painter_draw_hit = {"count": 0}
    from PySide6.QtGui import QPainter

    original_draw = QPainter.drawImage

    def patched_draw(self, target, image, *args, **kwargs):
        painter_draw_hit["count"] += 1
        if painter_draw_hit["count"] >= 1:
            event.set()
        return original_draw(self, target, image, *args, **kwargs)

    QPainter.drawImage = patched_draw  # type: ignore[method-assign]
    try:
        result = compose_screen_images(images, QSize(8, 4), dpr=1.0, cancel_event=event)
    finally:
        QPainter.drawImage = original_draw  # type: ignore[method-assign]

    assert result.isNull()


# -----------------------------------------------------------------------------
# write_clipboard_png
# ----------------------------------------------------------------------------


def test_write_clipboard_png_writes_file_and_trims_kept(tmp_path):
    event = threading.Event()
    image = QImage(3, 3, QImage.Format.Format_ARGB32)
    image.fill(QColor("green"))

    # 模拟两条已不存在的旧路径 + 一条仍存在
    existing = [tmp_path / "old1.png", tmp_path / "old2.png"]
    real_old = tmp_path / "real_old.png"
    _write_png(real_old, 1, 1)
    existing.append(real_old)

    result = write_clipboard_png(image, existing, max_files=2, cancel_event=event)

    assert isinstance(result, ClipboardPngResult)
    assert result.image is image
    assert result.path.exists()
    # kept 已剔除不存在的旧路径，保留真实 old，加入新 path，并裁剪到 max_files=2
    assert real_old in result.kept_paths
    assert result.path in result.kept_paths
    assert tmp_path / "old1.png" not in result.kept_paths
    assert len(result.kept_paths) <= 2
    # 超出 max_files 的最旧文件已被 unlink
    assert len(result.kept_paths) == 2


def test_write_clipboard_png_discard_unlinks():
    event = threading.Event()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue"))

    result = write_clipboard_png(image, [], max_files=10, cancel_event=event)
    assert result.path.exists()

    result.discard()
    assert not result.path.exists()


def test_write_clipboard_png_cancelled_before_returns_none(tmp_path):
    event = threading.Event()
    event.set()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)

    assert write_clipboard_png(image, [], max_files=1, cancel_event=event) is None


def test_write_clipboard_png_cancelled_after_encode_unlinks(tmp_path):
    """encode 后取消：应删除已写出的临时文件并返回 None。"""
    event = threading.Event()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))

    # 让 save 完成后 set event：通过包裹 QImage.save
    original_save = QImage.save

    def patched_save(self, *args, **kwargs):
        ok = original_save(self, *args, **kwargs)
        event.set()
        return ok

    QImage.save = patched_save  # type: ignore[method-assign]
    try:
        result = write_clipboard_png(image, [], max_files=1, cancel_event=event)
    finally:
        QImage.save = original_save  # type: ignore[method-assign]

    assert result is None


# -----------------------------------------------------------------------------
# save_image_file
# -----------------------------------------------------------------------------


def test_save_image_file_writes_and_returns_path(tmp_path):
    event = threading.Event()
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("black"))
    target = tmp_path / "out.png"

    returned = save_image_file(image, str(target), event)

    assert returned == str(target)
    assert target.exists()


def test_save_image_file_cancelled_returns_empty(tmp_path):
    event = threading.Event()
    event.set()
    target = tmp_path / "out.png"
    assert save_image_file(
        QImage(2, 2, QImage.Format.Format_ARGB32), str(target), event
    ) == ""
    assert not target.exists()


def test_save_image_file_failure_raises_oserror(tmp_path):
    """写入不存在的目录 → save 失败 → OSError。"""
    event = threading.Event()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    bad_path = str(tmp_path / "no_such_dir" / "out.png")
    with pytest.raises(OSError, match="保存图片失败"):
        save_image_file(image, bad_path, event)


# -----------------------------------------------------------------------------
# delete_files
# -----------------------------------------------------------------------------


def test_delete_files_removes_all(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("a", encoding="utf-8")
    f2.write_text("b", encoding="utf-8")
    event = threading.Event()

    assert delete_files([f1, f2], event) is True
    assert not f1.exists()
    assert not f2.exists()


def test_delete_files_missing_ok(tmp_path):
    """单个文件不存在不中止后续删除。"""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f2.write_text("b", encoding="utf-8")
    event = threading.Event()

    assert delete_files([f1, f2], event) is True
    assert not f2.exists()


def test_delete_files_cancel_returns_false(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("a", encoding="utf-8")
    event = threading.Event()
    event.set()

    assert delete_files([f1], event) is False
    assert f1.exists()  # 取消在删除前，文件仍在


# =============================================================================
# GenerationImageJobs：generation 丢弃、close/cancel/drain、_on_finished/failed
# 这些用 QThreadPool，需要 qapp 事件循环推进。
# =============================================================================


def _process_events_until(qapp, predicate, *, timeout_ms=2000):
    """泵 Qt 事件循环直到 predicate 为真或超时。"""
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        if time.monotonic() > deadline:
            return False
        qapp.processEvents()
        time.sleep(0.005)
    return True


@pytest.fixture(autouse=True)
def _drain_global_thread_pool_after_each(qapp):
    """每个用例后等全局 QThreadPool 排空，避免残留 _Job/_DiscardJob 与后续
    test_subprocess_manager 的 QThread 测试交叉导致 access violation。"""
    yield
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(2000)
    qapp.processEvents()


def test_generation_image_jobs_submit_completes_and_emits(qapp):
    jobs = GenerationImageJobs()
    completed: list[tuple[int, object]] = []
    jobs.completed.connect(lambda gen, result: completed.append((gen, result)))

    gen = jobs.submit(lambda cancel: "result-1")

    assert gen == 1
    assert jobs.generation == 1
    assert _process_events_until(qapp, lambda: bool(completed))
    assert completed == [(1, "result-1")]
    # 完成后无活跃 job
    assert _process_events_until(qapp, lambda: not jobs.is_running)


def test_generation_image_jobs_submit_after_close_returns_current_generation(qapp):
    jobs = GenerationImageJobs()
    jobs.close()

    gen = jobs.submit(lambda cancel: "x")
    # closing 时不真正提交，返回当前 generation
    assert gen == jobs.generation
    assert jobs.is_running is False


def test_generation_image_jobs_second_submit_cancels_first(qapp):
    """新 submit 取消旧 generation，旧结果被丢弃（_on_finished 走 discard 分支）。"""
    jobs = GenerationImageJobs()
    completed: list = []
    jobs.completed.connect(lambda *args: completed.append(args))

    release = threading.Event()
    entered = threading.Event()

    def slow_op(cancel):
        entered.set()
        release.wait(timeout=2)
        return "first"

    jobs.submit(slow_op)
    assert _process_events_until(qapp, entered.is_set)

    # 提交第二个 → 取消第一个
    gen2 = jobs.submit(lambda cancel: "second")
    assert gen2 == 2
    release.set()

    # 第一个被取消/过期，不发射 completed；第二个正常完成
    assert _process_events_until(qapp, lambda: any(g == 2 for g, _ in completed))
    first_emits = [r for g, r in completed if r == "first"]
    assert first_emits == []


def test_generation_image_jobs_failed_signal(qapp):
    """operation 抛异常 → failed 信号。"""
    jobs = GenerationImageJobs()
    failures: list[tuple[int, str]] = []
    jobs.failed.connect(lambda gen, err: failures.append((gen, err)))

    def broken(_cancel):
        raise RuntimeError("boom")

    gen = jobs.submit(broken)
    assert _process_events_until(qapp, lambda: bool(failures))
    assert failures[0][0] == gen
    assert "boom" in failures[0][1]


def test_generation_image_jobs_cancel_current_invalidates(qapp):
    """cancel_current 使当前 generation 失效，结果被丢弃。"""
    jobs = GenerationImageJobs()
    completed: list = []
    jobs.completed.connect(lambda *args: completed.append(args))

    release = threading.Event()
    entered = threading.Event()

    def slow_op(cancel):
        entered.set()
        release.wait(timeout=2)
        return "done"

    gen1 = jobs.submit(slow_op)
    assert _process_events_until(qapp, entered.is_set)

    jobs.cancel_current()
    assert jobs.generation > gen1
    release.set()

    # 过期 generation 的结果走 discard，不发 completed
    _process_events_until(qapp, lambda: not jobs.is_running, timeout_ms=1000)
    assert completed == []


def test_generation_image_jobs_close_discards_inflight(qapp):
    """close() 后在跑的 job 结果走 discard 分支，不发 completed。"""
    jobs = GenerationImageJobs()
    completed: list = []
    jobs.completed.connect(lambda *args: completed.append(args))

    release = threading.Event()
    entered = threading.Event()

    def slow_op(cancel):
        entered.set()
        release.wait(timeout=2)
        return "done"

    jobs.submit(slow_op)
    assert _process_events_until(qapp, entered.is_set)

    jobs.close()
    release.set()

    _process_events_until(qapp, lambda: not jobs.is_running, timeout_ms=1000)
    # close 后结果被丢弃
    assert completed == []


def test_generation_image_jobs_drain_waits_for_done(qapp):
    """drain 等待 worker 的 done_event。"""
    jobs = GenerationImageJobs()
    release = threading.Event()

    def slow_op(cancel):
        release.wait(timeout=2)
        return "done"

    jobs.submit(slow_op)
    release.set()
    # drain 应等到 job 完成
    assert jobs.drain(timeout_ms=2000)
    _process_events_until(qapp, lambda: not jobs.is_running, timeout_ms=1000)


def test_generation_image_jobs_drain_no_jobs_returns_true(qapp):
    jobs = GenerationImageJobs()
    assert jobs.drain(timeout_ms=100) is True


def test_discard_result_async_no_discard_method(qapp):
    """无 discard 属性的对象 → 直接返回，不抛。"""
    obj = object()  # 无 discard
    # 不抛异常
    _discard_result_async(obj)
    # 让 QThreadPool 处理 _DiscardJob
    qapp.processEvents()


def test_discard_result_async_with_discard_method(qapp, tmp_path):
    """有 discard 属性 → 排到 QThreadPool 异步执行 discard。"""
    f = tmp_path / "tmp.txt"
    f.write_text("x", encoding="utf-8")

    class Result:
        def discard(self):
            f.unlink(missing_ok=True)

    _discard_result_async(Result())
    # 等 _DiscardJob 在 QThreadPool 跑完
    import time
    deadline = time.monotonic() + 2
    while f.exists() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not f.exists()
