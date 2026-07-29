"""Frontend-only client SDK for the PySide application.

Legacy worker_host client code has been removed. The remaining helpers here
(filename derivation, unique-path, and a no-op shutdown hook) are still used
by the export/main-window code; ``get_backend_client`` was deleted because the
v2 supervisor is the only backend.
"""

__all__ = [
    "get_output_filename",
    "get_unique_output_path",
    "shutdown_backend_client",
]


def get_output_filename(source_name: str, export_format: str) -> str:
    """Get the output filename for an export."""
    from pathlib import Path
    stem = Path(source_name).stem
    extension = {
        "markdown": ".md",
        "html": ".html",
        "txt": ".txt",
        "docx": ".docx",
        "xlsx": ".xlsx",
    }.get(export_format, ".txt")
    return stem + extension


def get_unique_output_path(output_path):
    """Get a unique output path by appending a counter if the file exists."""
    if not output_path.exists():
        return output_path
    counter = 1
    while True:
        candidate = output_path.with_name(f"{output_path.stem}_{counter}{output_path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def shutdown_backend_client():
    """Tear down process-wide background resources (pdf supervisor loop).

    Called from ``pytest_sessionfinish`` and app shutdown hooks. Previously a
    no-op, which left the ``pdf-supervisor-loop`` daemon thread running for the
    whole pytest session and contributed to access-violation crashes when later
    Qt tests created widgets in the restricted CI session.

    The import is lazy *and* guarded: some CI jobs (e.g. the contracts gate)
    run with a minimal dependency set that lacks ``httpx``, and
    ``pdf_client`` imports it at module load. In that case we degrade to the
    prior no-op rather than crashing session teardown.
    """
    try:
        from vibeocr.classic.pdf_client import _shutdown_bg_loop
    except ImportError:
        return
    _shutdown_bg_loop()

