"""测试 SwitchWorker 协作式取消：不使用 QThread.terminate。

根因：closeEvent 直接 QThread.terminate() + 无限 wait()，强杀 Python 线程
可能发生在 pip/文件修改中间，造成 CPU/GPU 包都不完整；无限 wait 还可能
冻结关闭。修复：复用 InstallWorker 的 cancel_event + kill pip 子进程 +
关闭立即返回，由应用级生命周期注册表保活到原生线程结束。

``TestSwitchDialogUi`` 扩展覆盖 ``SwitchDialog``（UI 组装 + 进度/完成 slot）
与 ``SwitchWorker.run``（patch ``env_manager.switch_paddle_backend`` /
``NetworkDetector``，不启动真实子进程）。
"""

import threading
from unittest.mock import MagicMock, patch

from vibeocr.classic.widgets.switch_dialog import SwitchDialog, SwitchWorker


class TestSwitchWorkerCancel:
    """SwitchWorker 协作式取消（复用 InstallWorker 范式）。"""

    def test_request_cancel_sets_event_and_kills_proc(self):
        """request_cancel 设置 cancel_event 并 kill 当前子进程"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        worker._current_proc = None
        worker._proc_lock = threading.Lock()

        mock_proc = MagicMock()
        worker._current_proc = mock_proc

        worker.request_cancel()

        assert worker._cancel_event.is_set()
        mock_proc.kill.assert_called_once()

    def test_request_cancel_no_proc_does_not_raise(self):
        """无子进程时 request_cancel 不抛异常"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        worker._current_proc = None
        worker._proc_lock = threading.Lock()

        worker.request_cancel()
        assert worker._cancel_event.is_set()

    def test_is_cancelled_reflects_event(self):
        """is_cancelled 反映 cancel_event 状态"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        assert not worker.is_cancelled()
        worker._cancel_event.set()
        assert worker.is_cancelled()

    def test_close_event_uses_request_cancel_not_terminate(self):
        """closeEvent 只请求取消并立即返回，不阻塞 GUI。"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = MagicMock()
        dialog._worker.isRunning.return_value = True

        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)

        # closeEvent 应调用 request_cancel，但不能 terminate 或 wait 阻塞 GUI。
        dialog._worker.request_cancel.assert_called_once()
        dialog._worker.terminate.assert_not_called()
        dialog._worker.wait.assert_not_called()
        event.accept.assert_called_once()

    def test_close_event_when_no_worker(self):
        """无 worker 时 closeEvent 直接 accept"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = None
        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)
        event.accept.assert_called_once()


class TestSwitchDialogUi:
    """``SwitchDialog`` UI 组装 + 进度/完成 slot + ``showEvent``/``_start``。"""

    def test_setup_ui_gpu_title(self, qapp, tmp_path):
        """target=gpu → 标题与阶段标签都显示 GPU。"""
        dlg = SwitchDialog(tmp_path, "gpu")
        assert dlg.windowTitle() == "切换到 GPU 后端"
        assert "GPU" in dlg._title_label.text()

    def test_setup_ui_cpu_title(self, qapp, tmp_path):
        """target 非 gpu（如 cpu）→ 标题显示 CPU。"""
        dlg = SwitchDialog(tmp_path, "cpu")
        assert dlg.windowTitle() == "切换到 CPU 后端"
        assert "CPU" in dlg._title_label.text()

    def test_modal_and_minimum_size(self, qapp, tmp_path):
        dlg = SwitchDialog(tmp_path, "gpu")
        assert dlg.isModal()
        assert dlg.minimumSize().width() == 500
        assert dlg.minimumSize().height() == 400

    def test_on_progress_updates_stage_and_log(self, qapp, tmp_path):
        dlg = SwitchDialog(tmp_path, "gpu")
        dlg._on_progress("后端切换", "正在切换到 GPU...")
        assert dlg._stage_label.text() == "[后端切换] 正在切换到 GPU..."
        assert "后端切换" in dlg._log_text.toPlainText()

    def test_on_finished_success_emits_and_done_accepted(self, qapp, tmp_path, qtbot):
        dlg = SwitchDialog(tmp_path, "gpu")
        with qtbot.waitSignal(dlg.switch_succeeded, timeout=2000):
            dlg._on_finished(True, "切换成功")
        assert dlg._title_label.text() == "切换成功!"
        assert dlg._stage_label.text() == "后端已切换，即将启动 OCR 服务"
        assert not dlg._close_button.isHidden()
        assert dlg._progress_bar.isHidden()
        # done(1) → QDialog.Accepted
        assert dlg.result() == 1

    def test_on_finished_failure_sets_failure_ui_and_done_rejected(self, qapp, tmp_path):
        dlg = SwitchDialog(tmp_path, "cpu")
        dlg._on_finished(False, "切换出错详情")
        assert dlg._title_label.text() == "切换失败"
        assert dlg._stage_label.text() == "切换过程中出现错误"
        assert not dlg._close_button.isHidden()
        assert dlg._close_button.text() == "关闭"
        assert dlg.result() == 0
        assert "切换出错详情" in dlg._log_text.toPlainText()

    def test_log_appends(self, qapp, tmp_path):
        dlg = SwitchDialog(tmp_path, "gpu")
        dlg._log("第一行")
        dlg._log("第二行")
        text = dlg._log_text.toPlainText()
        assert "第一行" in text
        assert "第二行" in text

    def test_request_shutdown_cancels_and_closes(self, qapp, tmp_path):
        dlg = SwitchDialog.__new__(SwitchDialog)
        dlg._worker = MagicMock()
        dlg._worker.isRunning.return_value = True
        with patch.object(SwitchDialog, "close") as mock_close:
            dlg.request_shutdown()
        dlg._worker.request_cancel.assert_called_once()
        mock_close.assert_called_once()

    def test_request_shutdown_no_worker_still_closes(self, qapp, tmp_path):
        dlg = SwitchDialog.__new__(SwitchDialog)
        dlg._worker = None
        with patch.object(SwitchDialog, "close") as mock_close:
            dlg.request_shutdown()
        mock_close.assert_called_once()


class TestSwitchDialogStart:
    """``_start``/``showEvent``：构造 worker、连信号、``.start()``。"""

    @patch("vibeocr.classic.widgets.switch_dialog.SwitchWorker")
    @patch("vibeocr.classic.widgets.switch_dialog.track_dialog_worker")
    def test_start_creates_worker_and_connects(
        self, mock_track, mock_worker_cls, qapp, tmp_path
    ):
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        dlg = SwitchDialog(tmp_path, "gpu")
        dlg._start()

        mock_worker_cls.assert_called_once_with(tmp_path, "gpu")
        mock_worker.progress.connect.assert_called_once()
        mock_worker.completed.connect.assert_called_once()
        mock_worker.start.assert_called_once()
        mock_track.assert_called_once_with(mock_worker)
        assert dlg._worker is mock_worker
        # _start 先写日志
        assert "开始切换后端" in dlg._log_text.toPlainText()

    @patch("vibeocr.classic.widgets.switch_dialog.SwitchWorker")
    @patch("vibeocr.classic.widgets.switch_dialog.track_dialog_worker")
    def test_show_event_triggers_start_once(
        self, mock_track, mock_worker_cls, qapp, tmp_path
    ):
        """showEvent 在无 worker 时触发一次 _start；二次不重复触发。"""
        mock_worker_cls.return_value = MagicMock()
        dlg = SwitchDialog(tmp_path, "gpu")
        dlg.show()  # 触发 showEvent → _start
        assert mock_worker_cls.call_count == 1
        dlg.show()  # 二次显示，worker 已存在，不再启动
        assert mock_worker_cls.call_count == 1


class TestSwitchWorkerRun:
    """``SwitchWorker.run``：patch ``env_manager.switch_paddle_backend`` 与
    ``NetworkDetector``，验证成功/失败/异常三条路径——不启动真实子进程。"""

    def _make_worker(self, tmp_path, target="gpu") -> SwitchWorker:
        return SwitchWorker(tmp_path, target)

    @patch("vibeocr.backend.network_detector.NetworkDetector")
    @patch("vibeocr.classic.widgets.switch_dialog.env_manager.switch_paddle_backend")
    def test_run_success_emits_completed_true(
        self, mock_switch, mock_detector_cls, qapp, tmp_path, qtbot
    ):
        mock_switch.return_value = (True, "已切换到 GPU")
        worker = self._make_worker(tmp_path)
        with qtbot.waitSignal(worker.completed, timeout=5000) as blocker:
            worker.run()  # 直接在同线程调用 QThread.run
        assert blocker.args == [True, "已切换到 GPU"]

    @patch("vibeocr.backend.network_detector.NetworkDetector")
    @patch("vibeocr.classic.widgets.switch_dialog.env_manager.switch_paddle_backend")
    def test_run_failure_emits_completed_false(
        self, mock_switch, mock_detector_cls, qapp, tmp_path, qtbot
    ):
        mock_switch.return_value = (False, "pip 安装失败")
        worker = self._make_worker(tmp_path, "cpu")
        with qtbot.waitSignal(worker.completed, timeout=5000) as blocker:
            worker.run()
        assert blocker.args == [False, "pip 安装失败"]

    @patch("vibeocr.backend.network_detector.NetworkDetector")
    @patch("vibeocr.classic.widgets.switch_dialog.env_manager.switch_paddle_backend")
    def test_run_exception_emits_completed_false_with_msg(
        self, mock_switch, mock_detector_cls, qapp, tmp_path, qtbot
    ):
        mock_switch.side_effect = RuntimeError("boom")
        worker = self._make_worker(tmp_path)
        with qtbot.waitSignal(worker.completed, timeout=5000) as blocker:
            worker.run()
        success, msg = blocker.args
        assert success is False
        assert "boom" in msg

    @patch("vibeocr.backend.network_detector.NetworkDetector")
    @patch("vibeocr.classic.widgets.switch_dialog.env_manager.switch_paddle_backend")
    def test_run_passes_target_and_cancel_event(
        self, mock_switch, mock_detector_cls, qapp, tmp_path, qtbot
    ):
        """验证 run 把 target、cancel_event、on_proc 传给 switch_paddle_backend。"""
        mock_switch.return_value = (True, "ok")
        worker = self._make_worker(tmp_path, "gpu")
        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.run()
        _root, target, _net = mock_switch.call_args.args[0:3]
        kwargs = mock_switch.call_args.kwargs
        assert target == "gpu"
        assert kwargs["cancel_event"] is worker._cancel_event
        assert kwargs["on_proc"] == worker._on_proc

    @patch("vibeocr.backend.network_detector.NetworkDetector")
    @patch("vibeocr.classic.widgets.switch_dialog.env_manager.switch_paddle_backend")
    def test_request_cancel_during_run_kills_popen_proc(
        self, mock_switch, mock_detector_cls, qapp, tmp_path, qtbot
    ):
        """request_cancel 设置 cancel_event 并 kill 通过 on_proc 注册的子进程。

        直接验证 on_proc → request_cancel 的 kill 路径（与 SwitchDialog 解耦）。
        """
        mock_switch.return_value = (True, "ok")
        worker = self._make_worker(tmp_path)
        fake_proc = MagicMock()
        worker._on_proc(fake_proc)  # 模拟 switch_paddle_backend 内部回调注册子进程
        worker.request_cancel()
        assert worker.is_cancelled()
        fake_proc.kill.assert_called_once()
