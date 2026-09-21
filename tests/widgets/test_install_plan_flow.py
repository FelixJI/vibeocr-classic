from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from vibeocr.classic.runtime_maintenance import InstallationRecord
from vibeocr.classic.runtime_installation import RuntimeMaintenanceUpdate
from vibeocr.classic.widgets.install_dialog import (
    InstallWorker,
    build_maintenance_detail,
)


def test_cancelling_preview_never_installs_or_stops_service(qapp, tmp_path):
    worker = InstallWorker(tmp_path, install_component_ids=())
    worker.plan_ready.connect(lambda _plan: worker.request_cancel())
    with (
        patch(
            "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
        ) as factory,
        patch("vibeocr.classic.client.shutdown_backend_client") as shutdown,
    ):
        client = factory.return_value
        client.preview_install_plan.return_value = SimpleNamespace(
            plan_id="opaque", blockers=()
        )
        worker.run()
        client.preview_install_plan.assert_called_once_with(
            install_component_ids=(), download_source_ids=None
        )
        client.ensure.assert_not_called()
        client.repair.assert_not_called()
        shutdown.assert_not_called()
    assert InstallationRecord.read(tmp_path) is None


def test_confirmation_uses_plan_without_resending_mutable_selection(qapp, tmp_path):
    worker = InstallWorker(tmp_path, install_component_ids=("mineru-cpu",))
    worker.plan_ready.connect(lambda _plan: worker.confirm_install())
    with (
        patch(
            "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
        ) as factory,
        patch("vibeocr.classic.client.shutdown_backend_client"),
    ):
        client = factory.return_value
        client.preview_install_plan.return_value = SimpleNamespace(
            plan_id="opaque", blockers=()
        )
        worker.run()
        args = client.ensure.call_args.kwargs
        assert args["plan_id"] == "opaque"
        assert args["operation_id"] == InstallationRecord.read(tmp_path).operation_id
        assert "install_component_ids" not in args
        assert "download_source_ids" not in args


def test_pending_operation_is_observed_without_reinstallation(qapp, tmp_path):
    record = InstallationRecord(str(uuid4()), 3, "running", "old-plan", ())
    record.save(tmp_path)
    terminal = RuntimeMaintenanceUpdate(
        "snapshot",
        record.operation_id,
        4,
        "ensure",
        "failed",
        "install_profile",
        "win-x64-cpu",
        "2026-09-21T00:00:00Z",
        message_args={"reason_code": "download_failed", "next_action": "check_network"},
    )
    worker = InstallWorker(tmp_path)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as factory:
        client = factory.return_value
        client.observe.return_value = SimpleNamespace(
            events=(terminal,), snapshot=terminal, more=False, through_sequence=4
        )
        worker.run()
        client.observe.assert_called_once_with(record.operation_id, after_sequence=3)
        client.preview_install_plan.assert_not_called()
        client.ensure.assert_not_called()
    restored = InstallationRecord.read(tmp_path)
    assert restored.state == "failed"
    assert restored.sequence == 4
    assert restored.requested_component_ids == ()
    assert "download_failed" in restored.summary


def test_progress_above_qt_integer_limit_and_unknown_total():
    update = RuntimeMaintenanceUpdate(
        "progress",
        "op",
        1,
        "ensure",
        "running",
        "install_profile",
        "win-x64-cpu",
        "2026-09-21T00:00:00Z",
        progress_current=3 * 1024**3,
        progress_total=6 * 1024**3,
        progress_unit="bytes",
        message_args={"package": "torch", "elapsed_seconds": "120"},
    )
    view = build_maintenance_detail(update)
    assert (view.progress_value, view.progress_maximum) == (500, 1000)
    assert "50%" in view.detail and "torch" in view.detail and "120" in view.detail
    unknown = build_maintenance_detail(replace(update, progress_total=None))
    assert not unknown.determinate
    assert "总量未知" in unknown.detail and "3.0 GiB" in unknown.detail


def test_record_preserves_default_scope_and_explicit_base_scope(tmp_path):
    for scope in (None, (), ("mineru-cpu",)):
        record = InstallationRecord(str(uuid4()), 4, "failed", "plan", scope)
        record.save(tmp_path)
        assert InstallationRecord.read(tmp_path) == record


def test_recovery_reads_all_pages_before_terminal_snapshot(qapp, tmp_path):
    record = InstallationRecord(str(uuid4()), 3, "running", "old-plan", ())
    record.save(tmp_path)
    running = RuntimeMaintenanceUpdate(
        "progress",
        record.operation_id,
        4,
        "ensure",
        "running",
        "install_profile",
        "win-x64-cpu",
        "2026-09-21T00:00:00Z",
    )
    terminal = replace(
        running,
        sequence=5,
        operation_state="failed",
        message_args={"reason_code": "download_failed", "next_action": "check_network"},
    )
    snapshot = replace(terminal, message_args={})
    worker = InstallWorker(tmp_path)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as factory:
        client = factory.return_value
        client.observe.side_effect = [
            SimpleNamespace(
                events=(running,), snapshot=snapshot, more=True, through_sequence=4
            ),
            SimpleNamespace(
                events=(terminal,), snapshot=snapshot, more=False, through_sequence=5
            ),
        ]
        worker.run()
        assert client.observe.call_count == 2
        client.ensure.assert_not_called()
    restored = InstallationRecord.read(tmp_path)
    assert restored.sequence == 5
    assert restored.reason_code == "download_failed"
    assert restored.next_action == "check_network"


def test_failed_dialog_can_close_after_real_worker_deletion(qtbot, tmp_path):
    from vibeocr.classic.widgets.install_dialog import InstallDialog

    class FailingWorker(InstallWorker):
        def run(self):
            self.completed.emit(False, "plan expired")

    with patch("vibeocr.classic.widgets.install_dialog.InstallWorker", FailingWorker):
        dialog = InstallDialog(tmp_path)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitUntil(lambda: dialog._close_button.isVisible())
        qtbot.waitUntil(lambda: dialog._worker is None)
        assert "plan expired" in dialog._log_text.toPlainText()
        dialog.close()


def test_rejected_confirmation_persists_failure_instead_of_queued(qapp, tmp_path):
    from vibeocr.classic.runtime_maintenance import RuntimeInstallerClientError

    worker = InstallWorker(tmp_path, install_component_ids=())
    messages = []
    worker.completed.connect(lambda _ok, message: messages.append(message))
    worker.confirm_install()
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as factory:
        client = factory.return_value
        client.preview_install_plan.return_value = SimpleNamespace(
            plan_id="consumed", blockers=()
        )
        client.ensure.side_effect = RuntimeInstallerClientError(
            "already accepted", canonical_code="RUNTIME_OPERATION_ID_CONFLICT"
        )
        worker.run()
    record = InstallationRecord.read(tmp_path)
    assert record.state == "failed"
    assert record.reason_code == "RUNTIME_OPERATION_ID_CONFLICT"
    assert record.next_action == "refresh_install_plan"

    assert "重新读取安装计划" in messages[-1]


def test_expired_replay_cursor_resumes_retained_events_without_install(qapp, tmp_path):
    from vibeocr.classic.runtime_maintenance import RuntimeInstallerClientError

    record = InstallationRecord(str(uuid4()), 3, "running", "old-plan", ())
    record.save(tmp_path)
    terminal = RuntimeMaintenanceUpdate(
        "progress",
        record.operation_id,
        8,
        "ensure",
        "failed",
        "install_profile",
        "win-x64-cpu",
        "2026-09-21T00:00:00Z",
        message_args={"reason_code": "download_failed", "next_action": "check_network"},
    )
    worker = InstallWorker(tmp_path)
    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as factory:
        client = factory.return_value
        client.observe.side_effect = [
            RuntimeInstallerClientError(
                "expired",
                canonical_code="RUNTIME_CURSOR_EXPIRED",
                detail={"oldest_sequence": 8},
            ),
            SimpleNamespace(
                events=(terminal,), snapshot=terminal, more=False, through_sequence=8
            ),
        ]
        worker.run()
        assert client.observe.call_args.kwargs == {"after_sequence": 7}
        client.ensure.assert_not_called()
    assert InstallationRecord.read(tmp_path).reason_code == "download_failed"


def test_escape_preview_cancels_real_worker_without_installing(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from vibeocr.classic.widgets.install_dialog import InstallDialog

    with patch(
        "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
    ) as factory:
        client = factory.return_value
        dialog = InstallDialog(tmp_path, missing_only=True)
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitUntil(lambda: dialog._confirm_button.isVisible())
        try:
            qtbot.keyClick(dialog, Qt.Key.Key_Escape)
            qtbot.waitUntil(lambda: dialog._worker is None, timeout=2000)
            assert not dialog.isVisible()
            client.repair.assert_not_called()
            client.ensure.assert_not_called()
            assert InstallationRecord.read(tmp_path) is None
        finally:
            dialog.request_shutdown()
            qtbot.waitUntil(lambda: dialog._worker is None)


def test_escape_first_run_preview_cancels_worker_without_failure_popup(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from vibeocr.classic.widgets.backend_choice_dialog import BackendChoiceDialog

    with (
        patch(
            "vibeocr.classic.widgets.install_dialog.RuntimeInstallerClient"
        ) as factory,
        patch.object(BackendChoiceDialog, "_detect_and_set_default"),
        patch(
            "vibeocr.classic.widgets.backend_choice_dialog.QMessageBox.warning"
        ) as warning,
    ):
        dialog = BackendChoiceDialog(tmp_path, missing_only=True)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog._install_button.click()
        qtbot.waitUntil(lambda: dialog._install_button.text() == "确认并安装")
        try:
            qtbot.keyClick(dialog, Qt.Key.Key_Escape)
            qtbot.waitUntil(lambda: dialog._worker is None, timeout=2000)
            assert not dialog.isVisible()
            factory.return_value.repair.assert_not_called()
            warning.assert_not_called()
            assert InstallationRecord.read(tmp_path) is None
        finally:
            dialog.request_shutdown()
            qtbot.waitUntil(lambda: dialog._worker is None)
