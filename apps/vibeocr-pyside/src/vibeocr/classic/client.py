"""Frontend-only client SDK for the PySide application.

Legacy worker_host client code has been removed. The remaining helpers here
(filename derivation, unique-path, and a no-op shutdown hook) are still used
by the export/main-window code; ``get_backend_client`` was deleted because the
v2 supervisor is the only backend.
"""

import sys

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

    Closing an unused client must remain a no-op. Importing ``pdf_client`` here
    would create the dependency graph merely to tear it down and can perform
    its first heavy import from an installer ``QThread``. Only a module already
    loaded by a real PDF client can own a background loop that needs closing.
    """
    pdf_client = sys.modules.get("vibeocr.classic.pdf_client")
    if pdf_client is None:
        return
    shutdown = getattr(pdf_client, "_shutdown_bg_loop", None)
    if shutdown is not None:
        shutdown()

