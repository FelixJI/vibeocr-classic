"""应用级设置管理

管理工具栏自动隐藏、系统托盘最小化、开机自启动等设置的持久化。
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from vibeocr.classic.json_storage import write_json_atomic

if TYPE_CHECKING:
    from vibeocr.classic.managers.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# 默认设置
_DEFAULTS = {
    "show_toolbar": True,
    "auto_hide_toolbar": True,
    "minimize_to_tray": False,
    "auto_start": False,
    # 仅 Windows 用于一次性迁移旧版注册表自启到启动文件夹 .lnk；
    # 其它平台不读取，仅随配置文件持久化保持跨平台兼容。
    "autostart_migrated_to_lnk": False,
    "hide_delay_ms": 500,
    "toolbar_peek_pixels": 3,
    "toolbar_pos": None,
}

_CONFIG_FILENAME = "app_settings.json"

# 配置版本号
_CONFIG_VERSION = 1


class AppSettings:
    """应用设置管理器

    负责加载、保存和访问应用级设置。

    Usage:
        settings = AppSettings(config_manager)
        settings.auto_hide_toolbar = True
        settings.save()
    """

    def __init__(self, config_manager: "ConfigManager | Path") -> None:
        # 兼容旧的 Path 参数（逐步废弃）
        if isinstance(config_manager, Path):
            self._cm = None
            self._config_dir = config_manager
            self._config_path = config_manager / _CONFIG_FILENAME
        else:
            self._cm = config_manager
            self._config_dir = config_manager.config_dir
            self._config_path = self._config_dir / _CONFIG_FILENAME
        self._data: dict = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        """加载配置文件"""
        if self._cm is not None:
            data = self._cm._load_json(_CONFIG_FILENAME, {})
        else:
            import json

            if not self._config_path.exists():
                return
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"加载应用设置失败: {e}")
                return

        if not isinstance(data, dict):
            data = {}

        for key in _DEFAULTS:
            if key in data:
                self._data[key] = data[key]

        # 向后兼容：旧配置没有 show_toolbar，从旧的 auto_hide_toolbar 推断
        if "show_toolbar" not in data:
            old_auto_hide = self._data.get("auto_hide_toolbar", True)
            self._data["show_toolbar"] = old_auto_hide
            self._data["auto_hide_toolbar"] = True

        logger.debug("应用设置已加载")

    def save(self) -> bool:
        """保存配置到文件（合并写入，保留其他模块写入的同文件键）"""
        if self._cm is not None:
            existing = self._cm._load_json(_CONFIG_FILENAME, {})
            existing.update(self._data)
            existing["version"] = _CONFIG_VERSION
            return self._cm._save_json(_CONFIG_FILENAME, existing)

        # 旧路径兼容
        import json

        try:
            existing = {}
            if self._config_path.exists():
                with open(self._config_path, encoding="utf-8") as f:
                    existing = json.load(f)
                    if not isinstance(existing, dict):
                        existing = {}
            existing.update(self._data)
            existing["version"] = _CONFIG_VERSION
            write_json_atomic(self._config_path, existing)
            return True
        except Exception as e:
            logger.error(f"保存应用设置失败: {e}")
            return False

    # ---- 属性 ----

    @property
    def show_toolbar(self) -> bool:
        return bool(self._data.get("show_toolbar", True))

    @show_toolbar.setter
    def show_toolbar(self, value: bool) -> None:
        self._data["show_toolbar"] = value

    @property
    def auto_hide_toolbar(self) -> bool:
        return bool(self._data.get("auto_hide_toolbar", True))

    @auto_hide_toolbar.setter
    def auto_hide_toolbar(self, value: bool) -> None:
        self._data["auto_hide_toolbar"] = value

    @property
    def toolbar_pos(self) -> dict | None:
        pos = self._data.get("toolbar_pos")
        return pos if pos is not None else None

    @toolbar_pos.setter
    def toolbar_pos(self, value: dict | None) -> None:
        self._data["toolbar_pos"] = value

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self._data.get("minimize_to_tray", False))

    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool) -> None:
        self._data["minimize_to_tray"] = value

    @property
    def auto_start(self) -> bool:
        return bool(self._data.get("auto_start", False))

    @auto_start.setter
    def auto_start(self, value: bool) -> None:
        self._data["auto_start"] = value

    @property
    def autostart_migrated_to_lnk(self) -> bool:
        return bool(self._data.get("autostart_migrated_to_lnk", False))

    @autostart_migrated_to_lnk.setter
    def autostart_migrated_to_lnk(self, value: bool) -> None:
        self._data["autostart_migrated_to_lnk"] = value

    @property
    def hide_delay_ms(self) -> int:
        return int(self._data.get("hide_delay_ms", 500))

    @hide_delay_ms.setter
    def hide_delay_ms(self, value: int) -> None:
        self._data["hide_delay_ms"] = max(100, min(5000, value))

    @property
    def toolbar_peek_pixels(self) -> int:
        return int(self._data.get("toolbar_peek_pixels", 3))

    @toolbar_peek_pixels.setter
    def toolbar_peek_pixels(self, value: int) -> None:
        self._data["toolbar_peek_pixels"] = max(1, min(20, value))
