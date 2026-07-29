"""Compatibility error types for the legacy→v2 transition.

These were originally defined in ``vibeocr.worker_host.sync_client`` but
moved here so the ``worker_host`` package can be deleted. The error types
are still used in PySide tab legacy fallback catch blocks.
"""


class SyncBackendError(RuntimeError):
    """Raised by the legacy sync backend client on worker communication failure."""
