"""Compatibility import for the relocated PySide PDF session manager."""

from vibeocr.classic.pyside.pdf_session_manager import PdfSessionManager, _wait_thread

__all__ = ["PdfSessionManager", "_wait_thread"]
