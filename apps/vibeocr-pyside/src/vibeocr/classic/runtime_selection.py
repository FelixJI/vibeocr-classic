"""Classic-owned projection of Backend runtime selection catalogs.

The Backend owns the engine/component/source catalogs and every dependency
closure decision; Classic only renders choices and converts them to the
narrow Protocol intent at the maintenance/settings seams.  This module is
that single conversion surface: it parses capability-descriptor catalogs,
rejects duplicate business keys, keeps unknown source kinds without editing
them, and maps user semantics (feature + accelerator, per-kind source
choice) to wire intent with strict ``None`` vs empty semantics.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ENGINE_SELECTION_CAPABILITY = "ocr.engine-selection.v1"
RECOGNITION_MODE_CAPABILITY = "ocr.recognition-modes.v1"
COMPONENT_SELECTION_CAPABILITY = "runtime.component-selection.v1"
DOWNLOAD_SOURCES_CAPABILITY = "runtime.download-sources.v1"

ENGINE_AVAILABILITY_READY = "ready"
ENGINE_AVAILABILITY_PREPARATION_REQUIRED = "preparation_required"
ENGINE_AVAILABILITY_UNAVAILABLE = "unavailable"
_VALID_ENGINE_AVAILABILITY = frozenset(
    {
        ENGINE_AVAILABILITY_READY,
        ENGINE_AVAILABILITY_PREPARATION_REQUIRED,
        ENGINE_AVAILABILITY_UNAVAILABLE,
    }
)

# Protocol 保持 source kind 开放；Classic 只暴露稳定的上游来源选择。
# 选择只按 id 透传，模型下载、缓存与校验仍由各自的原生下载器负责。
# 未知 kind 保留在 catalog 中但不参与 UI 选择。
EDITABLE_SOURCE_KINDS = frozenset({"package_index", "model_registry"})

DEFAULT_ENGINE_ID = "rapidocr"
VALID_ENGINE_IDS = frozenset({"rapidocr", "windows", "paddleocr"})

# 显示文案属于 Classic 本地语义；Backend catalog 不携带产品文案。
ENGINE_DISPLAY_NAMES: Mapping[str, str] = {
    "rapidocr": "RapidOCR",
    "windows": "Windows OCR",
    "paddleocr": "PaddleOCR",
}

ENGINE_AVAILABILITY_LABELS: Mapping[str, str] = {
    ENGINE_AVAILABILITY_READY: "可用",
    ENGINE_AVAILABILITY_PREPARATION_REQUIRED: "需准备组件",
    ENGINE_AVAILABILITY_UNAVAILABLE: "不可用",
}

# Recognition mode 是用户可见的本地语义。Runtime catalog 只提供稳定 ID 和
# 执行投影，不能把 Backend 的实现名直接当成产品文案。
RECOGNITION_MODE_DISPLAY_NAMES: Mapping[str, str] = {
    "rapid_text": "快速 OCR（RapidOCR）",
    "windows_text": "Windows OCR（系统内置）",
    "paddle_text": "通用 OCR（PaddleOCR）",
    "paddle_structure": "文档结构识别（PP-StructureV3）",
    "paddle_document_vl": "视觉文档解析（PaddleOCR-VL）",
    "mineru_document": "深度文档解析（MinerU）",
    "paddle_table": "表格结构识别（PaddleOCR）",
    "paddle_formula": "数学公式识别（PaddleOCR）",
}

_VALID_RECOGNITION_MODE_FAMILIES = frozenset({"text", "document", "specialized"})
_VALID_RECOGNITION_MODE_PROVISIONING = frozenset(
    {"base_runtime", "operating_system", "advanced_component"}
)
_VALID_RECOGNITION_MODE_LIFECYCLES = frozenset(
    {"unmanaged", "model_residency", "process_keep_alive"}
)
_LEGACY_ENGINE_MODE_IDS: Mapping[str, str] = {
    "rapidocr": "rapid_text",
    "windows": "windows_text",
    "paddleocr": "paddle_text",
}

# 已发布的 Protocol v2 请求仍只接收 pipeline_id + engine。此静态投影既是
# 旧 Backend 的兼容 fallback，也是尚未绑定 2.8 SDK 时的严格 request 边界。
_LEGACY_MODE_PROJECTIONS: Mapping[str, tuple[str, str | None]] = {
    "rapid_text": ("OCR", "rapidocr"),
    "windows_text": ("OCR", "windows"),
    "paddle_text": ("OCR", "paddleocr"),
    "paddle_structure": ("PP-StructureV3", None),
    "paddle_document_vl": ("PaddleOCR-VL", None),
    "mineru_document": ("MinerU", None),
    "paddle_table": ("TABLE_RECOGNITION", None),
    "paddle_formula": ("FORMULA_RECOGNITION", None),
}

# ``ocr.recognition-modes.v1`` 不是开放式显示目录。只要 Backend 宣告该
# capability，它就必须是 Protocol 定义的完整八模式集合，固定执行投影与
# 生命周期语义；availability、reason 与 required_component 才是运行时变量。
_EXPECTED_RECOGNITION_MODE_SEMANTICS: Mapping[
    str, tuple[str, str, str | None, str, tuple[str, bool, bool, bool, bool]]
] = {
    "rapid_text": (
        "text",
        "OCR",
        "rapidocr",
        "base_runtime",
        ("unmanaged", False, False, False, False),
    ),
    "windows_text": (
        "text",
        "OCR",
        "windows",
        "operating_system",
        ("unmanaged", False, False, False, False),
    ),
    "paddle_text": (
        "text",
        "OCR",
        "paddleocr",
        "advanced_component",
        ("model_residency", True, True, True, True),
    ),
    "paddle_structure": (
        "document",
        "PP-StructureV3",
        None,
        "advanced_component",
        ("model_residency", True, True, True, True),
    ),
    "paddle_document_vl": (
        "document",
        "PaddleOCR-VL",
        None,
        "advanced_component",
        ("model_residency", True, True, True, True),
    ),
    "mineru_document": (
        "document",
        "MinerU",
        None,
        "advanced_component",
        ("process_keep_alive", False, True, False, True),
    ),
    "paddle_table": (
        "specialized",
        "TABLE_RECOGNITION",
        None,
        "advanced_component",
        ("model_residency", True, True, True, True),
    ),
    "paddle_formula": (
        "specialized",
        "FORMULA_RECOGNITION",
        None,
        "advanced_component",
        ("model_residency", True, True, True, True),
    ),
}


class RuntimeSelectionError(ValueError):
    """A catalog payload or a user selection cannot be honored."""


@dataclass(frozen=True, slots=True)
class OcrEngineEntry:
    engine_id: str
    availability: str
    included_in_base: bool
    reason_code: str | None = None
    required_component: str | None = None

    @property
    def display_name(self) -> str:
        return ENGINE_DISPLAY_NAMES.get(self.engine_id, self.engine_id)


@dataclass(frozen=True, slots=True)
class RecognitionModeLifecycle:
    """用户可管理的运行时生命周期能力。"""

    kind: str
    supports_preload: bool
    supports_ttl: bool
    supports_pinning: bool
    supports_release: bool


@dataclass(frozen=True, slots=True)
class RecognitionModeEntry:
    """一个用户识别模式到 Protocol v2 执行管道的投影。"""

    mode_id: str
    family: str
    pipeline_id: str
    engine: str | None
    provisioning: str
    availability: str
    lifecycle: RecognitionModeLifecycle
    reason_code: str | None = None
    required_component: str | None = None
    supported_options: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return RECOGNITION_MODE_DISPLAY_NAMES.get(self.mode_id, self.mode_id)


@dataclass(frozen=True, slots=True)
class DownloadSourceEntry:
    kind: str
    source_id: str
    endpoint: str

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE_SOURCE_KINDS


@dataclass(frozen=True, slots=True)
class ComponentVariantEntry:
    feature_id: str
    accelerator: str
    component_id: str


@dataclass(frozen=True, slots=True)
class RuntimeSelectionCatalog:
    """Parsed projection of the three Backend selection catalogs."""

    engines: tuple[OcrEngineEntry, ...] = ()
    modes: tuple[RecognitionModeEntry, ...] = ()
    sources: tuple[DownloadSourceEntry, ...] = ()
    variants: tuple[ComponentVariantEntry, ...] = ()
    # ``modes`` 可能由旧 engine catalog 合成；只有这个标记为真时，调用方
    # 才能把它作为完整的八模式用户选择目录。
    has_recognition_mode_catalog: bool = False

    def engine(self, engine_id: str) -> OcrEngineEntry | None:
        return next(
            (entry for entry in self.engines if entry.engine_id == engine_id), None
        )

    def mode(self, mode_id: str) -> RecognitionModeEntry | None:
        return next((entry for entry in self.modes if entry.mode_id == mode_id), None)

    def execution_projection(self, mode_id: str) -> tuple[str, str | None]:
        """Return the legacy ``pipeline_id + engine`` request projection."""

        mode = self.mode(mode_id)
        if mode is None:
            raise RuntimeSelectionError(f"当前 Backend 未声明识别模式: {mode_id}")
        return mode.pipeline_id, mode.engine

    def modes_supporting(self, capability: str) -> tuple[RecognitionModeEntry, ...]:
        """Return modes that explicitly advertise one lifecycle control."""

        attribute = f"supports_{capability}"
        if attribute not in {
            "supports_preload",
            "supports_ttl",
            "supports_pinning",
            "supports_release",
        }:
            raise RuntimeSelectionError(f"未知生命周期能力: {capability}")
        return tuple(
            mode for mode in self.modes if bool(getattr(mode.lifecycle, attribute))
        )

    def variants_for_accelerator(
        self, accelerator: str
    ) -> tuple[ComponentVariantEntry, ...]:
        return tuple(
            entry for entry in self.variants if entry.accelerator == accelerator
        )

    def component_ids_for_features(
        self, feature_ids: Iterable[str], accelerator: str
    ) -> tuple[str, ...]:
        """Map user feature semantics to wire component ids.

        Fails closed on features the current Backend catalog does not declare
        for the accelerator instead of guessing a component id.
        """

        by_feature = {
            entry.feature_id: entry
            for entry in self.variants_for_accelerator(accelerator)
        }
        resolved: list[str] = []
        for feature_id in feature_ids:
            entry = by_feature.get(feature_id)
            if entry is None:
                raise RuntimeSelectionError(
                    f"当前 Backend 未声明可选能力: {feature_id}/{accelerator}"
                )
            if entry.component_id not in resolved:
                resolved.append(entry.component_id)
        return tuple(resolved)

    def editable_sources_by_kind(
        self,
    ) -> dict[str, tuple[DownloadSourceEntry, ...]]:
        grouped: dict[str, list[DownloadSourceEntry]] = {}
        for source in self.sources:
            if source.editable:
                grouped.setdefault(source.kind, []).append(source)
        return {kind: tuple(entries) for kind, entries in grouped.items()}

    def normalize_source_selection(
        self, choices: Mapping[str, str]
    ) -> tuple[str, ...] | None:
        """Validate per-kind choices into wire ``download_source_ids``.

        ``choices`` maps source kind -> selected source id.  An empty mapping
        means "no explicit selection" and returns ``None`` so the wire field
        is omitted; the Backend then applies its own default sources.
        """

        if not choices:
            return None
        known = {source.source_id: source for source in self.sources}
        seen_kinds: set[str] = set()
        resolved: list[str] = []
        for kind, source_id in choices.items():
            source = known.get(source_id)
            if source is None:
                raise RuntimeSelectionError(f"未知下载源: {source_id}")
            if source.kind != kind:
                raise RuntimeSelectionError(f"下载源 {source_id} 不属于 {kind}")
            if kind in seen_kinds:
                raise RuntimeSelectionError(f"同一下载源类型选择了多个来源: {kind}")
            seen_kinds.add(kind)
            resolved.append(source_id)
        return tuple(resolved)

    def preserve_uneditable_source_ids(
        self, source_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Keep wire selections that Classic cannot edit through its UI."""

        editable_ids = {source.source_id for source in self.sources if source.editable}
        return tuple(
            source_id for source_id in source_ids if source_id not in editable_ids
        )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeSelectionError(f"catalog 字段无效: {field}")
    return value


def _parse_engine_catalog(value: object) -> tuple[OcrEngineEntry, ...]:
    if not isinstance(value, dict):
        raise RuntimeSelectionError("ocr_engine_catalog 无效")
    engines = value.get("engines")
    if not isinstance(engines, list):
        raise RuntimeSelectionError("ocr_engine_catalog.engines 无效")
    entries: list[OcrEngineEntry] = []
    seen: set[str] = set()
    for item in engines:
        if not isinstance(item, dict):
            raise RuntimeSelectionError("engine descriptor 无效")
        engine_id = _require_str(item.get("id"), "engine.id")
        if engine_id in seen:
            raise RuntimeSelectionError(f"重复的 engine id: {engine_id}")
        seen.add(engine_id)
        availability = item.get("availability")
        if availability not in _VALID_ENGINE_AVAILABILITY:
            raise RuntimeSelectionError(f"engine availability 无效: {engine_id}")
        included_in_base = item.get("included_in_base")
        if not isinstance(included_in_base, bool):
            raise RuntimeSelectionError(f"engine included_in_base 无效: {engine_id}")
        reason_code = item.get("reason_code")
        required_component = item.get("required_component")
        if reason_code is not None and not isinstance(reason_code, str):
            raise RuntimeSelectionError(f"engine reason_code 无效: {engine_id}")
        if required_component is not None and not isinstance(required_component, str):
            raise RuntimeSelectionError(f"engine required_component 无效: {engine_id}")
        entries.append(
            OcrEngineEntry(
                engine_id=engine_id,
                availability=str(availability),
                included_in_base=included_in_base,
                reason_code=reason_code,
                required_component=required_component,
            )
        )
    return tuple(entries)


def _parse_mode_lifecycle(value: object, mode_id: str) -> RecognitionModeLifecycle:
    if not isinstance(value, dict):
        raise RuntimeSelectionError(f"recognition mode lifecycle 无效: {mode_id}")
    kind = value.get("kind")
    if kind not in _VALID_RECOGNITION_MODE_LIFECYCLES:
        raise RuntimeSelectionError(f"recognition mode lifecycle.kind 无效: {mode_id}")
    flags: list[bool] = []
    for field in (
        "supports_preload",
        "supports_ttl",
        "supports_pinning",
        "supports_release",
    ):
        item = value.get(field)
        if not isinstance(item, bool):
            raise RuntimeSelectionError(
                f"recognition mode lifecycle.{field} 无效: {mode_id}"
            )
        flags.append(item)
    return RecognitionModeLifecycle(str(kind), *flags)


def _parse_recognition_mode_catalog(value: object) -> tuple[RecognitionModeEntry, ...]:
    if not isinstance(value, dict):
        raise RuntimeSelectionError("recognition_mode_catalog 无效")
    modes = value.get("modes")
    if not isinstance(modes, list):
        raise RuntimeSelectionError("recognition_mode_catalog.modes 无效")
    entries: list[RecognitionModeEntry] = []
    seen: set[str] = set()
    for item in modes:
        if not isinstance(item, dict):
            raise RuntimeSelectionError("recognition mode descriptor 无效")
        mode_id = _require_str(item.get("id"), "recognition_mode.id")
        expected = _EXPECTED_RECOGNITION_MODE_SEMANTICS.get(mode_id)
        if expected is None:
            raise RuntimeSelectionError(f"未知 recognition mode id: {mode_id}")
        if mode_id in seen:
            raise RuntimeSelectionError(f"重复的 recognition mode id: {mode_id}")
        seen.add(mode_id)
        family = item.get("family")
        provisioning = item.get("provisioning")
        availability = item.get("availability")
        if family not in _VALID_RECOGNITION_MODE_FAMILIES:
            raise RuntimeSelectionError(f"recognition mode family 无效: {mode_id}")
        if provisioning not in _VALID_RECOGNITION_MODE_PROVISIONING:
            raise RuntimeSelectionError(
                f"recognition mode provisioning 无效: {mode_id}"
            )
        if availability not in _VALID_ENGINE_AVAILABILITY:
            raise RuntimeSelectionError(
                f"recognition mode availability 无效: {mode_id}"
            )
        engine = item.get("engine")
        if engine is not None and engine not in VALID_ENGINE_IDS:
            raise RuntimeSelectionError(f"recognition mode engine 无效: {mode_id}")
        reason_code = item.get("reason_code")
        required_component = item.get("required_component")
        supported_options = item.get("supported_options")
        if reason_code is not None and not isinstance(reason_code, str):
            raise RuntimeSelectionError(f"recognition mode reason_code 无效: {mode_id}")
        if required_component is not None and not isinstance(required_component, str):
            raise RuntimeSelectionError(
                f"recognition mode required_component 无效: {mode_id}"
            )
        if (
            not isinstance(supported_options, list)
            or not all(
                isinstance(option, str) and option for option in supported_options
            )
            or len(set(supported_options)) != len(supported_options)
        ):
            raise RuntimeSelectionError(
                f"recognition mode supported_options 无效: {mode_id}"
            )
        lifecycle = _parse_mode_lifecycle(item.get("lifecycle"), mode_id)
        observed = (
            family,
            item.get("pipeline_id"),
            engine,
            provisioning,
            (
                lifecycle.kind,
                lifecycle.supports_preload,
                lifecycle.supports_ttl,
                lifecycle.supports_pinning,
                lifecycle.supports_release,
            ),
        )
        if observed != expected:
            raise RuntimeSelectionError(
                f"recognition mode 语义与 Protocol 固定契约不一致: {mode_id}"
            )
        entries.append(
            RecognitionModeEntry(
                mode_id=mode_id,
                family=str(family),
                pipeline_id=_require_str(
                    item.get("pipeline_id"), "recognition_mode.pipeline_id"
                ),
                engine=engine,
                provisioning=str(provisioning),
                availability=str(availability),
                lifecycle=lifecycle,
                reason_code=reason_code,
                required_component=required_component,
                supported_options=tuple(supported_options),
            )
        )
    if seen != set(_EXPECTED_RECOGNITION_MODE_SEMANTICS):
        raise RuntimeSelectionError("recognition_mode_catalog 必须声明完整八个稳定模式")
    return tuple(entries)


def _legacy_modes_from_engines(
    engines: Iterable[OcrEngineEntry],
) -> tuple[RecognitionModeEntry, ...]:
    """Keep pre-2.8 Backends usable through their engine catalog.

    The old catalog cannot safely express specialized pipelines or lifecycle
    controls, so the fallback intentionally offers only text OCR modes.
    """

    entries: list[RecognitionModeEntry] = []
    for engine in engines:
        mode_id = _LEGACY_ENGINE_MODE_IDS.get(engine.engine_id)
        if mode_id is None:
            continue
        provisioning = {
            "rapidocr": "base_runtime",
            "windows": "operating_system",
            "paddleocr": "advanced_component",
        }[engine.engine_id]
        lifecycle = RecognitionModeLifecycle(
            kind=(
                "model_residency" if engine.engine_id == "paddleocr" else "unmanaged"
            ),
            supports_preload=engine.engine_id == "paddleocr",
            supports_ttl=engine.engine_id == "paddleocr",
            supports_pinning=engine.engine_id == "paddleocr",
            supports_release=engine.engine_id == "paddleocr",
        )
        entries.append(
            RecognitionModeEntry(
                mode_id=mode_id,
                family="text",
                pipeline_id="OCR",
                engine=engine.engine_id,
                provisioning=provisioning,
                availability=engine.availability,
                lifecycle=lifecycle,
                reason_code=engine.reason_code,
                required_component=engine.required_component,
            )
        )
    return tuple(entries)


def recognition_mode_for_engine(engine_id: str | None) -> str | None:
    """Return the text-recognition mode represented by a legacy engine id."""

    return _LEGACY_ENGINE_MODE_IDS.get(engine_id or "")


def legacy_execution_projection(mode_id: str) -> tuple[str, str | None] | None:
    """Project a known mode without adding an unpublished request field."""

    return _LEGACY_MODE_PROJECTIONS.get(mode_id)


# Classic 的一个 Supervisor 会话只持有一份 health catalog。将其保留在这个
# 本地 projection seam，避免把未发布的 mode 字段写入 Protocol 请求；提交端
# 仍始终把 mode 转为已发布的 pipeline_id + engine。
_active_recognition_catalog: RuntimeSelectionCatalog | None = None


def set_active_recognition_catalog(catalog: RuntimeSelectionCatalog | None) -> None:
    """Publish the latest health descriptor for all local submit entry points."""

    global _active_recognition_catalog
    _active_recognition_catalog = catalog


def execution_projection_for_mode(mode_id: str) -> tuple[str, str | None] | None:
    """Resolve a mode through the negotiated catalog, then legacy fallback."""

    catalog = _active_recognition_catalog
    if catalog is not None and catalog.has_recognition_mode_catalog:
        return catalog.execution_projection(mode_id)
    return legacy_execution_projection(mode_id)


def supported_options_for_mode(
    mode_id: str, pipeline_id: str
) -> tuple[str, ...] | None:
    """Return negotiated option names for a mode, or ``None`` for legacy.

    An advertised recognition-mode catalog is authoritative not only for the
    execution projection but also for which UI fields can reach the strict
    PipelineSelection wire.  Legacy Backends have no mode-level option
    contract, so their existing pipeline schema remains the compatibility
    fallback.
    """

    catalog = _active_recognition_catalog
    if catalog is None or not catalog.has_recognition_mode_catalog:
        return None
    mode = catalog.mode(mode_id)
    if mode is None:
        raise RuntimeSelectionError(f"当前 Backend 未声明识别模式: {mode_id}")
    if mode.pipeline_id != pipeline_id:
        raise RuntimeSelectionError(f"recognition mode 投影不匹配: {mode_id}")
    return mode.supported_options


def _parse_download_source_catalog(value: object) -> tuple[DownloadSourceEntry, ...]:
    if not isinstance(value, dict):
        raise RuntimeSelectionError("download_source_catalog 无效")
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise RuntimeSelectionError("download_source_catalog.sources 无效")
    entries: list[DownloadSourceEntry] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            raise RuntimeSelectionError("download source descriptor 无效")
        kind = _require_str(item.get("kind"), "source.kind")
        source_id = _require_str(item.get("id"), "source.id")
        endpoint = _require_str(item.get("endpoint"), "source.endpoint")
        if source_id in seen:
            raise RuntimeSelectionError(f"重复的下载源 id: {source_id}")
        seen.add(source_id)
        entries.append(
            DownloadSourceEntry(kind=kind, source_id=source_id, endpoint=endpoint)
        )
    return tuple(entries)


def _parse_component_variant_catalog(
    value: object,
) -> tuple[ComponentVariantEntry, ...]:
    if not isinstance(value, dict):
        raise RuntimeSelectionError("component_variant_catalog 无效")
    variants = value.get("variants")
    if not isinstance(variants, list):
        raise RuntimeSelectionError("component_variant_catalog.variants 无效")
    entries: list[ComponentVariantEntry] = []
    seen: set[tuple[str, str]] = set()
    for item in variants:
        if not isinstance(item, dict):
            raise RuntimeSelectionError("component variant descriptor 无效")
        feature_id = _require_str(item.get("feature_id"), "variant.feature_id")
        accelerator = _require_str(item.get("accelerator"), "variant.accelerator")
        component_id = _require_str(item.get("component_id"), "variant.component_id")
        key = (feature_id, accelerator)
        if key in seen:
            raise RuntimeSelectionError(
                f"重复的可选能力业务键: {feature_id}/{accelerator}"
            )
        seen.add(key)
        entries.append(
            ComponentVariantEntry(
                feature_id=feature_id,
                accelerator=accelerator,
                component_id=component_id,
            )
        )
    return tuple(entries)


def parse_capability_catalogs(
    descriptors: Iterable[Mapping[str, Any]],
) -> RuntimeSelectionCatalog:
    """Extract selection catalogs from health capability descriptors.

    Catalogs the Backend did not advertise stay empty; callers must treat the
    corresponding capability as unsupported and hide the related UI instead
    of constructing requests.
    """

    engines: tuple[OcrEngineEntry, ...] = ()
    modes: tuple[RecognitionModeEntry, ...] = ()
    has_recognition_mode_catalog = False
    sources: tuple[DownloadSourceEntry, ...] = ()
    variants: tuple[ComponentVariantEntry, ...] = ()
    for descriptor in descriptors:
        name = descriptor.get("name")
        if not isinstance(name, str):
            raise RuntimeSelectionError("capability descriptor 缺少 name")
        if name == ENGINE_SELECTION_CAPABILITY:
            if "ocr_engine_catalog" in descriptor:
                engines = _parse_engine_catalog(descriptor["ocr_engine_catalog"])
        elif name == RECOGNITION_MODE_CAPABILITY:
            if "recognition_mode_catalog" in descriptor:
                modes = _parse_recognition_mode_catalog(
                    descriptor["recognition_mode_catalog"]
                )
                has_recognition_mode_catalog = True
        elif name == DOWNLOAD_SOURCES_CAPABILITY:
            if "download_source_catalog" in descriptor:
                sources = _parse_download_source_catalog(
                    descriptor["download_source_catalog"]
                )
        elif name == COMPONENT_SELECTION_CAPABILITY:
            if "component_variant_catalog" in descriptor:
                variants = _parse_component_variant_catalog(
                    descriptor["component_variant_catalog"]
                )
    if not modes:
        modes = _legacy_modes_from_engines(engines)
    return RuntimeSelectionCatalog(
        engines=engines,
        modes=modes,
        sources=sources,
        variants=variants,
        has_recognition_mode_catalog=has_recognition_mode_catalog,
    )


def resolve_engine_id(
    override: str | None, default_engine: str | None = None
) -> str | None:
    """Resolve the task engine without silently replacing unknown values.

    Returns ``None`` (wire omission, Backend default) when neither the task
    override nor the configured default is a known stable engine id.
    """

    for candidate in (override, default_engine):
        if candidate is not None and candidate not in VALID_ENGINE_IDS:
            continue
        if candidate is not None:
            return candidate
    return None


def normalize_stored_engine(value: object) -> tuple[str | None, bool]:
    """Load a persisted engine value.

    Returns ``(engine_id, requires_selection)``: missing values migrate to
    the RapidOCR default; unknown legacy values are kept out of the effective
    selection and flagged for explicit user re-selection.
    """

    if not isinstance(value, str) or not value:
        return DEFAULT_ENGINE_ID, False
    if value in VALID_ENGINE_IDS:
        return value, False
    return None, True


__all__ = [
    "COMPONENT_SELECTION_CAPABILITY",
    "DOWNLOAD_SOURCES_CAPABILITY",
    "DEFAULT_ENGINE_ID",
    "EDITABLE_SOURCE_KINDS",
    "ENGINE_AVAILABILITY_LABELS",
    "ENGINE_AVAILABILITY_PREPARATION_REQUIRED",
    "ENGINE_AVAILABILITY_READY",
    "ENGINE_AVAILABILITY_UNAVAILABLE",
    "ENGINE_DISPLAY_NAMES",
    "ENGINE_SELECTION_CAPABILITY",
    "RECOGNITION_MODE_CAPABILITY",
    "RECOGNITION_MODE_DISPLAY_NAMES",
    "ComponentVariantEntry",
    "DownloadSourceEntry",
    "OcrEngineEntry",
    "RecognitionModeEntry",
    "RecognitionModeLifecycle",
    "RuntimeSelectionCatalog",
    "RuntimeSelectionError",
    "VALID_ENGINE_IDS",
    "normalize_stored_engine",
    "legacy_execution_projection",
    "execution_projection_for_mode",
    "supported_options_for_mode",
    "parse_capability_catalogs",
    "recognition_mode_for_engine",
    "resolve_engine_id",
    "set_active_recognition_catalog",
]
