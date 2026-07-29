"""PreviewWidget 与 QrcodeTab 连续 resize 合并测试。"""

from __future__ import annotations

from importlib import import_module

from PIL import Image
from PySide6.QtGui import QPixmap


def test_preview_resize_events_coalesce_to_one_display_update(qapp, qtbot, monkeypatch):
    from vibeocr.classic.widgets.preview_widget import PreviewWidget

    widget = PreviewWidget()
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    widget.show()
    pixmap = QPixmap(100, 80)
    pixmap.fill()
    widget.set_pixmap(pixmap)
    calls: list[bool] = []
    original = widget._update_display

    def counted_update():
        calls.append(True)
        original()

    monkeypatch.setattr(widget, "_update_display", counted_update)
    for index in range(40):
        widget.resize(500 + index, 350 + index)
    qtbot.waitUntil(lambda: not widget._resize_timer.isActive(), timeout=1000)
    assert calls == [True]


class _QrBackend:
    pass


def test_qr_resize_events_coalesce_to_one_smooth_scale(qapp, qtbot, monkeypatch):
    module = import_module("vibeocr.classic.views.tabs.qrcode_tab")

    tab = module.QrcodeTab(backend=_QrBackend())
    qtbot.addWidget(tab)
    tab.resize(600, 400)
    tab.show()
    tab._current_image = Image.new("RGB", (100, 100), "black")
    tab._gen_preview_pixmap = module._pil_to_qpixmap(tab._current_image)
    calls: list[bool] = []
    original = module._scale_pixmap_for_label

    def counted_scale(pixmap, label):
        calls.append(True)
        return original(pixmap, label)

    monkeypatch.setattr(module, "_scale_pixmap_for_label", counted_scale)
    for index in range(40):
        tab.resize(700 + index, 450 + index)
    qtbot.waitUntil(lambda: not tab._preview_scale_timer.isActive(), timeout=1000)
    assert calls == [True]
