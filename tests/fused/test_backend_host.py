"""进程内后端宿主集成测试：真实启动、health 探测与关闭。"""

from __future__ import annotations

import threading

import httpx
import pytest

from vibeocr.classic.backend_host import (
    InProcessBackendHost,
    inprocess_backend_enabled,
)


def test_inprocess_backend_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)
    monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)
    assert inprocess_backend_enabled() is True


def test_inprocess_backend_disabled_by_escape_hatch(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOCR_SUPERVISOR_SUBPROCESS", "1")
    assert inprocess_backend_enabled() is False


def test_inprocess_backend_disabled_for_t6_smoke(monkeypatch) -> None:
    monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")
    assert inprocess_backend_enabled() is False


def test_host_start_serves_health_and_shuts_down(tmp_path) -> None:
    host = InProcessBackendHost(tmp_path)
    progress_messages: list[str] = []

    handle = host.start(progress=progress_messages.append)

    assert handle.base_url.startswith("http://127.0.0.1:")
    assert handle.session_token
    assert handle.ready.instance_id.startswith("sup-")
    assert handle.ready.capabilities
    assert progress_messages, "启动过程应输出可读的阶段进度"

    response = httpx.get(
        f"{handle.base_url}/v2/health",
        headers={"Authorization": f"Bearer {handle.session_token}"},
        timeout=10.0,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("ready") is True

    handle.shutdown(timeout_s=20.0)

    with pytest.raises(httpx.HTTPError):
        httpx.get(
            f"{handle.base_url}/v2/health",
            headers={"Authorization": f"Bearer {handle.session_token}"},
            timeout=5.0,
        )


def test_host_rejects_second_start(tmp_path) -> None:
    host = InProcessBackendHost(tmp_path)
    handle = host.start()
    try:
        with pytest.raises(RuntimeError):
            host.start()
    finally:
        handle.shutdown()


def test_host_cancel_before_start(tmp_path) -> None:
    host = InProcessBackendHost(tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError):
        host.start(cancel_event=cancel)
