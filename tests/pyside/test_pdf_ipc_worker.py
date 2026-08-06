"""``vibeocr.classic.pyside.pdf_ipc_worker`` 易测单元：_call_op 派发 + close/cancel run。

_call_op 用 ``__new__`` 绕过 QThread 构造，直接设字段调方法；close/cancel
worker 的 run() 直接同步调用（不 start QThread），用 MagicMock client 验证
正确 client 方法被调与信号发射。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibeocr.classic.pyside.pdf_ipc_worker import (
    PdfIpcCancelWorker,
    PdfIpcCloseWorker,
    PdfIpcMutateWorker,
)

# =============================================================================
# PdfIpcMutateWorker._call_op —— op → client 方法派发（~25 行大块）
# =============================================================================


def _make_mutate_worker(op: str, params: dict) -> PdfIpcMutateWorker:
    """绕过 QThread.__init__，直接构造一个仅用于 _call_op 测试的 worker。"""
    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    worker._client = MagicMock()
    worker._session_id = "sid"
    worker._op = op
    worker._params = params
    return worker


def test_call_op_rotate():
    worker = _make_mutate_worker("rotate", {"pages": [0, 1], "angle": 90})
    worker._call_op()
    worker._client.rotate.assert_called_once_with("sid", [0, 1], 90)


def test_call_op_delete_pages():
    worker = _make_mutate_worker("delete_pages", {"pages": [2]})
    worker._call_op()
    worker._client.delete_pages.assert_called_once_with("sid", [2])


def test_call_op_insert_blank_with_defaults():
    worker = _make_mutate_worker("insert_blank", {"after_index": 0})
    worker._call_op()
    # 缺省 width=612.0 / height=792.0
    worker._client.insert_blank.assert_called_once_with("sid", 0, 612.0, 792.0)


def test_call_op_insert_blank_with_custom_dims():
    worker = _make_mutate_worker(
        "insert_blank", {"after_index": 1, "width": 100.0, "height": 200.0}
    )
    worker._call_op()
    worker._client.insert_blank.assert_called_once_with("sid", 1, 100.0, 200.0)


def test_call_op_insert_from():
    worker = _make_mutate_worker(
        "insert_from", {"source_path": "/src.pdf", "after_index": 3}
    )
    worker._call_op()
    worker._client.insert_from.assert_called_once_with("sid", "/src.pdf", 3)


def test_call_op_move_page():
    worker = _make_mutate_worker("move_page", {"from_index": 0, "to_index": 2})
    worker._call_op()
    worker._client.move_page.assert_called_once_with("sid", 0, 2)


def test_call_op_reorder():
    worker = _make_mutate_worker("reorder", {"new_order": [2, 0, 1]})
    worker._call_op()
    worker._client.reorder.assert_called_once_with("sid", [2, 0, 1])


def test_call_op_save():
    worker = _make_mutate_worker("save", {"path": "/out.pdf", "pdf_settings": {"a": 1}})
    worker._call_op()
    worker._client.save.assert_called_once_with("sid", "/out.pdf", {"a": 1})


def test_call_op_save_with_none_path_and_settings():
    worker = _make_mutate_worker("save", {})
    worker._call_op()
    worker._client.save.assert_called_once_with("sid", None, None)


def test_call_op_add_text_layer():
    worker = _make_mutate_worker(
        "add_text_layer",
        {
            "page": 0,
            "ocr_result": {"blocks": []},
            "pdf_settings": {"x": 1},
            "overwrite": True,
        },
    )
    worker._call_op()
    worker._client.add_text_layer.assert_called_once_with(
        "sid", 0, {"blocks": []}, {"x": 1}, True
    )


def test_call_op_add_text_layer_default_overwrite():
    worker = _make_mutate_worker("add_text_layer", {"page": 1, "ocr_result": {}})
    worker._call_op()
    worker._client.add_text_layer.assert_called_once_with("sid", 1, {}, None, False)


def test_call_op_rewrite_text_layer():
    worker = _make_mutate_worker(
        "rewrite_text_layer",
        {"page": 2, "text_blocks": [], "preproc_angle": 90, "pdf_settings": {"s": 1}},
    )
    worker._call_op()
    worker._client.rewrite_text_layer.assert_called_once_with(
        "sid", 2, [], 90, {"s": 1}
    )


def test_call_op_rewrite_text_layer_defaults():
    worker = _make_mutate_worker("rewrite_text_layer", {"page": 0, "text_blocks": [1]})
    worker._call_op()
    worker._client.rewrite_text_layer.assert_called_once_with("sid", 0, [1], 0, None)


def test_call_op_update_block_text():
    worker = _make_mutate_worker(
        "update_block_text", {"page": 0, "block_index": 1, "new_text": "hi"}
    )
    worker._call_op()
    worker._client.update_block_text.assert_called_once_with("sid", 0, 1, "hi")


def test_call_op_unknown_op_raises():
    worker = _make_mutate_worker("bogus", {})
    with pytest.raises(ValueError, match="未知 op"):
        worker._call_op()


# =============================================================================
# PdfIpcMutateWorker.run —— delete_text_layers 流式分支 + reset_cancel 失败吞
# （顺带覆盖 run() 的流式分支与 all_done 发射，不做完整线程编排）
# =============================================================================


def test_mutate_run_delete_text_layers_streams_and_emits_all_done(monkeypatch):
    """run() delete_text_layers 分支：迭代流 + get_model → all_done。"""
    from vibeocr.runtime_contracts.pdf import PdfDocumentMirror, PdfModelDiff

    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    client = MagicMock()
    # reset_cancel 失败被吞（验证 except 分支）
    client.reset_cancel.side_effect = RuntimeError("reset failed")
    # 流式事件
    progress_events = [
        SimpleNamespaceProgress(
            page_index=0, current=1, total=2, page_payload={"p": 0}
        ),
        SimpleNamespaceProgress(
            page_index=1, current=2, total=2, page_payload={"p": 1}
        ),
    ]
    client.delete_text_layers_stream.return_value = iter(progress_events)
    full_model = PdfDocumentMirror(file_path="/f.pdf", pages=[])
    client.get_model.return_value = full_model

    worker._client = client
    worker._session_id = "sid"
    worker._op = "delete_text_layers"
    worker._params = {"pages": [0, 1]}
    worker._cancelled = False

    page_done_emits: list = []
    progress_emits: list = []
    all_done_emits: list = []
    failed_emits: list = []
    worker.page_done = MagicMock()
    worker.page_done.connect = lambda *a, **k: None
    worker.page_done.emit = lambda *a: page_done_emits.append(a)
    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: progress_emits.append(a)
    worker.all_done = MagicMock()
    worker.all_done.emit = lambda *a: all_done_emits.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed_emits.append(a)

    worker.run()

    # 流式事件被逐页转发
    assert len(page_done_emits) == 2
    assert page_done_emits[0] == ("sid", 0, {"p": 0})
    assert progress_emits == [("sid", 1, 2), ("sid", 2, 2)]
    # all_done 携带 ModelDiff + residual_pages
    assert len(all_done_emits) == 1
    sid, diff, extra = all_done_emits[0]
    assert sid == "sid"
    assert isinstance(diff, PdfModelDiff)
    assert extra == {"residual_pages": []}
    assert failed_emits == []


class SimpleNamespaceProgress:
    """模拟 ProgressEvent（run() 用属性访问 page_index/current/total/page_payload）。"""

    def __init__(self, *, page_index, current, total, page_payload):
        self.page_index = page_index
        self.current = current
        self.total = total
        self.page_payload = page_payload
        self.message = ""


def test_mutate_run_handles_exception_and_emits_failed():
    """run() 主路径抛异常 → failed 信号。"""
    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    client = MagicMock()
    client.rotate.side_effect = RuntimeError("rotate broke")

    worker._client = client
    worker._session_id = "sid"
    worker._op = "rotate"
    worker._params = {"pages": [0], "angle": 90}
    worker._cancelled = False

    failed_emits: list = []
    all_done_emits: list = []
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed_emits.append(a)
    worker.all_done = MagicMock()
    worker.all_done.emit = lambda *a: all_done_emits.append(a)
    worker.page_done = MagicMock()
    worker.progress = MagicMock()

    worker.run()

    assert len(failed_emits) == 1
    assert failed_emits[0][0] == "sid"
    assert "rotate broke" in failed_emits[0][1]
    assert all_done_emits == []


def test_mutate_run_non_stream_emits_all_done_with_path():
    """run() 非流式：resp 含 path 属性 → extra 用 {path}。"""
    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    client = MagicMock()
    resp = MagicMock()
    resp.diff = MagicMock()
    resp.extra = None
    resp.path = "/out.pdf"  # hasattr(resp, "path") → True
    client.save.return_value = resp

    worker._client = client
    worker._session_id = "sid"
    worker._op = "save"
    worker._params = {"path": "/out.pdf", "pdf_settings": None}
    worker._cancelled = False

    all_done_emits: list = []
    worker.all_done = MagicMock()
    worker.all_done.emit = lambda *a: all_done_emits.append(a)
    worker.failed = MagicMock()
    worker.page_done = MagicMock()
    worker.progress = MagicMock()

    worker.run()

    assert len(all_done_emits) == 1
    sid, diff, extra = all_done_emits[0]
    assert sid == "sid"
    assert diff is resp.diff
    assert extra == {"path": "/out.pdf"}


def test_mutate_run_uses_protocol_operation_extra() -> None:
    from vibeocr.runtime_contracts.pdf import PdfMutationResult

    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    client = MagicMock()
    response = PdfMutationResult.from_payload(
        {
            "schema_version": 2,
            "instance_id": "runtime-1",
            "diff": {},
            "extra": {"corrected_pages": [1]},
            "future_optional": True,
        }
    )
    client.rotate.return_value = response
    worker._client = client
    worker._session_id = "sid"
    worker._op = "rotate"
    worker._params = {"pages": [1], "angle": 90}
    worker._cancelled = False
    emitted: list[tuple[object, ...]] = []
    worker.all_done = MagicMock()
    worker.all_done.emit = lambda *args: emitted.append(args)
    worker.failed = MagicMock()
    worker.page_done = MagicMock()
    worker.progress = MagicMock()

    worker.run()

    assert emitted == [
        ("sid", response.diff, {"corrected_pages": [1]}),
    ]
    assert response.extra == {"future_optional": True}


def test_mutate_run_cancelled_before_op_returns_without_emit():
    """reset_cancel 后 _cancelled=True → 直接 return，不发 all_done/failed。"""
    worker = PdfIpcMutateWorker.__new__(PdfIpcMutateWorker)
    client = MagicMock()

    worker._client = client
    worker._session_id = "sid"
    worker._op = "rotate"
    worker._params = {"pages": [0], "angle": 90}
    worker._cancelled = True

    all_done_emits: list = []
    failed_emits: list = []
    worker.all_done = MagicMock()
    worker.all_done.emit = lambda *a: all_done_emits.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed_emits.append(a)
    worker.page_done = MagicMock()
    worker.progress = MagicMock()

    worker.run()

    assert all_done_emits == []
    assert failed_emits == []
    # 业务 op 未被调用
    client.rotate.assert_not_called()


# =============================================================================
# PdfIpcCloseWorker.run —— MagicMock client，断言 completed emit
# =============================================================================


def test_close_worker_run_emits_completed():
    worker = PdfIpcCloseWorker.__new__(PdfIpcCloseWorker)
    client = MagicMock()
    worker._client = client
    worker._session_id = "sid"

    emitted: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: emitted.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: emitted.append(("failed", *a))

    worker.run()

    client.close_session.assert_called_once_with("sid")
    assert emitted == [("sid",)]


def test_close_worker_run_emits_failed_on_exception():
    worker = PdfIpcCloseWorker.__new__(PdfIpcCloseWorker)
    client = MagicMock()
    client.close_session.side_effect = RuntimeError("net down")
    worker._client = client
    worker._session_id = "sid"

    completed: list = []
    failed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed.append(a)

    worker.run()

    assert completed == []
    assert failed == [("sid", "net down")]


def test_close_worker_session_id_property():
    worker = PdfIpcCloseWorker.__new__(PdfIpcCloseWorker)
    worker._session_id = "abc"
    assert worker.session_id == "abc"


# =============================================================================
# PdfIpcCancelWorker.run —— client.cancel；mock raise 验证吞异常
# =============================================================================


def test_cancel_worker_run_calls_client_cancel():
    worker = PdfIpcCancelWorker.__new__(PdfIpcCancelWorker)
    client = MagicMock()
    worker._client = client
    worker._session_id = "sid"

    # 不抛异常，正常退出
    worker.run()

    client.cancel.assert_called_once_with("sid")


def test_cancel_worker_run_swallows_exception():
    """client.cancel 抛异常 → run() 吞掉，不向上传播。"""
    worker = PdfIpcCancelWorker.__new__(PdfIpcCancelWorker)
    client = MagicMock()
    client.cancel.side_effect = RuntimeError("ignored")
    worker._client = client
    worker._session_id = "sid"

    # 不应抛出
    worker.run()
    client.cancel.assert_called_once_with("sid")


# =============================================================================
# MinerUPreflightWorker.run —— ensure_mineru_models 成功/失败/取消分支
# =============================================================================


def test_mineru_preflight_run_success_emits_progress_and_completed(monkeypatch):
    from vibeocr.classic.pyside.pdf_ipc_worker import MinerUPreflightWorker

    def fake_ensure(root, *, progress_callback=None):
        # 模拟底层通过 progress_callback 上报进度
        if progress_callback is not None:
            progress_callback("download", "50%")
            progress_callback("verify", "done")
        return True, "ok"

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", fake_ensure)
    monkeypatch.setattr("vibeocr.backend.env_manager.get_project_root", lambda: "/root")

    worker = MinerUPreflightWorker.__new__(MinerUPreflightWorker)
    worker._cancelled = False
    progress_emits: list = []
    completed_emits: list = []
    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: progress_emits.append(a)
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed_emits.append(a)

    worker.run()

    # progress callback 通过 report 转发（两条 stage 消息）
    assert ("download", "50%") in progress_emits
    assert ("verify", "done") in progress_emits
    assert completed_emits == [(True, "ok")]


def test_mineru_preflight_run_failure_emits_completed_false(monkeypatch):
    from vibeocr.classic.pyside.pdf_ipc_worker import MinerUPreflightWorker

    def fake_ensure(root, *, progress_callback=None):
        return False, "network down"

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", fake_ensure)
    monkeypatch.setattr("vibeocr.backend.env_manager.get_project_root", lambda: "/root")

    worker = MinerUPreflightWorker.__new__(MinerUPreflightWorker)
    worker._cancelled = False
    completed_emits: list = []
    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: None
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed_emits.append(a)

    worker.run()
    assert completed_emits == [(False, "network down")]


def test_mineru_preflight_run_exception_becomes_failure(monkeypatch):
    from vibeocr.classic.pyside.pdf_ipc_worker import MinerUPreflightWorker

    def fake_ensure(root, *, progress_callback=None):
        raise OSError("disk full")

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", fake_ensure)
    monkeypatch.setattr("vibeocr.backend.env_manager.get_project_root", lambda: "/root")

    worker = MinerUPreflightWorker.__new__(MinerUPreflightWorker)
    worker._cancelled = False
    completed_emits: list = []
    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: None
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed_emits.append(a)

    worker.run()
    # 异常被捕获，转成 (False, message)
    assert len(completed_emits) == 1
    assert completed_emits[0][0] is False
    assert "disk full" in completed_emits[0][1]


def test_mineru_preflight_run_cancelled_swallows_completed(monkeypatch):
    """取消后 run() 自然返回也不发 completed。"""
    from vibeocr.classic.pyside.pdf_ipc_worker import MinerUPreflightWorker

    def fake_ensure(root, *, progress_callback=None):
        # 模拟越过取消检查点后自然返回成功
        return True, "late"

    monkeypatch.setattr("vibeocr.backend.env_manager.ensure_mineru_models", fake_ensure)
    monkeypatch.setattr("vibeocr.backend.env_manager.get_project_root", lambda: "/root")

    worker = MinerUPreflightWorker.__new__(MinerUPreflightWorker)
    worker._cancelled = True  # 已取消
    progress_emits: list = []
    completed_emits: list = []
    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: progress_emits.append(a)
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed_emits.append(a)

    worker.run()
    # 取消后 progress/completed 均不发
    assert progress_emits == []
    assert completed_emits == []


def test_mineru_preflight_cancel_sets_flag_and_requests_interruption(monkeypatch):
    from vibeocr.classic.pyside.pdf_ipc_worker import MinerUPreflightWorker

    worker = MinerUPreflightWorker.__new__(MinerUPreflightWorker)
    worker._cancelled = False
    # requestInterruption 需要 QObject；monkeypatch 掉
    monkeypatch.setattr(worker, "requestInterruption", lambda: None)

    worker.cancel()
    assert worker._cancelled is True
    assert worker.is_cancelled is True


# =============================================================================
# PdfIpcPreviewWorker.run —— 成功/失败/取消/解码失败分支
# =============================================================================


def _png_bytes_qt(width: int = 2, height: int = 2) -> bytes:
    """用 Qt 生成合法小 PNG bytes。"""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def test_preview_worker_run_emits_completed(qapp):
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    client = MagicMock()
    png = _png_bytes_qt(3, 2)
    client.render_preview.return_value = png
    worker._client = client
    worker._session_id = "sid"
    worker._page_index = 1
    worker._generation = 5
    worker._detect_text = False
    worker._cancelled = False

    completed: list = []
    failed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed.append(a)

    worker.run()

    client.render_preview.assert_called_once_with("sid", 1, dpi=150)
    assert len(completed) == 1
    sid, idx, gen, image, layers = completed[0]
    assert sid == "sid"
    assert idx == 1
    assert gen == 5
    assert not image.isNull()
    assert layers is None
    assert failed == []


def test_preview_worker_run_detect_text_branch(qapp):
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    client = MagicMock()
    client.render_preview.return_value = _png_bytes_qt()
    layers_result = MagicMock()
    layers_result.text_layers = [{"t": 1}]
    client.detect_text_layers.return_value = layers_result
    worker._client = client
    worker._session_id = "sid"
    worker._page_index = 0
    worker._generation = 1
    worker._detect_text = True
    worker._cancelled = False

    completed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: None

    worker.run()

    client.detect_text_layers.assert_called_once_with("sid", 0)
    assert completed[0][4] == [{"t": 1}]


def test_preview_worker_run_invalid_png_emits_failed(qapp):
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    client = MagicMock()
    client.render_preview.return_value = b"not a png"
    worker._client = client
    worker._session_id = "sid"
    worker._page_index = 0
    worker._generation = 1
    worker._detect_text = False
    worker._cancelled = False

    completed: list = []
    failed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed.append(a)

    worker.run()

    assert completed == []
    assert len(failed) == 1
    assert "PNG 解码失败" in failed[0][3]


def test_preview_worker_run_render_exception_emits_failed(qapp):
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    client = MagicMock()
    client.render_preview.side_effect = RuntimeError("backend gone")
    worker._client = client
    worker._session_id = "sid"
    worker._page_index = 0
    worker._generation = 1
    worker._detect_text = False
    worker._cancelled = False

    failed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: None
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed.append(a)

    worker.run()
    assert len(failed) == 1
    assert "backend gone" in failed[0][3]


def test_preview_worker_run_cancelled_before_emit_swallows(qapp):
    """run 期间取消：completed/failed 均不发。"""
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    client = MagicMock()
    client.render_preview.return_value = _png_bytes_qt()
    worker._client = client
    worker._session_id = "sid"
    worker._page_index = 0
    worker._generation = 1
    worker._detect_text = True
    # 在 detect 前已取消：render 成功，detect 因 _cancelled 跳过，
    # 但 completed 也因 _cancelled 被吞
    worker._cancelled = True

    completed: list = []
    failed: list = []
    worker.completed = MagicMock()
    worker.completed.emit = lambda *a: completed.append(a)
    worker.failed = MagicMock()
    worker.failed.emit = lambda *a: failed.append(a)

    worker.run()
    assert completed == []
    assert failed == []


def test_preview_worker_cancel_sets_flag():
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcPreviewWorker

    worker = PdfIpcPreviewWorker.__new__(PdfIpcPreviewWorker)
    worker._cancelled = False
    worker.cancel()
    assert worker._cancelled is True


# =============================================================================
# PdfIpcOpenWorker 小测：is_cancelled / opened_sessions / incomplete_sessions
# 与 cancel_and_snapshot_sessions 原子快照
# =============================================================================


def test_open_worker_cancel_and_snapshot_is_atomic():
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcOpenWorker

    worker = PdfIpcOpenWorker.__new__(PdfIpcOpenWorker)
    worker._client = MagicMock()
    worker._paths = []
    worker._cancelled = False
    worker._opened_sessions = {"a.pdf": "sid-a"}
    worker._incomplete_sessions = {"b.pdf": "sid-b"}
    worker._sessions_lock = __import__("threading").Lock()

    opened, incomplete = worker.cancel_and_snapshot_sessions()

    assert opened == {"a.pdf": "sid-a"}
    assert incomplete == {"b.pdf": "sid-b"}
    assert worker.is_cancelled is True
    # 原子快照：原始 dict 仍可被独立修改（返回的是副本）
    worker._opened_sessions["c.pdf"] = "sid-c"
    assert "c.pdf" not in opened


def test_open_worker_opened_and_incomplete_sessions_properties():
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcOpenWorker

    worker = PdfIpcOpenWorker.__new__(PdfIpcOpenWorker)
    worker._client = MagicMock()
    worker._paths = []
    worker._cancelled = False
    worker._opened_sessions = {"x.pdf": "sid-x"}
    worker._incomplete_sessions = {}
    worker._sessions_lock = __import__("threading").Lock()

    assert worker.opened_sessions == {"x.pdf": "sid-x"}
    assert worker.incomplete_sessions == {}


def test_open_worker_cancelled_session_owned_by_worker():
    """_cancelled_session_owned_by_worker：已取消时返回 incomplete 中的 sid。"""
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcOpenWorker

    worker = PdfIpcOpenWorker.__new__(PdfIpcOpenWorker)
    worker._client = MagicMock()
    worker._paths = []
    worker._cancelled = True
    worker._opened_sessions = {}
    worker._incomplete_sessions = {"p.pdf": "sid-p"}
    worker._sessions_lock = __import__("threading").Lock()

    assert worker._cancelled_session_owned_by_worker("p.pdf") == "sid-p"
    assert worker._cancelled_session_owned_by_worker("missing.pdf") is None


def test_open_worker_complete_load_or_keep_cancel_ownership():
    """_complete_load_or_keep_cancel_ownership：取消时保留回收 ownership。"""
    from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcOpenWorker

    worker = PdfIpcOpenWorker.__new__(PdfIpcOpenWorker)
    worker._client = MagicMock()
    worker._paths = []
    worker._cancelled = True
    worker._opened_sessions = {}
    worker._incomplete_sessions = {"p.pdf": "sid-p"}
    worker._sessions_lock = __import__("threading").Lock()

    # 已取消 → 返回 session_id，incomplete 保留
    assert worker._complete_load_or_keep_cancel_ownership("p.pdf") == "sid-p"
    assert "p.pdf" in worker._incomplete_sessions

    # 未取消 → 移出 incomplete 并返回 None
    worker._cancelled = False
    assert worker._complete_load_or_keep_cancel_ownership("p.pdf") is None
    assert "p.pdf" not in worker._incomplete_sessions
