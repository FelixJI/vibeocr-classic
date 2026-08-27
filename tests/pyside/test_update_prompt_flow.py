"""Update prompt status flow and changelog rendering contract."""

from __future__ import annotations

from collections.abc import Callable

from vibeocr.classic.pyside import update as update_module
from vibeocr.classic.pyside.update import UpdateService, _format_changelog_for_dialog
from vibeocr.classic.services.update_coordinator import (
    UpdateApplyResult,
    UpdateApplyStatus,
    UpdateCheckResult,
    UpdateCheckStatus,
)


class _FakeCoordinator:
    def __init__(
        self,
        check_result: UpdateCheckResult,
        apply_result: UpdateApplyResult | None = None,
        on_apply: Callable[[Callable[[int], None]], None] | None = None,
    ) -> None:
        self._check_result = check_result
        self._apply_result = apply_result or UpdateApplyResult(
            UpdateApplyStatus.APPLY_STARTED
        )
        self._on_apply = on_apply

    async def check(self) -> UpdateCheckResult:
        return self._check_result

    async def download_and_apply(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: object | None = None,
    ) -> UpdateApplyResult:
        if self._on_apply is not None and progress is not None:
            self._on_apply(progress)
        return self._apply_result


class _FakeDialog:
    """Stands in for UpdateDialog with the user pressing 立即更新."""

    def __init__(
        self, update_info: object, current_version: str, parent: object
    ) -> None:
        self.update_info = update_info
        self.current_version = current_version
        self.user_action = "update"


async def test_status_bar_refreshes_immediately_after_user_accepts_update(
    monkeypatch, tmp_path
) -> None:
    statuses: list[tuple[str, int]] = []

    def report(progress: Callable[[int], None]) -> None:
        progress(5)

    coordinator = _FakeCoordinator(
        UpdateCheckResult(
            UpdateCheckStatus.AVAILABLE,
            current_version="0.10.9",
            version="0.10.10",
            release_notes="### Features\n\n- **runtime:** 新能力 (ca886ab)\n",
        ),
        on_apply=report,
    )

    async def _fake_await_dialog(dialog: object) -> int:
        return 1

    monkeypatch.setattr(update_module, "UpdateDialog", _FakeDialog)
    monkeypatch.setattr(update_module, "await_dialog", _fake_await_dialog)
    monkeypatch.setattr(update_module.UpdateService, "_force_quit", lambda self: None)

    service = UpdateService(
        tmp_path,
        status_callback=lambda text, timeout=0: statuses.append((text, timeout)),
        coordinator=coordinator,
    )
    # 隔离机器上的真实跳过/暂缓设置，保证状态序列确定。
    service._settings_path = tmp_path / "update_settings.json"

    await service.check_and_prompt(None, manual=True)

    # 点击「立即更新」后必须立刻离开"正在检查更新…"，且弹窗期间状态栏
    # 已反映检查结果；下载进度与重启阶段按序接管。
    assert statuses == [
        ("正在检查更新…", 0),
        ("发现新版本 v0.10.10", 0),
        ("正在准备更新…", 0),
        ("正在下载更新… 5%", 0),
        ("更新已就绪，正在重启…", 0),
    ]


def test_format_changelog_strips_scope_prefix_and_short_sha() -> None:
    notes = (
        "## 0.10.10\n"
        "\n"
        "### Features\n"
        "\n"
        "- **runtime:** 恢复中断安装后的可用引擎 (ca886ab)\n"
        "- fix(runtime): 修复首启安装范围与状态同步 (1a2b3c4)\n"
    )

    assert _format_changelog_for_dialog(notes) == (
        "· 恢复中断安装后的可用引擎\n· 修复首启安装范围与状态同步"
    )


def test_format_changelog_falls_back_to_plain_lines_without_list_items() -> None:
    assert _format_changelog_for_dialog("内部改进与维护。") == "· 内部改进与维护。"
