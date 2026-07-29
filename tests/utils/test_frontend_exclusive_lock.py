"""Tests for the cross-product exclusive Mutex (FrontendExclusiveLock).

Phase 1 of DUAL_UI_IMPLEMENTATION_PLAN.md §6.

These tests verify the mutex semantics that make two VibeOCR frontends
(PySide Classic + WinUI Next) mutually exclusive within one login session:
- first acquire succeeds;
- second acquire fails (no orphan WorkerHost should start);
- release allows re-acquire;
- context-manager protocol works.

On non-Windows the lock is a no-op pass-through, so we skip the Windows-only
concurrency assertions there.
"""

from __future__ import annotations

import sys

import pytest

from vibeocr.classic.utils.frontend_exclusive_lock import (
    EXCLUSIVE_MUTEX_NAME,
    FrontendExclusiveLock,
)

_IS_WINDOWS = sys.platform == "win32"
skip_non_windows = pytest.mark.skipif(
    not _IS_WINDOWS, reason="命名 Mutex 仅 Windows 可用"
)


@pytest.fixture()
def unique_mutex_name():
    """每条测试用独立 Mutex 名，避免测试间互相干扰。"""
    import uuid

    return rf"Local\VibeOCR.Test.Exclusive.{uuid.uuid4().hex}"


@skip_non_windows
def test_first_acquire_succeeds(unique_mutex_name: str) -> None:
    lock = FrontendExclusiveLock(name=unique_mutex_name)
    assert lock.try_acquire()
    assert lock.is_acquired
    lock.release()


@skip_non_windows
def test_second_acquire_fails_while_first_held(unique_mutex_name: str) -> None:
    """核心互斥断言：一方持有时，另一方获取失败。"""
    first = FrontendExclusiveLock(name=unique_mutex_name)
    assert first.try_acquire()
    second = FrontendExclusiveLock(name=unique_mutex_name)
    assert not second.try_acquire()
    assert not second.is_acquired
    first.release()


@skip_non_windows
def test_release_allows_reacquire(unique_mutex_name: str) -> None:
    """释放后另一方可获取——模拟前端退出后 Mutex 由 OS 释放。"""
    first = FrontendExclusiveLock(name=unique_mutex_name)
    first.try_acquire()
    first.release()
    second = FrontendExclusiveLock(name=unique_mutex_name)
    assert second.try_acquire()
    second.release()


@skip_non_windows
def test_context_manager_acquires_and_releases(unique_mutex_name: str) -> None:
    with FrontendExclusiveLock(name=unique_mutex_name) as acquired:
        assert acquired is True
    # 退出后已释放，可以再次获取
    other = FrontendExclusiveLock(name=unique_mutex_name)
    assert other.try_acquire()
    other.release()


@skip_non_windows
def test_context_manager_second_instance_not_acquired(unique_mutex_name: str) -> None:
    holder = FrontendExclusiveLock(name=unique_mutex_name)
    holder.try_acquire()
    try:
        with FrontendExclusiveLock(name=unique_mutex_name) as acquired:
            assert acquired is False
    finally:
        holder.release()


@skip_non_windows
def test_double_release_is_idempotent(unique_mutex_name: str) -> None:
    lock = FrontendExclusiveLock(name=unique_mutex_name)
    lock.try_acquire()
    lock.release()
    lock.release()  # must not raise
    assert not lock.is_acquired


@skip_non_windows
def test_try_acquire_after_release_can_reacquire(unique_mutex_name: str) -> None:
    lock = FrontendExclusiveLock(name=unique_mutex_name)
    assert lock.try_acquire()
    lock.release()
    assert lock.try_acquire()
    lock.release()


def test_default_mutex_name_matches_contract() -> None:
    """Mutex 名必须与 C# 端和 ADR 一致，否则互斥失效。"""
    assert EXCLUSIVE_MUTEX_NAME == r"Local\VibeOCR.Frontend.Exclusive.v2"


def test_windows_create_mutex_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibeocr.classic.utils.frontend_exclusive_lock.os_name", lambda: "nt"
    )
    monkeypatch.setattr(
        "vibeocr.classic.utils.frontend_exclusive_lock._create_mutex",
        lambda _name: 0,
    )
    lock = FrontendExclusiveLock()
    assert lock.try_acquire() is False
    assert lock.is_acquired is False


def test_non_windows_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows 环境放行（VibeOCR 仅面向 Windows）。"""
    monkeypatch.setattr(
        "vibeocr.classic.utils.frontend_exclusive_lock.os_name", lambda: "posix"
    )
    lock = FrontendExclusiveLock()
    assert lock.try_acquire()
    assert lock.is_acquired
    lock.release()
