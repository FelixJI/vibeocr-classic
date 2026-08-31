"""统一配置管理器

所有用户配置的唯一读写入口，提供统一的路径管理和 JSON 读写。
"""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject

from vibeocr.classic.json_storage import write_json_atomic

logger = logging.getLogger(__name__)


class ConfigManager(QObject):
    """统一配置管理器单例

    负责所有用户配置的读写、路径管理和版本迁移。
    """

    _instance: "ConfigManager | None" = None

    # 每管道默认 TTL（秒）。paddle 重管道（PP-StructureV3 / PaddleOCR-VL）
    # 显式 5 分钟；其他管道用 0 表示继承 Supervisor 默认 TTL。
    _DEFAULT_PIPELINE_TTLS: dict[str, int] = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._config_dir = project_root / "config"
        self._cache_dir = project_root / ".vibeocr"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance(cls, project_root: Path | None = None) -> "ConfigManager":
        if cls._instance is None:
            if project_root is None:
                raise RuntimeError("ConfigManager 首次创建必须传入 project_root")
            cls._instance = cls(project_root)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅供测试使用）。"""
        cls._instance = None

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def project_root(self) -> Path:
        return self._project_root

    def _load_json(self, filename: str, default: dict | None = None) -> dict:
        filepath = self._config_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            return (
                data
                if isinstance(data, dict)
                else (default if default is not None else {})
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载配置文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_json(self, filename: str, data: dict) -> bool:
        filepath = self._config_dir / filename
        try:
            write_json_atomic(filepath, data)
            return True
        except (OSError, TypeError, ValueError) as e:
            logger.error("保存配置文件 %s 失败: %s", filename, e)
            return False

    def _load_cache_json(self, filename: str, default: dict | None = None) -> dict:
        filepath = self._cache_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            return (
                data
                if isinstance(data, dict)
                else (default if default is not None else {})
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载缓存文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_cache_json(self, filename: str, data: dict) -> bool:
        filepath = self._cache_dir / filename
        try:
            write_json_atomic(filepath, data)
            return True
        except (OSError, TypeError, ValueError) as e:
            logger.error("保存缓存文件 %s 失败: %s", filename, e)
            return False

    def get_preload_pipelines(self) -> list[str]:
        """返回启动时预加载的非歧义驻留管道。"""
        from vibeocr.runtime_contracts.contracts.pipelines import (
            get_preloadable_pipelines,
        )

        data = self._load_json("app_settings.json", {})
        raw = data.get("preload_pipelines")
        if raw is None:
            # 旧 cache 的 OCR 默认值不知道实际会路由到 Rapid、Windows 还是
            # Paddle。它不能再被迁移为“模型预加载”，新安装默认按需加载。
            raw = []
        valid = {
            pipeline.value.lower(): pipeline.value
            for pipeline in get_preloadable_pipelines()
        }
        valid.pop("ocr", None)
        selected: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                if not isinstance(value, str):
                    continue
                normalized = valid.get(value.lower())
                if normalized is not None and normalized not in selected:
                    selected.append(normalized)
        return selected

    def get_preload_enabled(self) -> bool:
        data = self._load_json("app_settings.json", {})
        if "preload_enabled" in data:
            return bool(data["preload_enabled"])
        return bool(self.get_preload_pipelines())

    def set_preload_enabled(self, enabled: bool) -> bool:
        data = self._load_json("app_settings.json", {})
        data["preload_enabled"] = bool(enabled)
        return self._save_json("app_settings.json", data)

    def set_preload_pipelines(self, pipelines: list[str]) -> bool:
        from vibeocr.runtime_contracts.contracts.pipelines import (
            get_preloadable_pipelines,
        )

        valid = {
            pipeline.value.lower(): pipeline.value
            for pipeline in get_preloadable_pipelines()
        }
        # Protocol 2.8 的 recognition mode request 尚未随 Classic 绑定发布；
        # 在此之前不允许把歧义的 legacy OCR pipeline 写入预加载设置。
        valid.pop("ocr", None)
        normalized: list[str] = []
        for value in pipelines:
            selected = valid.get(str(value).lower())
            if selected is not None and selected not in normalized:
                normalized.append(selected)
        data = self._load_json("app_settings.json", {})
        data["preload_pipelines"] = normalized
        return self._save_json("app_settings.json", data)

    def get_pipeline_ttls(self) -> dict[str, int]:
        """返回完整 6 管道 TTL 字典；缺失补默认；自动一次性迁移旧字段。

        迁移语义（spec §2.4）：
          - 旧 ``pipeline_ttl_seconds`` 存在且无 ``pipeline_ttls``：
            paddle 重管道（PP-StructureV3 / PaddleOCR-VL）= 旧值；其余 = 0。
            迁移后删除旧字段。
          - 新旧字段并存：以 dict 为准，不迁移、不删旧字段（避免误删用户手填数据）。
          - 缺失管道：补默认（重管道 300，其余 0）。
          - 损坏值（非 int / bool）：回退到默认。
        """
        data = self._load_json("app_settings.json", {})
        # 一次性迁移：仅当 dict 不存在时执行
        if "pipeline_ttl_seconds" in data and "pipeline_ttls" not in data:
            legacy_raw = data.pop("pipeline_ttl_seconds")
            # bool 是 int 子类：True→1 / False→0 都不应被静默接受，
            # 视为损坏值，回退默认（300）。
            if isinstance(legacy_raw, bool):
                legacy = self._DEFAULT_PIPELINE_TTLS["PP-StructureV3"]
            else:
                try:
                    legacy = max(0, int(legacy_raw))
                except (TypeError, ValueError):
                    legacy = self._DEFAULT_PIPELINE_TTLS["PP-StructureV3"]
            data["pipeline_ttls"] = {
                "OCR": 0,
                "TABLE_RECOGNITION": 0,
                "FORMULA_RECOGNITION": 0,
                "PP-StructureV3": legacy,
                "MinerU": 0,
                "PaddleOCR-VL": legacy,
            }
            self._save_json("app_settings.json", data)
        return self._normalize_ttls(data.get("pipeline_ttls", {}))

    def set_pipeline_ttl(self, pipeline_name: str, ttl: int) -> bool:
        """设置单个管道的 TTL（-1=持久，0=继承）。未知管道名返回 False。"""
        if pipeline_name not in self._DEFAULT_PIPELINE_TTLS:
            return False
        ttls = self.get_pipeline_ttls()
        normalized_ttl = int(ttl)
        ttls[pipeline_name] = normalized_ttl if normalized_ttl >= -1 else 0
        return self.set_pipeline_ttls(ttls)

    def set_pipeline_ttls(self, ttls: dict[str, int]) -> bool:
        """批量设置每管道 TTL；非法值回退默认；仅写入已知管道。"""
        data = self._load_json("app_settings.json", {})
        data["pipeline_ttls"] = self._normalize_ttls(ttls)
        return self._save_json("app_settings.json", data)

    def _normalize_ttls(self, raw: object) -> dict[str, int]:
        """规范化 TTL 字典：补齐缺失管道，丢弃非法值（非 int 或 bool）。"""
        if not isinstance(raw, dict):
            raw = {}
        result: dict[str, int] = {}
        for name, default in self._DEFAULT_PIPELINE_TTLS.items():
            val = raw.get(name, default) if isinstance(raw, dict) else default
            # bool 是 int 子类，必须显式拒绝（避免 True/False 被当成 1/0）
            if isinstance(val, bool) or not isinstance(val, int):
                val = default
            result[name] = val if val >= -1 else 0
        return result

    def get_max_heavy_pipelines(self) -> int | None:
        """手动覆盖的重管道并存上限，None=按显存自动分档。"""
        data = self._load_json("app_settings.json", {})
        val = data.get("max_heavy_pipelines")
        return int(val) if val is not None else None

    def set_max_heavy_pipelines(self, value: int | None) -> bool:
        data = self._load_json("app_settings.json", {})
        data["max_heavy_pipelines"] = value
        return self._save_json("app_settings.json", data)

    def get_log_level(self) -> str:
        """返回持久化日志级别；无效旧值自动回退到 INFO。"""
        data = self._load_json("app_settings.json", {})
        level = str(data.get("log_level", "INFO")).upper()
        return level if level in {"DEBUG", "INFO", "WARNING"} else "INFO"

    def set_log_level(self, level: str) -> bool:
        normalized = str(level).upper()
        if normalized not in {"DEBUG", "INFO", "WARNING"}:
            normalized = "INFO"
        data = self._load_json("app_settings.json", {})
        data["log_level"] = normalized
        return self._save_json("app_settings.json", data)

    def get_offline_component_features(self, accelerator: str) -> list[str]:
        """按 accelerator 读取用户勾选的离线能力 feature id 集合。"""

        data = self._load_json("app_settings.json", {})
        raw = data.get("offline_component_features")
        if not isinstance(raw, dict):
            return []
        features = raw.get(accelerator)
        if not isinstance(features, list):
            return []
        return [feature for feature in features if isinstance(feature, str) and feature]

    def set_offline_component_features(
        self, accelerator: str, features: list[str]
    ) -> bool:
        """保存某 accelerator 的离线能力勾选；feature 语义由 Classic 持有。"""

        normalized: list[str] = []
        for feature in features:
            if isinstance(feature, str) and feature and feature not in normalized:
                normalized.append(feature)
        data = self._load_json("app_settings.json", {})
        raw = data.get("offline_component_features")
        stored = dict(raw) if isinstance(raw, dict) else {}
        if normalized:
            stored[accelerator] = normalized
        else:
            stored.pop(accelerator, None)
        data["offline_component_features"] = stored
        return self._save_json("app_settings.json", data)

    def get_export_settings(self) -> dict:
        return self._load_json(
            "export_settings.json",
            {
                "version": 1,
                "format": "markdown",
                "location_mode": "same_as_source",
                "custom_directory": "",
                "last_custom_directory": "",
            },
        )

    def save_export_settings(self, settings: dict) -> bool:
        data = {"version": 1, **settings}
        return self._save_json("export_settings.json", data)
