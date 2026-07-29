"""system_memory 工具单元测试。"""

from __future__ import annotations

from vibeocr.backend.utils.system_memory import FALLBACK_RAM_MB, get_available_ram_mb


def test_get_available_ram_mb_returns_positive_int():
    """在真实环境上应返回正值（单位 MB）。"""
    result = get_available_ram_mb()
    assert isinstance(result, int)
    assert result > 0


def test_get_available_ram_mb_at_least_some_memory():
    """任何能跑测试的机器可用内存至少应有 64MB。"""
    assert get_available_ram_mb() >= 64


def test_fallback_constant_is_conservative():
    """回退值应为 2048（2GB），保证 batch 至少为 1-2。"""
    assert FALLBACK_RAM_MB == 2048


from vibeocr.backend.utils.system_memory import estimate_cpu_batch_size  # noqa: E402


def test_estimate_cpu_batch_size_8g_ram():
    """8G RAM（free 4G）、A4@300 → 4096*0.3/199.13=6.17 → 6。"""
    assert estimate_cpu_batch_size(free_mb=4096, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_4g_ram():
    """4G RAM（free 2G）→ 2048*0.3/199.13=3.08 → 3。"""
    assert estimate_cpu_batch_size(free_mb=2048, avg_pixels=8_700_000) == 3


def test_estimate_cpu_batch_size_16g_ram_caps_at_6():
    """16G RAM（free 8G）→ 8192*0.3/199.13=12.3 → 夹到 6。"""
    assert estimate_cpu_batch_size(free_mb=8192, avg_pixels=8_700_000) == 6


def test_estimate_cpu_batch_size_minimum_is_1():
    """2G RAM（free 1G）→ 1024*0.3/199.13=1.54 → int 1。"""
    assert estimate_cpu_batch_size(free_mb=1024, avg_pixels=8_700_000) == 1


def test_estimate_cpu_batch_size_zero_inputs_returns_1():
    """零或负输入兜底返回 1。"""
    assert estimate_cpu_batch_size(free_mb=0, avg_pixels=8_700_000) == 1
    assert estimate_cpu_batch_size(free_mb=4096, avg_pixels=0) == 1


def test_get_available_ram_mb_falls_back_on_exception(monkeypatch):
    """_read_available_ram 抛异常时回退到 FALLBACK_RAM_MB（line 32-36）。"""
    import vibeocr.backend.utils.system_memory as sm

    def _raise():
        raise OSError("denied")

    monkeypatch.setattr(sm, "_read_available_ram", _raise)
    assert sm.get_available_ram_mb() == FALLBACK_RAM_MB


def test_read_available_ram_returns_none_on_unsupported_platform(monkeypatch):
    """非 win/linux 平台返回 None（line 43-45）。"""
    import vibeocr.backend.utils.system_memory as sm

    monkeypatch.setattr(sm.sys, "platform", "darwin")
    assert sm._read_available_ram() is None


def test_read_windows_returns_none_when_status_call_fails(monkeypatch):
    """GlobalMemoryStatusEx 返回 0（失败）时 _read_windows 返回 None（line 68）。"""
    import vibeocr.backend.utils.system_memory as sm

    class _FakeKernel32:
        def GlobalMemoryStatusEx(self, _ref):
            return 0  # 失败

    monkeypatch.setattr(sm.sys, "platform", "win32")
    # ctypes.windll.kernel32 在测试中替换
    import ctypes

    class _FakeWindll:
        kernel32 = _FakeKernel32()

    monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)
    assert sm._read_windows() is None


def test_read_available_ram_dispatches_to_linux_on_linux(monkeypatch):
    """sys.platform=linux 时调用 _read_linux 分支（line 44）。"""
    import vibeocr.backend.utils.system_memory as sm

    monkeypatch.setattr(sm.sys, "platform", "linux")
    # /proc/meminfo 不存在（Windows 测试主机）→ 返回 None，但分发路径被覆盖
    result = sm._read_available_ram()
    assert result is None


def test_get_available_ram_mb_falls_back_when_read_returns_none(monkeypatch):
    """_read_available_ram 返回 None/0 时回退（line 30->36 falsy 分支）。"""
    import vibeocr.backend.utils.system_memory as sm

    monkeypatch.setattr(sm, "_read_available_ram", lambda: None)
    assert sm.get_available_ram_mb() == FALLBACK_RAM_MB

    monkeypatch.setattr(sm, "_read_available_ram", lambda: 0)
    assert sm.get_available_ram_mb() == FALLBACK_RAM_MB


def test_estimate_cpu_batch_size_dead_branch_returns_1():
    """per_page_peak_mb<=0 在 avg_pixels>0 下不可达，但仍验证夹紧（line 110-111）。"""
    from vibeocr.backend.utils.system_memory import estimate_cpu_batch_size

    # 极大像素也不会出错
    assert estimate_cpu_batch_size(4096, 10**12) == 1
