# tests/widgets/test_edge_toolbar.py
"""Tests for EdgeToolbar (桌面边缘隐身悬浮操作栏)."""

from types import SimpleNamespace

from PySide6.QtCore import QPointF, QPoint, QRect, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from vibeocr.classic.widgets import toolbar as toolbar_module
from vibeocr.classic.widgets.toolbar import EdgeSide, EdgeToolbar


def _make_docked_top_toolbar(
    qapp, monkeypatch, *, hidden: bool
) -> tuple[EdgeToolbar, QRect, list]:
    """构造停靠在主屏顶部、自动隐藏已启用的工具栏及其可见几何。

    返回 (工具栏, 可见态几何, 可 monkeypatch 的光标位置容器)。
    """
    tb = EdgeToolbar()
    screen_geo = qapp.primaryScreen().availableGeometry()
    visible_geo = QRect(
        screen_geo.center().x() - tb.width() // 2,
        screen_geo.top(),
        tb.width(),
        tb.height(),
    )
    tb._auto_hide_enabled = True
    tb._docked_side = EdgeSide.TOP
    tb._is_hidden = hidden
    if hidden:
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)
        tb.setGeometry(hidden_geo)
    else:
        tb.setGeometry(visible_geo)

    cursor_pos = [QPoint(visible_geo.center().x(), screen_geo.bottom())]
    monkeypatch.setattr(
        toolbar_module,
        "QCursor",
        SimpleNamespace(pos=lambda: cursor_pos[0]),
    )
    tb.set_hide_delay(150)
    return tb, visible_geo, cursor_pos


class TestEdgeToolbar:
    def test_is_widget(self, qapp):
        tb = EdgeToolbar()
        assert isinstance(tb, QWidget)

    def test_styled_background_enabled(self, qapp):
        """浅色背景依赖 WA_StyledBackground：否则背景透明，样式表 background-color 失效。"""
        tb = EdgeToolbar()
        assert tb.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def test_paints_light_background(self, qapp):
        """浅色实体背景由 paintEvent 绘制（透明顶层窗口下 QSS 背景不可靠）。

        判定方式：渲染到 pixmap，确认画出了非透明像素（即主题 surface 浅色背景）。
        """
        from PySide6.QtGui import QColor, QImage

        from vibeocr.classic.ui import theme

        tb = EdgeToolbar()
        img = QImage(tb.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)  # 全透明基准
        tb.render(img)
        # 取中心点像素，应为不透明的主题浅色背景
        pixel = img.pixelColor(tb.width() // 2, tb.height() // 2)
        assert pixel.alpha() == 255
        assert pixel.red() > 200  # 接近 #ffffff
        assert pixel.green() > 200
        assert pixel.blue() > 200
        # 与 theme surface 一致
        assert pixel.name() == QColor(theme.Colors.surface).name().lower()

    def test_revealed_from_detection_margin_rehides_when_pointer_stays_outside(
        self, qapp, monkeypatch
    ):
        """外扩检测区误触发展开后，鼠标未进窗口也必须再次收回。"""
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=True
        )
        screen_geo = qapp.primaryScreen().availableGeometry()
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)

        cursor_pos[0] = QPoint(hidden_geo.left() - 5, screen_geo.top() + 1)
        tb._mouse_check_timer.start()

        # 鼠标位于隐藏窗口外、但落在额外 10px 检测区内，会触发展开。
        tb._check_mouse_position()
        assert not tb._is_hidden

        # 鼠标从未进入实际窗口，不会产生 leaveEvent；状态机仍须自行收回。
        cursor_pos[0] = QPoint(screen_geo.right(), screen_geo.bottom())
        QTest.qWait(400)

        assert tb._is_hidden
        tb.close()

    def test_rehides_after_enter_swallows_countdown_and_no_leave(
        self, qapp, monkeypatch
    ):
        """启用自动隐藏后仅靠事件驱动会卡在展开态：轮询必须兜底收回。

        场景：设置/启动路径启用自动隐藏（旧实现只武装倒计时不启动轮询），
        Qt 把窗口移到静止指针下方补发 enterEvent 停掉倒计时，而对应的
        leaveEvent 因指针从未真正进入窗口而丢失 —— 旧实现从此永远展开。
        """
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=False
        )
        # 指针静止停在工具栏上（从未触发过真实 enter/leave）
        cursor_pos[0] = visible_geo.center()

        tb.set_auto_hide(True)
        # 模拟 Qt 在窗口 show/move 到静止指针下方时补发的 Enter
        tb.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))

        # 指针离开且此后不再有任何事件
        cursor_pos[0] = QPoint(0, visible_geo.bottom() + 300)
        QTest.qWait(900)

        assert tb._is_hidden
        tb.close()

    def test_reveal_cancels_armed_countdown(self, qapp, monkeypatch):
        """展开瞬间必须取消隐藏期间武装的倒计时，否则刚展开又立即缩回。

        隐藏态下 leaveEvent（指针掠过 3px 露条后离开）会武装倒计时；
        轮询随后因指针进入揭示区而展开 —— 旧实现 _slide_show 不停表，
        残留的倒计时触发后把刚展开的工具栏立刻收回。
        """
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=True
        )
        screen_geo = qapp.primaryScreen().availableGeometry()
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)

        tb._mouse_check_timer.start()
        # 模拟隐藏期间 leaveEvent 武装的在途倒计时
        tb._hide_timer.start(150)

        cursor_pos[0] = QPoint(hidden_geo.center().x(), screen_geo.top() + 2)
        tb._check_mouse_position()
        assert not tb._is_hidden

        QTest.qWait(600)
        # 指针仍停在揭示区（展开后属于保持区），工具栏必须保持展开
        assert not tb._is_hidden
        tb.close()

    def test_hides_when_pointer_parks_in_adjacent_app_area(self, qapp, monkeypatch):
        """指针停在工具栏下方 20px（停靠边应用的标题/标签栏一带）必须收回。

        旧实现可见态保持区外扩 ±30px，把邻近应用的顶部区域划入保持区，
        轮询每 100ms 停一次倒计时，工具栏"出来了不回去"。
        """
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=True
        )
        screen_geo = qapp.primaryScreen().availableGeometry()
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)

        tb._mouse_check_timer.start()
        cursor_pos[0] = QPoint(hidden_geo.center().x(), screen_geo.top() + 2)
        tb._check_mouse_position()
        assert not tb._is_hidden

        # 展开后指针移到工具栏下 20px：在旧 ±30 保持区内、新 ±8 保持区外
        cursor_pos[0] = QPoint(visible_geo.center().x(), visible_geo.bottom() + 20)
        QTest.qWait(900)

        assert tb._is_hidden
        tb.close()

    def test_stays_visible_while_pointer_hovers_toolbar(self, qapp, monkeypatch):
        """指针持续悬停在工具栏上时不收回（保持区行为契约）。"""
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=True
        )
        screen_geo = qapp.primaryScreen().availableGeometry()
        hidden_geo = QRect(visible_geo)
        hidden_geo.moveTop(screen_geo.top() - tb.height() + 3)

        tb._mouse_check_timer.start()
        cursor_pos[0] = QPoint(hidden_geo.center().x(), screen_geo.top() + 2)
        tb._check_mouse_position()
        assert not tb._is_hidden

        cursor_pos[0] = visible_geo.center()
        QTest.qWait(900)

        assert not tb._is_hidden
        tb.close()

    def test_no_auto_hide_mid_drag(self, qapp, monkeypatch):
        """拖拽过程中不得自动收回（倒计时到期也要让位给拖拽）。"""
        tb, visible_geo, cursor_pos = _make_docked_top_toolbar(
            qapp, monkeypatch, hidden=False
        )
        tb._mouse_check_timer.start()
        tb._dragging = True
        # 模拟竞态下已武装的倒计时
        tb._hide_timer.start(120)

        QTest.qWait(600)

        assert not tb._is_hidden
        tb._dragging = False
        tb.close()


class TestPeekPixels:
    """隐藏时露出像素（_peek_pixels）的配置与即时生效。"""

    def test_default_and_clamp(self, qapp):
        tb = EdgeToolbar()
        assert tb._peek_pixels == 3
        tb.set_peek_pixels(0)
        assert tb._peek_pixels == 1
        tb.set_peek_pixels(99)
        assert tb._peek_pixels == 20
        tb.set_peek_pixels(8)
        assert tb._peek_pixels == 8
        tb.close()

    def test_hidden_geometry_uses_peek_pixels(self, qapp):
        tb = EdgeToolbar()
        screen_geo = qapp.primaryScreen().availableGeometry()
        tb.setGeometry(
            QRect(
                screen_geo.center().x() - tb.width() // 2,
                screen_geo.top(),
                tb.width(),
                tb.height(),
            )
        )
        tb._docked_side = EdgeSide.TOP
        tb.set_peek_pixels(8)
        hidden = tb._hidden_geometry(screen_geo)
        assert hidden.top() == screen_geo.top() - tb.height() + 8
        tb.close()

    def test_set_peek_pixels_while_hidden_repositions_immediately(
        self, qapp, monkeypatch
    ):
        """隐藏状态下修改露出像素必须立即移动到新位置，无需等待下一次隐藏。"""
        screen = qapp.primaryScreen()
        screen_geo = screen.availableGeometry()
        monkeypatch.setattr(
            toolbar_module.QApplication,
            "screenAt",
            staticmethod(lambda _geo: screen),
        )
        tb = EdgeToolbar()
        tb._docked_side = EdgeSide.TOP
        tb._is_hidden = True
        tb.setGeometry(
            QRect(
                screen_geo.center().x() - tb.width() // 2,
                screen_geo.top() - tb.height() + 3,
                tb.width(),
                tb.height(),
            )
        )

        tb.set_peek_pixels(10)
        assert tb.geometry().top() == screen_geo.top() - tb.height() + 10
        tb.close()
