"""下载来源选择：依赖包索引与模型来源，用户可安装时或设置页设置。

融合形态下这是下载来源的唯一事实来源（存于 ``state/config/download-sources.json``）：

- 依赖下载源（package index）：安装识别引擎时传给 uv 的 ``--default-index``。
  清单带哈希锁，镜像只影响下载速度与出处，不影响内容字节。
- 模型下载源（model registry）：投影为引擎官方支持的环境变量
  （``PADDLE_PDX_MODEL_SOURCE`` / ``MINERU_MODEL_SOURCE``），由进程内后端
  启动时与保存设置时写入；模型文件本身仍由各引擎原生机制下载与管理。

选项与旧产品保持一致（默认国内镜像），避免中国用户首装体验退化。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from vibeocr.classic.json_storage import write_json_atomic

logger = logging.getLogger(__name__)

_STORE_FILE_NAME = "download-sources.json"


@dataclass(frozen=True, slots=True)
class PackageIndex:
    id: str
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class ModelSource:
    id: str
    label: str
    environment: dict[str, str]


#: 依赖下载源。安装引擎环境时逐字校验哈希，镜像不会改变安装内容。
PACKAGE_INDEXES: tuple[PackageIndex, ...] = (
    PackageIndex(
        id="tuna",
        label="国内镜像（清华 TUNA，下载快，默认）",
        url="https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple",
    ),
    PackageIndex(
        id="pypi",
        label="官方源（PyPI）",
        url="https://pypi.org/simple",
    ),
)

#: 模型下载源。环境变量取自后端 runtime_selection 的既有约定。
MODEL_SOURCES: tuple[ModelSource, ...] = (
    ModelSource(
        id="huggingface",
        label="HuggingFace（默认）",
        environment={
            "PADDLE_PDX_MODEL_SOURCE": "huggingface",
            "MINERU_MODEL_SOURCE": "huggingface",
        },
    ),
    ModelSource(
        id="modelscope",
        label="ModelScope（魔搭社区，国内下载快）",
        environment={
            "PADDLE_PDX_MODEL_SOURCE": "modelscope",
            "MINERU_MODEL_SOURCE": "modelscope",
        },
    ),
)

DEFAULT_PACKAGE_INDEX_ID = "tuna"
DEFAULT_MODEL_SOURCE_ID = "huggingface"

_PACKAGE_INDEXES_BY_ID = {item.id: item for item in PACKAGE_INDEXES}
_MODEL_SOURCES_BY_ID = {item.id: item for item in MODEL_SOURCES}

_DEFAULTS = {
    "package_index_id": DEFAULT_PACKAGE_INDEX_ID,
    "model_source_id": DEFAULT_MODEL_SOURCE_ID,
}


def get_package_index(package_index_id: str | None) -> PackageIndex:
    """按 id 取依赖下载源；未知 id 回退默认。"""

    return _PACKAGE_INDEXES_BY_ID.get(
        package_index_id or "", _PACKAGE_INDEXES_BY_ID[DEFAULT_PACKAGE_INDEX_ID]
    )


def get_model_source(model_source_id: str | None) -> ModelSource:
    """按 id 取模型下载源；未知 id 回退默认。"""

    return _MODEL_SOURCES_BY_ID.get(
        model_source_id or "", _MODEL_SOURCES_BY_ID[DEFAULT_MODEL_SOURCE_ID]
    )


class DownloadSourceStore:
    """下载来源的持久化（JSON，原子写）。"""

    def __init__(self, state_root: Path) -> None:
        self._path = Path(state_root) / "config" / _STORE_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dict(_DEFAULTS)
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        return {
            "package_index_id": get_package_index(data.get("package_index_id")).id,
            "model_source_id": get_model_source(data.get("model_source_id")).id,
        }

    def save(self, *, package_index_id: str, model_source_id: str) -> None:
        data = {
            "schema_version": 1,
            "package_index_id": get_package_index(package_index_id).id,
            "model_source_id": get_model_source(model_source_id).id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path, data)

    # ------------------------------------------------------------------

    @property
    def package_index_id(self) -> str:
        return self.load()["package_index_id"]

    @property
    def model_source_id(self) -> str:
        return self.load()["model_source_id"]

    def package_index_url(self) -> str:
        return get_package_index(self.package_index_id).url

    def model_source_environment(self) -> dict[str, str]:
        return dict(get_model_source(self.model_source_id).environment)

    def apply_model_source_environment(
        self, env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """把所选模型源写入环境变量（默认写进程环境）。

        引擎子进程继承这些变量决定从哪里准备模型；运行中的引擎服务
        不会热切换，重启后按新来源工作。
        """

        target = os.environ if env is None else env
        values = self.model_source_environment()
        target.update(values)
        return dict(values)


__all__ = [
    "DEFAULT_MODEL_SOURCE_ID",
    "DEFAULT_PACKAGE_INDEX_ID",
    "MODEL_SOURCES",
    "PACKAGE_INDEXES",
    "DownloadSourceStore",
    "ModelSource",
    "PackageIndex",
    "get_model_source",
    "get_package_index",
]
