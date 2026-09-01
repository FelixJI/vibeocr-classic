"""桌面边缘工具栏

可拖拽到屏幕边缘并自动隐藏的浮动工具栏。
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.ui.toolbar_icons import toolbar_icon

logger = logging.getLogger(__name__)

# 靠边检测阈值（像素）
_EDGE_THRESHOLD = 20
# 隐藏后默认露出的像素宽度（可经 set_peek_pixels 配置）
_VISIBLE_STRIP = 3
# 露出像素的合法范围
_PEEK_MIN = 1
_PEEK_MAX = 20
# 动画持续时间（毫秒）
_ANIM_DURATION = 50


class EdgeSide(Enum):
    """工具栏停靠的屏幕边"""

    NONE = auto()
    TOP = auto()
    LEFT = auto()
    RIGHT = auto()


# 拖拽阈值（像素），超过此距离视为拖拽而非点击
_DRAG_THRESHOLD = 5


class _ButtonDragFilter(QObject):
    """事件过滤器：在按钮上按下并拖拽时移动工具栏，短按仍触发按钮点击"""

    def __init__(self, toolbar: EdgeToolbar) -> None:
        super().__init__(toolbar)
        self._toolbar = toolbar
        self._press_pos: QPoint | None = None
        self._is_dragging = False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event if isinstance(event, QMouseEvent) else None
            if me and me.button() == Qt.MouseButton.LeftButton:
                self._press_pos = me.globalPosition().toPoint()
                self._is_dragging = False
        elif event.type() == QEvent.Type.MouseMove:
            me = event if isinstance(event, QMouseEvent) else None
            if (
                self._press_pos is not None
                and me
                and me.buttons() & Qt.MouseButton.LeftButton
            ):
                if not self._is_dragging:
                    delta = me.globalPosition().toPoint() - self._press_pos
                    if delta.manhattanLength() > _DRAG_THRESHOLD:
                        self._is_dragging = True
                        self._toolbar._drag_pos = self._press_pos - self._toolbar.pos()
                        self._toolbar._dragging = True
                        self._toolbar.setCursor(Qt.CursorShape.ClosedHandCursor)
                if self._is_dragging and self._toolbar._drag_pos is not None:
                    self._toolbar.move(
                        me.globalPosition().toPoint() - self._toolbar._drag_pos
                    )
                    return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if self._is_dragging:
                self._is_dragging = False
                self._press_pos = None
                self._toolbar._dragging = False
                self._toolbar.setCursor(Qt.CursorShape.OpenHandCursor)
                self._toolbar._detect_edge()
                self._toolbar.position_changed.emit(self._toolbar.pos())
                return True
            self._press_pos = None
        return False


class EdgeToolbar(QWidget):
    """桌面边缘工具栏

    特性：
      - 无边框、置顶浮动窗口
      - 可拖拽移动
      - 靠近屏幕边缘自动隐藏（可配置延迟）
      - 鼠标移到边缘时平滑显示

    Signals:
        screenshot_requested: 截图按钮点击
        show_main_requested: 显示主窗口按钮点击
    """

    screenshot_requested = Signal()
    show_main_requested = Signal()
    position_changed = Signal(QPoint)
    pipeline_screenshot_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._dragging = False
        self._docked_side: EdgeSide = EdgeSide.NONE
        self._is_hidden = False
        self._auto_hide_enabled = False
        self._hide_delay_ms = 500
        self._peek_pixels = _VISIBLE_STRIP
        self._recognition_catalog = None
        self._mode_buttons: dict[str, QPushButton] = {}

        # 隐藏延迟定时器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._slide_hide)

        # 鼠标检测定时器（检测鼠标是否离开）；50ms 轮询压缩隐藏态下
        # 鼠标靠近边缘到触发展开的等待窗口
        self._mouse_check_timer = QTimer(self)
        self._mouse_check_timer.setInterval(50)
        self._mouse_check_timer.timeout.connect(self._check_mouse_position)

        # 动画
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(_ANIM_DURATION)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """初始化工具栏 UI"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # 透明背景以支持圆角；浅色实体背景改由 paintEvent 绘制
        # （WA_TranslucentBackground 下，QSS 的 background-color 在 Windows 上不可靠）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                padding: 5px 6px;
            }}
            QPushButton:hover {{
                background-color: {theme.Colors.hover_bg};
                border-radius: {theme.Radius.sm}px;
            }}
            QPushButton#gripBtn:hover {{
                background-color: transparent;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 4, 2)
        layout.setSpacing(0)

        # 拖拽把手
        btn_grip = QPushButton()
        btn_grip.setObjectName("gripBtn")
        btn_grip.setIcon(toolbar_icon("grip", size=16, color=theme.Colors.text_subtle))
        btn_grip.setIconSize(QSize(16, 16))
        btn_grip.setToolTip("拖拽移动")
        btn_grip.setCursor(Qt.CursorShape.OpenHandCursor)
        layout.addWidget(btn_grip)

        # 分隔线
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            f"color: {theme.Colors.border}; max-width: 1px; margin: 4px 4px;"
        )
        layout.addWidget(sep)

        # 截图按钮
        btn_screenshot = QPushButton()
        btn_screenshot.setIcon(
            toolbar_icon("scissors", size=16, color=theme.Colors.text)
        )
        btn_screenshot.setIconSize(QSize(16, 16))
        btn_screenshot.setToolTip("截图识别 (Ctrl+S)")
        btn_screenshot.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_screenshot.clicked.connect(self.screenshot_requested.emit)
        layout.addWidget(btn_screenshot)

        # 分隔线
        sep2 = QFrame(self)
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(
            f"color: {theme.Colors.border}; max-width: 1px; margin: 4px 4px;"
        )
        layout.addWidget(sep2)

        # 文本识别快捷按钮
        btn_text = QPushButton()
        btn_text.setIcon(toolbar_icon("text", size=16, color=theme.Colors.text))
        btn_text.setIconSize(QSize(16, 16))
        btn_text.setToolTip("文本识别截图")
        btn_text.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_text.clicked.connect(
            lambda: self.pipeline_screenshot_requested.emit("rapid_text")
        )
        self._mode_buttons["rapid_text"] = btn_text
        layout.addWidget(btn_text)

        # 表格识别快捷按钮
        btn_table = QPushButton()
        btn_table.setIcon(toolbar_icon("table", size=16, color=theme.Colors.text))
        btn_table.setIconSize(QSize(16, 16))
        btn_table.setToolTip("表格识别截图")
        btn_table.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_table.clicked.connect(
            lambda: self.pipeline_screenshot_requested.emit("paddle_table")
        )
        self._mode_buttons["paddle_table"] = btn_table
        layout.addWidget(btn_table)

        # 公式识别快捷按钮
        btn_formula = QPushButton()
        btn_formula.setIcon(toolbar_icon("sigma", size=16, color=theme.Colors.text))
        btn_formula.setIconSize(QSize(16, 16))
        btn_formula.setToolTip("公式识别截图")
        btn_formula.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_formula.clicked.connect(
            lambda: self.pipeline_screenshot_requested.emit("paddle_formula")
        )
        self._mode_buttons["paddle_formula"] = btn_formula
        layout.addWidget(btn_formula)

        # 显示主窗口按钮
        btn_main = QPushButton()
        btn_main.setIcon(toolbar_icon("home", size=16, color=theme.Colors.text))
        btn_main.setIconSize(QSize(16, 16))
        btn_main.setToolTip("显示主窗口")
        btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_main.clicked.connect(self.show_main_requested.emit)
        layout.addWidget(btn_main)

        # 仅在把手按钮上安装拖拽过滤器
        self._btn_drag_filter = _ButtonDragFilter(self)
        btn_grip.installEventFilter(self._btn_drag_filter)

        # 工具栏背景可拖拽（OpenHand），按钮已单独设置 PointingHand
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.setFixedHeight(36)
        self.setMinimumWidth(220)
        self.adjustSize()

    def set_recognition_catalog(self, catalog) -> None:
        """Gate semantic screenshot shortcuts by the negotiated mode catalog."""

        self._recognition_catalog = catalog
        has_catalog = bool(catalog is not None and catalog.has_recognition_mode_catalog)
        for mode_id, button in self._mode_buttons.items():
            mode = catalog.mode(mode_id) if has_catalog else None
            if not has_catalog:
                button.setEnabled(True)
                continue
            available = mode is not None and mode.availability != "unavailable"
            button.setEnabled(available)
            if mode is None or mode.availability == "unavailable":
                reason = mode.reason_code if mode is not None else "未声明"
                button.setToolTip(f"当前 Runtime 不支持此识别模式（{reason}）")
            elif mode.availability == "preparation_required":
                button.setToolTip("需先准备对应组件，点击后开始准备")

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制浅色圆角背景 + 边框。

        顶层透明窗口下 QSS 背景绘制不可靠，这里手动绘制实体背景，
        确保工具栏在任意桌面壁纸上都清晰可读。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.Colors.surface))
        painter.drawRoundedRect(self.rect(), theme.Radius.md, theme.Radius.md)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.setPen(QPen(QColor(theme.Colors.border), 1))
        painter.drawRoundedRect(self.rect(), theme.Radius.md, theme.Radius.md)

    # ============================================================
    # 公共接口
    # ============================================================

    def set_auto_hide(self, enabled: bool) -> None:
        """启用/禁用自动隐藏"""
        self._auto_hide_enabled = enabled
        if not enabled:
            self._hide_timer.stop()
            self._mouse_check_timer.stop()
            if self._is_hidden:
                self._slide_show()
        else:
            # 如果当前已靠边，启动隐藏检测
            if self._docked_side != EdgeSide.NONE:
                self._start_hide_countdown()

    def set_hide_delay(self, delay_ms: int) -> None:
        """设置隐藏延迟（毫秒）"""
        self._hide_delay_ms = max(100, min(5000, delay_ms))

    def set_peek_pixels(self, pixels: int) -> None:
        """设置自动隐藏后仍露出屏幕边缘的像素宽度。

        隐藏状态下调用会立即把工具栏移动到新的露出位置，使设置即时生效。
        """
        peek = max(_PEEK_MIN, min(_PEEK_MAX, pixels))
        if peek == self._peek_pixels:
            return
        self._peek_pixels = peek
        if not self._is_hidden:
            return
        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        self._anim.stop()
        self.setGeometry(self._hidden_geometry(screen.availableGeometry()))

    def set_initial_position(self) -> None:
        """将工具栏定位到主屏幕顶部居中"""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        x = screen_geo.center().x() - self.width() // 2
        self.move(x, screen_geo.top())

    # ============================================================
    # 拖拽逻辑
    # ============================================================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._is_hidden:
                self._slide_show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                self._detect_edge()
                self.position_changed.emit(self.pos())
        super().mouseReleaseEvent(event)

    # ============================================================
    # 边缘检测与自动隐藏
    # ============================================================

    def _detect_edge(self) -> None:
        """检测工具栏是否靠近屏幕边缘"""
        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        pos = self.pos()
        geo = self.geometry()

        if pos.y() - screen_geo.top() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.TOP
            # 吸附到顶部边缘
            self.move(pos.x(), screen_geo.top())
        elif pos.x() - screen_geo.left() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.LEFT
            self.move(screen_geo.left(), pos.y())
        elif screen_geo.right() - geo.right() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.RIGHT
            self.move(screen_geo.right() - self.width(), pos.y())
        else:
            self._docked_side = EdgeSide.NONE

        if self._docked_side != EdgeSide.NONE:
            logger.debug(f"工具栏停靠于 {self._docked_side.name}")
            if self._auto_hide_enabled:
                self._start_hide_countdown()
        else:
            self._hide_timer.stop()
            self._mouse_check_timer.stop()

    def _start_hide_countdown(self) -> None:
        """启动隐藏倒计时"""
        self._hide_timer.start(self._hide_delay_ms)

    def _hidden_geometry(self, screen_geo: QRect) -> QRect:
        """计算当前停靠边下仅露出 _peek_pixels 像素的隐藏目标几何"""
        geo = self.geometry()
        target = QRect(geo)
        if self._docked_side == EdgeSide.TOP:
            target.moveTop(screen_geo.top() - geo.height() + self._peek_pixels)
        elif self._docked_side == EdgeSide.LEFT:
            target.moveLeft(screen_geo.left() - geo.width() + self._peek_pixels)
        elif self._docked_side == EdgeSide.RIGHT:
            target.moveLeft(screen_geo.right() - self._peek_pixels)
        return target

    def _slide_hide(self) -> None:
        """将工具栏滑出屏幕边缘，仅露出几个像素"""
        if self._is_hidden or self._docked_side == EdgeSide.NONE:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        geo = self.geometry()

        target = self._hidden_geometry(screen_geo)

        self._anim.stop()
        self._anim.setStartValue(geo)
        self._anim.setEndValue(target)
        self._anim.start()
        self._is_hidden = True

        # 启动鼠标位置检查
        self._mouse_check_timer.start()
        logger.debug("工具栏已隐藏")

    def _slide_show(self) -> None:
        """将工具栏从屏幕边缘滑入恢复显示"""
        if not self._is_hidden or self._docked_side == EdgeSide.NONE:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        geo = self.geometry()

        target = QRect(geo)
        if self._docked_side == EdgeSide.TOP:
            target.moveTop(screen_geo.top())
        elif self._docked_side == EdgeSide.LEFT:
            target.moveLeft(screen_geo.left())
        elif self._docked_side == EdgeSide.RIGHT:
            target.moveLeft(screen_geo.right() - self.width())

        self._anim.stop()
        self._anim.setStartValue(geo)
        self._anim.setEndValue(target)
        self._anim.start()
        self._is_hidden = False
        # 保持全局鼠标检测：检测区比窗口本身更大，鼠标可能只进入检测区便触发
        # 展开，却从未真正进入 QWidget，因而不会收到 leaveEvent。此时若停止
        # 检测，工具栏将永久停留在屏幕边缘。
        if self._auto_hide_enabled:
            self._mouse_check_timer.start()
        else:
            self._mouse_check_timer.stop()
        logger.debug("工具栏已显示")

    def _check_mouse_position(self) -> None:
        """定期检查鼠标位置，在鼠标靠近边缘时显示工具栏"""
        cursor_pos = QCursor.pos()
        geo = self.geometry()

        # 扩大检测区域（包含工具栏完全展开后的区域 + 一些余量）
        detect_rect = geo.adjusted(-10, -10, 10, 10)

        if self._is_hidden:
            # 隐藏状态：检测鼠标是否在工具栏附近（边缘检测区）
            if detect_rect.contains(cursor_pos):
                self._slide_show()
        else:
            # 显示状态：如果鼠标离开，重新启动隐藏倒计时
            expanded_rect = geo.adjusted(-30, -30, 30, 30)
            if expanded_rect.contains(cursor_pos):
                self._hide_timer.stop()
            elif self._auto_hide_enabled and self._docked_side != EdgeSide.NONE:
                # 轮询频率高于隐藏延迟时，反复 start 会不断推迟单次计时器，
                # 导致永远无法触发隐藏。
                if not self._hide_timer.isActive():
                    self._start_hide_countdown()

    def enterEvent(self, event) -> None:
        """鼠标进入时显示工具栏"""
        if self._is_hidden:
            self._slide_show()
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开时启动隐藏倒计时"""
        if self._auto_hide_enabled and self._docked_side != EdgeSide.NONE:
            self._start_hide_countdown()
        super().leaveEvent(event)
