"""Small in-memory fake for the synchronous generic job client."""

from __future__ import annotations

from uuid import uuid4

from vibeocr.runtime_contracts import (
    ItemOutcome,
    ItemState,
    JobItem,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    JobUpdate,
)


def _ocr_result_payload(result: object) -> dict[str, object]:
    """Build the small ``ocr.v1`` payload needed by this in-memory fake."""

    if isinstance(result, dict):
        return result
    return {
        "raw_text": str(getattr(result, "raw_text", "")),
        "text_blocks": list(getattr(result, "text_blocks", ()) or ()),
        "preproc_angle": int(getattr(result, "preproc_angle", 0) or 0),
    }


class FakeSyncSupervisorJobClient:
    def __init__(self, result_factory) -> None:
        self._result_factory = result_factory
        self._jobs = {}
        self.submit_calls = []
        self.command_calls = []

    def submit(self, request, attachments):
        self.submit_calls.append((request, attachments))
        job_id = str(uuid4())
        items = tuple(
            JobItem(
                item_id=f"{job_id}:{item.ordinal}",
                display_name=item.display_name,
                state=ItemState.SUCCEEDED,
                client_item_key=item.client_item_key,
                ordinal=item.ordinal,
            )
            for item in request.items
        )
        outcomes = tuple(
            ItemOutcome(
                item_id=item.item_id,
                state=ItemState.SUCCEEDED,
                attempt=1,
                payload_type="ocr.v1",
                payload=_ocr_result_payload(self._result_factory(index, request)),
            )
            for index, item in enumerate(items)
        )
        ref = JobRef(job_id=job_id, state=JobState.ACCEPTED, items=items)
        self._jobs[job_id] = (request, items, outcomes)
        return ref

    def observe(self, job_id, *, after_sequence=0):
        request, items, outcomes = self._jobs[job_id]
        snapshot = JobSnapshot(
            job_id=job_id,
            kind=request.kind,
            priority=request.priority,
            state=JobState.COMPLETED,
            items=items,
            summary=JobSummary(succeeded=len(items), total=len(items)),
            progress_current=len(items),
            progress_total=len(items),
            result_available=True,
            pipeline=request.pipeline,
        )
        return JobUpdate(
            snapshot=snapshot,
            events=(),
            outcomes=outcomes if after_sequence == 0 else (),
            through_sequence=1,
        )

    def command(self, command):
        self.command_calls.append(command)

    def close(self) -> None:
        pass
