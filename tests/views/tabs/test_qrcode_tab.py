# -*- coding: utf-8 -*-
"""二维码 tab 与 Backend QrcodeService 的 options/wire 契约回归测试。

回归背景：前端曾把 QR 请求的 options["format"] 写成 "qrcode"（条形码则是
"barcode" + barcode_format），而 Backend 契约是 "qr" 或条形码类名；错位导致
QrcodeService 走条形码分支抛 BarcodeNotFoundError，端点兜底成 HTTP 500。
"""

from __future__ import annotations

import base64

import pytest

from vibeocr.classic.views.tabs.qrcode_tab import FORMAT_ITEMS, QrcodeTab


class _AdapterStub:
    def __init__(self, client) -> None:
        self._client = client
        self.is_started = True

    @property
    def inference_sync_client(self):
        return self._client


class _ClientStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_qrcode(self, data, *, fmt="qrcode", options=None):
        self.calls.append({"data": data, "fmt": fmt, "options": dict(options or {})})
        return base64.b64encode(b"png-bytes").decode("ascii")


def _select_format(tab: QrcodeTab, fmt_key: str) -> None:
    index = next(i for i, (_name, key) in enumerate(FORMAT_ITEMS) if key == fmt_key)
    tab._format_combo.setCurrentIndex(index)


def test_build_options_uses_backend_contract_for_qr(qtbot) -> None:
    tab = QrcodeTab()
    qtbot.addWidget(tab)
    _select_format(tab, "qr")
    options = tab._build_options()
    assert options["format"] == "qr"
    assert "barcode_format" not in options


def test_build_options_uses_backend_contract_for_barcode(qtbot) -> None:
    tab = QrcodeTab()
    qtbot.addWidget(tab)
    _select_format(tab, "code128")
    options = tab._build_options()
    assert options["format"] == "code128"
    assert "barcode_format" not in options


def test_generate_via_supervisor_sends_wire_format_qrcode(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = QrcodeTab()
    qtbot.addWidget(tab)
    client = _ClientStub()

    import vibeocr.classic.pyside.supervisor_adapter as adapter_mod

    monkeypatch.setattr(
        adapter_mod, "get_supervisor_adapter", lambda: _AdapterStub(client)
    )
    _select_format(tab, "qr")
    result = tab._generate_via_supervisor("hello", tab._build_options())
    assert result == b"png-bytes"
    assert client.calls[0]["fmt"] == "qrcode"
    assert client.calls[0]["options"]["format"] == "qr"

    _select_format(tab, "code128")
    tab._generate_via_supervisor("hello", tab._build_options())
    assert client.calls[1]["fmt"] == "qrcode"
    assert client.calls[1]["options"]["format"] == "code128"


def test_qr_capability_blocks_preview_when_capability_missing(qtbot) -> None:
    """health 明确缺 qrcode.v2 时不发起生成，直接给出修复指引。"""
    tab = QrcodeTab()
    qtbot.addWidget(tab)
    tab._on_health_loaded({"capabilities": []})
    assert tab._qr_capability_state is False
    assert tab._qr_capability_blocked() is True

    tab._on_health_loaded({"capabilities": ["qrcode.v2"]})
    assert tab._qr_capability_state is True
    assert tab._qr_capability_blocked() is False


def test_qr_capability_probe_skipped_for_injected_backend(qtbot) -> None:
    class _Backend:
        pass

    tab = QrcodeTab(backend=_Backend())
    qtbot.addWidget(tab)
    assert tab._qr_capability_blocked() is False
