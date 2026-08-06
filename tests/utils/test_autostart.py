"""autostart 单元测试。

覆盖：
- ``_get_exe_path`` 的 frozen / 非 frozen 分支。
- 平台分发（mock ``sys.platform``）。
- Windows ``.lnk`` 自启的启用/禁用（mock ``create_windows_shortcut`` 与
  文件系统）。
- ``migrate_legacy_autostart`` 的迁移、幂等与失败回退（注入 fake winreg）。
- ``set_autostart`` 异常吞掉返回 False。

非 Windows 环境通过 monkeypatch ``sys.platform`` 和注入 fake ``winreg``
来覆盖 Windows 分支，避免依赖真实 Windows；平台特定语义保留
``skip_non_windows`` 用于个别断言。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vibeocr.classic.utils import autostart


# ---------------------------------------------------------------------------
# 通用 fixture：把 Windows 分支所需的 APPDATA 指向临时目录，使
# ``get_windows_startup_dir`` 在非 Windows 测试机也能计算出真实路径。
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_appdata(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


# ===========================================================================
# _get_exe_path
# ===========================================================================


class TestGetExePath:
    def test_development_mode_returns_module_form(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        # ``sys.executable`` 在测试进程里是真实解释器路径
        expected = f'"{sys.executable}" -m vibeocr'
        assert autostart._get_exe_path() == expected

    def test_frozen_mode_returns_sys_executable(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "/frozen/VibeOCR.exe")
        assert autostart._get_exe_path() == "/frozen/VibeOCR.exe"


# ===========================================================================
# 平台分发
# ===========================================================================


class TestPlatformDispatch:
    def test_is_autostart_enabled_dispatches_windows(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        called = {}

        def fake_is_enabled():
            called["win"] = True
            return False

        monkeypatch.setattr(autostart, "_win32_is_enabled", fake_is_enabled)
        assert autostart.is_autostart_enabled() is False
        assert called == {"win": True}

    def test_is_autostart_enabled_dispatches_macos(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "darwin")
        monkeypatch.setattr(autostart, "_macos_is_enabled", lambda: True)
        assert autostart.is_autostart_enabled() is True

    def test_is_autostart_enabled_dispatches_linux(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "linux")
        monkeypatch.setattr(autostart, "_linux_is_enabled", lambda: True)
        assert autostart.is_autostart_enabled() is True

    def test_set_autostart_dispatches_windows(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        captured = {}

        def fake_set(enabled):
            captured["enabled"] = enabled
            return True

        monkeypatch.setattr(autostart, "_win32_set", fake_set)
        assert autostart.set_autostart(True) is True
        assert captured == {"enabled": True}


# ===========================================================================
# Windows .lnk 实现
# ===========================================================================


class TestWin32Lnk:
    def test_is_enabled_reflects_shortcut_existence(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        startup = autostart.get_windows_startup_dir()
        startup.mkdir(parents=True, exist_ok=True)
        lnk = startup / autostart._WIN_LNK_NAME

        assert autostart._win32_is_enabled() is False
        lnk.write_text("dummy")
        assert autostart._win32_is_enabled() is True

    def test_set_true_creates_shortcut(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            autostart.sys, "executable", str(fake_appdata / "VibeOCR.exe")
        )

        created = {}

        def fake_create(
            target, shortcut_path, description="", icon_path="", working_dir=""
        ):
            created["target"] = target
            created["shortcut_path"] = shortcut_path
            Path(shortcut_path).parent.mkdir(parents=True, exist_ok=True)
            Path(shortcut_path).write_text("lnk")
            return True

        monkeypatch.setattr(autostart, "create_windows_shortcut", fake_create)
        assert autostart._win32_set(True) is True
        assert created["shortcut_path"] == str(
            autostart.get_windows_startup_dir() / autostart._WIN_LNK_NAME
        )
        assert autostart._win32_is_enabled() is True

    def test_set_false_removes_shortcut(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        startup = autostart.get_windows_startup_dir()
        startup.mkdir(parents=True, exist_ok=True)
        lnk = startup / autostart._WIN_LNK_NAME
        lnk.write_text("dummy")
        assert autostart._win32_is_enabled() is True

        assert autostart._win32_set(False) is True
        assert not lnk.exists()
        assert autostart._win32_is_enabled() is False

    def test_set_false_missing_shortcut_is_idempotent(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        # 不预先创建 .lnk，禁用应仍成功（missing_ok 语义）
        assert autostart._win32_set(False) is True

    def test_set_true_returns_false_when_shortcut_creation_fails(
        self, monkeypatch, fake_appdata
    ):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        monkeypatch.setattr(autostart, "create_windows_shortcut", lambda *a, **k: False)
        assert autostart._win32_set(True) is False


# ===========================================================================
# migrate_legacy_autostart
# ===========================================================================


class _FakeKey:
    """极简 fake winreg key，支持上下文管理与 QueryValueEx / DeleteValue。"""

    def __init__(self, has_value: bool, value: str = "C:\\VibeOCR.exe"):
        self._has_value = has_value
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query_value_ex(self, name):
        if not self._has_value:
            raise FileNotFoundError(name)
        # 第二个元素是注册表类型，固定 REG_SZ(1)
        return self._value, 1

    def delete_value(self, name):
        if not self._has_value:
            raise FileNotFoundError(name)
        self._has_value = False


def _install_fake_winreg(monkeypatch, has_value: bool):
    """向 ``sys.modules`` 注入 fake winreg 并让 autostart 使用它。"""
    fake_key = _FakeKey(has_value=has_value)

    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = 1
    fake_winreg.KEY_READ = 0x20019
    fake_winreg.KEY_SET_VALUE = 0x0002
    fake_winreg.REG_SZ = 1

    def open_key(root, subkey, reserved=0, access=0):
        return fake_key

    fake_winreg.OpenKey = open_key

    def query_value_ex(key, name):
        return key.query_value_ex(name)

    fake_winreg.QueryValueEx = query_value_ex

    def delete_value(key, name):
        key.delete_value(name)

    fake_winreg.DeleteValue = delete_value

    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    # autostart.migrate_legacy_autostart 内部 ``import winreg`` 会拿到该假模块
    return fake_key


class TestMigrateLegacyAutostart:
    def test_non_windows_is_noop(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "darwin")
        # 不应抛异常、不应触碰任何东西
        autostart.migrate_legacy_autostart()

    def test_migrates_when_legacy_registry_present(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        fake_key = _install_fake_winreg(monkeypatch, has_value=True)

        created = {}

        def fake_create(
            target, shortcut_path, description="", icon_path="", working_dir=""
        ):
            created["shortcut_path"] = shortcut_path
            Path(shortcut_path).parent.mkdir(parents=True, exist_ok=True)
            Path(shortcut_path).write_text("lnk")
            return True

        monkeypatch.setattr(autostart, "create_windows_shortcut", fake_create)

        autostart.migrate_legacy_autostart()

        # 旧注册表项已被删除
        assert fake_key._has_value is False
        # 新 .lnk 已创建在启动文件夹
        assert (autostart.get_windows_startup_dir() / autostart._WIN_LNK_NAME).exists()

    def test_no_legacy_is_noop(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        _install_fake_winreg(monkeypatch, has_value=False)

        def fake_create(*a, **k):
            pytest.fail("旧注册表项不存在时不应创建 .lnk")

        monkeypatch.setattr(autostart, "create_windows_shortcut", fake_create)
        autostart.migrate_legacy_autostart()
        assert not (
            autostart.get_windows_startup_dir() / autostart._WIN_LNK_NAME
        ).exists()

    def test_keeps_legacy_when_shortcut_creation_fails(self, monkeypatch, fake_appdata):
        monkeypatch.setattr(autostart.sys, "platform", "win32")
        fake_key = _install_fake_winreg(monkeypatch, has_value=True)
        monkeypatch.setattr(autostart, "create_windows_shortcut", lambda *a, **k: False)

        autostart.migrate_legacy_autostart()

        # .lnk 创建失败时应保留旧注册表项，避免丢失自启
        assert fake_key._has_value is True


# ===========================================================================
# set_autostart 异常吞掉
# ===========================================================================


class TestSetAutostartErrorHandling:
    def test_set_autostart_returns_false_on_exception(self, monkeypatch):
        monkeypatch.setattr(autostart.sys, "platform", "win32")

        def boom(_enabled):
            raise RuntimeError("boom")

        monkeypatch.setattr(autostart, "_win32_set", boom)
        assert autostart.set_autostart(True) is False
