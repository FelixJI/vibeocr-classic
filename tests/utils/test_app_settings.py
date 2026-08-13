"""AppSettings 单元测试"""

import json

import pytest

from vibeocr.classic.utils.app_settings import AppSettings


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path


@pytest.fixture
def settings(config_dir):
    return AppSettings(config_dir)


class TestAppSettingsDefaults:
    def test_show_toolbar_default_true(self, settings):
        assert settings.show_toolbar is True

    def test_auto_hide_toolbar_default_true(self, settings):
        assert settings.auto_hide_toolbar is True

    def test_hide_delay_default(self, settings):
        assert settings.hide_delay_ms == 500

    def test_toolbar_pos_default_none(self, settings):
        assert settings.toolbar_pos is None


class TestAppSettingsProperties:
    def test_set_show_toolbar(self, settings):
        settings.show_toolbar = False
        assert settings.show_toolbar is False

    def test_set_auto_hide_toolbar(self, settings):
        settings.auto_hide_toolbar = False
        assert settings.auto_hide_toolbar is False

    def test_set_toolbar_pos(self, settings):
        settings.toolbar_pos = {"x": 100, "y": 200}
        assert settings.toolbar_pos == {"x": 100, "y": 200}

    def test_set_toolbar_pos_none(self, settings):
        settings.toolbar_pos = {"x": 100, "y": 200}
        settings.toolbar_pos = None
        assert settings.toolbar_pos is None


class TestAppSettingsPersistence:
    def test_save_and_reload(self, config_dir):
        s1 = AppSettings(config_dir)
        s1.show_toolbar = False
        s1.auto_hide_toolbar = False
        s1.hide_delay_ms = 1000
        s1.toolbar_pos = {"x": 50, "y": 60}
        s1.save()

        s2 = AppSettings(config_dir)
        assert s2.show_toolbar is False
        assert s2.auto_hide_toolbar is False
        assert s2.hide_delay_ms == 1000
        assert s2.toolbar_pos == {"x": 50, "y": 60}


class TestAppSettingsBackwardCompat:
    def test_old_auto_hide_true(self, config_dir):
        """旧配置 auto_hide_toolbar=True → show_toolbar=True"""
        config_file = config_dir / "app_settings.json"
        config_file.write_text(
            json.dumps({"auto_hide_toolbar": True}), encoding="utf-8"
        )
        s = AppSettings(config_dir)
        assert s.show_toolbar is True
        assert s.auto_hide_toolbar is True

    def test_old_auto_hide_false(self, config_dir):
        """旧配置 auto_hide_toolbar=False → show_toolbar=False"""
        config_file = config_dir / "app_settings.json"
        config_file.write_text(
            json.dumps({"auto_hide_toolbar": False}), encoding="utf-8"
        )
        s = AppSettings(config_dir)
        assert s.show_toolbar is False
        assert s.auto_hide_toolbar is True


class TestAppSettingsConfigManagerMode:
    """ConfigManager 模式（非 Path）的 load/save 分支。"""

    class _FakeCM:
        """模拟 ConfigManager。"""

        def __init__(self, stored=None, config_dir=None):
            from pathlib import Path

            self.config_dir = config_dir or Path()
            self._stored = stored if stored is not None else {}

        def _load_json(self, filename, default=None):
            return dict(self._stored)

        def _save_json(self, filename, data):
            self._stored = dict(data)
            return True

    def test_init_with_config_manager(self, tmp_path):
        """ConfigManager 模式 init（line 49-51, 58）。"""
        cm = self._FakeCM(
            {"show_toolbar": False, "hide_delay_ms": 1000}, config_dir=tmp_path
        )
        settings = AppSettings(cm)
        assert settings.show_toolbar is False
        assert settings.hide_delay_ms == 1000

    def test_save_with_config_manager(self, tmp_path):
        """ConfigManager 模式 save（line 89-92）。"""
        cm = self._FakeCM(config_dir=tmp_path)
        settings = AppSettings(cm)
        settings.auto_start = True
        assert settings.save() is True
        assert cm._stored.get("auto_start") is True
        assert cm._stored.get("version") == 1


class TestAppSettingsPathModeEdgeCases:
    """Path 模式的异常/边界分支。"""

    def test_load_corrupt_json_uses_defaults(self, tmp_path):
        """损坏 JSON 时用默认值（line 67-69）。"""
        (tmp_path / "app_settings.json").write_text("{ corrupt")
        settings = AppSettings(tmp_path)
        assert settings.show_toolbar is True  # 默认

    def test_load_non_dict_data_uses_defaults(self, tmp_path):
        """JSON 非 dict 时用默认值（line 71-72）。"""
        (tmp_path / "app_settings.json").write_text("[1, 2, 3]")
        settings = AppSettings(tmp_path)
        assert settings.show_toolbar is True

    def test_save_merges_existing_non_dict(self, tmp_path):
        """save 合并已存在的非 dict 文件（line 101-104）。"""
        (tmp_path / "app_settings.json").write_text('"a string"')
        settings = AppSettings(tmp_path)
        assert settings.save() is True

    def test_save_exception_returns_false(self, tmp_path, monkeypatch):
        """save 写入异常时返回 False（line 110-112）。"""
        settings = AppSettings(tmp_path)

        def _fail_write(*_a, **_kw):
            raise OSError("denied")

        monkeypatch.setattr(
            "vibeocr.classic.utils.app_settings.write_json_atomic", _fail_write
        )
        assert settings.save() is False

    def test_backward_compat_infers_show_toolbar(self, tmp_path):
        """旧配置无 show_toolbar → 从 auto_hide_toolbar 推断（line 79-82）。"""
        import json

        (tmp_path / "app_settings.json").write_text(
            json.dumps({"auto_hide_toolbar": False})
        )
        settings = AppSettings(tmp_path)
        # show_toolbar 从旧 auto_hide_toolbar(False) 推断
        assert settings.show_toolbar is False
        assert settings.auto_hide_toolbar is True  # 被重置为 True

    def test_all_property_setters(self, tmp_path):
        """所有 property setter（line 143/147/151/155 等）。"""
        settings = AppSettings(tmp_path)
        settings.minimize_to_tray = True
        assert settings.minimize_to_tray is True
        settings.auto_start = True
        assert settings.auto_start is True
        settings.toolbar_pos = {"x": 10, "y": 20}
        assert settings.toolbar_pos == {"x": 10, "y": 20}
        settings.hide_delay_ms = 200
        assert settings.hide_delay_ms == 200
        # hide_delay_ms 夹紧到 [100, 5000]
        settings.hide_delay_ms = 50
        assert settings.hide_delay_ms == 100
        settings.hide_delay_ms = 99999
        assert settings.hide_delay_ms == 5000
