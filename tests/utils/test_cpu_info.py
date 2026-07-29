"""cpu_info 工具单元测试。"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from vibeocr.backend.utils import cpu_info
from vibeocr.backend.utils.cpu_info import (
    CPU_THREADS_CAP,
    _version_in_range,
    detect_cpu_features,
    get_cpu_thread_count,
)

# ---------------------------------------------------------------------------
# get_cpu_thread_count
# ---------------------------------------------------------------------------


def test_get_cpu_thread_count_returns_positive_int():
    """在真实环境上应返回正值。"""
    result = get_cpu_thread_count()
    assert isinstance(result, int)
    assert result >= 1


def test_get_cpu_thread_count_capped(monkeypatch):
    """逻辑核数超过上限时夹到 CPU_THREADS_CAP。"""
    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)
    with patch("vibeocr.backend.utils.cpu_info.os.cpu_count", return_value=128):
        assert get_cpu_thread_count() == CPU_THREADS_CAP


def test_get_cpu_thread_count_respects_user_override(monkeypatch):
    """VIBEOCR_CPU_THREADS 显式覆盖优先，且不受上限限制。"""
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "24")
    with patch("vibeocr.backend.utils.cpu_info.os.cpu_count", return_value=4):
        assert get_cpu_thread_count() == 24


def test_get_cpu_thread_count_invalid_override_ignored(monkeypatch):
    """非整数的覆盖值被忽略，回退到探测。"""
    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "abc")
    with patch("vibeocr.backend.utils.cpu_info.os.cpu_count", return_value=8):
        assert get_cpu_thread_count() == 8


def test_get_cpu_thread_count_fallback_on_probe_failure(monkeypatch):
    """cpu_count 返回 None 时回退到 FALLBACK_CPU_THREADS。"""
    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)
    with patch("vibeocr.backend.utils.cpu_info.os.cpu_count", return_value=None):
        assert get_cpu_thread_count() == cpu_info.FALLBACK_CPU_THREADS


# ---------------------------------------------------------------------------
# detect_cpu_features
# ---------------------------------------------------------------------------


def test_detect_cpu_features_returns_dict_with_expected_keys():
    """返回的字典必须含约定键。"""
    feats = detect_cpu_features()
    assert set(feats.keys()) == {"avx", "avx2", "avx512", "fma", "amx"}


def test_detect_cpu_features_when_flags_empty():
    """flags 探测为空时所有特性为 False。"""
    with patch("vibeocr.backend.utils.cpu_info._read_cpu_flags_text", return_value=""):
        feats = detect_cpu_features()
    assert feats == {
        "avx": False,
        "avx2": False,
        "avx512": False,
        "fma": False,
        "amx": False,
    }


def test_detect_cpu_features_parses_linux_flags():
    """Linux flags 行正确解析各指令集（含 AVX-512 子集）。"""
    flags = "fpu vme de pe avx avx2 fma avx512f avx512cd amx_bf16"
    with patch("vibeocr.backend.utils.cpu_info._read_cpu_flags_text", return_value=flags):
        feats = detect_cpu_features()
    assert feats["avx"] is True
    assert feats["avx2"] is True
    assert feats["fma"] is True
    assert feats["avx512"] is True
    assert feats["amx"] is True


# ---------------------------------------------------------------------------
# can_safely_enable_onednn
# ---------------------------------------------------------------------------


def test_onednn_force_enable(monkeypatch):
    """VIBEOCR_FORCE_ONEDNN=1 强制启用。"""
    monkeypatch.setenv("VIBEOCR_FORCE_ONEDNN", "1")
    safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is True
    assert "强制启用" in reason


def test_onednn_force_disable(monkeypatch):
    """VIBEOCR_FORCE_ONEDNN=0 强制禁用。"""
    monkeypatch.setenv("VIBEOCR_FORCE_ONEDNN", "0")
    safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "强制禁用" in reason


def test_onednn_rejected_without_avx2(monkeypatch):
    """无 AVX2 的 CPU 一律拒绝。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.4.0")
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": False,
            "avx512": False,
            "fma": False,
            "amx": False,
        },
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "AVX2" in reason


def test_onednn_rejected_for_blacklisted_paddle(monkeypatch):
    """paddle 3.3.x 落在黑名单内则拒绝。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.3.1")
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": True,
            "avx512": False,
            "fma": False,
            "amx": False,
        },
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "77340" in reason


def test_onednn_rejected_for_unvalidated_future_paddle(monkeypatch):
    """未来版本即使有 AVX2，也不能因不在黑名单就自动启用。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.4.0")
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": True,
            "avx512": True,
            "fma": True,
            "amx": False,
        },
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "尚未通过" in reason


def test_onednn_rejected_when_paddle_version_unknown(monkeypatch):
    """paddle 未安装或导入失败时必须 fail-closed。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: None)
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": True,
            "avx512": False,
            "fma": False,
            "amx": False,
        },
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is False
    assert "无法读取" in reason


def test_onednn_allowed_only_for_validated_paddle_range(monkeypatch):
    """只有显式加入真实推理验证范围的版本才默认启用。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.4.1")
    monkeypatch.setattr(
        cpu_info,
        "_ONEDNN_VALIDATED_SAFE_PADDLE_RANGES",
        [("3.4.0", "3.4.2")],
    )
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": True,
            "avx512": True,
            "fma": True,
            "amx": False,
        },
    ):
        safe, reason = cpu_info.can_safely_enable_onednn()
    assert safe is True
    assert "已通过验证" in reason


def test_onednn_paddle_version_with_build_suffix(monkeypatch):
    """带构建后缀的版本号（如 3.3.1+cu126）也正确判定为黑名单。"""
    monkeypatch.delenv("VIBEOCR_FORCE_ONEDNN", raising=False)
    monkeypatch.setattr(cpu_info, "_get_paddle_version", lambda: "3.3.1+cu126")
    with patch(
        "vibeocr.backend.utils.cpu_info.detect_cpu_features",
        return_value={
            "avx": True,
            "avx2": True,
            "avx512": False,
            "fma": False,
            "amx": False,
        },
    ):
        safe, _ = cpu_info.can_safely_enable_onednn()
    assert safe is False


# ---------------------------------------------------------------------------
# _version_in_range
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ver,lo,hi,expected",
    [
        ("3.3.1", "3.3.0", "3.3.99", True),
        ("3.3.0", "3.3.0", "3.3.99", True),
        ("3.3.99", "3.3.0", "3.3.99", True),
        ("3.4.0", "3.3.0", "3.3.99", False),
        ("3.2.9", "3.3.0", "3.3.99", False),
        ("3.3.1+cu126", "3.3.0", "3.3.99", True),
    ],
)
def test_version_in_range(ver, lo, hi, expected):
    assert _version_in_range(ver, lo, hi) is expected


# ---------------------------------------------------------------------------
# _ver_tuple
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ver,expected",
    [
        ("3.3.1", (3, 3, 1)),
        ("3.3.1+cu126", (3, 3, 1)),
        ("3.3.1~rc0", (3, 3, 1)),
        ("1", (1,)),
        ("10.20.30", (10, 20, 30)),
    ],
)
def test_ver_tuple_parses_core_version(ver, expected):
    from vibeocr.backend.utils.cpu_info import _ver_tuple

    assert _ver_tuple(ver) == expected


def test_version_in_range_invalid_returns_false():
    """畸形版本号返回 False 而非抛异常"""
    assert _version_in_range("not.a.version", "3.0", "4.0") is False
    assert _version_in_range(None, "3.0", "4.0") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _get_paddle_version
# ---------------------------------------------------------------------------


def test_get_paddle_version_returns_version_when_installed():
    """paddle 已安装且有 __version__ 时返回版本号"""
    import sys
    from types import ModuleType

    fake = ModuleType("paddle")
    fake.__version__ = "3.3.1"  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"paddle": fake}):
        from vibeocr.backend.utils.cpu_info import _get_paddle_version

        assert _get_paddle_version() == "3.3.1"


def test_get_paddle_version_none_when_not_installed():
    """paddle 未安装时返回 None"""
    import sys

    with patch.dict(sys.modules, {"paddle": None}):
        from vibeocr.backend.utils.cpu_info import _get_paddle_version

        assert _get_paddle_version() is None


def test_get_paddle_version_none_when_empty_version():
    """paddle 有 __version__ 但为空 → None"""
    import sys
    from types import ModuleType

    fake = ModuleType("paddle")
    fake.__version__ = ""  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"paddle": fake}):
        from vibeocr.backend.utils.cpu_info import _get_paddle_version

        assert _get_paddle_version() is None


# ---------------------------------------------------------------------------
# _read_windows_features / _read_linux_flags
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows 指令集探测仅在 Windows 可测",
)
def test_read_windows_features_returns_known_flags():
    """Windows 上 _read_windows_features 应返回已知指令集子集（非空）"""
    from vibeocr.backend.utils.cpu_info import _read_windows_features

    result = _read_windows_features()
    # 现代 x86 CPU 至少有 sse/sse2
    flags = set(result.split())
    assert "sse" in flags or "sse2" in flags


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="非 Windows 上 ctypes.windll 不存在，测异常回退",
)
def test_read_windows_features_returns_empty_on_non_windows():
    """非 Windows 平台 _read_windows_features 走 except 返回空串"""
    from vibeocr.backend.utils.cpu_info import _read_windows_features

    assert _read_windows_features() == ""


def test_read_linux_flags_missing_proc(monkeypatch):
    """/proc/cpuinfo 不存在时返回空串"""
    from vibeocr.backend.utils.cpu_info import _read_linux_flags

    # Windows 上 /proc/cpuinfo 不存在，自然走 except 返回空串
    result = _read_linux_flags()
    assert isinstance(result, str)



def test_get_cpu_thread_count_falls_back_when_cpu_count_raises(monkeypatch):
    """os.cpu_count() 抛异常时回退（line 55-56）。"""
    from vibeocr.backend.utils import cpu_info

    monkeypatch.delenv("VIBEOCR_CPU_THREADS", raising=False)

    def _raise():
        raise OSError("probe failed")

    monkeypatch.setattr(cpu_info.os, "cpu_count", _raise)
    assert cpu_info.get_cpu_thread_count() == cpu_info.FALLBACK_CPU_THREADS


def test_read_cpu_features_dispatches_by_os_name(monkeypatch):
    """detect_cpu_features 按 os.name 分发；非 nt/posix 返回空（line 98-102）。"""
    from vibeocr.backend.utils import cpu_info

    # posix 分发到 _read_linux_flags（Windows 测试主机上 /proc 不存在 → 空串）
    monkeypatch.setattr(cpu_info.os, "name", "posix")
    flags = cpu_info._read_cpu_flags_text()
    assert isinstance(flags, str)

    # 未知 os.name → 空串
    monkeypatch.setattr(cpu_info.os, "name", "unknown_os")
    assert cpu_info._read_cpu_flags_text() == ""


def test_read_windows_features_all_branches_via_fake_kernel(monkeypatch):
    """用 fake kernel32 精确触发每个 feature 分支（含 avx512f）。

    ctypes.windll 是 C 实现的特殊属性，无法直接 monkeypatch；
    故改为 patch 模块内的 ctypes 引用，间接注入 fake。
    """
    import types as _types

    from vibeocr.backend.utils import cpu_info

    supported = {1, 10, 13, 39, 40, 43}  # SSE/SSE2/SSE3/AVX/AVX2/AVX512F

    class _FakeKernel:
        def IsProcessorFeaturePresent(self, fid):
            return 1 if int(fid) in supported else 0

    class _FakeWindll:
        kernel32 = _FakeKernel()

    fake_ctypes = _types.SimpleNamespace(
        windll=_FakeWindll(),
        c_uint=lambda v: v,
        sizeof=lambda _c: 0,
        byref=lambda _x: _x,
        Structure=type("S", (), {}),
        c_ulong=int,
        c_ulonglong=int,
    )
    # 函数内 ``import ctypes`` 拿到的是 sys.modules['ctypes']，这里直接替换
    import sys

    orig_ctypes = sys.modules["ctypes"]
    try:
        sys.modules["ctypes"] = fake_ctypes
        result = cpu_info._read_windows_features()
    finally:
        sys.modules["ctypes"] = orig_ctypes
    flags = set(result.split())
    assert flags == {"sse", "sse2", "sse3", "avx", "avx2", "avx512f"}


def test_read_windows_features_falls_back_when_kernel_call_raises():
    """kernel32 调用抛异常时返回空串（line 142-144）。"""
    import sys
    import types as _types

    from vibeocr.backend.utils import cpu_info

    class _BrokenKernel:
        def IsProcessorFeaturePresent(self, _fid):
            raise OSError("kernel broken")

    class _FakeWindll:
        kernel32 = _BrokenKernel()

    fake_ctypes = _types.SimpleNamespace(
        windll=_FakeWindll(),
        c_uint=lambda v: v,
        sizeof=lambda _c: 0,
        byref=lambda _x: _x,
    )
    orig_ctypes = sys.modules["ctypes"]
    try:
        sys.modules["ctypes"] = fake_ctypes
        assert cpu_info._read_windows_features() == ""
    finally:
        sys.modules["ctypes"] = orig_ctypes


def test_read_windows_features_partial_support_exercises_false_branches():
    """只支持 SSE/AVX2（其余 False），覆盖每个 if 的 False 分支。"""
    import sys
    import types as _types

    from vibeocr.backend.utils import cpu_info

    supported = {1, 40}  # 仅 SSE + AVX2

    class _PartialKernel:
        def IsProcessorFeaturePresent(self, fid):
            return 1 if int(fid) in supported else 0

    class _FakeWindll:
        kernel32 = _PartialKernel()

    fake_ctypes = _types.SimpleNamespace(
        windll=_FakeWindll(),
        c_uint=lambda v: v,
        sizeof=lambda _c: 0,
        byref=lambda _x: _x,
    )
    orig_ctypes = sys.modules["ctypes"]
    try:
        sys.modules["ctypes"] = fake_ctypes
        result = cpu_info._read_windows_features()
    finally:
        sys.modules["ctypes"] = orig_ctypes
    flags = set(result.split())
    assert flags == {"sse", "avx2"}


def test_get_cpu_thread_count_non_positive_override_ignored(monkeypatch):
    """VIBEOCR_CPU_THREADS 是有效整数但 <=0 时忽略（line 48->53）。"""
    from vibeocr.backend.utils import cpu_info

    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "0")
    # 应回退到正常 cpu_count 路径，返回正值
    result = cpu_info.get_cpu_thread_count()
    assert result >= 1

    monkeypatch.setenv("VIBEOCR_CPU_THREADS", "-4")
    result2 = cpu_info.get_cpu_thread_count()
    assert result2 >= 1


def test_read_windows_features_no_support_returns_empty():
    """CPU 不支持任何已知指令集时返回空串（覆盖所有 if False 分支）。"""
    import sys
    import types as _types

    from vibeocr.backend.utils import cpu_info

    class _NoSupportKernel:
        def IsProcessorFeaturePresent(self, _fid):
            return 0

    class _FakeWindll:
        kernel32 = _NoSupportKernel()

    fake_ctypes = _types.SimpleNamespace(
        windll=_FakeWindll(),
        c_uint=lambda v: v,
        sizeof=lambda _c: 0,
        byref=lambda _x: _x,
    )
    orig_ctypes = sys.modules["ctypes"]
    try:
        sys.modules["ctypes"] = fake_ctypes
        assert cpu_info._read_windows_features() == ""
    finally:
        sys.modules["ctypes"] = orig_ctypes
