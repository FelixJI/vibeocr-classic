"""可折叠 QGroupBox。

内容容器 setVisible 实现可靠的高度收缩（折叠时整体 sizeHint 只剩标题区）。
仍是 QGroupBox 子类，自动继承全局样式表（边框/标题）。标题前缀 ▼/▶ 指示符。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget


class CollapsibleGroupBox(QGroupBox):
    """可折叠的 QGroupBox。

    子类应把内容控件加到 ``self.contentLayout()`` 而非直接加到 self 的布局。
    折叠时隐藏内容容器，整体高度收缩到仅剩标题区。
    """

    collapsed_changed = Signal(bool)

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        # 暂存纯净标题，构造时先不传给父类（由 _refresh_title 叠加指示符）。
        super().__init__("", parent)
        self._pure_title: str = title
        self._collapsed: bool = False

        # 外层布局只放一个内容容器
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 8, 8)
        self._content_layout.setSpacing(6)
        outer.addWidget(self._content)

        self._refresh_title()

    # ── 公开 API ──

    def contentLayout(self) -> QVBoxLayout:
        """返回内容容器上的布局，子类把控件加到这里。"""
        return self._content_layout

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠/展开内容。状态不变时不发信号。"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._content.setVisible(not collapsed)
        self._refresh_title()
        self.collapsed_changed.emit(collapsed)
        self.updateGeometry()

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ── QGroupBox 兼容 ──

    def setTitle(self, title: str) -> None:
        self._pure_title = title
        self._refresh_title()

    # ── 折叠交互 ──

    def mousePressEvent(self, event) -> None:
        """点击标题区切换折叠。标题区约在顶部 margin-top 一带。"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 标题区：顶部到内容容器起点之间。margin-top 通常 ~12px。
            if event.position().y() <= 12:
                self.set_collapsed(not self._collapsed)
                event.accept()
                return
        super().mousePressEvent(event)

    # ── 内部 ──

    def _refresh_title(self) -> None:
        indicator = "▶ " if self._collapsed else "▼ "
        super().setTitle(f"{indicator}{self._pure_title}")
