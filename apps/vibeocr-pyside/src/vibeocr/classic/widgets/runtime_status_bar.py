"""Compact, structured runtime status bar."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStatusBar,
    QWidget,
)

from vibeocr.classic.ui.theme import Colors


class RuntimeStatusBar(QStatusBar):
    """Four fixed channels: service, residency, current task and last result.

    ``showMessage``/``currentMessage`` remain compatible with ``QStatusBar`` and
    map to the current-task channel, so existing controllers can migrate
    incrementally without overwriting the other three facts.
    """

    _DEFAULT_SERVICE = "环境检测中"
    _DEFAULT_RESIDENCY = "未确认"
    _DEFAULT_TASK = "空闲"
    _DEFAULT_RESULT = "—"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._values = {
            "service": self._DEFAULT_SERVICE,
            "residency": self._DEFAULT_RESIDENCY,
            "task": self._DEFAULT_TASK,
            "result": self._DEFAULT_RESULT,
        }
        self._task_reset_timer = QTimer(self)
        self._task_reset_timer.setSingleShot(True)
        self._task_reset_timer.timeout.connect(self._reset_timed_task)
        self.setSizeGripEnabled(False)

        panel = QWidget(self)
        panel.setObjectName("runtimeStatusPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._service_label = self._add_channel(
            layout, "服务", "runtimeServiceStatus", stretch=2
        )
        self._residency_label = self._add_channel(
            layout, "驻留", "runtimeResidencyStatus", stretch=2
        )
        self._task_label = self._add_channel(
            layout, "任务", "runtimeTaskStatus", stretch=3
        )
        self._result_label = self._add_channel(
            layout, "结果", "runtimeResultStatus", stretch=4
        )
        self.addPermanentWidget(panel, 1)

        self.setStyleSheet(
            f"""
            QStatusBar {{
                background: {Colors.surface_alt};
                border-top: 1px solid {Colors.border};
            }}
            QStatusBar::item {{ border: none; }}
            QLabel[runtimeChannel="true"] {{
                color: {Colors.text_muted};
                padding: 3px 9px;
                border-right: 1px solid {Colors.border};
            }}
            QLabel#runtimeResultStatus {{ border-right: none; }}
            """
        )
        self._refresh_all()

    def _add_channel(
        self,
        layout: QHBoxLayout,
        title: str,
        object_name: str,
        *,
        stretch: int,
    ) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setProperty("runtimeChannel", True)
        label.setProperty("runtimeTitle", title)
        label.setMinimumWidth(90)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(label, stretch)
        return label

    def _refresh_all(self) -> None:
        self._render(self._service_label, "服务", self._values["service"])
        self._render(self._residency_label, "驻留", self._values["residency"])
        self._render(self._task_label, "任务", self._values["task"])
        self._render(self._result_label, "结果", self._values["result"])

    @staticmethod
    def _render(label: QLabel, title: str, value: str) -> None:
        label.setText(f"{title}  {value}")
        label.setToolTip(value)
        label.setAccessibleName(f"{title}：{value}")

    def _set_value(self, channel: str, message: str, fallback: str) -> None:
        value = " ".join(str(message).split()) or fallback
        self._values[channel] = value
        label: QLabel = getattr(self, f"_{channel}_label")
        title = str(label.property("runtimeTitle"))
        self._render(label, title, value)

    def set_service(self, message: str) -> None:
        self._set_value("service", message, self._DEFAULT_SERVICE)

    def set_residency(self, message: str) -> None:
        self._set_value("residency", message, self._DEFAULT_RESIDENCY)

    def set_task(self, message: str) -> None:
        self._task_reset_timer.stop()
        self._set_value("task", message, self._DEFAULT_TASK)

    def set_result(self, message: str) -> None:
        self._set_value("result", message, self._DEFAULT_RESULT)

    def finish_task(self, result: str) -> None:
        self.set_result(result)
        self.set_task(self._DEFAULT_TASK)

    def showMessage(self, message: str, timeout: int = 0) -> None:
        """Compatibility API: publish a current task without hiding other fields."""
        self.set_task(message)
        if timeout > 0:
            self._task_reset_timer.start(timeout)

    def _reset_timed_task(self) -> None:
        self.set_task(self._DEFAULT_TASK)

    def currentMessage(self) -> str:
        return self._values["task"]

    def clearMessage(self) -> None:
        self.set_task(self._DEFAULT_TASK)

    def serviceMessage(self) -> str:
        return self._values["service"]

    def residencyMessage(self) -> str:
        return self._values["residency"]

    def resultMessage(self) -> str:
        return self._values["result"]
