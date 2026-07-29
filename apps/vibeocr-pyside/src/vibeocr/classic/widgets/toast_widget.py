"""浮层 Toast 通知组件

提供轻量级的"保存成功"类提示，自动淡入淡出，不阻塞交互。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import QLabel, QWidget

from vibeocr.classic.ui.theme import Colors, Radius, Typography

_TOAST_DURATION = 2000  # ms


class ToastWidget(QLabel):
    """浮层 Toast 通知。

    显示在父控件顶部居中位置，带淡入/淡出动画，自动消失。
    鼠标可穿透，不响应用户交互。

    Usage::

        toast = ToastWidget(self, "保存成功")
        toast.show_at_top()
    """

    # 淡出动画完成时发出。show_toast 据此从 _active_toasts 移除并 deleteLater，
    # 形成完整生命周期（避开 destroyed 信号在对象析构时访问 wrapper 的时序坑）。
    faded_out = Signal()

    def __init__(
        self,
        parent: QWidget,
        text: str,
        duration: int = _TOAST_DURATION,
    ) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.surface};
                color: {Colors.text};
                border: 1px solid {Colors.border};
                padding: 6px 14px;
                border-radius: {Radius.md}px;
                font-size: {Typography.body}px;
                font-weight: {Typography.weight_medium};
            }}
        """)
        # 鼠标可穿透，不抢焦点
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._duration = duration
        self._fade_in: QPropertyAnimation | None = None
        self._fade_out: QPropertyAnimation | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_fade_out)

        self.hide()

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def show_at_top(self, offset_y: int = 50) -> None:
        """在父控件顶部居中显示，并启动自动消失计时器。"""
        parent = self.parent()
        if parent is None:
            return

        self.adjustSize()
        pw = parent.width()
        x = (pw - self.width()) // 2
        self.move(x, offset_y)
        self.raise_()

        self.setWindowOpacity(0.0)
        self.show()

        # 淡入
        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(180)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in.start()

        self._timer.start(self._duration)

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    def _start_fade_out(self) -> None:
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(300)
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        # 淡出完成：先 hide（立即不可见），再发 faded_out 信号让外部清理引用并
        # deleteLater。旧实现只 hide 不 deleteLater → QLabel 对象常驻 +
        # _active_toasts 的 Python 引用 → toast 永不析构 → 列表无限增长（内存泄漏）。
        self._fade_out.finished.connect(self.hide)
        self._fade_out.finished.connect(self.faded_out)
        self._fade_out.start()


# ----------------------------------------------------------------
# Convenience singleton-style helper for one-shot toasts
# ----------------------------------------------------------------

# 持有 toast 引用防止 GC 过早回收（动画未完成即消失）
_active_toasts: list[ToastWidget] = []


def _release_toast(toast: ToastWidget) -> None:
    """从 _active_toasts 移除 toast 并调度其析构。

    在淡出完成时调用——此时对象仍完全有效，可安全访问 wrapper。先从 Python 列表
    移除（解除引用），再 deleteLater 让 Qt 在事件循环空闲时回收 C++ 对象。
    避开 destroyed 信号时序坑：destroyed 触发时 wrapper 已半失效，在那时访问
    列表移除不安全。
    """
    try:
        _active_toasts.remove(toast)
    except ValueError:
        pass
    toast.deleteLater()


def show_toast(parent: QWidget, text: str, duration: int = _TOAST_DURATION) -> None:
    """便捷函数：在 *parent* 的顶部居中弹出 Toast 后自动消失。

    *parent* 通常传 ``self.window()`` 或 ``self``（主窗口），避免被 Tab 裁剪。

    toast 的生命周期：show → 淡入 → 计时到期 → 淡出 → ``_release_toast`` 从
    ``_active_toasts`` 移除并 ``deleteLater`` 回收，形成 GC 闭环。
    ``_active_toasts`` 仅在动画期间持有引用，防止过早回收。
    """
    toast = ToastWidget(parent, text, duration)
    _active_toasts.append(toast)
    # 旧实现 lambda obj=t 的 t 未定义 → NameError → destroyed 清理从未生效，加上
    # 旧 _start_fade_out 只 hide 不 deleteLater，toast 永不析构 → _active_toasts
    # 无限增长。改在淡出完成时同步释放（对象仍有效，安全）。
    toast.faded_out.connect(lambda t=toast: _release_toast(t))
    toast.show_at_top()
