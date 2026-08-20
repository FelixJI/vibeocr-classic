"""Temporary compatibility seams for released Protocol SDK defects."""

from __future__ import annotations

from functools import wraps
from threading import Lock
from typing import Any

from vibeocr.runtime_contracts import PipelineSelection
from vibeocr.runtime_contracts import parser as contract_parser
from vibeocr.runtime_contracts.dtos import OcrEngine
from vibeocr.runtime_contracts.parser import ContractError

_COMPAT_MARKER = "__vibeocr_classic_pipeline_engine_compat__"
_ENABLE_LOCK = Lock()
_PROBE_PAYLOAD = PipelineSelection("OCR", engine=OcrEngine.RAPIDOCR).to_payload()


def _upstream_parser_supports_engine() -> bool:
    parser = contract_parser.parse_pipeline_selection
    try:
        selection = parser(_PROBE_PAYLOAD)
    except ContractError as exc:
        if str(exc) == "pipeline selection has unknown field(s): engine":
            return False
        raise RuntimeError(
            "installed Protocol parser rejects a valid OCR engine unexpectedly"
        ) from exc
    if selection.engine is OcrEngine.RAPIDOCR:
        return True
    raise RuntimeError("installed Protocol parser silently discarded the OCR engine")


def _parse_engine(raw_engine: object) -> OcrEngine:
    if not isinstance(raw_engine, str):
        raise ContractError(
            f"OCR engine must be a string, got {type(raw_engine).__name__}"
        )
    try:
        return OcrEngine(raw_engine)
    except ValueError as exc:
        raise ContractError(f"unknown OCR engine: {raw_engine!r}") from exc


def enable_pipeline_engine_parser_compatibility() -> None:
    """Repair the Protocol 2.7 engine parser asymmetry, if still present.

    Protocol 2.7.0 and 2.7.1 serialize ``PipelineSelection.engine`` but their
    parser rejects that same field. This process-local seam can be deleted once
    the released parser round-trips the probe without assistance.
    """

    with _ENABLE_LOCK:
        current_parser = contract_parser.parse_pipeline_selection
        if getattr(current_parser, _COMPAT_MARKER, False):
            return
        if _upstream_parser_supports_engine():
            return

        @wraps(current_parser)
        def parse_pipeline_selection(
            payload: dict[str, Any],
        ) -> PipelineSelection:
            if not isinstance(payload, dict) or "engine" not in payload:
                return current_parser(payload)

            upstream_payload = dict(payload)
            raw_engine = upstream_payload.pop("engine")
            selection = current_parser(upstream_payload)
            engine = _parse_engine(raw_engine)
            if selection.pipeline_id != "OCR":
                raise ContractError("OCR engine is only valid for the OCR pipeline")
            return PipelineSelection(
                pipeline_id=selection.pipeline_id,
                options_version=selection.options_version,
                options=selection.options,
                engine=engine,
            )

        setattr(parse_pipeline_selection, _COMPAT_MARKER, True)
        contract_parser.parse_pipeline_selection = parse_pipeline_selection


__all__ = ["enable_pipeline_engine_parser_compatibility"]
