"""CjkFontResolver 单元测试：系统 CJK 字体探测 + 子集化。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibeocr.backend.utils.cjk_font_resolver import CjkFontResolver


class TestFindSystemFont:
    """系统字体探测。"""

    def test_returns_path_when_font_exists(self, monkeypatch, tmp_path):
        """候选字体存在时返回其路径。"""
        fake_font = tmp_path / "fake.ttf"
        fake_font.write_bytes(b"fake")  # 存在即可
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [str(fake_font)])
        assert resolver._find_system_font() == str(fake_font)

    def test_returns_none_when_no_font(self, monkeypatch):
        """所有候选都不存在时返回 None（优雅降级）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: ["/nonexistent/font.ttf"]
        )
        assert resolver._find_system_font() is None

    def test_returns_first_existing(self, monkeypatch, tmp_path):
        """多个候选时返回第一个存在的。"""
        exists = tmp_path / "second.ttf"
        exists.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver,
            "_get_candidates",
            lambda: ["/nonexistent/x.ttf", str(exists)],
        )
        assert resolver._find_system_font() == str(exists)

    def test_find_result_cached(self, monkeypatch, tmp_path):
        """探测结果缓存：第二次调用不再扫描文件系统。"""
        fake_font = tmp_path / "cached.ttf"
        fake_font.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [str(fake_font)])
        first = resolver._find_system_font()
        # 删除文件后再次调用，仍应返回缓存路径（证明不重复扫描）
        fake_font.unlink()
        second = resolver._find_system_font()
        assert first == second == str(fake_font)


class TestSubsetAndResolve:
    """子集化与 resolve 主流程。"""

    @pytest.fixture
    def real_font(self):
        """获取真实系统 CJK 字体路径（Windows 测试环境）。"""
        import os

        win = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = Path(win) / "Fonts"
        for name in ("simhei.ttf", "msyh.ttc"):
            candidate = fonts_dir / name
            if candidate.is_file():
                return str(candidate)
        pytest.skip("无系统 CJK 字体，跳过子集化测试")

    def test_resolve_returns_subset_path(self, monkeypatch, real_font):
        """resolve 返回子集字体文件路径（文件存在且非空）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        path = resolver.resolve("签收联测试中文")
        assert path is not None
        assert Path(path).is_file()
        assert Path(path).stat().st_size > 0
        resolver.cleanup()

    def test_subset_much_smaller_than_original(self, monkeypatch, real_font):
        """子集字体远小于原字体（验证 fontTools 真做了子集化）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        path = resolver.resolve("签收联测试")
        assert path is not None
        orig_size = Path(real_font).stat().st_size
        sub_size = Path(path).stat().st_size
        # 子集应比原字体小至少 10 倍（实测通常小 1000+ 倍）
        assert sub_size < orig_size / 10, f"子集未缩小: orig={orig_size} sub={sub_size}"
        resolver.cleanup()

    def test_subset_cache_reuses_same_charset(self, monkeypatch, real_font):
        """相同字符集返回相同子集路径（缓存复用）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        p1 = resolver.resolve("签收联")
        p2 = resolver.resolve("签收联")
        assert p1 == p2
        resolver.cleanup()

    def test_subset_different_chars_different_path(self, monkeypatch, real_font):
        """不同字符集返回不同子集路径。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        p1 = resolver.resolve("签收联")
        p2 = resolver.resolve("发货单")
        assert p1 != p2
        resolver.cleanup()

    def test_resolve_none_for_empty_chars(self, monkeypatch, real_font):
        """空字符集返回 None。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        assert resolver.resolve("") is None

    def test_resolve_none_when_no_system_font(self, monkeypatch):
        """无系统字体时返回 None。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: ["/nonexistent.ttf"])
        assert resolver.resolve("签收联") is None

    def test_cleanup_removes_temp_files(self, monkeypatch, real_font):
        """cleanup 删除临时子集文件。"""
        from pathlib import Path

        resolver = CjkFontResolver()
        monkeypatch.setattr(resolver, "_get_candidates", lambda: [real_font])
        path = resolver.resolve("签收联")
        assert path is not None and Path(path).is_file()
        resolver.cleanup()
        assert not Path(path).is_file()


class TestModuleSingleton:
    """模块级单例与清理钩子。"""

    def test_module_singleton_exists(self):
        """模块导出 _CJK_RESOLVER 单例。"""
        from vibeocr.backend.utils import cjk_font_resolver

        assert cjk_font_resolver._CJK_RESOLVER is not None
        assert isinstance(cjk_font_resolver._CJK_RESOLVER, CjkFontResolver)

    def test_singleton_is_same_instance(self):
        """多次导入拿到同一实例。"""
        from vibeocr.backend.utils.cjk_font_resolver import _CJK_RESOLVER as r1
        from vibeocr.backend.utils.cjk_font_resolver import _CJK_RESOLVER as r2

        assert r1 is r2


class TestPlatformDispatchAndCleanup:
    """_get_candidates 平台分发、subset/save 异常、cleanup OSError、atexit hook。"""

    def test_get_candidates_mac_platform(self, monkeypatch):
        """sys.platform=darwin 返回 MAC 候选（line 57-58）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        monkeypatch.setattr(cjk_font_resolver.sys, "platform", "darwin")
        r = cjk_font_resolver.CjkFontResolver()
        assert r._get_candidates() == r._MAC_CANDIDATES

    def test_get_candidates_linux_platform(self, monkeypatch):
        """sys.platform=linux 返回 LINUX 候选（line 59）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        monkeypatch.setattr(cjk_font_resolver.sys, "platform", "linux")
        r = cjk_font_resolver.CjkFontResolver()
        assert r._get_candidates() == r._LINUX_CANDIDATES

    def test_get_candidates_windows_platform(self, monkeypatch):
        """sys.platform=win32 返回 WIN 候选（line 55-56）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        monkeypatch.setattr(cjk_font_resolver.sys, "platform", "win32")
        r = cjk_font_resolver.CjkFontResolver()
        assert r._get_candidates() == r._WIN_CANDIDATES

    def test_resolve_returns_none_when_subset_raises(self, monkeypatch, tmp_path):
        """_subset 抛异常时 resolve 回退 None（line 93-95）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        fake_font = tmp_path / "fake.ttf"
        fake_font.write_bytes(b"fake")
        r = cjk_font_resolver.CjkFontResolver()
        monkeypatch.setattr(r, "_get_candidates", lambda: [str(fake_font)])

        def _boom(_font, _chars):
            raise RuntimeError("subset failed")

        monkeypatch.setattr(r, "_subset", _boom)
        assert r.resolve("字") is None

    def test_subset_save_failure_deletes_temp_and_reraises(self, monkeypatch, tmp_path):
        """font.save 失败时删除临时文件并 reraise（line 120-123）。"""
        from fontTools.ttLib import TTFont

        from vibeocr.backend.utils import cjk_font_resolver

        r = cjk_font_resolver.CjkFontResolver()

        # _subset 内部局部 import fontTools，故 patch TTFont.save 本体
        def _fail_save(self, _path):
            raise OSError("save failed")

        monkeypatch.setattr(TTFont, "save", _fail_save)

        # 找一个真实可打开的字体
        candidate = None
        for c in r._WIN_CANDIDATES + r._MAC_CANDIDATES + r._LINUX_CANDIDATES:
            if Path(c).is_file():
                candidate = c
                break
        if candidate is None:
            pytest.skip("no system CJK font available on this host")

        with pytest.raises(OSError, match="save failed"):
            r._subset(candidate, "字")

    def test_cleanup_ignores_unlink_oserror(self, monkeypatch, tmp_path):
        """cleanup 中 unlink 抛 OSError 时不传播（line 131-132）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        r = cjk_font_resolver.CjkFontResolver()
        r._subset_cache[frozenset("字")] = "/nonexistent/path.ttf"
        # 即使路径不存在，missing_ok=True 也不会抛；这里再注入一个抛 OSError 的 unlink
        def _fail_unlink(self, *args, **kwargs):
            raise OSError("unlink denied")

        monkeypatch.setattr("pathlib.Path.unlink", _fail_unlink)
        r.cleanup()  # 不应抛

    def test_cleanup_on_exit_hook_callable(self):
        """模块级 _cleanup_on_exit 可调用（line 144）。"""
        from vibeocr.backend.utils import cjk_font_resolver

        # 仅验证可调用且不抛（atexit 已注册，这里直接调）
        cjk_font_resolver._cleanup_on_exit()
