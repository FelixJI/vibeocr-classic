"""Tests for MainWindow."""

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from shiboken6 import isValid

from vibeocr.classic.views.main_window import MainWindow


@pytest.fixture
def main_window(qapp, qtbot, tmp_path, monkeypatch):
    """提供 MainWindow 实例。"""
    from vibeocr.classic.managers.config_manager import ConfigManager

    ConfigManager.reset_instance()
    ConfigManager.instance(tmp_path)
    # 主窗口单元测试不应建立真实 WorkerHost；后台启动由 manager 专项测试覆盖。
    monkeypatch.setattr(
        "vibeocr.classic.managers.subprocess_manager.SubprocessManager.start_supervisor",
        lambda self: None,
    )
    # GPU 探测线程由 BackendOptionsWidget 专项测试覆盖；MainWindow 单元测试
    # 隔离真实 nvidia-smi 子进程，避免跨用例 COM/clipboard teardown 污染。
    monkeypatch.setattr(
        "vibeocr.classic.widgets.backend_options_widget.BackendOptionsWidget._start_gpu_detection",
        lambda self: None,
    )
    window = MainWindow()
    window.show()
    qtbot.addWidget(window)
    yield window
    # closeEvent 会触发整条应用关闭链（settings/pdf/subprocess...），
    # 与 qtbot 的 widget 回收存在竞态：teardown 时 MainWindow 的 C++ 对象
    # 可能已被 Qt 父对象回收，再调 close() 抛
    # "libshiboken: Internal C++ object already deleted."。
    # isValid 守卫：仅当底层 C++ 对象仍存活时才触发关闭链。
    if isValid(window):
        window._force_quit = True
        if getattr(window, "_shutdown_phase", "idle") == "idle":
            window._begin_shutdown_requests()
            probes = window._collect_shutdown_gui_probes()
            qtbot.waitUntil(
                lambda: all(bool(probe()) for _name, probe in probes), timeout=7000
            )
            window._shutdown_phase = "ready"
        window.close()
    ConfigManager.reset_instance()


class TestMainWindow:
    """测试 MainWindow 集成功能。"""

    def test_window_title(self, main_window):
        """窗口标题正确。"""
        assert main_window.windowTitle() == "VibeOCR"

    def test_close_polls_gui_owners_before_backend_and_widget_cleanup(
        self, main_window, qtbot, monkeypatch
    ):
        """Qt owner 只在 GUI poll，全部终态后才后台关后端并清理视图。"""
        calls: list[str] = []

        class _ResultWidget:
            def __init__(self, name: str) -> None:
                self._name = name

            def cleanup(self) -> None:
                calls.append(f"cleanup:{self._name}")

        window = main_window
        # 构造阶段的真实依赖探测必须先送达 GUI，再替换为顺序记录桩；否则
        # 迟到信号会命中已失去 owner 的 DependencyManager。
        real_dependency_manager = window._dependency_manager
        real_dependency_manager.request_shutdown()
        qtbot.waitUntil(real_dependency_manager.is_drained, timeout=3000)
        window._force_quit = True
        window._app_settings = SimpleNamespace(
            minimize_to_tray=False,
            save=lambda: calls.append("settings:save"),
        )
        window._tray_icon = None
        window._single_tab = SimpleNamespace(
            request_shutdown=lambda: calls.append("single:request"),
            is_drained=lambda: calls.append("single:probe") or True,
            _result_widget=_ResultWidget("single"),
        )
        window._batch_tab = SimpleNamespace(
            request_shutdown=lambda: calls.append("batch:request"),
            is_drained=lambda: calls.append("batch:probe") or True,
            _result_widget=_ResultWidget("batch"),
        )
        window._qrcode_tab = SimpleNamespace(
            request_shutdown=lambda: calls.append("qr:request"),
            is_drained=lambda: calls.append("qr:probe") or True,
        )

        class _Overlay:
            def __init__(self, name: str) -> None:
                self.name = name

            def request_shutdown(self) -> None:
                calls.append(f"overlay:{self.name}:request")

            def is_drained(self) -> bool:
                calls.append(f"overlay:{self.name}:probe")
                return True

            def deleteLater(self) -> None:
                calls.append(f"overlay:{self.name}:delete")

        current_overlay = _Overlay("current")
        retired_overlay = _Overlay("retired")
        window._overlay = current_overlay
        window._retired_overlays = {retired_overlay}
        window._settings_controller = SimpleNamespace(
            request_shutdown=lambda: calls.append("settings:request"),
            is_drained=lambda: calls.append("settings:probe") or True,
        )
        window._pdf_tab = SimpleNamespace(
            request_shutdown=lambda: calls.append("pdf:request"),
            is_drained=lambda: calls.append("pdf:probe") or True,
        )
        window._edge_toolbar = SimpleNamespace(close=lambda: calls.append("edge:close"))
        window._subprocess_manager = SimpleNamespace(
            request_shutdown=lambda: calls.append("subprocess:request"),
            is_drained=lambda: calls.append("subprocess:probe") or True,
            take_shutdown_callable=lambda: lambda: calls.append("subprocess:shutdown"),
        )
        window._dependency_manager = SimpleNamespace(
            request_shutdown=lambda: calls.append("dependency:request"),
            is_drained=lambda: calls.append("dependency:probe") or True,
        )
        monkeypatch.setattr(window, "_save_layout", lambda: calls.append("layout:save"))
        monkeypatch.setattr(
            "vibeocr.classic.client.shutdown_backend_client",
            lambda: calls.append("backend:shutdown"),
        )
        monkeypatch.setattr(
            "vibeocr.classic.utils.qt_async.get_async_runner",
            lambda: SimpleNamespace(active_count=0),
        )

        window.close()
        qtbot.waitUntil(lambda: window._shutdown_phase == "ready", timeout=2000)

        assert window._closing is True
        assert calls.index("single:request") < calls.index("single:probe")
        assert calls.index("settings:request") < calls.index("settings:probe")
        assert calls.index("pdf:request") < calls.index("pdf:probe")
        assert calls.index("batch:request") < calls.index("batch:probe")
        assert calls.index("overlay:current:probe") < calls.index("backend:shutdown")
        assert calls.index("backend:shutdown") < calls.index("cleanup:batch")
        assert calls.index("subprocess:shutdown") < calls.index("cleanup:batch")
        assert calls.index("overlay:current:probe") < calls.index(
            "overlay:current:delete"
        )

    def test_recognition_task_and_result_use_separate_channels(self, main_window):
        main_window._single_tab.task_status_changed.emit("单次识别 · 处理中")
        assert main_window._statusbar.currentMessage() == "单次识别 · 处理中"

        main_window._single_tab.result_status_changed.emit(
            "识别到 3 个文本框 · 低置信（<80%）1 个 · 耗时 280 ms"
        )
        assert main_window._statusbar.currentMessage() == "空闲"
        assert "识别到 3 个文本框" in main_window._statusbar.resultMessage()

    def test_open_image_file_loads_pixmap(self, main_window, qtbot, temp_image_file):
        """直接加载图片文件到预览组件。"""
        from PySide6.QtGui import QPixmap

        # 直接加载图片（绕过文件对话框和 OCR）
        pixmap = QPixmap(str(temp_image_file))
        assert not pixmap.isNull()

        main_window._ui.previewWidget.set_pixmap(pixmap)

        # 验证图片已加载
        assert main_window._ui.previewWidget.pixmap() is not None

    def test_overlay_exists(self, main_window):
        """截图遮罩组件已创建。"""
        assert main_window._overlay is not None


class TestQrcodeTabIntegration:
    def test_main_window_has_qrcode_tab(self, main_window):
        tab_widget = main_window._ui.tabWidget
        tab_names = [tab_widget.tabText(i) for i in range(tab_widget.count())]
        assert "二维码" in tab_names

    def test_qrcode_tab_position_before_settings(self, main_window):
        tab_widget = main_window._ui.tabWidget
        qrcode_idx = None
        settings_idx = None
        for i in range(tab_widget.count()):
            text = tab_widget.tabText(i)
            if text == "二维码":
                qrcode_idx = i
            elif "设置" in text:
                settings_idx = i
        assert qrcode_idx is not None
        assert settings_idx is not None
        assert qrcode_idx < settings_idx


class TestSettingsInstallSucceededTriggersRecheck:
    """设置页重装依赖成功后应联动 MainWindow 重新检测（Bug A 修复）

    回归：用户在设置页重装 OCR 依赖成功后，截图界面仍提示"OCR功能未就绪"。
    根因：设置页重装路径（_open_reinstall_dialog）只连 dialog.finished 刷新
    设置页表格，没连 dialog.install_succeeded，也没触发 dependency_manager
    重新检测 + 启动子进程 Worker。修复：SettingsPageController 接收
    install_succeeded_callback，MainWindow 传入一个触发 check_dependencies
    的回调，使设置页安装成功后与首启路径行为一致。
    """

    def test_settings_controller_receives_install_succeeded_callback(self, main_window):
        """MainWindow 应把 install_succeeded_callback 传给 SettingsPageController"""
        controller = main_window._settings_controller
        assert hasattr(controller, "_install_succeeded_callback"), (
            "SettingsPageController 应持有 install_succeeded_callback"
        )
        assert callable(controller._install_succeeded_callback), (
            "install_succeeded_callback 应是可调用对象"
        )

    def test_callback_triggers_dependency_recheck(self, main_window):
        """install_succeeded_callback 触发时应调用 dependency_manager.check_dependencies

        mock check_dependencies 验证联动，不真正跑后台检测（避免测试耗时长）。
        """
        import unittest.mock as _mock

        dep_mgr = main_window._dependency_manager
        with _mock.patch.object(dep_mgr, "check_dependencies") as mock_check:
            # 调用 callback（模拟设置页 install_succeeded 信号）
            main_window._settings_controller._install_succeeded_callback()
            (
                mock_check.assert_called_once(),
                (
                    "install_succeeded_callback 应触发 dependency_manager.check_dependencies"
                ),
            )


class TestMainWindowShutdown:
    def test_close_shuts_down_settings_controller(self, main_window, monkeypatch):
        called = []
        original_request = getattr(
            main_window._settings_controller, "request_shutdown", None
        )

        def request_shutdown():
            called.append(True)
            if original_request is not None:
                original_request()

        monkeypatch.setattr(
            main_window._settings_controller,
            "request_shutdown",
            request_shutdown,
            raising=False,
        )

        main_window.close()

        assert called == [True]


class TestOverlayWindowRestore:
    """测试截图结束后主窗口状态的恢复逻辑。

    截图开始前主窗口会被最小化（_on_screenshot 调用 showMinimized）。
    截图结束后，不同操作对主窗口的处理应分类：
    - 识别（confirmed）：需立即展示 OCR 结果 → 恢复可见并激活/置顶。
    - 复制/保存/取消：静默操作 → 仅恢复可见性，不抢焦点；若截图前已最小化则保持最小化。
    """

    def test_restore_init_state_default(self, main_window, qtbot):
        """_init_preset_combo 应将截图前窗口状态标记初始化为未最小化。"""
        assert hasattr(main_window, "_main_window_minimized_before_capture"), (
            "MainWindow 应持有 _main_window_minimized_before_capture 属性"
        )
        assert main_window._main_window_minimized_before_capture is False

    def test_confirmed_restores_and_activates(self, main_window, qtbot):
        """识别结束后应恢复窗口可见并激活、置顶。"""
        import unittest.mock as _mock

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
            _mock.patch.object(main_window._single_tab, "run_ocr"),
            _mock.patch.object(main_window._single_tab, "set_image_for_recognition"),
            _mock.patch.object(main_window._single_tab, "set_pixmap"),
        ):
            main_window._main_window_minimized_before_capture = False
            main_window._on_overlay_confirmed(pixmap, None)

        mock_show.assert_called_once()
        mock_activate.assert_called_once()
        mock_raise.assert_called_once()

    def test_copied_restores_without_activating(self, main_window, qtbot):
        """复制为静默操作，应恢复可见但不激活/置顶。"""
        import unittest.mock as _mock

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
        ):
            main_window._main_window_minimized_before_capture = False
            main_window._on_overlay_copied(pixmap)

        mock_show.assert_called_once()
        mock_activate.assert_not_called()
        mock_raise.assert_not_called()

    def test_saved_restores_without_activating(self, main_window, qtbot):
        """保存为静默操作，应恢复可见但不激活/置顶。"""
        import unittest.mock as _mock

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
        ):
            main_window._main_window_minimized_before_capture = False
            main_window._on_overlay_saved("/tmp/fake.png")

        mock_show.assert_called_once()
        mock_activate.assert_not_called()
        mock_raise.assert_not_called()

    def test_cancelled_restores_without_activating(self, main_window, qtbot):
        """取消为静默操作，应恢复可见但不激活/置顶。"""
        import unittest.mock as _mock

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
        ):
            main_window._main_window_minimized_before_capture = False
            main_window._on_overlay_cancelled()

        mock_show.assert_called_once()
        mock_activate.assert_not_called()
        mock_raise.assert_not_called()

    def test_silent_ops_keep_minimized_when_was_minimized(self, main_window, qtbot):
        """截图前已最小化时，复制/保存/取消应保持最小化（不调用 showNormal）。"""
        import unittest.mock as _mock

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)

        main_window._main_window_minimized_before_capture = True

        for slot, arg in [
            (main_window._on_overlay_copied, pixmap),
            (main_window._on_overlay_saved, "/tmp/fake.png"),
            (main_window._on_overlay_cancelled, None),
        ]:
            with (
                _mock.patch.object(main_window, "showNormal") as mock_show,
                _mock.patch.object(main_window, "activateWindow") as mock_activate,
                _mock.patch.object(main_window, "raise_") as mock_raise,
            ):
                if arg is None:
                    slot()
                else:
                    slot(arg)
            mock_show.assert_not_called()
            mock_activate.assert_not_called()
            mock_raise.assert_not_called()


class TestOcrFinishedRaisesWindow:
    """识别完成（截图来源）后应把主窗口提到前台。

    根因：主窗口的激活/置顶此前只在「截图确认瞬间」（_on_overlay_confirmed，
    OCR 开始前）发生一次。OCR 是异步、可能数秒（首次还需下载模型）才完成。
    这期间用户/系统切走窗口后，_on_ocr_finished 不做任何前置动作，窗口就
    静悄悄留在后台——表现为「识别后主界面不弹出」。

    修复：SingleRecognitionTab 在截图来源识别完成时发出 bring_to_front_requested，
    MainWindow 连接该信号，在 OCR 完成时再次 showNormal + activateWindow + raise_。
    文件打开来源（用户本就在应用内）不发信号，避免无谓抢焦点。
    """

    def test_main_window_connects_bring_to_front_signal(self, main_window):
        """MainWindow 应连接 single_tab.bring_to_front_requested 到前置槽。"""
        assert hasattr(main_window, "_bring_main_window_to_front"), (
            "MainWindow 应有 _bring_main_window_to_front 槽"
        )
        assert callable(main_window._bring_main_window_to_front)
        # 信号连接：发出信号应能触发槽（通过 mock 验证回调链通畅）
        import unittest.mock as _mock

        with _mock.patch.object(main_window, "_bring_main_window_to_front") as m:
            main_window._single_tab.bring_to_front_requested.emit()
            m.assert_called_once()

    def test_ocr_finished_raises_window(self, main_window, monkeypatch):
        """single_tab 发出 bring_to_front_requested 时，MainWindow 应前置。"""
        import unittest.mock as _mock

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
        ):
            main_window._single_tab.bring_to_front_requested.emit()

        mock_show.assert_called_once()
        mock_activate.assert_called_once()
        mock_raise.assert_called_once()

    def test_screenshot_confirmed_marks_screenshot_origin(
        self, main_window, monkeypatch
    ):
        """_on_overlay_confirmed 应标记本次识别来自截图（让 tab 在完成时发信号）。

        验证 run_ocr 以 from_screenshot=True 被调用。
        """
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)

        captured: dict = {}
        monkeypatch.setattr(
            main_window._single_tab,
            "run_ocr",
            lambda pm, options=None, **kw: captured.update(
                {"from_screenshot": kw.get("from_screenshot", False)}
            ),
        )
        monkeypatch.setattr(
            main_window._single_tab, "set_image_for_recognition", lambda *a, **k: None
        )
        monkeypatch.setattr(main_window._single_tab, "set_pixmap", lambda *a, **k: None)
        # 抑制 _restore_main_window 真正操作窗口
        monkeypatch.setattr(main_window, "showNormal", lambda: None)
        monkeypatch.setattr(main_window, "activateWindow", lambda: None)
        monkeypatch.setattr(main_window, "raise_", lambda: None)

        main_window._main_window_minimized_before_capture = False
        main_window._on_overlay_confirmed(pixmap, None)

        assert captured.get("from_screenshot") is True

    def test_on_overlay_confirmed_skips_when_busy(self, main_window, monkeypatch):
        """异步化回归：OCR 进行中再次截图确认应被忽略，不触发 run_ocr。

        异步化前 run_ocr 同步阻塞，天然串行；异步化后事件循环在 OCR 期间转动，
        用户可能再次触发截图确认。_on_overlay_confirmed 应检查 is_processing 并
        静默跳过（状态栏提示），避免旧结果覆盖新图。
        """
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)

        run_calls: list = []
        monkeypatch.setattr(
            main_window._single_tab,
            "run_ocr",
            lambda *a, **kw: run_calls.append(kw),
        )
        monkeypatch.setattr(
            main_window._single_tab, "set_image_for_recognition", lambda *a, **k: None
        )
        monkeypatch.setattr(main_window._single_tab, "set_pixmap", lambda *a, **k: None)
        # is_processing 是只读 property，patch 它需走 __dict__
        monkeypatch.setattr(
            type(main_window._single_tab),
            "is_processing",
            property(lambda self: True),
        )
        monkeypatch.setattr(main_window, "showNormal", lambda: None)
        monkeypatch.setattr(main_window, "activateWindow", lambda: None)
        monkeypatch.setattr(main_window, "raise_", lambda: None)

        main_window._main_window_minimized_before_capture = False
        main_window._on_overlay_confirmed(pixmap, None)

        assert run_calls == [], "忙时不应调用 run_ocr"


class TestRestoreMainWindowWhenMinimized:
    """_restore_main_window 在「识别路径 + 截图前已最小化」时仍应恢复可见。

    回归：旧逻辑用 if not self._main_window_minimized_before_capture: showNormal()
    来让复制/保存/取消在「截图前已最小化」时保持最小化（静默操作不抢焦点）。
    但识别（confirmed）路径用户明确想看结果，即便截图前窗口是最小化的，
    也必须把窗口恢复出来——否则工具栏/托盘触发截图后窗口永远不出现。
    """

    def test_confirmed_restores_even_when_was_minimized(self, main_window, monkeypatch):
        """识别路径下，即便截图前已最小化，showNormal 仍应被调用。"""
        import unittest.mock as _mock

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.white)
        monkeypatch.setattr(
            main_window._single_tab, "run_ocr", lambda *args, **kwargs: None
        )

        with (
            _mock.patch.object(main_window, "showNormal") as mock_show,
            _mock.patch.object(main_window, "activateWindow") as mock_activate,
            _mock.patch.object(main_window, "raise_") as mock_raise,
        ):
            main_window._main_window_minimized_before_capture = True
            main_window._on_overlay_confirmed(pixmap, None)

        mock_show.assert_called_once()
        mock_activate.assert_called_once()
        mock_raise.assert_called_once()


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="ScreenCaptureOverlay 在 offscreen/headless 下全量测试易崩溃(需真实屏幕)",
)
class TestFreshOverlayPerCapture:
    """每次截图应创建全新的 ScreenCaptureOverlay（消除分层窗口后备存储残留
    导致的「一闪而过上一次截图界面」）。
    """

    def test_fresh_overlay_replaces_old(self, main_window, monkeypatch):
        """_start_fresh_overlay_capture 应创建新 overlay 并释放旧的。"""
        from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay

        old_overlay = main_window._overlay
        assert old_overlay is not None

        # mock start_capture 避免真实截图（grabWindow）在 headless 环境失败
        started: list = []
        monkeypatch.setattr(
            ScreenCaptureOverlay, "start_capture", lambda self: started.append(self)
        )

        main_window._start_fresh_overlay_capture()

        # 新实例已创建并 start_capture 被调用
        assert main_window._overlay is not old_overlay
        assert isinstance(main_window._overlay, ScreenCaptureOverlay)
        assert len(started) == 1

    def test_fresh_overlay_reconnects_signals(self, main_window, monkeypatch):
        """新 overlay 的信号应连接到 MainWindow 的槽。"""
        from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay

        monkeypatch.setattr(ScreenCaptureOverlay, "start_capture", lambda self: None)
        monkeypatch.setattr(
            main_window._single_tab, "run_ocr", lambda *args, **kwargs: None
        )
        main_window._start_fresh_overlay_capture()
        # confirmed 信号连接可触发槽（不报错）
        received: list = []
        main_window._overlay.confirmed.connect(lambda *a: received.append(a))
        main_window._overlay.confirmed.emit(QPixmap(2, 2), None)
        assert len(received) == 1

    def test_fresh_overlay_retires_until_confirmed_save_notification_finishes(
        self, main_window, qtbot, monkeypatch
    ):
        from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay

        old_overlay = main_window._overlay
        drained = False
        deleted: list[bool] = []
        messages: list[str] = []
        monkeypatch.setattr(ScreenCaptureOverlay, "start_capture", lambda self: None)
        monkeypatch.setattr(old_overlay, "finish_capture", lambda: None)
        monkeypatch.setattr(old_overlay, "request_save_shutdown", lambda: None)
        monkeypatch.setattr(old_overlay, "drain_saves", lambda _timeout: drained)
        monkeypatch.setattr(old_overlay, "deleteLater", lambda: deleted.append(True))
        monkeypatch.setattr(
            main_window._statusbar,
            "showMessage",
            lambda message, *_args: messages.append(message),
        )

        main_window._start_fresh_overlay_capture()

        assert old_overlay in main_window._retired_overlays
        assert deleted == []

        drained = True
        old_overlay.saved.emit("C:/saved.png")
        qtbot.waitUntil(lambda: old_overlay not in main_window._retired_overlays)

        assert deleted == [True]
        assert any("C:/saved.png" in message for message in messages)

    def test_late_overlay_save_during_shutdown_only_releases_retired_overlay(
        self, main_window, qtbot, monkeypatch
    ):
        overlay = main_window._overlay
        restored: list[bool] = []
        messages: list[str] = []
        deleted: list[bool] = []
        monkeypatch.setattr(overlay, "drain_saves", lambda _timeout: True)
        monkeypatch.setattr(overlay, "deleteLater", lambda: deleted.append(True))
        monkeypatch.setattr(
            main_window,
            "_restore_main_window",
            lambda **_kwargs: restored.append(True),
        )
        monkeypatch.setattr(
            main_window._statusbar,
            "showMessage",
            lambda message, *_args: messages.append(message),
        )
        main_window._retired_overlays.add(overlay)
        main_window._closing = True

        main_window._on_overlay_saved_for(overlay, "C:/late.png")
        qtbot.waitUntil(lambda: overlay not in main_window._retired_overlays)

        assert restored == []
        assert messages == []
        assert deleted == [True]

    def test_pipeline_passed_to_fresh_overlay(self, main_window, monkeypatch):
        """快捷管道截图应把 pipeline 传给新 overlay。"""
        from vibeocr.classic.widgets.screen_capture_overlay import ScreenCaptureOverlay

        monkeypatch.setattr(ScreenCaptureOverlay, "start_capture", lambda self: None)
        main_window._start_fresh_overlay_capture("FORMULA_RECOGNITION")
        assert main_window._overlay._pending_pipeline == "FORMULA_RECOGNITION"


class TestTabOrder:
    """标签页顺序回归测试。

    期望顺序：单次识别 → 批量识别 → 二维码 → PDF 处理 → 设置 → 关于。
    关于页应位于末尾（设置页之后），符合「关于」居末的惯例。
    """

    def test_tab_order(self, main_window):
        """标签页应按 单次识别 → 批量 → 二维码 → PDF → 设置 → 关于 排列。"""
        tw = main_window._ui.tabWidget
        titles = [tw.tabText(i) for i in range(tw.count())]
        expected = ["单次识别", "批量识别", "二维码", "PDF 处理", "设置", "关于"]
        assert titles == expected, f"标签页顺序不符：实际 {titles}，期望 {expected}"

    def test_about_tab_is_last(self, main_window):
        """关于页应在最后一个位置。"""
        tw = main_window._ui.tabWidget
        assert tw.tabText(tw.count() - 1) == "关于", "关于页应在末尾"
        # 设置页应在关于页之前
        settings_idx = next(i for i in range(tw.count()) if tw.tabText(i) == "设置")
        about_idx = tw.count() - 1
        assert settings_idx < about_idx, "设置页应在关于页之前"


class TestPrewarmResultWebEngine:
    """prewarm_result_webengine：窗口显示后延迟预热单次识别结果页 WebEngine。

    见 .superpowers/sdd/fix-task2-brief.md：消除首次截图结果前主界面闪烁，
    把 Chromium 冷启动成本从「首次结果渲染时」前移到「启动空闲片段」。
    用 __new__ + mock 注入轻量构造，仅测该方法逻辑（不拉起真实 MainWindow）。
    """

    def _make_window_with_single_tab(self, prewarm):
        """构造一个仅含 mock _single_tab._result_widget 的 MainWindow 外壳。

        参考 TestMainWindowClosePolls 的 __new__ 风格：避免真实 MainWindow 构造
        的重型依赖，直接验证 prewarm_result_webengine 的转发 + 守卫逻辑。
        """
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        window._single_tab = SimpleNamespace(
            _result_widget=SimpleNamespace(prewarm_webengine=prewarm)
        )
        return window

    def test_prewarm_result_webengine_invokes_single_tab_prewarm_once(self):
        """非 closing 状态下应调用 _single_tab._result_widget.prewarm_webengine 一次。"""
        calls = []
        window = self._make_window_with_single_tab(prewarm=lambda: calls.append(1))

        window.prewarm_result_webengine()

        assert len(calls) == 1, "应转发调用到单次识别结果页的 prewarm_webengine"

    def test_prewarm_result_webengine_respects_closing_guard(self):
        """_closing 为真时不应调用 prewarm_webengine。"""
        calls = []
        window = self._make_window_with_single_tab(prewarm=lambda: calls.append(1))
        window._closing = True

        window.prewarm_result_webengine()

        assert calls == [], "_closing 为真时不应转发预热调用"

    def test_prewarm_result_webengine_handles_missing_result_widget(self):
        """_result_widget 缺失（或无 prewarm_webengine）时应静默跳过，不抛异常。"""
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        # _single_tab 存在但 _result_widget 为 None（防御性 getattr 路径）。
        window._single_tab = SimpleNamespace(_result_widget=None)

        # 不应抛异常。
        window.prewarm_result_webengine()
