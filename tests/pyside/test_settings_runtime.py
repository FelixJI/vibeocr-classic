"""settings_runtime Qt-free 逻辑测试。

覆盖 get_log_level/set_log_level 的 RuntimeError 回退与正常路径。
"""

from vibeocr.classic.pyside.settings_runtime import get_log_level, set_log_level


def test_get_log_level_returns_info_when_config_unavailable(monkeypatch):
    """ConfigManager 未初始化时 get_log_level 回退 INFO（line 28-31）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    def _raise():
        raise RuntimeError("config not initialized")

    monkeypatch.setattr(sr, "_config_manager", _raise)
    assert get_log_level() == "INFO"


def test_set_log_level_returns_false_when_config_unavailable(monkeypatch):
    """ConfigManager 未初始化时 set_log_level 返回 False（line 38-39）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    def _raise():
        raise RuntimeError("config not initialized")

    monkeypatch.setattr(sr, "_config_manager", _raise)
    assert set_log_level("DEBUG") is False


def test_get_log_level_delegates_to_config(monkeypatch):
    """正常路径：委托 ConfigManager.get_log_level（line 27）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    class _FakeConfig:
        def get_log_level(self):
            return "WARNING"

    monkeypatch.setattr(sr, "_config_manager", lambda: _FakeConfig())
    assert get_log_level() == "WARNING"


def test_set_log_level_success_applies(monkeypatch):
    """set_log_level 成功时调用 _apply_log_level（line 40-43）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    applied = {"level": None}

    class _FakeConfig:
        def set_log_level(self, level):
            return True

    def _fake_apply(level):
        applied["level"] = level

    monkeypatch.setattr(sr, "_config_manager", lambda: _FakeConfig())
    monkeypatch.setattr(sr, "_apply_log_level", _fake_apply)
    assert set_log_level("DEBUG") is True
    assert applied["level"] == "DEBUG"


def test_set_log_level_returns_false_when_config_rejects(monkeypatch):
    """ConfigManager.set_log_level 返回 False 时传播（line 40-41）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    class _FakeConfig:
        def set_log_level(self, level):
            return False

    monkeypatch.setattr(sr, "_config_manager", lambda: _FakeConfig())
    assert set_log_level("BOGUS") is False


def test_config_manager_lazy_import_works():
    """_config_manager 的 lazy import 链可正常执行（line 12-14）。"""
    import vibeocr.classic.pyside.settings_runtime as sr

    # 直接调用内部函数，触发真实 import（可能因 ConfigManager 未初始化抛 RuntimeError）
    try:
        sr._config_manager()
    except RuntimeError:
        pass  # 预期：测试环境 ConfigManager 可能未初始化


def test_apply_log_level_lazy_import_works(caplog):
    """_apply_log_level 的 lazy import 链可执行（line 19-21）。"""
    import logging

    import vibeocr.classic.pyside.settings_runtime as sr

    logging.getLogger("vibeocr").setLevel("DEBUG")
    sr._apply_log_level("INFO")  # 不应抛
