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
FORBIDDEN_BACKEND_OCR_PRESENTATION_MODULES = {
    "vibeocr.backend.models",
    "vibeocr.backend.models.DISCARDED_BLOCK_TYPES",
    "vibeocr.backend.models.OCRResult",
    "vibeocr.backend.models.TextBlock",
    "vibeocr.backend.models.normalize_bbox",
    "vibeocr.backend.models.ocr_result",
    "vibeocr.backend.models.ocr_result_from_payload",
    "vibeocr.backend.models.ocr_result_serializer",
}
FORBIDDEN_BACKEND_APP_PATH_MODULES = {"vibeocr.backend.env_manager"}
FORBIDDEN_BACKEND_APP_PATH_HELPERS = {
    "get_bundled_changelog_path",
    "get_bundled_resources_dir",
    "get_project_root",
    "get_resources_path",
}
APP_PATH_ONLY_CONSUMERS = (
    CLASSIC_SOURCE_ROOT / "main.py",
    CLASSIC_SOURCE_ROOT / "services" / "update_service.py",
    CLASSIC_SOURCE_ROOT / "views" / "tabs" / "about_tab.py",
)


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


def _forbidden_imports(imported: set[str], forbidden_roots: set[str]) -> set[str]:
    return {
        module
        for module in imported
        if any(
            module == root or module.startswith(f"{root}.") for root in forbidden_roots
        )
    }


def _backend_env_path_calls(source: str, *, filename: str = "<source>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    module_aliases = {"vibeocr.backend.env_manager"}
    direct_helpers: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_aliases.update(
                alias.asname
                for alias in node.names
                if alias.name == "vibeocr.backend.env_manager" and alias.asname
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "vibeocr.backend":
                module_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "env_manager"
                )
            elif node.module == "vibeocr.backend.env_manager":
                direct_helpers.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in FORBIDDEN_BACKEND_APP_PATH_HELPERS
                )

    violations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in direct_helpers:
            violations.add(node.func.id)
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        parts = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        dotted = ".".join(reversed(parts))
        module, _, helper = dotted.rpartition(".")
        if helper in FORBIDDEN_BACKEND_APP_PATH_HELPERS and module in module_aliases:
            violations.add(dotted)
    return violations


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


def test_ocr_presentation_guard_recognizes_symbols_and_submodules() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.models import OCRResult, ocr_result_from_payload\n"
        "import vibeocr.backend.models.ocr_result.compat\n"
        "import vibeocr.backend.models as models\n"
        "from vibeocr.backend import models\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_OCR_PRESENTATION_MODULES)


def test_app_path_guard_recognizes_backend_helper_imports() -> None:
    imported = _imported_modules(
        "from vibeocr.backend import env_manager\n"
        "from vibeocr.backend.env_manager import get_project_root\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_APP_PATH_MODULES)


def test_app_path_guard_recognizes_direct_and_aliased_calls() -> None:
    calls = _backend_env_path_calls(
        "from vibeocr.backend import env_manager as em\n"
        "from vibeocr.backend.env_manager import get_project_root as root\n"
        "import vibeocr.backend.env_manager\n"
        "em.get_bundled_resources_dir()\n"
        "root()\n"
        "vibeocr.backend.env_manager.get_resources_path()\n"
    )

    assert calls == {
        "em.get_bundled_resources_dir",
        "root",
        "vibeocr.backend.env_manager.get_resources_path",
    }


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


def test_classic_does_not_import_backend_ocr_presentation_models() -> None:
    violations: list[str] = []

    for source_file in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        imported = _imported_modules(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        forbidden = _forbidden_imports(
            imported, FORBIDDEN_BACKEND_OCR_PRESENTATION_MODULES
        )
        violations.extend(
            f"{source_file.relative_to(CLASSIC_SOURCE_ROOT)}: {module}"
            for module in sorted(forbidden)
        )

    assert not violations, "Backend OCR presentation leaked into Classic:\n" + (
        "\n".join(violations)
    )


def test_app_path_owned_consumers_do_not_import_backend_env_manager() -> None:
    violations: list[str] = []

    for source_file in APP_PATH_ONLY_CONSUMERS:
        imported = _imported_modules(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        forbidden = _forbidden_imports(imported, FORBIDDEN_BACKEND_APP_PATH_MODULES)
        violations.extend(
            f"{source_file.relative_to(CLASSIC_SOURCE_ROOT)}: {module}"
            for module in sorted(forbidden)
        )

    assert not violations, "Backend app paths leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_does_not_call_backend_path_helpers() -> None:
    violations: list[str] = []

    for source_file in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        calls = _backend_env_path_calls(
            source_file.read_text(encoding="utf-8"), filename=str(source_file)
        )
        violations.extend(
            f"{source_file.relative_to(CLASSIC_SOURCE_ROOT)}: {call}"
            for call in sorted(calls)
        )

    assert not violations, "Backend path helpers leaked into Classic:\n" + "\n".join(
        violations
    )
