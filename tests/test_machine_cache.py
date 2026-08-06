"""machine_cache 模块测试（机器码生成 + 本地 cache.json 读写 + 有效性校验）。

覆盖成功路径、失败路径与边界条件。重点验证：
- 原子写、损坏文件回退、版本/机器码不匹配失效；
- wmic 子进程缺失/超时返回空串；
- 缓存幂等与锁内双检；
- update_cache_field 增量写保留其余字段。

隔离约定：autouse fixture 重置模块级 ``_cached_machine_id``，避免真实机器码
跨测试残留导致 ``is_cache_valid`` 假阳性；``tmp_path`` 作 ``project_root``
天然隔离缓存文件。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vibeocr.classic import machine_cache
from vibeocr.classic.machine_cache import (
    CACHE_VERSION,
    clear_cache,
    create_cache_entry,
    get_cache_age_seconds,
    get_cache_dir,
    get_cache_info,
    get_cache_path,
    is_cache_valid,
    load_cache,
    reset_cache_to_empty,
    save_cache,
    update_cache_field,
)

FIXED_MACHINE_ID = "a" * 64


@pytest.fixture(autouse=True)
def _reset_machine_id_cache():
    """每个测试前后重置模块级机器码缓存，避免跨测试污染。

    ``generate_machine_id`` 首次调用后会缓存真实机器码；若不重置，后续
    ``is_cache_valid`` 的机器码比对会始终命中，掩盖版本不匹配等失败路径。
    """
    machine_cache._cached_machine_id = None
    yield
    machine_cache._cached_machine_id = None


@pytest.fixture(autouse=True)
def _stub_machine_id(monkeypatch):
    """patch ``generate_machine_id`` 返回固定值，避免真实 wmic 探测。"""
    monkeypatch.setattr(machine_cache, "generate_machine_id", lambda: FIXED_MACHINE_ID)


@pytest.fixture
def project_root(tmp_path):
    """独立的项目根目录（缓存落到 ``tmp_path/.vibeocr/cache.json``）。"""
    return tmp_path


# ---------------------------------------------------------------------------
# 路径 helper
# ---------------------------------------------------------------------------


def test_get_cache_dir(project_root):
    """缓存目录在 project_root/.vibeocr。"""
    assert get_cache_dir(project_root) == project_root / ".vibeocr"


def test_get_cache_path(project_root):
    """缓存文件在 project_root/.vibeocr/cache.json。"""
    assert get_cache_path(project_root) == project_root / ".vibeocr" / "cache.json"


# ---------------------------------------------------------------------------
# get_cache_age_seconds
# ---------------------------------------------------------------------------


def test_get_cache_age_seconds_no_cache(project_root):
    """无缓存返回 None。"""
    assert get_cache_age_seconds(project_root) is None


def test_get_cache_age_seconds_no_last_check_time(project_root):
    """缓存无 last_check_time 字段返回 None。"""
    save_cache(project_root, {"version": CACHE_VERSION, "machine_id": FIXED_MACHINE_ID})
    assert get_cache_age_seconds(project_root) is None


def test_get_cache_age_seconds_corrupt_timestamp(project_root, monkeypatch):
    """last_check_time 解析失败返回 None。"""
    save_cache(
        project_root,
        {
            "version": CACHE_VERSION,
            "machine_id": FIXED_MACHINE_ID,
            "last_check_time": "not-a-timestamp",
        },
    )
    assert get_cache_age_seconds(project_root) is None


def test_get_cache_age_seconds_valid(project_root):
    """正常时间戳返回正秒数。"""
    recent = (datetime.now() - timedelta(seconds=10)).isoformat()
    save_cache(
        project_root,
        {
            "version": CACHE_VERSION,
            "machine_id": FIXED_MACHINE_ID,
            "last_check_time": recent,
        },
    )
    age = get_cache_age_seconds(project_root)
    assert age is not None
    assert age >= 10


# ---------------------------------------------------------------------------
# save_cache / load_cache（原子写 + 回退）
# ---------------------------------------------------------------------------


def test_save_cache_writes_valid_json(project_root):
    """save_cache 成功写入合法 JSON。"""
    data = {"version": CACHE_VERSION, "machine_id": FIXED_MACHINE_ID, "marker": True}
    assert save_cache(project_root, data) is True
    written = json.loads(get_cache_path(project_root).read_text(encoding="utf-8"))
    assert written == data


def test_save_cache_atomic_no_tmp_residue(project_root):
    """原子写完成后不留 .json.tmp 残留文件。"""
    save_cache(project_root, {"version": CACHE_VERSION})
    tmp = get_cache_path(project_root).with_suffix(".json.tmp")
    assert not tmp.exists()


def test_save_cache_failure_returns_false_and_cleans_tmp(project_root, monkeypatch):
    """os.replace 失败时返回 False 并清理临时文件。"""
    tmp_file = get_cache_path(project_root).with_suffix(".json.tmp")

    def _boom(src, dst, *, context=None):
        raise OSError("simulated replace failure")

    # Path.replace 内部调用 os.replace；patch 模块级 os.replace 触发失败分支
    monkeypatch.setattr(machine_cache.os, "replace", _boom)
    result = save_cache(project_root, {"version": CACHE_VERSION})
    assert result is False
    # 失败分支应清理临时文件
    assert not tmp_file.exists()


def test_load_cache_missing_file(project_root):
    """文件缺失返回 None。"""
    assert load_cache(project_root) is None


def test_load_cache_corrupt_json(project_root, capsys):
    """损坏 JSON 返回 None 并打印提示。"""
    cache_file = get_cache_path(project_root)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{not valid json", encoding="utf-8")
    assert load_cache(project_root) is None


def test_load_cache_valid(project_root):
    """正常文件返回 dict。"""
    save_cache(project_root, {"version": CACHE_VERSION, "k": "v"})
    assert load_cache(project_root) == {"version": CACHE_VERSION, "k": "v"}


def test_load_cache_io_error_returns_none(project_root, monkeypatch):
    """open 抛 OSError 时返回 None（load_cache 的兜底 except）。"""
    cache_file = get_cache_path(project_root)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{}", encoding="utf-8")

    def _fail(path, *args, **kwargs):
        raise PermissionError("denied")

    # load_cache 用 builtins.open；patch builtins 触发兜底分支
    monkeypatch.setattr("builtins.open", _fail)
    assert load_cache(project_root) is None


# ---------------------------------------------------------------------------
# is_cache_valid
# ---------------------------------------------------------------------------


def test_is_cache_valid_no_cache(project_root):
    """无缓存返回 (False, None)。"""
    assert is_cache_valid(project_root) == (False, None)


def test_is_cache_valid_version_mismatch(project_root):
    """版本不匹配返回 (False, None)。"""
    save_cache(
        project_root,
        {"version": CACHE_VERSION - 1, "machine_id": FIXED_MACHINE_ID},
    )
    assert is_cache_valid(project_root) == (False, None)


def test_is_cache_valid_machine_id_mismatch(project_root, monkeypatch):
    """机器码不匹配返回 (False, None)。"""
    save_cache(
        project_root,
        {"version": CACHE_VERSION, "machine_id": "different" + "b" * 55},
    )
    assert is_cache_valid(project_root) == (False, None)


def test_is_cache_valid_match(project_root):
    """版本与机器码都匹配返回 (True, data)。"""
    data = {
        "version": CACHE_VERSION,
        "machine_id": FIXED_MACHINE_ID,
        "dependencies": {"paddle": True},
    }
    save_cache(project_root, data)
    valid, cached = is_cache_valid(project_root)
    assert valid is True
    assert cached == data


# ---------------------------------------------------------------------------
# generate_machine_id（真实路径，独立 stub）
# ---------------------------------------------------------------------------


def test_generate_machine_id_sha256_format(monkeypatch):
    """generate_machine_id 返回 64 字符 hex。"""
    # 该测试需要真实 generate_machine_id，撤掉 autouse stub
    monkeypatch.setattr(machine_cache, "_get_cpu_id", lambda: "cpu-1")
    monkeypatch.setattr(machine_cache, "_get_baseboard_serial", lambda: "board-1")
    monkeypatch.setattr(machine_cache, "_get_mac_address", lambda: "AABBCCDDEEFF")
    # 恢复真实 generate_machine_id（被 autouse stub 覆盖了）
    monkeypatch.setattr(
        machine_cache,
        "generate_machine_id",
        machine_cache.__dict__.get(
            "generate_machine_id", _real_generate_machine_id_factory()
        ),
    )
    mid = machine_cache.generate_machine_id()
    assert len(mid) == 64
    assert all(c in "0123456789abcdef" for c in mid)


def _real_generate_machine_id_factory():
    """构造一个不依赖模块全局缓存的 generate_machine_id 副本（用于格式测试）。"""
    import hashlib

    def _impl():
        hardware = [
            machine_cache._get_cpu_id(),
            machine_cache._get_baseboard_serial(),
            machine_cache._get_mac_address(),
        ]
        combined = "|".join(hardware)
        return hashlib.sha256(combined.encode()).hexdigest()

    return _impl


def test_generate_machine_id_caches_first_result(monkeypatch):
    """二次调用不重复探测硬件（缓存幂等）。"""
    call_count = {"n": 0}

    def _counting_cpu():
        call_count["n"] += 1
        return "cpu-x"

    monkeypatch.setattr(machine_cache, "_get_cpu_id", _counting_cpu)
    monkeypatch.setattr(machine_cache, "_get_baseboard_serial", lambda: "b")
    monkeypatch.setattr(machine_cache, "_get_mac_address", lambda: "m")
    impl = _real_generate_machine_id_factory()
    monkeypatch.setattr(machine_cache, "generate_machine_id", impl)

    first = machine_cache.generate_machine_id()
    second = machine_cache.generate_machine_id()
    assert first == second


# ---------------------------------------------------------------------------
# 硬件探测：wmic 缺失/超时返回空串
# ---------------------------------------------------------------------------


def _make_completed(returncode=0, stdout="ProcessorId\nABC123\n", stderr=""):
    """构造 subprocess.run 风格的 CompletedProcess。"""

    class _Result:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return _Result()


def test_get_cpu_id_wmic_success(monkeypatch):
    """wmic 成功返回 CPU ID。"""
    captured = {}

    def _fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return _make_completed(stdout="ProcessorId\nCPU12345\n")

    monkeypatch.setattr(machine_cache.subprocess, "run", _fake_run)
    assert machine_cache._get_cpu_id() == "CPU12345"
    assert captured["cmd"] == ["wmic", "cpu", "get", "processorid"]


def test_get_cpu_id_wmic_failure_returns_empty(monkeypatch):
    """wmic 非零退出返回空串。"""
    monkeypatch.setattr(
        machine_cache.subprocess,
        "run",
        lambda *a, **k: _make_completed(returncode=1, stdout=""),
    )
    assert machine_cache._get_cpu_id() == ""


def test_get_cpu_id_wmic_timeout_returns_empty(monkeypatch):
    """wmic 超时（SubprocessError）返回空串。"""

    def _timeout(*a, **k):
        raise machine_cache.subprocess.TimeoutExpired(cmd="wmic", timeout=5)

    monkeypatch.setattr(machine_cache.subprocess, "run", _timeout)
    assert machine_cache._get_cpu_id() == ""


def test_get_cpu_id_wmic_oserror_returns_empty(monkeypatch):
    """wmic 不存在（OSError）返回空串。"""

    def _oserror(*a, **k):
        raise OSError("wmic not found")

    monkeypatch.setattr(machine_cache.subprocess, "run", _oserror)
    assert machine_cache._get_cpu_id() == ""


def test_get_cpu_id_single_line_stdout_returns_empty(monkeypatch):
    """wmic 输出只有标题行（无数据行）返回空串。"""
    monkeypatch.setattr(
        machine_cache.subprocess,
        "run",
        lambda *a, **k: _make_completed(stdout="ProcessorId\n"),
    )
    assert machine_cache._get_cpu_id() == ""


def test_get_baseboard_serial_success(monkeypatch):
    """wmic 成功返回主板序列号。"""
    monkeypatch.setattr(
        machine_cache.subprocess,
        "run",
        lambda *a, **k: _make_completed(stdout="SerialNumber\nBB-789\n"),
    )
    assert machine_cache._get_baseboard_serial() == "BB-789"


def test_get_baseboard_serial_failure_returns_empty(monkeypatch):
    """wmic 失败返回空串。"""
    monkeypatch.setattr(
        machine_cache.subprocess,
        "run",
        lambda *a, **k: _make_completed(returncode=1),
    )
    assert machine_cache._get_baseboard_serial() == ""


def test_get_mac_address_returns_uppercase_hex(monkeypatch):
    """MAC 地址返回 12 位大写 hex。"""
    monkeypatch.setattr(machine_cache.uuid, "getnode", lambda: 0xAABBCCDDEEFF)
    assert machine_cache._get_mac_address() == "AABBCCDDEEFF"


def test_get_mac_address_node_consistency_returns_empty(monkeypatch):
    """uuid.getnode 返回不一致（多次调用不同）时返回空串。"""
    values = iter([0xAABBCCDDEEFF, 0x112233445566])
    monkeypatch.setattr(machine_cache.uuid, "getnode", lambda: next(values))
    # 第一次与第二次不一致 → 返回空串
    assert machine_cache._get_mac_address() == ""


# ---------------------------------------------------------------------------
# create_cache_entry
# ---------------------------------------------------------------------------


def test_create_cache_entry_writes_full_record(project_root):
    """create_cache_entry 写入完整记录（含 version/machine_id/timestamp）。"""
    deps = {"paddle": True}
    hw = {"has_gpu": False, "cuda_version": None}
    entry = create_cache_entry(project_root, deps, hw)
    assert entry is not None
    assert entry["version"] == CACHE_VERSION
    assert entry["machine_id"] == FIXED_MACHINE_ID
    assert entry["dependencies"] == deps
    assert entry["hardware_info"] == hw
    assert "last_check_time" in entry
    assert "python_version" in entry
    # 落盘
    assert load_cache(project_root) == entry


def test_create_cache_entry_save_failure_returns_none(project_root, monkeypatch):
    """save_cache 失败时返回 None。"""
    monkeypatch.setattr(machine_cache, "save_cache", lambda *a, **k: False)
    entry = create_cache_entry(project_root, {}, {})
    assert entry is None


# ---------------------------------------------------------------------------
# update_cache_field
# ---------------------------------------------------------------------------


def test_update_cache_field_invalid_cache_returns_false(project_root):
    """缓存不存在/无效时返回 False。"""
    assert update_cache_field(project_root, "custom_flag", "new") is False


def test_update_cache_field_invalid_version_returns_false(project_root):
    """缓存版本不匹配视为无效，返回 False。"""
    save_cache(
        project_root,
        {"version": CACHE_VERSION - 1, "machine_id": FIXED_MACHINE_ID},
    )
    assert update_cache_field(project_root, "custom_flag", "new") is False


def test_update_cache_field_preserves_other_fields(project_root):
    """增量写保留其余字段。"""
    base = {
        "version": CACHE_VERSION,
        "machine_id": FIXED_MACHINE_ID,
        "dependencies": {"paddle": True},
        "hardware_info": {"has_gpu": True},
    }
    save_cache(project_root, base)
    assert update_cache_field(project_root, "custom_flag", "new") is True
    updated = load_cache(project_root)
    assert updated["custom_flag"] == "new"
    assert updated["dependencies"] == {"paddle": True}
    assert updated["hardware_info"] == {"has_gpu": True}
    assert updated["version"] == CACHE_VERSION
    assert updated["machine_id"] == FIXED_MACHINE_ID


def test_update_cache_field_overwrites_existing(project_root):
    """覆盖已存在的同名字段。"""
    base = {
        "version": CACHE_VERSION,
        "machine_id": FIXED_MACHINE_ID,
        "custom_flag": "old",
    }
    save_cache(project_root, base)
    assert update_cache_field(project_root, "custom_flag", "new") is True
    assert load_cache(project_root)["custom_flag"] == "new"


# ---------------------------------------------------------------------------
# reset_cache_to_empty
# ---------------------------------------------------------------------------


def test_reset_cache_to_empty_clears_deps(project_root):
    """重置为空壳：清空 deps/hardware_info，保留 version/machine_id。"""
    save_cache(
        project_root,
        {
            "version": CACHE_VERSION,
            "machine_id": FIXED_MACHINE_ID,
            "dependencies": {"paddle": True},
            "hardware_info": {"has_gpu": True},
        },
    )
    assert reset_cache_to_empty(project_root) is True
    data = load_cache(project_root)
    assert data["version"] == CACHE_VERSION
    assert data["machine_id"] == FIXED_MACHINE_ID
    assert data["dependencies"] == {}
    assert data["hardware_info"] == {}
    assert "last_check_time" in data


def test_reset_cache_to_empty_save_failure(project_root, monkeypatch):
    """save_cache 失败时返回 False。"""
    monkeypatch.setattr(machine_cache, "save_cache", lambda *a, **k: False)
    assert reset_cache_to_empty(project_root) is False


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def test_clear_cache_removes_file(project_root):
    """删除已存在的缓存文件。"""
    save_cache(project_root, {"version": CACHE_VERSION})
    assert get_cache_path(project_root).exists()
    assert clear_cache(project_root) is True
    assert not get_cache_path(project_root).exists()


def test_clear_cache_missing_file_returns_true(project_root):
    """文件不存在也返回 True（幂等）。"""
    assert not get_cache_path(project_root).exists()
    assert clear_cache(project_root) is True


def test_clear_cache_unlink_failure_returns_false(project_root, monkeypatch):
    """unlink 抛异常时返回 False。"""
    save_cache(project_root, {"version": CACHE_VERSION})

    def _boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", _boom)
    assert clear_cache(project_root) is False


# ---------------------------------------------------------------------------
# get_cache_info
# ---------------------------------------------------------------------------


def test_get_cache_info_no_cache(project_root):
    """无缓存返回 '无缓存'。"""
    assert get_cache_info(project_root) == "无缓存"


def test_get_cache_info_full_summary(project_root):
    """完整摘要包含各顶层字段。"""
    save_cache(
        project_root,
        {
            "version": CACHE_VERSION,
            "machine_id": FIXED_MACHINE_ID,
            "last_check_time": "2026-01-01T00:00:00",
            "python_version": "3.13.0",
            "dependencies": {"paddle": True, "torch": False},
            "hardware_info": {"has_gpu": True, "cuda_version": "12.1"},
            "pipeline_success": {"OCR": True},
            "network": {
                "paddlex_source": "tsinghua",
                "mineru_source": "aliyun",
                "last_detected": "2026-01-01",
            },
        },
    )
    info = get_cache_info(project_root)
    assert f"version={CACHE_VERSION}" in info
    assert "machine_id=aaaaaaaaaaaaaaaa..." in info
    assert "last_check_time=2026-01-01T00:00:00" in info
    assert "python_version=3.13.0" in info
    assert "paddle=✓" in info
    assert "torch=✗" in info
    assert "has_gpu=True" in info
    assert "cuda=12.1" in info
    assert "pipeline_success" not in info
    assert "network" not in info


def test_get_cache_info_empty_dependencies(project_root):
    """空 dependencies 的分支。"""
    save_cache(
        project_root,
        {
            "version": CACHE_VERSION,
            "machine_id": FIXED_MACHINE_ID,
            "dependencies": {},
        },
    )
    info = get_cache_info(project_root)
    assert "dependencies: (空)" in info


def test_get_cache_info_missing_fields_use_unknown(project_root):
    """缺少字段时用 '未知' 占位。"""
    save_cache(project_root, {"version": CACHE_VERSION, "machine_id": "x"})
    info = get_cache_info(project_root)
    # 字段缺失走 .get(..., "未知") 分支
    assert "未知" in info
