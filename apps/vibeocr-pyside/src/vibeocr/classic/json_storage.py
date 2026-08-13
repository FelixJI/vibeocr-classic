"""JSON persistence primitives shared by UI and UI-free modules."""

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data: object) -> None:
    """Serialize *data* and atomically replace *path* with the result.

    Serialization happens before the target directory is touched. The temporary
    file is created beside the target so ``Path.replace`` stays on one volume,
    including on Windows.
    """
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(serialized)
        temporary.replace(path)
    except Exception:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise
