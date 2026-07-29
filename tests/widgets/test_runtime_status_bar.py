from vibeocr.classic.widgets.runtime_status_bar import RuntimeStatusBar


def test_defaults_and_blank_values_are_normalized(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    assert bar.serviceMessage() == "环境检测中"
    assert bar.residencyMessage() == "未确认"
    assert bar.currentMessage() == "空闲"
    assert bar.resultMessage() == "—"

    bar.set_service("  Supervisor\n已连接  ")
    bar.set_residency(" \n ")
    bar.set_task("")
    bar.set_result(" 2 个文本框\t· 18 ms ")

    assert bar.serviceMessage() == "Supervisor 已连接"
    assert bar.residencyMessage() == "未确认"
    assert bar.currentMessage() == "空闲"
    assert bar.resultMessage() == "2 个文本框 · 18 ms"


def test_channels_are_independent(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    bar.set_service("Supervisor 已连接")
    bar.set_residency("文本、表格 2/2")
    bar.showMessage("正在识别第 3 页")
    bar.set_result("识别到 8 个文本框 · 低置信 1 个 · 320 ms")

    assert bar.serviceMessage() == "Supervisor 已连接"
    assert bar.residencyMessage() == "文本、表格 2/2"
    assert bar.currentMessage() == "正在识别第 3 页"
    assert bar.resultMessage() == "识别到 8 个文本框 · 低置信 1 个 · 320 ms"


def test_timed_task_does_not_clear_a_newer_task(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    bar.showMessage("短提示", 10)
    bar.showMessage("仍在识别")
    qtbot.wait(20)

    assert bar.currentMessage() == "仍在识别"


def test_timed_task_returns_to_idle(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    bar.showMessage("短提示", 10)
    qtbot.waitUntil(lambda: bar.currentMessage() == "空闲", timeout=500)


def test_clear_message_only_resets_task(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)
    bar.set_result("上一任务成功")
    bar.showMessage("正在处理")

    bar.clearMessage()

    assert bar.currentMessage() == "空闲"
    assert bar.resultMessage() == "上一任务成功"


def test_finish_task_keeps_result_and_returns_task_to_idle(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    bar.showMessage("正在删除文字层")
    bar.finish_task("文字层删除完成")

    assert bar.currentMessage() == "空闲"
    assert bar.resultMessage() == "文字层删除完成"


def test_timed_task_is_cancelled_with_destroyed_status_bar(qtbot):
    bar = RuntimeStatusBar()
    qtbot.addWidget(bar)

    bar.showMessage("即将销毁", 10)
    bar.deleteLater()
    qtbot.wait(20)
