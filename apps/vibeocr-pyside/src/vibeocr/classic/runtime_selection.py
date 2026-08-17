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

# Protocol 保持 source kind 开放；Classic 只把已知 kind 暴露为可编辑选项，
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
    sources: tuple[DownloadSourceEntry, ...] = ()
    variants: tuple[ComponentVariantEntry, ...] = ()

    def engine(self, engine_id: str) -> OcrEngineEntry | None:
        return next(
            (entry for entry in self.engines if entry.engine_id == engine_id), None
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
    sources: tuple[DownloadSourceEntry, ...] = ()
    variants: tuple[ComponentVariantEntry, ...] = ()
    for descriptor in descriptors:
        name = descriptor.get("name")
        if not isinstance(name, str):
            raise RuntimeSelectionError("capability descriptor 缺少 name")
        if name == ENGINE_SELECTION_CAPABILITY:
            if "ocr_engine_catalog" in descriptor:
                engines = _parse_engine_catalog(descriptor["ocr_engine_catalog"])
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
    return RuntimeSelectionCatalog(engines=engines, sources=sources, variants=variants)


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
    "ComponentVariantEntry",
    "DownloadSourceEntry",
    "OcrEngineEntry",
    "RuntimeSelectionCatalog",
    "RuntimeSelectionError",
    "VALID_ENGINE_IDS",
    "normalize_stored_engine",
    "parse_capability_catalogs",
    "resolve_engine_id",
]
