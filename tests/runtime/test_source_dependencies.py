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
FORBIDDEN_BACKEND_PDF_MODULES = {
    "vibeocr.backend.ipc.schemas",
    "vibeocr.backend.ipc.model_bridge",
    "vibeocr.backend.models.pdf_session",
}
FORBIDDEN_BACKEND_SETTINGS_MODULES = {
    "vibeocr.backend.core.pipelines",
    "vibeocr.backend.models.export_settings",
    "vibeocr.backend.models.ocr_options",
    "vibeocr.backend.models.pdf_ocr_options",
    "vibeocr.backend.models.text_block_options",
}


def _imported_modules(source: str, *, filename: str = "<source>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _forbidden_imports(
    imported: set[str], forbidden_roots: set[str]
) -> set[str]:
    return {
        module
        for module in imported
        if any(
            module == root or module.startswith(f"{root}.")
            for root in forbidden_roots
        )
    }


def test_pdf_dependency_guard_recognizes_equivalent_import_syntax() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.ipc import schemas, model_bridge\n"
        "from vibeocr.backend.models import pdf_session\n"
    )

    assert FORBIDDEN_BACKEND_PDF_MODULES <= imported


def test_settings_dependency_guard_recognizes_backend_submodules() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.core.pipelines import pipeline_mineru\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_SETTINGS_MODULES)


def test_pdf_frontend_does_not_import_backend_wire_or_session_modules() -> None:
    violations: list[str] = []

    for source_file in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        imported = _imported_modules(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        forbidden = _forbidden_imports(imported, FORBIDDEN_BACKEND_PDF_MODULES)
        violations.extend(
            f"{source_file.relative_to(CLASSIC_SOURCE_ROOT)}: {module}"
            for module in sorted(forbidden)
        )

    assert not violations, "Backend PDF seam leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_settings_do_not_import_backend_value_models() -> None:
    violations: list[str] = []

    for source_file in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        imported = _imported_modules(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        forbidden = _forbidden_imports(imported, FORBIDDEN_BACKEND_SETTINGS_MODULES)
        violations.extend(
            f"{source_file.relative_to(CLASSIC_SOURCE_ROOT)}: {module}"
            for module in sorted(forbidden)
        )

    assert not violations, "Backend settings leaked into Classic:\n" + "\n".join(
        violations
    )
