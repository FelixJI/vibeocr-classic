"""Classic 运行时选择 facade 的公开行为契约（catalog 解析与意图转换）。"""

from __future__ import annotations

import pytest

from vibeocr.classic.runtime_selection import (
    DOWNLOAD_SOURCES_CAPABILITY,
    EDITABLE_SOURCE_KINDS,
    ENGINE_SELECTION_CAPABILITY,
    RECOGNITION_MODE_CAPABILITY,
    RuntimeSelectionError,
    parse_capability_catalogs,
    resolve_engine_id,
)


def _descriptors(**overrides):
    engine_catalog = overrides.get(
        "engine_catalog",
        {
            "engines": [
                {
                    "id": "rapidocr",
                    "availability": "ready",
                    "included_in_base": True,
                    "reason_code": None,
                    "required_component": None,
                },
                {
                    "id": "paddleocr",
                    "availability": "preparation_required",
                    "included_in_base": False,
                    "reason_code": "component_missing",
                    "required_component": "win-x64-cpu-document-parsing",
                },
            ]
        },
    )
    source_catalog = overrides.get(
        "source_catalog",
        {
            "sources": [
                {
                    "kind": "package_index",
                    "id": "tuna-pypi",
                    "endpoint": "https://mirrors.tuna.tsinghua.edu.cn/pypi",
                },
                {"kind": "package_index", "id": "pypi", "endpoint": "https://pypi.org"},
                {
                    "kind": "internal_mirror",
                    "id": "legacy-unknown",
                    "endpoint": "https://example.invalid",
                },
            ]
        },
    )
    variant_catalog = overrides.get(
        "variant_catalog",
        {
            "variants": [
                {
                    "feature_id": "document_parsing",
                    "accelerator": "cpu",
                    "component_id": "win-x64-cpu-document-parsing",
                },
                {
                    "feature_id": "document_parsing",
                    "accelerator": "nvidia_cuda",
                    "component_id": "win-x64-cu126-document-parsing",
                },
                {
                    "feature_id": "gpu_runtime",
                    "accelerator": "nvidia_cuda",
                    "component_id": "win-x64-cu126-gpu-runtime",
                },
            ]
        },
    )
    descriptors = []
    if engine_catalog is not None:
        descriptors.append(
            {"name": ENGINE_SELECTION_CAPABILITY, "ocr_engine_catalog": engine_catalog}
        )
    if source_catalog is not None:
        descriptors.append(
            {
                "name": DOWNLOAD_SOURCES_CAPABILITY,
                "download_source_catalog": source_catalog,
            }
        )
    variant_name = "runtime.component-selection.v1"
    if variant_catalog is not None:
        descriptors.append(
            {"name": variant_name, "component_variant_catalog": variant_catalog}
        )
    return descriptors


def test_parse_capability_catalogs_projects_all_three_catalogs() -> None:
    catalog = parse_capability_catalogs(_descriptors())

    assert [entry.engine_id for entry in catalog.engines] == [
        "rapidocr",
        "paddleocr",
    ]
    assert catalog.engines[1].required_component == "win-x64-cpu-document-parsing"
    assert [source.source_id for source in catalog.sources] == [
        "tuna-pypi",
        "pypi",
        "legacy-unknown",
    ]
    assert catalog.variants_for_accelerator("nvidia_cuda") == (
        catalog.variants[1],
        catalog.variants[2],
    )


def test_recognition_mode_catalog_owns_labels_lifecycle_and_execution_projection() -> (
    None
):
    """新目录以用户模式表达选择，但作业仍使用 legacy pipeline + engine。"""

    descriptors = _descriptors(
        engine_catalog=None,
    )
    descriptors.append(
        {
            "name": RECOGNITION_MODE_CAPABILITY,
            "recognition_mode_catalog": {
                "modes": [
                    {
                        "id": "rapid_text",
                        "family": "text",
                        "pipeline_id": "OCR",
                        "engine": "rapidocr",
                        "provisioning": "base_runtime",
                        "availability": "ready",
                        "reason_code": None,
                        "required_component": None,
                        "supported_options": [],
                        "lifecycle": {
                            "kind": "unmanaged",
                            "supports_preload": False,
                            "supports_ttl": False,
                            "supports_pinning": False,
                            "supports_release": False,
                        },
                    },
                    {
                        "id": "paddle_text",
                        "family": "text",
                        "pipeline_id": "OCR",
                        "engine": "paddleocr",
                        "provisioning": "advanced_component",
                        "availability": "preparation_required",
                        "reason_code": "component_missing",
                        "required_component": "document_parsing",
                        "supported_options": ["use_doc_unwarping"],
                        "lifecycle": {
                            "kind": "model_residency",
                            "supports_preload": True,
                            "supports_ttl": True,
                            "supports_pinning": True,
                            "supports_release": True,
                        },
                    },
                    {
                        "id": "mineru_document",
                        "family": "document",
                        "pipeline_id": "MinerU",
                        "engine": None,
                        "provisioning": "advanced_component",
                        "availability": "preparation_required",
                        "reason_code": "component_missing",
                        "required_component": "document_parsing",
                        "supported_options": [],
                        "lifecycle": {
                            "kind": "process_keep_alive",
                            "supports_preload": False,
                            "supports_ttl": True,
                            "supports_pinning": False,
                            "supports_release": True,
                        },
                    },
                ]
            },
        }
    )
    mode_descriptors = descriptors[-1]["recognition_mode_catalog"]["modes"]
    mode_descriptors.extend(
        [
            {
                "id": mode_id,
                "family": family,
                "pipeline_id": pipeline_id,
                "engine": engine,
                "provisioning": provisioning,
                "availability": "ready",
                "reason_code": None,
                "required_component": None,
                "supported_options": [],
                "lifecycle": {
                    "kind": lifecycle[0],
                    "supports_preload": lifecycle[1],
                    "supports_ttl": lifecycle[2],
                    "supports_pinning": lifecycle[3],
                    "supports_release": lifecycle[4],
                },
            }
            for mode_id, family, pipeline_id, engine, provisioning, lifecycle in (
                (
                    "windows_text",
                    "text",
                    "OCR",
                    "windows",
                    "operating_system",
                    ("unmanaged", False, False, False, False),
                ),
                (
                    "paddle_structure",
                    "document",
                    "PP-StructureV3",
                    None,
                    "advanced_component",
                    ("model_residency", True, True, True, True),
                ),
                (
                    "paddle_document_vl",
                    "document",
                    "PaddleOCR-VL",
                    None,
                    "advanced_component",
                    ("model_residency", True, True, True, True),
                ),
                (
                    "paddle_table",
                    "specialized",
                    "TABLE_RECOGNITION",
                    None,
                    "advanced_component",
                    ("model_residency", True, True, True, True),
                ),
                (
                    "paddle_formula",
                    "specialized",
                    "FORMULA_RECOGNITION",
                    None,
                    "advanced_component",
                    ("model_residency", True, True, True, True),
                ),
            )
        ]
    )

    catalog = parse_capability_catalogs(descriptors)

    rapid = catalog.mode("rapid_text")
    paddle = catalog.mode("paddle_text")
    mineru = catalog.mode("mineru_document")
    assert rapid is not None
    assert paddle is not None
    assert mineru is not None
    assert rapid.display_name == "快速 OCR（RapidOCR）"
    assert paddle.display_name == "通用 OCR（PaddleOCR）"
    assert mineru.display_name == "深度文档解析（MinerU）"
    assert catalog.execution_projection("paddle_text") == ("OCR", "paddleocr")
    assert catalog.execution_projection("mineru_document") == ("MinerU", None)
    assert rapid.lifecycle.supports_preload is False
    assert paddle.lifecycle.supports_preload is True
    assert paddle.lifecycle.supports_pinning is True
    assert mineru.lifecycle.supports_preload is False
    assert mineru.lifecycle.supports_ttl is True
    assert mineru.lifecycle.supports_pinning is False

    mode_descriptors[0]["pipeline_id"] = "MinerU"
    with pytest.raises(RuntimeSelectionError, match="固定契约"):
        parse_capability_catalogs(descriptors)


def test_recognition_mode_catalog_rejects_missing_stable_modes() -> None:
    descriptors = [
        {
            "name": RECOGNITION_MODE_CAPABILITY,
            "recognition_mode_catalog": {"modes": []},
        }
    ]

    with pytest.raises(RuntimeSelectionError, match="完整八个稳定模式"):
        parse_capability_catalogs(descriptors)


def test_legacy_engine_catalog_falls_back_to_text_recognition_modes() -> None:
    catalog = parse_capability_catalogs(_descriptors())

    assert [mode.mode_id for mode in catalog.modes] == ["rapid_text", "paddle_text"]
    assert catalog.execution_projection("rapid_text") == ("OCR", "rapidocr")


def test_unknown_source_kind_is_preserved_but_not_editable() -> None:
    catalog = parse_capability_catalogs(_descriptors())

    unknown = catalog.sources[2]
    assert unknown.kind == "internal_mirror"
    assert unknown.kind not in EDITABLE_SOURCE_KINDS
    assert unknown.editable is False
    editable = catalog.editable_sources_by_kind()
    assert set(editable) == {"package_index"}
    assert [source.source_id for source in editable["package_index"]] == [
        "tuna-pypi",
        "pypi",
    ]


def test_model_registry_is_an_editable_upstream_source_hint() -> None:
    descriptors = _descriptors()
    descriptors[1]["download_source_catalog"]["sources"].append(
        {
            "kind": "model_registry",
            "id": "legacy-models",
            "endpoint": "https://models.example.invalid",
        }
    )

    catalog = parse_capability_catalogs(descriptors)

    registry = next(
        source for source in catalog.sources if source.source_id == "legacy-models"
    )
    assert registry.kind == "model_registry"
    assert registry.endpoint == "https://models.example.invalid"
    assert registry.editable is True
    assert catalog.editable_sources_by_kind()["model_registry"] == (registry,)


def test_model_registry_rejects_malformed_endpoint() -> None:
    descriptors = _descriptors()
    descriptors[1]["download_source_catalog"]["sources"].append(
        {"kind": "model_registry", "id": "legacy-models", "endpoint": {"opaque": True}}
    )

    with pytest.raises(RuntimeSelectionError, match="source.endpoint"):
        parse_capability_catalogs(descriptors)


def test_missing_catalogs_yield_empty_projection() -> None:
    catalog = parse_capability_catalogs(
        [{"name": ENGINE_SELECTION_CAPABILITY}, {"name": "unrelated.capability.v1"}]
    )

    assert catalog.engines == ()
    assert catalog.sources == ()
    assert catalog.variants == ()


@pytest.mark.parametrize(
    ("field", "payload"),
    [
        ("engine_catalog", {"engines": []}),
        ("source_catalog", {"sources": []}),
        ("variant_catalog", {"variants": []}),
    ],
)
def test_empty_catalogs_are_valid(field, payload) -> None:
    catalog = parse_capability_catalogs(_descriptors(**{field: payload}))

    assert (
        getattr(
            catalog,
            {
                "engine_catalog": "engines",
                "source_catalog": "sources",
                "variant_catalog": "variants",
            }[field],
        )
        == ()
    )


def test_duplicate_engine_id_is_rejected() -> None:
    duplicate = {
        "engines": [
            {
                "id": "rapidocr",
                "availability": "ready",
                "included_in_base": True,
            },
            {
                "id": "rapidocr",
                "availability": "unavailable",
                "included_in_base": False,
            },
        ]
    }

    with pytest.raises(RuntimeSelectionError, match="重复的 engine id"):
        parse_capability_catalogs(_descriptors(engine_catalog=duplicate))


def test_duplicate_source_id_is_rejected() -> None:
    duplicate = {
        "sources": [
            {"kind": "package_index", "id": "pypi", "endpoint": "https://a"},
            {"kind": "model_registry", "id": "pypi", "endpoint": "https://b"},
        ]
    }

    with pytest.raises(RuntimeSelectionError, match="重复的下载源 id"):
        parse_capability_catalogs(_descriptors(source_catalog=duplicate))


def test_duplicate_variant_business_key_is_rejected() -> None:
    duplicate = {
        "variants": [
            {
                "feature_id": "document_parsing",
                "accelerator": "cpu",
                "component_id": "a",
            },
            {
                "feature_id": "document_parsing",
                "accelerator": "cpu",
                "component_id": "b",
            },
        ]
    }

    with pytest.raises(RuntimeSelectionError, match="重复的可选能力业务键"):
        parse_capability_catalogs(_descriptors(variant_catalog=duplicate))


def test_invalid_engine_availability_is_rejected() -> None:
    invalid = {
        "engines": [
            {"id": "rapidocr", "availability": "maybe", "included_in_base": True}
        ]
    }

    with pytest.raises(RuntimeSelectionError, match="availability"):
        parse_capability_catalogs(_descriptors(engine_catalog=invalid))


def test_component_ids_for_features_maps_and_fails_closed() -> None:
    catalog = parse_capability_catalogs(_descriptors())

    assert catalog.component_ids_for_features(["document_parsing"], "nvidia_cuda") == (
        "win-x64-cu126-document-parsing",
    )
    assert catalog.component_ids_for_features(
        ["document_parsing", "gpu_runtime"], "nvidia_cuda"
    ) == ("win-x64-cu126-document-parsing", "win-x64-cu126-gpu-runtime")

    with pytest.raises(RuntimeSelectionError, match="未声明可选能力"):
        catalog.component_ids_for_features(["gpu_runtime"], "cpu")


def test_normalize_source_selection_omits_empty_and_validates() -> None:
    catalog = parse_capability_catalogs(_descriptors())

    assert catalog.normalize_source_selection({}) is None
    assert catalog.normalize_source_selection({"package_index": "pypi"}) == ("pypi",)

    descriptors = _descriptors()
    descriptors[1]["download_source_catalog"]["sources"].append(
        {
            "kind": "model_registry",
            "id": "modelscope",
            "endpoint": "https://www.modelscope.cn",
        }
    )
    catalog = parse_capability_catalogs(descriptors)
    assert catalog.normalize_source_selection(
        {"package_index": "pypi", "model_registry": "modelscope"}
    ) == ("pypi", "modelscope")

    with pytest.raises(RuntimeSelectionError, match="未知下载源"):
        catalog.normalize_source_selection({"package_index": "not-a-source"})
    with pytest.raises(RuntimeSelectionError, match="不属于"):
        catalog.normalize_source_selection({"model_registry": "pypi"})


def test_resolve_engine_id_prefers_override_and_never_sends_unknown() -> None:
    assert resolve_engine_id("windows") == "windows"
    assert resolve_engine_id("legacy-unknown") is None
    assert resolve_engine_id(None) is None
