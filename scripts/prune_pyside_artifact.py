"""Remove PySide6 development and unused release payload from a frozen product."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path


DEVELOPMENT_DIRECTORIES = (
    "QtAsyncio",
    "doc",
    "glue",
    "include",
    "lib",
    "metatypes",
    "qml",
    "scripts",
    "support",
    "typesystems",
)

UNUSED_PLUGIN_DIRECTORIES = (
    "plugins/qmltooling",
)

UNUSED_QT_BINARY_PREFIXES = (
    "Qt63DQuick",
    "Qt6Labs",
    "Qt6Quick3D",
    "Qt6QuickControls2",
    "Qt6QuickDialogs2",
)

UNUSED_QT_BINARIES = (
    "Qt63DAnimation.dll",
    "Qt63DCore.dll",
    "Qt63DExtras.dll",
    "Qt63DInput.dll",
    "Qt63DLogic.dll",
    "Qt63DRender.dll",
    "Qt6Charts.dll",
    "Qt6ChartsQml.dll",
    "Qt6DataVisualization.dll",
    "Qt6DataVisualizationQml.dll",
    "Qt6Graphs.dll",
    "Qt6Location.dll",
    "Qt6Multimedia.dll",
    "Qt6MultimediaQuick.dll",
    "Qt6PdfQuick.dll",
    "Qt6PositioningQuick.dll",
    "Qt6Quick3D.dll",
    "Qt6QuickControls2.dll",
    "Qt6QuickControls2Basic.dll",
    "Qt6QuickControls2Fusion.dll",
    "Qt6QuickControls2Imagine.dll",
    "Qt6QuickControls2Material.dll",
    "Qt6QuickControls2Universal.dll",
    "Qt6QuickDialogs2.dll",
    "Qt6QuickEffects.dll",
    "Qt6QuickLayouts.dll",
    "Qt6QuickParticles.dll",
    "Qt6QuickShapes.dll",
    "Qt6QuickTemplates2.dll",
    "Qt6QuickTest.dll",
    "Qt6QuickTimeline.dll",
    "Qt6RemoteObjects.dll",
    "Qt6Scxml.dll",
    "Qt6Sensors.dll",
    "Qt6SensorsQuick.dll",
    "Qt6SerialPort.dll",
    "Qt6ShaderTools.dll",
    "Qt6SpatialAudio.dll",
    "Qt6Test.dll",
    "Qt6TextToSpeech.dll",
    "Qt6VirtualKeyboard.dll",
    "Qt6WebSockets.dll",
    "Qt6WebView.dll",
)

KEPT_TRANSLATIONS = {
    "qt_zh_CN.qm",
    "qtbase_zh_CN.qm",
    "qtwebengine_locales/en-US.pak",
    "qtwebengine_locales/zh-CN.pak",
}


@dataclass(frozen=True, slots=True)
class PruneResult:
    files_removed: int
    bytes_removed: int


def _tree_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def prune_pyside_artifact(product_root: Path) -> PruneResult:
    pyside = product_root.resolve(strict=True) / "_internal" / "PySide6"
    if not pyside.is_dir():
        raise ValueError(f"frozen PySide6 directory is missing: {pyside}")

    files_removed = 0
    bytes_removed = 0

    def remove_file(path: Path) -> None:
        nonlocal files_removed, bytes_removed
        if not path.is_file():
            return
        bytes_removed += path.stat().st_size
        path.unlink()
        files_removed += 1

    for name in DEVELOPMENT_DIRECTORIES:
        directory = pyside / name
        if not directory.is_dir():
            continue
        file_count, byte_count = _tree_stats(directory)
        shutil.rmtree(directory)
        files_removed += file_count
        bytes_removed += byte_count

    for name in UNUSED_PLUGIN_DIRECTORIES:
        directory = pyside / name
        if not directory.is_dir():
            continue
        file_count, byte_count = _tree_stats(directory)
        shutil.rmtree(directory)
        files_removed += file_count
        bytes_removed += byte_count

    for path in tuple(pyside.glob("*.dll")):
        if path.name.startswith(UNUSED_QT_BINARY_PREFIXES):
            remove_file(path)

    for name in UNUSED_QT_BINARIES:
        remove_file(pyside / name)

    resources = pyside / "resources"
    if resources.is_dir():
        for path in tuple(resources.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(
                "qtwebengine_devtools_resources"
            ) or path.name.endswith((".debug.bin", ".debug.pak")):
                remove_file(path)

    translations = pyside / "translations"
    if translations.is_dir():
        for path in tuple(translations.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(translations).as_posix()
            if relative not in KEPT_TRANSLATIONS:
                remove_file(path)
        for directory in sorted(
            (path for path in translations.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()

    return PruneResult(files_removed=files_removed, bytes_removed=bytes_removed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", type=Path, required=True)
    args = parser.parse_args()
    result = prune_pyside_artifact(args.product_root)
    print(
        "Pruned PySide6 release payload: "
        f"{result.files_removed} files, {result.bytes_removed / (1024 * 1024):.2f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
