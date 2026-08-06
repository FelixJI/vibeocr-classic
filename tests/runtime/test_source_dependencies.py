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
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLASSIC_TEST_ROOT = REPOSITORY_ROOT / "tests"
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
FORBIDDEN_BACKEND_BATCH_BUDGET_MODULES = {
    "vibeocr.backend.core.batch_budget",
}
FORBIDDEN_BACKEND_TEXT_LAYOUT_MODULES = {
    "vibeocr.backend.utils.text_layout",
}
FORBIDDEN_BACKEND_OCR_SIDECAR_MODULES = {
    "vibeocr.backend.utils.ocr_sidecar",
}
FORBIDDEN_BACKEND_TABLE_MODULES = {
    "vibeocr.backend.tables",
    "vibeocr.backend.utils.html_tables",
}
BACKEND_GPU_CACHE_PATH = "vibeocr.backend.env_manager._runtime_gpu_capability_cache"
FORBIDDEN_BACKEND_APP_PATH_HELPERS = {
    "get_bundled_changelog_path",
    "get_bundled_resources_dir",
    "get_project_root",
    "get_resources_path",
}
APP_PATH_ONLY_CONSUMERS = (
    CLASSIC_SOURCE_ROOT / "main.py",
    CLASSIC_SOURCE_ROOT / "services" / "update_service.py",
    CLASSIC_SOURCE_ROOT / "views" / "main_window.py",
    CLASSIC_SOURCE_ROOT / "views" / "tabs" / "about_tab.py",
)

# Temporary migration ledger.  Every remaining production import from the
# Backend source package must be named here and this mapping must only shrink.
# Protocol/runtime executable identity strings are intentionally outside this
# source-import boundary.
EXPECTED_BACKEND_SOURCE_IMPORTS = {
    "pyside/pdf_ipc_worker.py": {
        "vibeocr.backend.env_manager",
        "vibeocr.backend.env_manager.ensure_mineru_models",
    },
    "pyside/pdf_session_manager.py": {
        "vibeocr.backend.pipeline_status",
        "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded",
    },
    "services/update_service.py": {
        "vibeocr.backend.network_detector",
        "vibeocr.backend.network_detector.NetworkDetector",
    },
    "views/settings_page_controller.py": {
        "vibeocr.backend",
        "vibeocr.backend.env_manager",
    },
    "widgets/backend_choice_dialog.py": {
        "vibeocr.backend",
        "vibeocr.backend.env_manager",
    },
    "widgets/backend_options_widget.py": {
        "vibeocr.backend",
        "vibeocr.backend.env_manager",
    },
    "widgets/switch_dialog.py": {
        "vibeocr.backend",
        "vibeocr.backend.env_manager",
        "vibeocr.backend.network_detector",
        "vibeocr.backend.network_detector.NetworkDetector",
    },
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


def _backend_gpu_cache_accesses(source: str, *, filename: str = "<source>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    module_aliases = {"vibeocr.backend.env_manager"}
    direct_cache_names: set[str] = set()
    violations: set[str] = set()

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
                imported_cache_names = {
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "_runtime_gpu_capability_cache"
                }
                if imported_cache_names:
                    direct_cache_names.update(imported_cache_names)
                    violations.add(BACKEND_GPU_CACHE_PATH)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in direct_cache_names:
            violations.add(BACKEND_GPU_CACHE_PATH)
            continue

        if isinstance(node, ast.Attribute):
            parts = [node.attr]
            value = node.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            dotted = ".".join(reversed(parts))
            module, _, name = dotted.rpartition(".")
            if name == "_runtime_gpu_capability_cache" and module in module_aliases:
                violations.add(BACKEND_GPU_CACHE_PATH)
            continue

        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        owner, attribute = node.args[:2]
        if (
            isinstance(owner, ast.Name)
            and owner.id in module_aliases
            and isinstance(attribute, ast.Constant)
            and attribute.value == "_runtime_gpu_capability_cache"
        ):
            violations.add(BACKEND_GPU_CACHE_PATH)

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


def test_batch_budget_guard_recognizes_equivalent_import_syntax() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.core import batch_budget\n"
        "from vibeocr.backend.core.batch_budget import BatchBudget\n"
        "import vibeocr.backend.core.batch_budget as budget\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_BATCH_BUDGET_MODULES) == {
        "vibeocr.backend.core.batch_budget",
        "vibeocr.backend.core.batch_budget.BatchBudget",
    }


def test_text_layout_guard_recognizes_equivalent_import_syntax() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.utils import text_layout\n"
        "from vibeocr.backend.utils.text_layout import TextBlockProcessor\n"
        "import vibeocr.backend.utils.text_layout as layout\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_TEXT_LAYOUT_MODULES) == {
        "vibeocr.backend.utils.text_layout",
        "vibeocr.backend.utils.text_layout.TextBlockProcessor",
    }


def test_ocr_sidecar_guard_recognizes_equivalent_import_syntax() -> None:
    imported = _imported_modules(
        "from vibeocr.backend.utils import ocr_sidecar\n"
        "from vibeocr.backend.utils.ocr_sidecar import restore_pending_pages\n"
        "import vibeocr.backend.utils.ocr_sidecar as sidecar\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_OCR_SIDECAR_MODULES) == {
        "vibeocr.backend.utils.ocr_sidecar",
        "vibeocr.backend.utils.ocr_sidecar.restore_pending_pages",
    }


def test_table_guard_recognizes_backend_modules_and_symbols() -> None:
    imported = _imported_modules(
        "from vibeocr.backend import tables\n"
        "from vibeocr.backend.tables.reducer import update_result_table_cell\n"
        "from vibeocr.backend.utils import html_tables\n"
        "import vibeocr.backend.utils.html_tables as legacy_tables\n"
    )

    assert _forbidden_imports(imported, FORBIDDEN_BACKEND_TABLE_MODULES) == {
        "vibeocr.backend.tables",
        "vibeocr.backend.tables.reducer",
        "vibeocr.backend.tables.reducer.update_result_table_cell",
        "vibeocr.backend.utils.html_tables",
    }


def test_gpu_cache_guard_recognizes_private_cache_access_syntaxes() -> None:
    accesses = _backend_gpu_cache_accesses(
        "from vibeocr.backend.env_manager import "
        "_runtime_gpu_capability_cache as cached_gpu\n"
        "from vibeocr.backend import env_manager as em\n"
        "import vibeocr.backend.env_manager\n"
        "cached_gpu\n"
        "em._runtime_gpu_capability_cache\n"
        "vibeocr.backend.env_manager._runtime_gpu_capability_cache\n"
        'monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)\n'
    )

    assert accesses == {BACKEND_GPU_CACHE_PATH}


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


def test_remaining_backend_source_imports_match_migration_ledger() -> None:
    actual: dict[str, set[str]] = {}

    for source_file in CLASSIC_SOURCE_ROOT.rglob("*.py"):
        imported = {
            module
            for module in _imported_modules(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            if module == "vibeocr.backend" or module.startswith("vibeocr.backend.")
        }
        if imported:
            actual[source_file.relative_to(CLASSIC_SOURCE_ROOT).as_posix()] = imported

    assert actual == EXPECTED_BACKEND_SOURCE_IMPORTS


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


def test_classic_source_and_tests_do_not_import_backend_batch_budget() -> None:
    violations: list[str] = []

    for root in (CLASSIC_SOURCE_ROOT, CLASSIC_TEST_ROOT):
        for source_file in root.rglob("*.py"):
            imported = _imported_modules(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            forbidden = _forbidden_imports(
                imported, FORBIDDEN_BACKEND_BATCH_BUDGET_MODULES
            )
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)}: {module}"
                for module in sorted(forbidden)
            )

    assert not violations, "Backend batch budget leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_source_and_tests_do_not_import_backend_text_layout() -> None:
    violations: list[str] = []

    for root in (CLASSIC_SOURCE_ROOT, CLASSIC_TEST_ROOT):
        for source_file in root.rglob("*.py"):
            imported = _imported_modules(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            forbidden = _forbidden_imports(
                imported, FORBIDDEN_BACKEND_TEXT_LAYOUT_MODULES
            )
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)}: {module}"
                for module in sorted(forbidden)
            )

    assert not violations, "Backend text layout leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_source_and_tests_do_not_import_backend_ocr_sidecar() -> None:
    violations: list[str] = []

    for root in (CLASSIC_SOURCE_ROOT, CLASSIC_TEST_ROOT):
        for source_file in root.rglob("*.py"):
            imported = _imported_modules(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            forbidden = _forbidden_imports(
                imported, FORBIDDEN_BACKEND_OCR_SIDECAR_MODULES
            )
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)}: {module}"
                for module in sorted(forbidden)
            )

    assert not violations, "Backend OCR sidecar leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_source_and_tests_do_not_import_backend_table_modules() -> None:
    violations: list[str] = []

    for root in (CLASSIC_SOURCE_ROOT, CLASSIC_TEST_ROOT):
        for source_file in root.rglob("*.py"):
            imported = _imported_modules(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            forbidden = _forbidden_imports(imported, FORBIDDEN_BACKEND_TABLE_MODULES)
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)}: {module}"
                for module in sorted(forbidden)
            )

    assert not violations, "Backend table modules leaked into Classic:\n" + "\n".join(
        violations
    )


def test_classic_source_and_tests_do_not_access_backend_gpu_cache() -> None:
    violations: list[str] = []

    for root in (CLASSIC_SOURCE_ROOT, CLASSIC_TEST_ROOT):
        for source_file in root.rglob("*.py"):
            forbidden = _backend_gpu_cache_accesses(
                source_file.read_text(encoding="utf-8"), filename=str(source_file)
            )
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)}: {access}"
                for access in sorted(forbidden)
            )

    assert not violations, "Backend GPU cache leaked into Classic:\n" + "\n".join(
        violations
    )
