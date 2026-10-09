"""下载来源（依赖索引 + 模型来源）的目录、存储与环境投影测试。"""

from __future__ import annotations

import json


from vibeocr.classic.download_sources import (
    DEFAULT_MODEL_SOURCE_ID,
    DEFAULT_PACKAGE_INDEX_ID,
    MODEL_SOURCES,
    PACKAGE_INDEXES,
    DownloadSourceStore,
    get_model_source,
    get_package_index,
)


def test_catalog_has_expected_options() -> None:
    assert [item.id for item in PACKAGE_INDEXES] == ["tuna", "pypi"]
    assert [item.id for item in MODEL_SOURCES] == ["huggingface", "modelscope"]
    tuna = get_package_index("tuna")
    assert tuna.url.startswith("https://mirrors.tuna.tsinghua.edu.cn")
    assert get_package_index("pypi").url == "https://pypi.org/simple"


def test_unknown_ids_fall_back_to_defaults() -> None:
    assert get_package_index(None).id == DEFAULT_PACKAGE_INDEX_ID
    assert get_package_index("nope").id == DEFAULT_PACKAGE_INDEX_ID
    assert get_model_source(None).id == DEFAULT_MODEL_SOURCE_ID
    assert get_model_source("nope").id == DEFAULT_MODEL_SOURCE_ID


def test_store_round_trip(tmp_path) -> None:
    store = DownloadSourceStore(tmp_path)
    assert store.package_index_id == DEFAULT_PACKAGE_INDEX_ID
    assert store.model_source_id == DEFAULT_MODEL_SOURCE_ID

    store.save(package_index_id="pypi", model_source_id="modelscope")
    assert store.package_index_id == "pypi"
    assert store.model_source_id == "modelscope"
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_store_invalid_file_falls_back(tmp_path) -> None:
    store = DownloadSourceStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not-json", encoding="utf-8")
    assert store.package_index_id == DEFAULT_PACKAGE_INDEX_ID
    store.path.write_text('["list"]', encoding="utf-8")
    assert store.model_source_id == DEFAULT_MODEL_SOURCE_ID


def test_model_source_environment_projection(tmp_path, monkeypatch) -> None:
    store = DownloadSourceStore(tmp_path)
    store.save(package_index_id="tuna", model_source_id="modelscope")

    monkeypatch.delenv("MINERU_MODEL_SOURCE", raising=False)
    monkeypatch.delenv("PADDLE_PDX_MODEL_SOURCE", raising=False)

    import os

    applied = store.apply_model_source_environment()
    assert applied == {
        "PADDLE_PDX_MODEL_SOURCE": "modelscope",
        "MINERU_MODEL_SOURCE": "modelscope",
    }
    assert os.environ["MINERU_MODEL_SOURCE"] == "modelscope"

    # 传 dict 时不写进程环境。
    sandbox: dict[str, str] = {}
    store.save(package_index_id="tuna", model_source_id="huggingface")
    store.apply_model_source_environment(sandbox)
    assert sandbox["MINERU_MODEL_SOURCE"] == "huggingface"


def test_package_index_url_used_by_installer(tmp_path) -> None:
    store = DownloadSourceStore(tmp_path)
    store.save(package_index_id="pypi", model_source_id="huggingface")
    assert store.package_index_url() == "https://pypi.org/simple"


def test_backend_host_applies_model_source_environment(tmp_path, monkeypatch) -> None:
    from vibeocr.classic.backend_host import InProcessBackendHost

    store = DownloadSourceStore(tmp_path)
    store.save(package_index_id="tuna", model_source_id="modelscope")
    for key in ("MINERU_MODEL_SOURCE", "PADDLE_PDX_MODEL_SOURCE"):
        monkeypatch.delenv(key, raising=False)

    import os

    host = InProcessBackendHost(tmp_path)
    host._apply_environment(tmp_path / "stager")

    assert os.environ["MINERU_MODEL_SOURCE"] == "modelscope"
    assert os.environ["PADDLE_PDX_MODEL_SOURCE"] == "modelscope"
