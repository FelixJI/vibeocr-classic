"""VibeOCR Classic desktop application."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vibeocr-classic")
except PackageNotFoundError:
    __version__ = "0.8.0"  # x-release-please-version

__all__ = ["__version__"]
