"""Compatibility import for relocated PySide PDF RPC workers."""

from vibeocr.classic.pyside.pdf_ipc_worker import PdfIpcMutateWorker, PdfIpcOpenWorker

__all__ = ["PdfIpcMutateWorker", "PdfIpcOpenWorker"]
