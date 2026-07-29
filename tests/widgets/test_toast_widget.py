"""Toast 组件生命周期测试

回归：toast_widget.show_toast 曾有 NameError（lambda obj=t 的 t 未定义）+
内存泄漏（淡出后只 hide 不 deleteLater，_active_toasts 无限增长）。
"""

import pytest
from PySide6.QtWidgets import QWidget

from vibeocr.classic.widgets.toast_widget import (
    ToastWidget,
    _active_toasts,
    show_toast,
)


@pytest.fixture
def host(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    return w


@pytest.fixture(autouse=True)
def clean_active_toasts():
    """每个测试前后清空模块级 _active_toasts（测试隔离）"""
    _active_toasts.clear()
    yield
    _active_toasts.clear()


def test_show_toast_appends_to_active_list(host, qtbot):
    """show_toast 应创建 toast 并加入 _active_toasts 持有引用"""
    show_toast(host, "保存成功")
    assert len(_active_toasts) == 1
    assert isinstance(_active_toasts[0], ToastWidget)


def test_show_toast_no_longer_raises_name_error(host, qtbot, caplog):
    """回归：旧 show_toast 的 lambda obj=t 引用未定义的 t，触发 NameError。

    修复后 show_toast 不应在创建/显示阶段抛任何异常（含日志里的 [Toast] 显示失败）。
    """
    import logging

    with caplog.at_level(logging.ERROR, logger="vibeocr.classic.views.settings_page_controller"):
        show_toast(host, "测试")
    # 不应有 ERROR 级日志（旧 bug 会触发 _show_settings_toast 的 except 分支）
    assert not any("Toast" in r.message and "失败" in r.message for r in caplog.records)


def test_toast_released_after_fade_out(host, qtbot):
    """淡出完成后 toast 应从 _active_toasts 移除并调度 deleteLater（不泄漏）

    回归：旧 _start_fade_out 只 hide 不 deleteLater，加上 destroyed 信号因
    NameError 从未连上 → toast 永不析构 → _active_toasts 无限增长。
    """
    show_toast(host, "测试")
    toast = _active_toasts[0]

    # 触发淡出（绕过 duration 计时，直接调内部方法）
    toast._start_fade_out()
    # 推进淡出动画（300ms）让 finished 信号发出
    qtbot.wait(400)

    assert len(_active_toasts) == 0, "淡出后应从 _active_toasts 移除"


def test_multiple_toasts_each_cleaned_up(host, qtbot):
    """连续多次 show_toast，每次淡出后都应各自清理，列表不累积"""
    for i in range(3):
        show_toast(host, f"提示 {i}")

    assert len(_active_toasts) == 3
    # 全部触发淡出
    for toast in list(_active_toasts):
        toast._start_fade_out()
    qtbot.wait(400)

    assert len(_active_toasts) == 0, "3 个 toast 都应清理，列表归零"
