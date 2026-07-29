"""布局管理器

负责窗口和分割器状态的持久化。
"""

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray

if TYPE_CHECKING:
    from vibeocr.classic.managers.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class LayoutManager:
    """布局管理器 - 负责窗口和分割器状态的持久化

    管理主窗口几何信息和分割器状态的保存与加载。
    使用 base64 编码存储 QByteArray 数据。

    Attributes:
        CONFIG_VERSION: 配置文件版本号
        CONFIG_FILENAME: 配置文件名
    """

    CONFIG_VERSION = 1
    CONFIG_FILENAME = "layout.json"

    def __init__(self, config_manager: "ConfigManager | Path") -> None:
        """初始化布局管理器

        Args:
            config_manager: ConfigManager 实例或配置目录路径（兼容旧接口）
        """
        if isinstance(config_manager, Path):
            self._cm = None
            self._config_dir = config_manager
            self._config_path = config_manager / self.CONFIG_FILENAME
        else:
            self._cm = config_manager
            self._config_dir = config_manager.config_dir
            self._config_path = self._config_dir / self.CONFIG_FILENAME

        # 布局数据
        self._main_window_geometry: QByteArray | None = None
        self._splitters: dict[str, QByteArray] = {}
        self._tab_index: int | None = None

        # 加载配置
        self._load()

    def _load(self) -> None:
        """加载配置文件

        配置文件不存在、损坏或版本不匹配时使用默认值（不报错）。
        """
        if self._cm is not None:
            data = self._cm._load_json(self.CONFIG_FILENAME)
        else:
            import json

            if not self._config_path.exists():
                logger.debug("布局配置文件不存在，使用默认值")
                return
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"加载布局配置失败: {e}，使用默认值")
                return

        if not data:
            logger.debug("布局配置文件不存在，使用默认值")
            return

        # 检查版本
        if data.get("version") != self.CONFIG_VERSION:
            logger.warning(
                f"布局配置版本不匹配 (期望 {self.CONFIG_VERSION}, 实际 {data.get('version')})，使用默认值"
            )
            return

        # 加载主窗口几何信息
        main_window = data.get("main_window", {})
        if geometry_b64 := main_window.get("geometry"):
            self._main_window_geometry = QByteArray(base64.b64decode(geometry_b64))

        # 加载标签页索引
        tab_index = data.get("tab_index")
        if tab_index is not None:
            self._tab_index = tab_index

        # 加载分割器状态
        splitters = data.get("splitters", {})
        for splitter_id, state_b64 in splitters.items():
            self._splitters[splitter_id] = QByteArray(base64.b64decode(state_b64))

        logger.debug("布局配置已加载")

    def save(self) -> None:
        """保存配置文件"""
        # 构建配置数据
        data: dict[str, Any] = {
            "version": self.CONFIG_VERSION,
            "main_window": {},
            "splitters": {},
        }

        # 保存标签页索引
        if self._tab_index is not None:
            data["tab_index"] = self._tab_index

        # 保存主窗口几何信息
        if self._main_window_geometry is not None:
            data["main_window"]["geometry"] = base64.b64encode(
                self._main_window_geometry.data()
            ).decode("utf-8")

        # 保存分割器状态
        for splitter_id, state in self._splitters.items():
            data["splitters"][splitter_id] = base64.b64encode(state.data()).decode(
                "utf-8"
            )

        if self._cm is not None:
            self._cm._save_json(self.CONFIG_FILENAME, data)
            logger.debug("布局配置已保存")
        else:
            import json

            self._config_dir.mkdir(parents=True, exist_ok=True)
            try:
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.debug("布局配置已保存")
            except Exception as e:
                logger.error(f"保存布局配置失败: {e}")

    def get_main_window_geometry(self) -> QByteArray | None:
        """获取主窗口几何信息

        Returns:
            主窗口几何信息的 QByteArray，如果未设置则返回 None
        """
        return self._main_window_geometry

    def set_main_window_geometry(self, geometry: QByteArray) -> None:
        """保存主窗口几何信息

        Args:
            geometry: 主窗口几何信息的 QByteArray
        """
        self._main_window_geometry = geometry

    def get_splitter_state(self, splitter_id: str) -> QByteArray | None:
        """获取分割器状态

        Args:
            splitter_id: 分割器标识符

        Returns:
            分割器状态的 QByteArray，如果未设置则返回 None
        """
        return self._splitters.get(splitter_id)

    def set_splitter_state(self, splitter_id: str, state: QByteArray) -> None:
        """保存分割器状态

        Args:
            splitter_id: 分割器标识符
            state: 分割器状态的 QByteArray
        """
        self._splitters[splitter_id] = state

    def get_tab_index(self) -> int | None:
        """获取标签页索引"""
        return self._tab_index

    def set_tab_index(self, index: int) -> None:
        """保存标签页索引"""
        self._tab_index = index
