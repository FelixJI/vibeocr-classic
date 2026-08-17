"""InstallDialog（安装进度对话框）UI 与 slot 测试。

现有 ``test_install_worker_*.py`` 仅覆盖 ``InstallWorker`` 线程逻辑，
``InstallDialog`` 类（UI 组装 + 进度/完成/取消 slot）此前完全未测。
本文件复用既有约定：``__new__`` + 手动属性注入避免重型 ``__init__``，
直接调用 ``@Slot`` 方法并断言内部状态；构造完整对话框时用 ``tmp_path``。
"""

from dataclasses import replace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from vibeocr.classic.widgets.install_dialog import (
    InstallDialog,
    InstallWorker,
    build_maintenance_detail,
)
from vibeocr.classic.runtime_installation import (
    RuntimeComponentDescriptor,
    RuntimeMaintenanceUpdate,
    RuntimeProfileDescriptor,
)


def _show_dialog(dlg: InstallDialog) -> None:
    """显示对话框使子控件 ``isVisible()`` 反映 ``setVisible`` 调用。

    预设 ``_worker``（truthy）以避免 ``showEvent`` 触发真实的 ``_start_install``
    去构造并 ``.start()`` 一个 InstallWorker 线程。
    """
    dlg._worker = MagicMock()  # truthy → showEvent 的 ``if not self._worker`` 跳过
    dlg.show()


def test_install_worker_coalesces_burst_progress_for_ui_and_info_log(
    qapp, tmp_path, caplog
):
    worker = InstallWorker(tmp_path)
    emitted: list[RuntimeMaintenanceUpdate] = []
    worker.maintenance.connect(emitted.append)

    with caplog.at_level("INFO", logger="vibeocr.classic.widgets.install_dialog"):
        for sequence in range(1, 104):
            worker._emit_maintenance(
                RuntimeMaintenanceUpdate(
                    event_type="progress",
                    operation_id="op-1",
                    sequence=sequence,
                    operation="ensure",
                    operation_state="running",
                    phase="prepare_runtime",
                    profile_id="win-x64-cpu",
                    updated_at="2026-08-09T16:36:55Z",
                    component_id="runtime_base",
                    progress_current=sequence,
                    progress_total=103,
                    progress_unit="steps",
                    message_code="runtime.extract_python",
                )
            )
        terminal = RuntimeMaintenanceUpdate(
            event_type="snapshot",
            operation_id="op-1",
            sequence=104,
            operation="ensure",
            operation_state="succeeded",
            phase="prepare_runtime",
            profile_id="win-x64-cpu",
            updated_at="2026-08-09T16:36:56Z",
            component_id="runtime_base",
            progress_current=103,
            progress_total=103,
            progress_unit="steps",
            message_code="runtime.ready",
        )
        worker._emit_maintenance(terminal)

    records = [
        record
        for record in caplog.records
        if record.name == "vibeocr.classic.widgets.install_dialog"
        and record.levelname == "INFO"
    ]
    assert len(emitted) <= 103
    assert len(records) == 2
    assert emitted[-1] is terminal


def test_byte_progress_refreshes_ui_without_repeating_info_log(qapp, tmp_path, caplog):
    worker = InstallWorker(tmp_path)
    emitted: list[RuntimeMaintenanceUpdate] = []
    worker.maintenance.connect(emitted.append)
    first = RuntimeMaintenanceUpdate(
        event_type="progress",
        operation_id="op-1",
        sequence=3217,
        operation="ensure",
        operation_state="running",
        phase="prepare_runtime",
        profile_id="win-x64-cpu",
        updated_at="2026-08-10T05:28:22Z",
        component_id="runtime_base",
        progress_current=1_000,
        progress_total=1_000_000,
        progress_unit="bytes",
        message_code="runtime.extract_python",
    )

    with (
        patch(
            "vibeocr.classic.widgets.install_dialog.time.monotonic",
            side_effect=[100.0, 101.0],
        ),
        caplog.at_level("INFO", logger="vibeocr.classic.widgets.install_dialog"),
    ):
        worker._emit_maintenance(first)
        worker._emit_maintenance(replace(first, sequence=3233, progress_current=2_000))

    records = [
        record
        for record in caplog.records
        if record.name == "vibeocr.classic.widgets.install_dialog"
        and record.levelname == "INFO"
    ]
    assert [update.sequence for update in emitted] == [3217, 3233]
    assert len(records) == 1


class TestSetupUiTitleBranches:
    """``_setup_ui`` 三种标题分支：single_pkg / packages / default。"""

    def test_single_pkg_title(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path, single_pkg="numpy")
        assert dlg.windowTitle() == "重装依赖：numpy"
        assert "numpy" in dlg._title_text

    def test_packages_batch_title(self, qapp, tmp_path):
        pkgs = ["scipy", "numpy", "pandas"]
        dlg = InstallDialog(tmp_path, packages=pkgs)
        assert dlg.windowTitle() == "批量重装 3 个依赖包"
        assert "3" in dlg._title_text

    def test_default_title(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        assert dlg.windowTitle() == "安装OCR依赖"
        assert dlg._title_text == "正在安装OCR依赖..."

    def test_modal_and_minimum_size(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        assert dlg.isModal()
        assert dlg.minimumSize().width() == 620
        assert dlg.minimumSize().height() == 520

    def test_close_and_cancel_buttons_initially_hidden(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        _show_dialog(dlg)
        # 完成前关闭/取消按钮均隐藏
        assert not dlg._close_button.isVisible()
        assert not dlg._cancel_button.isVisible()


class TestBuildMaintenanceDetail:
    """共享渲染 helper：首启与设置页两个对话框的进度文案契约。"""

    def test_bytes_progress_is_determinate(self):
        update = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=3,
            operation="ensure",
            operation_state="running",
            phase="prepare_runtime",
            profile_id="win-x64-cpu",
            updated_at="2026-08-14T00:00:00Z",
            progress_current=50 * 1024 * 1024,
            progress_total=100 * 1024 * 1024,
            progress_unit="bytes",
        )
        rendered = build_maintenance_detail(update)
        assert rendered.determinate
        assert rendered.progress_value == 50 * 1024 * 1024
        assert rendered.progress_maximum == 100 * 1024 * 1024
        assert "50%" in rendered.detail
        assert "MiB" in rendered.detail
        assert rendered.phase_label == "准备 Python 运行时"
        assert rendered.state_label == "进行中"

    def test_steps_progress_without_clock_has_no_elapsed(self):
        update = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=4,
            operation="ensure",
            operation_state="running",
            phase="install_profile",
            profile_id="win-x64-cpu",
            updated_at="2026-08-14T00:00:00Z",
            progress_current=2,
            progress_total=7,
            progress_unit="steps",
        )
        rendered = build_maintenance_detail(update)
        assert not rendered.determinate
        assert "2/7 步" in rendered.detail
        assert "已用时" not in rendered.detail

    def test_running_without_clock_omits_fake_elapsed(self):
        update = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=5,
            operation="ensure",
            operation_state="running",
            phase="prepare_runtime",
            profile_id="win-x64-cpu",
            updated_at="2026-08-14T00:00:00Z",
        )
        rendered = build_maintenance_detail(update)
        assert "已用时" not in rendered.detail

    def test_matching_scope_stays_silent(self):
        update = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=6,
            operation="ensure",
            operation_state="running",
            phase="install_profile",
            profile_id="win-x64-cpu",
            updated_at="2026-08-14T00:00:00Z",
            requested_component_ids=("win-x64-cpu-document-parsing",),
            effective_component_ids=("win-x64-cpu-document-parsing",),
        )
        rendered = build_maintenance_detail(update)
        assert "实际安装" not in rendered.detail

    def test_closure_expansion_is_reported_not_hidden(self):
        update = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=7,
            operation="ensure",
            operation_state="running",
            phase="install_profile",
            profile_id="win-x64-cpu",
            updated_at="2026-08-14T00:00:00Z",
            requested_component_ids=("win-x64-cpu-document-parsing",),
            effective_component_ids=(
                "win-x64-cpu-document-parsing",
                "win-x64-cpu-pinned-dep",
            ),
        )
        rendered = build_maintenance_detail(update)
        assert "实际安装" in rendered.detail
        assert "win-x64-cpu-pinned-dep" in rendered.detail
        assert "请求：win-x64-cpu-document-parsing" in rendered.detail


class TestOnProgress:
    def test_progress_updates_stage_and_log(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._on_progress("网络检测", "正在检测网络环境...")
        assert dlg._stage_label.text() == "[网络检测] 正在检测网络环境..."
        assert "网络检测" in dlg._log_text.toPlainText()

    def test_log_appends_multiple_lines(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._on_progress("阶段一", "消息一")
        dlg._on_progress("阶段二", "消息二")
        text = dlg._log_text.toPlainText()
        assert "消息一" in text
        assert "消息二" in text

    def test_maintenance_updates_component_and_status_callback(self, qapp, tmp_path):
        summaries: list[str] = []
        dlg = InstallDialog(tmp_path, maintenance_callback=summaries.append)
        dlg._on_profile(
            RuntimeProfileDescriptor(
                "win-x64-cpu",
                "cpu",
                (RuntimeComponentDescriptor("ocr_engine", "OCR engine", "3.7.0"),),
            )
        )
        dlg._on_maintenance(
            RuntimeMaintenanceUpdate(
                event_type="progress",
                operation_id="op-1",
                sequence=2,
                operation="ensure",
                operation_state="running",
                phase="install_profile",
                profile_id="win-x64-cpu",
                updated_at="2026-08-05T00:00:00Z",
                component_id="ocr_engine",
                progress_current=2,
                progress_total=7,
                progress_unit="steps",
                message_code="runtime.installing",
            )
        )

        item = dlg._components_tree.topLevelItem(0)
        assert item.text(0) == "OCR engine"
        assert item.text(1) == "进行中"
        assert item.text(2) == "3.7.0"
        assert dlg._progress_bar.maximum() == 0
        assert "2/7 步" in dlg._stage_label.text()
        assert summaries == ["Runtime 安装运行时依赖：OCR engine · 进行中"]

    def test_bytes_progress_is_determinate_and_human_readable(self, qapp, tmp_path):
        summaries: list[str] = []
        dlg = InstallDialog(tmp_path, maintenance_callback=summaries.append)
        first = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=3,
            operation="ensure",
            operation_state="running",
            phase="prepare_runtime",
            profile_id="win-x64-cpu",
            updated_at="2026-08-05T00:00:01Z",
            component_id="runtime_base",
            progress_current=50 * 1024 * 1024,
            progress_total=100 * 1024 * 1024,
            progress_unit="bytes",
            estimated_remaining_seconds=4,
        )
        dlg._on_maintenance(first)
        dlg._on_maintenance(
            replace(
                first,
                sequence=4,
                progress_current=60 * 1024 * 1024,
                estimated_remaining_seconds=3,
            )
        )

        assert dlg._progress_bar.maximum() == 100 * 1024 * 1024
        assert dlg._progress_bar.value() == 60 * 1024 * 1024
        assert "60%" in dlg._stage_label.text()
        assert "60.0 MiB / 100.0 MiB" in dlg._stage_label.text()
        assert "预计剩余 3 秒" in dlg._stage_label.text()
        assert dlg._log_text.toPlainText().count("Runtime 准备 Python 运行时") == 1
        assert summaries == ["Runtime 准备 Python 运行时：runtime_base · 进行中"]

    def test_indeterminate_progress_shows_elapsed_time_without_duplicate_log(
        self, qapp, tmp_path
    ):
        dlg = InstallDialog(tmp_path)
        first = RuntimeMaintenanceUpdate(
            event_type="progress",
            operation_id="op-1",
            sequence=3217,
            operation="ensure",
            operation_state="running",
            phase="prepare_runtime",
            profile_id="win-x64-cpu",
            updated_at="2026-08-10T05:28:22Z",
            component_id="runtime_base",
            message_code="runtime.extract_python",
        )

        with patch(
            "vibeocr.classic.widgets.install_dialog.time.monotonic",
            side_effect=[100.0, 102.0],
        ):
            dlg._on_maintenance(first)
            initial_label = dlg._stage_label.text()
            dlg._on_maintenance(replace(first, sequence=3233))

        assert dlg._stage_label.text() != initial_label
        assert "已用时 2 秒" in dlg._stage_label.text()
        assert dlg._log_text.toPlainText().count("Runtime 准备 Python 运行时") == 1


class TestOnFinished:
    """``done(int)`` 设置结果码并隐藏对话框，不抛异常。"""

    def test_success_sets_ui_and_done_accepted(self, qapp, tmp_path, qtbot):
        dlg = InstallDialog(tmp_path)
        _show_dialog(dlg)
        with qtbot.waitSignal(dlg.install_succeeded, timeout=2000):
            dlg._on_finished(True, "numpy 安装成功")
        assert dlg._title_label.text() == "安装成功!"
        assert dlg._stage_label.text() == "numpy 安装成功"
        # done(1) 会 hide 对话框，子控件 isVisible() 受父可见性影响；
        # isHidden() 只反映显式 setVisible 调用，可稳定验证 slot 显示了关闭按钮。
        assert not dlg._close_button.isHidden()
        assert dlg._progress_bar.isHidden()
        # done(1) → QDialog.Accepted
        assert dlg.result() == 1

    def test_success_blank_message_uses_default(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        _show_dialog(dlg)
        dlg._on_finished(True, "")
        assert dlg._stage_label.text() == "OCR依赖安装完成"
        assert dlg.result() == 1

    def test_failure_sets_failure_ui_and_done_rejected(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        _show_dialog(dlg)
        dlg._on_finished(False, "网络错误")
        assert dlg._title_label.text() == "安装失败"
        assert dlg._stage_label.text() == "安装过程中出现错误"
        # done(0) hide 对话框，用 isHidden() 验证显式 setVisible 调用。
        assert not dlg._close_button.isHidden()
        assert dlg._close_button.text() == "关闭"
        # done(0) → QDialog.Rejected
        assert dlg.result() == 0

    def test_failure_logs_message(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        _show_dialog(dlg)
        dlg._on_finished(False, "下载失败详情")
        assert "下载失败详情" in dlg._log_text.toPlainText()


class TestOnCancelClicked:
    """取消按钮：QMessageBox.question Yes/No 两分支 + worker 运行与否。"""

    def test_no_reply_does_nothing(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._cancel_button.setVisible(True)
        with patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            dlg._on_cancel_clicked()
        # 选 No → 直接返回，按钮状态不变
        assert dlg._cancel_button.isEnabled()
        assert dlg._cancel_button.text() == "取消安装"
        assert dlg._worker is None

    def test_yes_with_running_worker_requests_cancel(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._worker = MagicMock()
        dlg._worker.isRunning.return_value = True
        with patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dlg._on_cancel_clicked()
        dlg._worker.request_cancel.assert_called_once()
        assert not dlg._cancel_button.isEnabled()
        assert dlg._cancel_button.text() == "正在取消..."
        assert "用户取消安装" in dlg._log_text.toPlainText()

    def test_yes_without_running_worker_hides_button(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._cancel_button.setVisible(True)
        dlg._worker = MagicMock()
        dlg._worker.isRunning.return_value = False
        with patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dlg._on_cancel_clicked()
        # 无运行 worker → 显式隐藏取消按钮，且不调用 request_cancel
        assert dlg._cancel_button.isHidden()
        dlg._worker.request_cancel.assert_not_called()


class TestStartInstall:
    """``_start_install`` 构造 worker、连信号、显示取消按钮、``.start()``。"""

    @patch("vibeocr.classic.widgets.install_dialog.InstallWorker")
    @patch("vibeocr.classic.widgets.install_dialog.track_dialog_worker")
    def test_start_creates_worker_and_connects_signals(
        self, mock_track, mock_worker_cls, qapp, tmp_path
    ):
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        dlg = InstallDialog(tmp_path, single_pkg="scipy")
        _show_dialog(dlg)  # 重置 _worker 为 mock；下面调 _start_install 覆盖之
        dlg._start_install()

        mock_worker_cls.assert_called_once()
        mock_worker.progress.connect.assert_called_once()
        mock_worker.completed.connect.assert_called_once()
        mock_worker.start.assert_called_once()
        mock_track.assert_called_once_with(mock_worker)
        assert not dlg._cancel_button.isHidden()
        assert dlg._worker is mock_worker

    @patch("vibeocr.classic.widgets.install_dialog.InstallWorker")
    @patch("vibeocr.classic.widgets.install_dialog.track_dialog_worker")
    def test_start_logs_intro_message(
        self, mock_track, mock_worker_cls, qapp, tmp_path
    ):
        mock_worker_cls.return_value = MagicMock()
        dlg = InstallDialog(tmp_path, packages=["a", "b"])
        dlg._start_install()
        assert "2" in dlg._log_text.toPlainText()


class TestCloseEventAndShutdown:
    def test_close_event_requests_cancel_when_worker_running(self, qapp, tmp_path):
        dlg = InstallDialog.__new__(InstallDialog)
        dlg._worker = MagicMock()
        dlg._worker.isRunning.return_value = True
        event = MagicMock()
        InstallDialog.closeEvent(dlg, event)
        dlg._worker.request_cancel.assert_called_once()
        event.accept.assert_called_once()

    def test_close_event_no_worker_accepts(self, qapp, tmp_path):
        dlg = InstallDialog.__new__(InstallDialog)
        dlg._worker = None
        event = MagicMock()
        InstallDialog.closeEvent(dlg, event)
        event.accept.assert_called_once()

    def test_request_shutdown_cancels_and_closes(self, qapp, tmp_path):
        dlg = InstallDialog.__new__(InstallDialog)
        dlg._worker = MagicMock()
        dlg._worker.isRunning.return_value = True
        with patch.object(InstallDialog, "close") as mock_close:
            dlg.request_shutdown()
        dlg._worker.request_cancel.assert_called_once()
        mock_close.assert_called_once()

    def test_request_shutdown_no_worker_still_closes(self, qapp, tmp_path):
        dlg = InstallDialog.__new__(InstallDialog)
        dlg._worker = None
        with patch.object(InstallDialog, "close") as mock_close:
            dlg.request_shutdown()
        mock_close.assert_called_once()


class TestLog:
    def test_log_appends_and_is_readable(self, qapp, tmp_path):
        dlg = InstallDialog(tmp_path)
        dlg._log("测试消息")
        assert "测试消息" in dlg._log_text.toPlainText()
