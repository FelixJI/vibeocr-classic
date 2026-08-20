"""Packaged offline-base product probe shared by release verification seams."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeocr.classic.runtime_installation import RuntimeLaunch


def probe_runtime_launch(launch: RuntimeLaunch, state_root: Path) -> None:
    """Run one explicit RapidOCR job and one basic PDF session."""
    from PIL import Image, ImageDraw
    from vibeocr.classic.pdf_client import SyncPdfSupervisorClient
    from vibeocr.classic.protocol_compat import (
        enable_pipeline_engine_parser_compatibility,
    )

    enable_pipeline_engine_parser_compatibility()

    from vibeocr.runtime_client.client import SupervisorClient
    from vibeocr.runtime_client.job_handle import JobHandle
    from vibeocr.runtime_client.process import SupervisorProcess
    from vibeocr.runtime_contracts import (
        JobKind,
        JobPriority,
        PipelineSelection,
        SubmitItem,
        SubmitRequest,
    )
    from vibeocr.runtime_contracts.dtos import OcrEngine

    small = Image.new("RGB", (210, 60), "white")
    ImageDraw.Draw(small).text((12, 20), "VibeOCR 123", fill="black")
    image = small.resize((840, 240))
    png = io.BytesIO()
    image.save(png, format="PNG")
    pdf_path = state_root / "temp" / "offline-base-smoke.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(pdf_path, format="PDF")

    process = SupervisorProcess.launch(
        python_exe=launch.python_executable,
        module=launch.supervisor_module,
        extra_env=launch.environment,
        working_directory=launch.working_directory,
        startup_timeout=60.0,
    )
    try:

        async def recognize() -> list[Any]:
            async with SupervisorClient(
                base_url=process.base_url,
                session_token=process.session_token,
                instance_id=process.ready.instance_id,
            ) as client:
                request = SubmitRequest(
                    request_id="classic-offline-base-rapidocr",
                    kind=JobKind.RECOGNITION,
                    priority=JobPriority.INTERACTIVE,
                    pipeline=PipelineSelection("OCR", engine=OcrEngine.RAPIDOCR),
                    items=(
                        SubmitItem(
                            client_item_key="rapidocr-0",
                            ordinal=0,
                            display_name="rapidocr-fixed.png",
                            source={
                                "type": "upload.v1",
                                "attachment": "input-0",
                            },
                        ),
                    ),
                )
                ref = await client.submit(
                    request,
                    {"input-0": ("image/png", png.getvalue())},
                )
                handle = JobHandle(client=client, ref=ref)
                await handle.wait_for_terminal(timeout=120.0)
                return await handle.result()

        results = asyncio.run(recognize())
        if len(results) != 1 or results[0].error_code or not results[0].payload:
            raise RuntimeError("offline base RapidOCR smoke returned no usable result")

        pdf = SyncPdfSupervisorClient(
            base_url=process.base_url,
            session_token=process.session_token,
            instance_id=process.ready.instance_id,
        )
        try:
            opened = pdf.open_session(str(pdf_path))
            tuple(pdf.load_stream(opened.session_id))
            model = pdf.get_model(opened.session_id)
            thumbnail = pdf.render_thumbnail(opened.session_id, 0, size=160)
            if len(model.pages) != 1 or not thumbnail:
                raise RuntimeError("offline base PDF smoke returned no page/thumbnail")
            pdf.close_session(opened.session_id)
        finally:
            pdf.close()
    finally:
        process.shutdown(timeout=10.0)
        pdf_path.unlink(missing_ok=True)


__all__ = ["probe_runtime_launch"]
