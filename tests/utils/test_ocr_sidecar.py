# tests/utils/test_ocr_sidecar.py
import hashlib
import json
import os
from pathlib import Path

from vibeocr.backend.utils.ocr_sidecar import (
    compute_fingerprint,
    load_sidecar,
    mark_completed,
    mark_pages_saved,
    refresh_baseline,
    restore_pending_pages,
    sidecar_path,
)


def _bump_mtime(path: Path, extra_bytes: int = 100) -> None:
    """模拟 incremental save：append 字节（size 增长）并显式推高 mtime。

    显式 os.utime 是为了避免某些平台 mtime 分辨率过粗导致两次写落在同一 ns。
    """
    with open(path, "ab") as fh:
        fh.write(b"X" * extra_bytes)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_compute_fingerprint_uses_size_and_mtime(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    fp = compute_fingerprint(str(f))
    size, mtime = fp.split(":")
    assert size == "5"
    assert int(mtime) > 0


def test_sidecar_path_is_path_slug_under_backend_state(tmp_path):
    """sidecar 文件名按规范化绝对路径的 md5 命名（不按指纹）。"""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    p = sidecar_path(str(f))
    assert p.parent.name == "ocr_sessions"
    assert p.parent.parent.name == "backend"
    assert p.parent.parent.parent.name == "data"
    assert p.suffix == ".json"
    # 文件名 = md5(abspath)，32 位 hex
    expected = hashlib.md5(str(f.resolve()).encode("utf-8")).hexdigest()
    assert p.stem == expected


def test_sidecar_path_stable_across_file_changes(tmp_path):
    """文件内容/大小变化时 sidecar_path 不变（路径键稳定）—— 修复核心 bug。"""
    f = tmp_path / "stable.pdf"
    f.write_bytes(b"abc")
    before = sidecar_path(str(f))
    _bump_mtime(f, extra_bytes=500)
    after = sidecar_path(str(f))
    assert before == after


def test_mark_pages_saved_merges_into_existing(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 90}) is True
    data = load_sidecar(str(f))
    assert data is not None
    assert data["completed"] is False
    assert data["pages"] == {"0": {"has_text_layer": True, "ocr_preproc_angle": 0},
                              "1": {"has_text_layer": True, "ocr_preproc_angle": 90}}
    # 第二批合并
    assert mark_pages_saved(str(f), [2], {2: 0}) is True
    data = load_sidecar(str(f))
    assert data is not None
    assert set(data["pages"].keys()) == {"0", "1", "2"}


def test_mark_completed_sets_flag(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    assert mark_completed(str(f)) is True
    data = load_sidecar(str(f))
    assert data is not None
    assert data["completed"] is True


def test_mark_completed_preserves_pages_when_validation_fails(tmp_path, monkeypatch):
    """回归：sidecar 文件存在但 load_sidecar 因增长校验失败返回 None 时，
    mark_completed 应读原文保留 pages（不创建空 sidecar 丢失数据）。

    复现：6C 末尾压缩使文件变小，且 refresh_baseline 因故未跑（manager 故障）
    → load_sidecar 返回 None → 旧实现 _new_sidecar 丢全部 page 记录。
    """
    f = tmp_path / "d.pdf"
    f.write_bytes(b"baseline-pdf-content-here")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    # 积累两批页记录
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 90}) is True
    # 文件缩小（模拟压缩/换文件），使 load_sidecar 增长校验失败
    st = f.stat()
    f.write_bytes(b"short")  # 5 < 原 size
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert load_sidecar(str(f)) is None  # 校验确实失败
    # mark_completed 不应丢失 pages
    assert mark_completed(str(f)) is True
    # 直接读原文（绕过校验）确认 pages 仍在
    raw = json.loads(sidecar_path(str(f)).read_text(encoding="utf-8"))
    assert raw["completed"] is True
    assert set(raw["pages"].keys()) == {"0", "1"}
    assert raw["pages"]["1"]["ocr_preproc_angle"] == 90


def test_mark_completed_creates_new_when_sidecar_absent(tmp_path, monkeypatch):
    """无 sidecar 文件时 mark_completed 仍新建（行为不变）。"""
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    assert mark_completed(str(f)) is True
    raw = json.loads(sidecar_path(str(f)).read_text(encoding="utf-8"))
    assert raw["completed"] is True
    assert raw["pages"] == {}


def test_restore_pending_pages_returns_dict_when_incomplete(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0, 2], {0: 0, 2: 90})
    result = restore_pending_pages(str(f))
    assert result == {0: 0, 2: 90}


def test_restore_pending_pages_none_when_completed(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    mark_completed(str(f))
    assert restore_pending_pages(str(f)) is None


# ---- 关键回归测试：增量保存增长不得失效 sidecar ----


def test_incremental_save_growth_keeps_sidecar_valid(tmp_path, monkeypatch):
    """核心回归：OCR 增量保存 append 字节后，下一批 mark_pages_saved 仍读到
    上一批累积的页记录（不返回 None→空 sidecar→丢批次）。

    这是修复的主要 bug：旧实现按 size:mtime 指纹命名/校验，incremental save
    改变二者 → load_sidecar 返回 None → _new_sidecar → 旧批次丢失。
    """
    f = tmp_path / "grow.pdf"
    f.write_bytes(b"baseline-pdf")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    # 批 1
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 0}) is True
    # 模拟批 1 的 incremental save（append + mtime 增长）
    _bump_mtime(f, extra_bytes=200)
    # 批 2：此时 load_sidecar 应仍读到批 1 的页
    assert mark_pages_saved(str(f), [2], {2: 90}) is True
    data = load_sidecar(str(f))
    assert data is not None
    assert set(data["pages"].keys()) == {"0", "1", "2"}
    # restore 仍能拿到全部 3 页
    assert restore_pending_pages(str(f)) == {0: 0, 1: 0, 2: 90}


def test_file_shrink_invalidates_sidecar(tmp_path, monkeypatch):
    """文件被替换/缩小（用户换文件、回退版本）→ sidecar 失效返回 None。"""
    f = tmp_path / "shrink.pdf"
    f.write_bytes(b"longer-baseline-content-here")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    # 用户用更小的文件替换（size 变小）—— 模拟回退/换文件
    st = f.stat()
    f.write_bytes(b"short")  # 5 字节 < 28 字节
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert load_sidecar(str(f)) is None
    assert restore_pending_pages(str(f)) is None


def test_file_older_mtime_invalidates_sidecar(tmp_path, monkeypatch):
    """mtime 回退（文件被旧版本覆盖，size 不变或更大）→ 失效。"""
    f = tmp_path / "older.pdf"
    f.write_bytes(b"same-size-content")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    st = f.stat()
    # 同 size，但 mtime 回拨到更早
    f.write_bytes(b"same-size-content")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))  # -10s
    assert load_sidecar(str(f)) is None


def test_refresh_baseline_after_compression(tmp_path, monkeypatch):
    """6C 全量压缩后文件变小，refresh_baseline 把基线刷新到压缩后状态，
    随后 load_sidecar/mark_completed 才不会因 size < original 失效。"""
    f = tmp_path / "compress.pdf"
    f.write_bytes(b"bloated-" * 50)  # 大基线
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0, 1], {0: 0, 1: 0})
    # 模拟 6C 全量压缩重写：文件显著变小（实际场景子集字体合并 + deflate）
    st_before = f.stat()
    f.write_bytes(b"small")
    os.utime(f, ns=(st_before.st_atime_ns, st_before.st_mtime_ns + 3_000_000))
    # 未 refresh 前 load_sidecar 失效
    assert load_sidecar(str(f)) is None
    # refresh_baseline 修复
    assert refresh_baseline(str(f)) is True
    # 现在 load_sidecar 重新有效
    data = load_sidecar(str(f))
    assert data is not None
    assert set(data["pages"].keys()) == {"0", "1"}
    # 紧接着 mark_completed 也能成功（不再被增长校验拦截）
    assert mark_completed(str(f)) is True
    completed = load_sidecar(str(f))
    assert completed is not None
    assert completed["completed"] is True


def test_refresh_baseline_missing_sidecar(tmp_path, monkeypatch):
    """无 sidecar 时 refresh_baseline 返回 False（不抛异常）。"""
    f = tmp_path / "none.pdf"
    f.write_bytes(b"x")
    monkeypatch.setattr(
        "vibeocr.backend.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    assert refresh_baseline(str(f)) is False


def test_growth_ok_false_when_baseline_missing(tmp_path):
    """sidecar 缺 original_size/original_mtime_ns 时 _growth_ok 返回 False（line 74）。"""
    from vibeocr.backend.utils.ocr_sidecar import _growth_ok

    f = tmp_path / "f.pdf"
    f.write_bytes(b"x")
    assert _growth_ok({}, str(f)) is False
    assert _growth_ok({"original_size": 1}, str(f)) is False


def test_growth_ok_false_when_stat_raises(tmp_path, monkeypatch):
    """文件 stat 失败时 _growth_ok 返回 False（line 77-78）。"""

    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "missing.pdf"
    data = {"original_size": 1, "original_mtime_ns": 1}
    assert ocr_sidecar._growth_ok(data, str(f)) is False


def test_load_sidecar_returns_none_on_version_mismatch(tmp_path, monkeypatch):
    """sidecar 版本不符时返回 None（line 90）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"original")
    # 直接写一个旧版本 sidecar
    p = ocr_sidecar.sidecar_path(str(f))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"version": "0.0", "original_size": 5}', encoding="utf-8")
    assert ocr_sidecar.load_sidecar(str(f)) is None


def test_load_sidecar_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    """sidecar JSON 损坏时返回 None（line 94-96）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"original")
    p = ocr_sidecar.sidecar_path(str(f))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not valid json", encoding="utf-8")
    assert ocr_sidecar.load_sidecar(str(f)) is None


def test_save_sidecar_returns_false_on_write_failure(tmp_path, monkeypatch):
    """save_sidecar 写入失败时返回 False 并清理 tmp（line 108-114）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")

    def _fail_replace(self, _target):
        raise OSError("replace denied")

    monkeypatch.setattr("pathlib.Path.replace", _fail_replace)
    assert ocr_sidecar.save_sidecar(str(f), {"version": "1.0"}) is False


def test_save_sidecar_cleans_up_tmp_even_when_unlink_fails(tmp_path, monkeypatch):
    """save_sidecar 写入失败且 tmp unlink 也失败时仍返回 False（line 112-113）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")

    def _fail_replace(self, _target):
        raise OSError("replace denied")

    def _fail_unlink(self, *args, **kwargs):
        raise OSError("unlink denied")

    monkeypatch.setattr("pathlib.Path.replace", _fail_replace)
    monkeypatch.setattr("pathlib.Path.unlink", _fail_unlink)
    assert ocr_sidecar.save_sidecar(str(f), {"version": "1.0"}) is False


def test_mark_completed_falls_back_when_sidecar_corrupt(tmp_path, monkeypatch):
    """mark_completed 在 sidecar 损坏时回退新建（line 167-170）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"original-content-here")
    p = ocr_sidecar.sidecar_path(str(f))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ corrupt", encoding="utf-8")
    # mark_completed 应不崩溃，新建 sidecar 并标记 completed
    assert ocr_sidecar.mark_completed(str(f)) is True
    data = ocr_sidecar.load_sidecar(str(f))
    assert data is not None
    assert data.get("completed") is True


def test_mark_completed_falls_back_when_version_old(tmp_path, monkeypatch):
    """mark_completed 在 sidecar 版本旧时回退新建（line 166-167）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"original-content")
    p = ocr_sidecar.sidecar_path(str(f))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"version": "ancient"}', encoding="utf-8")
    assert ocr_sidecar.mark_completed(str(f)) is True


def test_refresh_baseline_returns_false_on_inner_failure(tmp_path, monkeypatch):
    """refresh_baseline 在内部步骤（如 compute_fingerprint）失败时返回 False（line 195-197）。"""
    from vibeocr.backend.utils import ocr_sidecar

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"original")
    # 先成功创建一个 sidecar
    ocr_sidecar.mark_completed(str(f))

    # 让 compute_fingerprint 抛异常 → refresh_baseline 进 except
    def _fail(_p):
        raise OSError("fingerprint failed")

    monkeypatch.setattr(ocr_sidecar, "compute_fingerprint", _fail)
    assert ocr_sidecar.refresh_baseline(str(f)) is False
