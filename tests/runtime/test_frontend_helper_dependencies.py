"""Classic 自有纯 helper 不得回退到 Backend 实现。"""

from __future__ import annotations

import ast
from pathlib import Path


CLASSIC_SOURCE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "vibeocr-pyside"
    / "src"
    / "vibeocr"
    / "classic"
)
FORBIDDEN_BACKEND_HELPERS = {
    "vibeocr.backend.utils.cpu_info",
    "vibeocr.backend.utils.mime_types",
    "vibeocr.backend.utils.pdf_coords",
}


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_classic_does_not_import_backend_pure_helpers() -> None:
    violations: list[str] = []
    for source_path in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        forbidden = _imported_modules(source_path) & FORBIDDEN_BACKEND_HELPERS
        violations.extend(
            f"{source_path.relative_to(CLASSIC_SOURCE_ROOT)}: {module}"
            for module in sorted(forbidden)
        )

    assert not violations, "\n".join(violations)
