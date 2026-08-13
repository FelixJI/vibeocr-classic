"""UI-free public update interface used by every Classic caller."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class UpdateCheckStatus(str, Enum):
    LATEST = "latest"
    AVAILABLE = "available"
    NOT_INSTALLED = "not-installed"
    FETCH_FAILED = "fetch-failed"


class UpdateApplyStatus(str, Enum):
    DOWNLOADED = "downloaded"
    APPLY_STARTED = "apply-started"
    CANCELLED = "cancelled"
    FAILED = "failed"


class UpdateApplyMode(str, Enum):
    VELOPACK = "velopack"
    SETUP_BRIDGE = "setup-bridge"


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    status: UpdateCheckStatus
    current_version: str | None = None
    version: str | None = None
    release_notes: str = ""
    detail: str | None = None
    apply_mode: UpdateApplyMode = UpdateApplyMode.VELOPACK


@dataclass(frozen=True, slots=True)
class UpdateApplyResult:
    status: UpdateApplyStatus
    detail: str | None = None


class UpdateCoordinator(Protocol):
    async def check(self) -> UpdateCheckResult: ...

    async def download_and_apply(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> UpdateApplyResult: ...


__all__ = [
    "UpdateApplyResult",
    "UpdateApplyMode",
    "UpdateApplyStatus",
    "UpdateCheckResult",
    "UpdateCheckStatus",
    "UpdateCoordinator",
]
