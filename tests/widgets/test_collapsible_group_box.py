"""CollapsibleGroupBox 组件测试"""

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from vibeocr.classic.widgets.collapsible_group_box import CollapsibleGroupBox


@pytest.fixture
def app(qtbot):
    return QApplication.instance() or QApplication([])


@pytest.fixture
def group(app, qtbot):
    g = CollapsibleGroupBox("标题")
    qtbot.addWidget(g)
    g.contentLayout().addWidget(QLabel("内容A"))
    g.contentLayout().addWidget(QLabel("内容B"))
    g.show()
    qtbot.waitExposed(g)
    return g


class TestCollapsibleGroupBox:
    def test_initial_expanded(self, group):
        """初始为展开状态。"""
        assert group.is_collapsed() is False

    def test_set_collapsed_true_hides_content(self, group):
        """折叠后内容容器不可见。"""
        group.set_collapsed(True)
        assert group.is_collapsed() is True
        # 内容容器（_content）应不可见
        assert group._content.isVisible() is False

    def test_set_collapsed_false_shows_content(self, group):
        """展开后内容容器可见。"""
        group.set_collapsed(True)
        group.set_collapsed(False)
        assert group._content.isVisible() is True

    def test_collapsed_height_less_than_expanded(self, group):
        """折叠后整体高度小于展开时。"""
        expanded_h = group.sizeHint().height()
        group.set_collapsed(True)
        collapsed_h = group.sizeHint().height()
        assert collapsed_h < expanded_h

    def test_collapsed_changed_signal_emitted(self, group, qtbot):
        """切换折叠状态时发出 collapsed_changed 信号。"""
        with qtbot.waitSignal(group.collapsed_changed, timeout=1000) as blocker:
            group.set_collapsed(True)
        assert blocker.args == [True]

    def test_no_signal_when_no_change(self, group, qtbot):
        """状态未改变时不发出信号。"""
        emitted = []
        group.collapsed_changed.connect(lambda v: emitted.append(v))
        group.set_collapsed(False)  # 已是展开，不应发信号
        assert emitted == []

    def test_title_displays_indicator(self, group):
        """标题前缀折叠指示符：展开 ▼、折叠 ▶。"""
        assert "▼" in group.title()
        group.set_collapsed(True)
        assert "▶" in group.title()

    def test_set_title_preserves_pure_title(self, group):
        """setTitle 设置纯净标题，指示符由内部叠加。"""
        group.setTitle("新标题")
        assert "新标题" in group.title()
        group.set_collapsed(True)
        # 折叠后标题仍是「新标题」，只换了指示符
        assert "新标题" in group.title()
        assert "▶" in group.title()

    def test_click_title_area_toggles(self, group, qtbot):
        """点击标题区域切换折叠（标题区在顶部 margin-top 一带）。"""
        # PySide6 把 QMouseEvent 所有构造式标为 deprecated（推荐走事件发送），
        # 此处仅做同步命中测试，过滤该构造器告警以保持输出干净。
        import warnings

        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        assert group.is_collapsed() is False
        # 标题区大致在顶部 4px 处（QGroupBox margin-top 默认）
        pos = QPointF(group.width() / 2, 4)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ev = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            group.mousePressEvent(ev)
        assert group.is_collapsed() is True
