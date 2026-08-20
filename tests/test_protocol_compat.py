from __future__ import annotations

from collections.abc import Iterator

import pytest
from vibeocr.classic.protocol_compat import (
    enable_pipeline_engine_parser_compatibility,
)
from vibeocr.runtime_client.client import SupervisorClient
from vibeocr.runtime_contracts import (
    JobKind,
    JobPriority,
    JobSnapshot,
    JobState,
    JobUpdate,
    PipelineSelection,
)
from vibeocr.runtime_contracts import parser as contract_parser
from vibeocr.runtime_contracts.dtos import OcrEngine
from vibeocr.runtime_contracts.parser import ContractError


@pytest.fixture(autouse=True)
def restore_protocol_parser() -> Iterator[None]:
    original = contract_parser.parse_pipeline_selection
    try:
        yield
    finally:
        contract_parser.parse_pipeline_selection = original


def test_compat_parser_round_trips_the_protocol_engine_field() -> None:
    enable_pipeline_engine_parser_compatibility()
    enable_pipeline_engine_parser_compatibility()

    selection = contract_parser.parse_pipeline_selection(
        PipelineSelection("OCR", engine=OcrEngine.RAPIDOCR).to_payload()
    )

    assert selection.engine is OcrEngine.RAPIDOCR


@pytest.mark.parametrize(
    ("payload_override", "message"),
    [
        ({"engine": None}, "OCR engine must be a string"),
        ({"engine": "cuda"}, "unknown OCR engine"),
        (
            {"pipeline_id": "TABLE_RECOGNITION", "engine": "rapidocr"},
            "OCR engine is only valid for the OCR pipeline",
        ),
        (
            {"engine": "rapidocr", "future_field": True},
            "pipeline selection has unknown field.*future_field",
        ),
    ],
)
def test_compat_parser_keeps_engine_and_upstream_fields_strict(
    payload_override: dict[str, object], message: str
) -> None:
    enable_pipeline_engine_parser_compatibility()
    payload: dict[str, object] = {
        "pipeline_id": "OCR",
        "options_version": 1,
        "options": {},
    }
    payload.update(payload_override)

    with pytest.raises(ContractError, match=message):
        contract_parser.parse_pipeline_selection(payload)


@pytest.mark.asyncio
async def test_supervisor_client_observe_parses_engine_from_a_real_update() -> None:
    enable_pipeline_engine_parser_compatibility()
    payload = JobUpdate(
        snapshot=JobSnapshot(
            job_id="job-1",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.RUNNING,
            pipeline=PipelineSelection("OCR", engine=OcrEngine.WINDOWS),
        ),
        events=(),
        outcomes=(),
        through_sequence=0,
    ).to_payload()

    class FakeRuntimeTransport:
        def observe_job(
            self, job_id: str, *, after_sequence: int = 0
        ) -> dict[str, object]:
            assert job_id == "job-1"
            assert after_sequence == 3
            return payload

    client = SupervisorClient(
        base_url="http://127.0.0.1:43210",
        session_token="token",
        instance_id="runtime-1",
    )
    client._transport = FakeRuntimeTransport()

    async with client:
        update = await client.observe("job-1", after_sequence=3)

    assert update.snapshot.pipeline is not None
    assert update.snapshot.pipeline.engine is OcrEngine.WINDOWS
