"""startup_metrics 模块测试（T0–T6 启动里程碑 + percentile/summarize 算法）。

覆盖成功路径、失败路径与边界条件。重点验证：
- StartupRecorder.record 首次/重复语义、is_complete、to_dict；
- flush 幂等 + 环境变量控制 + JSONL 脱敏；
- percentile 空序列 ValueError + nearest-rank 边界；
- summarize_runs 多 run 聚合；
- set_startup_origin + record_startup 默认/显式时间戳。

隔离约定：autouse fixture 重置全局 _global_recorder / _startup_origin / 环境变量。
"""

import json

import pytest

from vibeocr.classic import startup_metrics
from vibeocr.classic.startup_metrics import (
    TRACE_ENV_VAR,
    StartupEvent,
    StartupRecorder,
    flush_startup,
    get_recorder,
    percentile,
    record_startup,
    set_startup_origin,
    summarize_runs,
)


@pytest.fixture(autouse=True)
def _reset_global_state(monkeypatch):
    """每个测试前后重置全局 recorder / origin / 环境变量。"""
    monkeypatch.delenv(TRACE_ENV_VAR, raising=False)
    saved_recorder = startup_metrics._global_recorder
    saved_origin = startup_metrics._startup_origin
    startup_metrics._global_recorder = None
    startup_metrics._startup_origin = 0.0
    yield
    startup_metrics._global_recorder = saved_recorder
    startup_metrics._startup_origin = saved_origin


# ---------------------------------------------------------------------------
# StartupRecorder.record
# ---------------------------------------------------------------------------


def test_record_first_timestamp():
    """首次记录保留时间戳。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    assert rec.events[StartupEvent.PROCESS_START] == 0.0


def test_record_duplicate_keeps_first():
    """重复记录同一事件只保留首次时间戳。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.RUNTIME_READY, 1.0)
    rec.record(StartupEvent.RUNTIME_READY, 5.0)
    assert rec.events[StartupEvent.RUNTIME_READY] == 1.0


def test_record_different_events_independent():
    """不同事件各自记录（乱序也各自记录）。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.INTERACTIVE, 3.2)
    rec.record(StartupEvent.PROCESS_START, 0.0)
    assert rec.events[StartupEvent.INTERACTIVE] == 3.2
    assert rec.events[StartupEvent.PROCESS_START] == 0.0


def test_record_float_coercion():
    """timestamp 被转为 float。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.SHELL_CREATED, 2)  # int
    assert rec.events[StartupEvent.SHELL_CREATED] == 2.0
    assert isinstance(rec.events[StartupEvent.SHELL_CREATED], float)


def test_events_property_returns_copy():
    """events 属性返回副本（修改不影响内部状态）。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    snapshot = rec.events
    snapshot[StartupEvent.RUNTIME_READY] = 9.9
    assert StartupEvent.RUNTIME_READY not in rec._events


# ---------------------------------------------------------------------------
# is_complete
# ---------------------------------------------------------------------------


def test_is_complete_all_seven_events():
    """全部 7 个事件记录后 is_complete 为 True。"""
    rec = StartupRecorder()
    for i, ev in enumerate(StartupEvent):
        rec.record(ev, float(i))
    assert rec.is_complete() is True


def test_is_complete_partial_false():
    """缺少任一事件 is_complete 为 False。"""
    rec = StartupRecorder()
    events = list(StartupEvent)
    for ev in events[:-1]:  # 少记最后一个
        rec.record(ev, 1.0)
    assert rec.is_complete() is False


def test_is_complete_empty_false():
    """空 recorder 的 is_complete 为 False。"""
    assert StartupRecorder().is_complete() is False


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


def test_to_dict_uses_event_values():
    """to_dict 用 event 的 wire value（T0–T6）作键。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.record(StartupEvent.INTERACTIVE, 3.0)
    d = rec.to_dict()
    assert d == {"T0": 0.0, "T6": 3.0}


def test_to_dict_empty():
    """空 recorder 的 to_dict 为空 dict。"""
    assert StartupRecorder().to_dict() == {}


# ---------------------------------------------------------------------------
# flush
# ---------------------------------------------------------------------------


def test_flush_no_env_noop(tmp_path):
    """无 VIBEOCR_STARTUP_TRACE 时不写文件。"""
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.flush()  # 不抛、不写
    # 无文件被创建（无法直接断言，但不抛即通过）


def test_flush_writes_jsonl(tmp_path, monkeypatch):
    """设置环境变量时写一行 JSONL。"""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.record(StartupEvent.INTERACTIVE, 3.0)
    rec.flush()
    content = trace.read_text(encoding="utf-8").strip()
    doc = json.loads(content)
    assert doc["T0"] == 0.0
    assert doc["T6"] == 3.0


def test_flush_idempotent(tmp_path, monkeypatch):
    """已 flush 的 recorder 二次调用不重复写入。"""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.flush()
    rec.flush()  # 二次 no-op
    lines = [ln for ln in trace.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 1


def test_flush_creates_parent_dir(tmp_path, monkeypatch):
    """trace 路径的父目录不存在时自动创建。"""
    trace = tmp_path / "nested" / "deep" / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.flush()
    assert trace.exists()


def test_flush_no_absolute_paths(tmp_path, monkeypatch):
    """JSONL 脱敏：不含本机绝对路径。"""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    rec = StartupRecorder()
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.flush()
    content = trace.read_text(encoding="utf-8")
    # 内容只含事件值与时间戳，不应含 tmp_path 绝对路径
    assert str(tmp_path) not in content


def test_flush_sorted_keys(tmp_path, monkeypatch):
    """JSONL 行的键按 sort_keys 排序。"""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    rec = StartupRecorder()
    # 故意乱序记录
    rec.record(StartupEvent.INTERACTIVE, 3.0)
    rec.record(StartupEvent.PROCESS_START, 0.0)
    rec.flush()
    content = trace.read_text(encoding="utf-8").strip()
    keys = list(json.loads(content).keys())
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------


def test_percentile_empty_raises():
    """空序列抛 ValueError。"""
    with pytest.raises(ValueError, match="empty"):
        percentile([], 50)


def test_percentile_single_element():
    """单元素序列任意百分位返回该元素。"""
    assert percentile([42.0], 0) == 42.0
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 100) == 42.0


def test_percentile_p50_even():
    """p50（nearest-rank, n=4）：rank=ceil(0.5*4)=2 → sorted[1]。"""
    data = [10, 20, 30, 40]
    assert percentile(data, 50) == 20


def test_percentile_p50_odd():
    """p50（n=5）：rank=ceil(0.5*5)=3 → sorted[2]。"""
    data = [10, 20, 30, 40, 50]
    assert percentile(data, 50) == 30


def test_percentile_p95():
    """p95（n=20）：rank=ceil(0.95*20)=19 → sorted[18]。"""
    data = list(range(1, 21))  # 1..20
    assert percentile(data, 95) == 19


def test_percentile_p0_clamps_to_min():
    """p0：rank=ceil(0)=0 → max(1,0)=1 → sorted[0]（最小值）。"""
    data = [5, 3, 1, 4, 2]
    assert percentile(data, 0) == 1


def test_percentile_p100_returns_max():
    """p100：rank=ceil(100)=n → sorted[n-1]（最大值）。"""
    data = [5, 3, 1, 4, 2]
    assert percentile(data, 100) == 5


def test_percentile_unsorted_input():
    """未排序输入先排序。"""
    assert percentile([3, 1, 2], 50) == 2


def test_percentile_high_pct_clamps_to_n():
    """超高百分位 clamp 到 n（不越界）。"""
    data = [1.0, 2.0]
    assert percentile(data, 99) == 2.0


# ---------------------------------------------------------------------------
# summarize_runs
# ---------------------------------------------------------------------------


def test_summarize_runs_basic():
    """多 run 聚合 p50/p95/count/min/max。"""
    runs = [
        {"T0": 0.0, "T6": 3.0},
        {"T0": 0.0, "T6": 5.0},
        {"T0": 0.0, "T6": 1.0},
    ]
    summary = summarize_runs(runs)
    assert "T0" in summary
    assert "T6" in summary
    t6 = summary["T6"]
    assert t6["count"] == 3.0
    assert t6["min"] == 1.0
    assert t6["max"] == 5.0
    # p50 nearest-rank: sorted=[1,3,5], rank=ceil(0.5*3)=2 → 3
    assert t6["p50"] == 3.0


def test_summarize_runs_empty():
    """空 runs 返回空 dict。"""
    assert summarize_runs([]) == {}


def test_summarize_runs_partial_events():
    """部分 run 缺少某些里程碑时，count 仅统计记录过的 run。"""
    runs = [
        {"T0": 0.0, "T6": 3.0},
        {"T0": 0.0},  # 缺 T6
    ]
    summary = summarize_runs(runs)
    assert summary["T0"]["count"] == 2.0
    assert summary["T6"]["count"] == 1.0


def test_summarize_runs_single_run():
    """单 run 的 p50 == p95 == 该值。"""
    runs = [{"T0": 1.5}]
    summary = summarize_runs(runs)
    assert summary["T0"]["p50"] == 1.5
    assert summary["T0"]["p95"] == 1.5


def test_summarize_runs_p95_field():
    """p95 字段存在且合理。"""
    runs = [{"T0": float(x)} for x in range(1, 21)]
    summary = summarize_runs(runs)
    assert summary["T0"]["p95"] == 19.0


# ---------------------------------------------------------------------------
# 全局 recorder / record_startup
# ---------------------------------------------------------------------------


def test_get_recorder_creates_singleton():
    """get_recorder 首次调用创建单例，二次返回同一实例。"""
    r1 = get_recorder()
    r2 = get_recorder()
    assert r1 is r2


def test_record_startup_records_to_global():
    """record_startup 在全局 recorder 上记录。"""
    record_startup(StartupEvent.PROCESS_START, 0.0)
    rec = get_recorder()
    assert rec.events[StartupEvent.PROCESS_START] == 0.0


def test_record_startup_default_timestamp_uses_origin():
    """timestamp=None 时用 perf_counter() - _startup_origin。"""
    set_startup_origin(100.0)
    record_startup(StartupEvent.RUNTIME_READY)  # 无显式 timestamp
    rec = get_recorder()
    ts = rec.events[StartupEvent.RUNTIME_READY]
    # _startup_origin=100，当前 perf_counter 远大于 100，故 ts 应为大正数
    assert isinstance(ts, float)
    assert ts > 0


def test_record_startup_explicit_timestamp_overrides():
    """显式 timestamp 覆盖默认计算。"""
    set_startup_origin(100.0)
    record_startup(StartupEvent.BACKEND_READY, 2.5)
    assert get_recorder().events[StartupEvent.BACKEND_READY] == 2.5


def test_set_startup_origin_sets_value():
    """set_startup_origin 设置全局 origin。"""
    set_startup_origin(42.0)
    assert startup_metrics._startup_origin == 42.0


def test_flush_startup_uses_global_recorder(tmp_path, monkeypatch):
    """flush_startup flush 全局 recorder。"""
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv(TRACE_ENV_VAR, str(trace))
    record_startup(StartupEvent.PROCESS_START, 0.0)
    flush_startup()
    assert trace.exists()


# ---------------------------------------------------------------------------
# StartupEvent 枚举
# ---------------------------------------------------------------------------


def test_startup_event_values():
    """StartupEvent 的 wire value 是 T0–T6。"""
    assert StartupEvent.PROCESS_START.value == "T0"
    assert StartupEvent.RUNTIME_READY.value == "T1"
    assert StartupEvent.SHELL_CREATED.value == "T2"
    assert StartupEvent.FIRST_WINDOW.value == "T3"
    assert StartupEvent.SUPERVISOR_READY.value == "T4"
    assert StartupEvent.BACKEND_READY.value == "T5"
    assert StartupEvent.INTERACTIVE.value == "T6"


def test_startup_event_count():
    """共 7 个事件。"""
    assert len(list(StartupEvent)) == 7
