"""PySide update prompt backed exclusively by Velopack."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from vibeocr.classic.services.update_coordinator import (
    UpdateApplyStatus,
    UpdateCheckStatus,
    UpdateCoordinator,
)
from vibeocr.classic.services.update_preferences import (
    REMIND_LATER_SECONDS,
    is_remind_later_active,
    save_remind_later,
    save_skip_version,
    should_skip_version,
)
from vibeocr.classic.services.update_transport import resolve_update_source_candidates
from vibeocr.classic.services.velopack_update import VelopackUpdateCoordinator
from vibeocr.classic.ui import theme

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

__all__ = ["UpdateDialog", "UpdateInfo", "UpdateService", "await_dialog"]

_CHANGELOG_COMMIT_PREFIX_RE = re.compile(
    r"^(?:feat|fix|perf|refactor|docs|chore|test|style|ci|build|revert)"
    r"(?:\([^)]+\))?!?:\s*",
    re.IGNORECASE,
)
_CHANGELOG_ORDERED_ITEM_RE = re.compile(r"^\d+[.)]\s+")
_CHANGELOG_SECTION_TITLES = {
    "added",
    "changed",
    "deprecated",
    "removed",
    "fixed",
    "security",
    "新增",
    "变更",
    "修复",
    "安全",
    "移除",
}


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    changelog: str
    file_size: int = 0


def _clean_changelog_item(text: str) -> str:
    text = text.strip().strip("#*- ")
    text = re.sub(r"^\[[ xX]\]\s+", "", text)
    text = _CHANGELOG_COMMIT_PREFIX_RE.sub("", text)
    return " ".join(text.split())


def _format_changelog_for_dialog(changelog: str, max_items: int = 10) -> str:
    items: list[str] = []
    fallback_items: list[str] = []
    for raw_line in changelog.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        is_indented = raw_line[:1].isspace()
        unordered = stripped.startswith(("- ", "* "))
        ordered_match = _CHANGELOG_ORDERED_ITEM_RE.match(stripped)
        if unordered:
            if is_indented:
                continue
            item = stripped[2:]
            target = items
        elif ordered_match:
            if is_indented:
                continue
            item = stripped[ordered_match.end() :]
            target = items
        else:
            if is_indented:
                continue
            item = stripped
            target = fallback_items
        item = _clean_changelog_item(item)
        if not item or item.casefold() in _CHANGELOG_SECTION_TITLES:
            continue
        target.append(item)
    return "\n".join(f"· {item}" for item in (items or fallback_items)[:max_items])


async def await_dialog(dialog: QDialog) -> int:
    """Await a modal dialog without nesting the qasync event loop."""

    loop = asyncio.get_event_loop()
    future: asyncio.Future[int] = loop.create_future()

    def on_finished(result_code: int) -> None:
        if not future.done():
            future.set_result(result_code)

    dialog.finished.connect(on_finished)
    dialog.show()
    try:
        return await future
    finally:
        try:
            dialog.finished.disconnect(on_finished)
        except (RuntimeError, TypeError, AttributeError):
            pass


class UpdateDialog(QDialog):
    def __init__(
        self,
        update_info: UpdateInfo,
        current_version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(420)
        self._action = "cancel"
        self._setup_ui(update_info, current_version)

    def _setup_ui(self, info: UpdateInfo, current_version: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        version_label = QLabel(
            f"当前版本: v{current_version}\n最新版本: v{info.version}"
        )
        version_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(version_label)
        changelog = _format_changelog_for_dialog(info.changelog)
        if changelog:
            changelog_label = QLabel("更新内容:")
            changelog_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(changelog_label)
            content = QLabel(changelog)
            content.setWordWrap(True)
            layout.addWidget(content)
        if info.file_size > 0:
            size = QLabel(f"更新包大小: {info.file_size / (1024 * 1024):.1f} MB")
            size.setStyleSheet(f"color: {theme.Colors.text_muted};")
            layout.addWidget(size)
        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch()
        update = QPushButton("立即更新")
        update.setDefault(True)
        update.clicked.connect(self._on_update)
        buttons.addWidget(update)
        later = QPushButton("稍后提醒")
        later.clicked.connect(self._on_later)
        buttons.addWidget(later)
        skip = QPushButton("跳过此版本")
        skip.clicked.connect(self._on_skip)
        buttons.addWidget(skip)
        layout.addLayout(buttons)

    def _on_update(self) -> None:
        self._action = "update"
        self.accept()

    def _on_later(self) -> None:
        self._action = "later"
        self.reject()

    def _on_skip(self) -> None:
        self._action = "skip"
        self.reject()

    @property
    def user_action(self) -> str:
        return self._action


class UpdateService:
    """Serialize UI checks and map Velopack results to the Classic shell."""

    _check_lock: asyncio.Lock | None = None
    _active_cancel_event: asyncio.Event | None = None
    _download_state = "idle"
    _state_listeners: list[Callable[[str], None]] = []

    @classmethod
    def _get_check_lock(cls) -> asyncio.Lock:
        if cls._check_lock is None:
            cls._check_lock = asyncio.Lock()
        return cls._check_lock

    @classmethod
    def request_cancel(cls) -> None:
        if cls._active_cancel_event is not None:
            cls._active_cancel_event.set()
            cls._set_download_state("idle")

    @classmethod
    def register_state_listener(cls, callback: Callable[[str], None]) -> None:
        cls._state_listeners.append(callback)
        callback(cls._download_state)

    @classmethod
    def unregister_state_listener(cls, callback: Callable[[str], None]) -> None:
        try:
            cls._state_listeners.remove(callback)
        except ValueError:
            pass

    @classmethod
    def _set_download_state(cls, state: str) -> None:
        cls._download_state = state
        for callback in list(cls._state_listeners):
            try:
                callback(state)
            except Exception:
                logger.exception("下载状态监听器异常")

    def __init__(
        self,
        app_dir: Path,
        status_callback: Callable[[str, int], None] | None = None,
        coordinator: UpdateCoordinator | None = None,
    ) -> None:
        self._app_dir = app_dir
        from vibeocr.classic.update_config import get_update_settings_path

        self._settings_path = get_update_settings_path()
        self._coordinator = coordinator or VelopackUpdateCoordinator(
            source_resolver=resolve_update_source_candidates
        )
        self._status_callback = status_callback

    def _status(self, text: str, timeout: int = 0) -> None:
        if self._status_callback is not None:
            self._status_callback(text, timeout)

    async def check_and_prompt(
        self,
        parent: QWidget | None = None,
        *,
        manual: bool = False,
        now: float | None = None,
    ) -> None:
        async with self._get_check_lock():
            self._status("正在检查更新…", 0)
            result = await self._coordinator.check()
            if result.status is UpdateCheckStatus.FETCH_FAILED:
                self._status(result.detail or "检查更新失败，请检查网络", 5000)
                if manual:
                    await await_dialog(
                        QMessageBox(
                            QMessageBox.Icon.Warning,
                            "检查更新",
                            result.detail or "无法连接 Velopack 更新源。",
                            QMessageBox.StandardButton.Ok,
                            parent,
                        )
                    )
                return
            if result.status is UpdateCheckStatus.LATEST:
                self._status("当前已是最新版本", 3000)
                return
            if result.status is not UpdateCheckStatus.AVAILABLE or not result.version:
                return
            if not manual and is_remind_later_active(self._settings_path, now=now):
                self._status("已暂缓更新提醒，稍后再试", 3000)
                return
            if should_skip_version(result.version, self._settings_path):
                self._status(f"已跳过版本 v{result.version}", 3000)
                return
            dialog = UpdateDialog(
                UpdateInfo(result.version, result.release_notes),
                result.current_version or "",
                parent,
            )
            await await_dialog(dialog)
            if dialog.user_action == "skip":
                save_skip_version(result.version, self._settings_path)
                return
            if dialog.user_action == "later":
                current = time.time() if now is None else now
                save_remind_later(current + REMIND_LATER_SECONDS, self._settings_path)
                return
            if dialog.user_action != "update":
                return
            await self._download_and_apply(parent)

    async def _download_and_apply(self, parent: QWidget | None) -> None:
        cls = type(self)
        cancel_event = asyncio.Event()
        cls._active_cancel_event = cancel_event
        cls._set_download_state("downloading")
        try:
            result = await self._coordinator.download_and_apply(
                lambda value: self._status(f"正在下载更新… {value}%", 0),
                cancel_event,
            )
            if result.status is UpdateApplyStatus.CANCELLED:
                self._status("已取消下载更新", 3000)
                return
            if result.status is UpdateApplyStatus.FAILED:
                await await_dialog(
                    QMessageBox(
                        QMessageBox.Icon.Critical,
                        "更新失败",
                        result.detail or "Velopack 更新失败。",
                        QMessageBox.StandardButton.Ok,
                        parent,
                    )
                )
                return
            if result.status is UpdateApplyStatus.APPLY_STARTED:
                self._status("更新已就绪，正在重启…", 0)
                self._force_quit()
        finally:
            cls._active_cancel_event = None
            cls._set_download_state("idle")

    def _force_quit(self) -> None:
        logger.info("Velopack apply 已启动，主程序退出以释放文件锁")
        try:
            asyncio.get_event_loop().stop()
        except Exception:
            pass
        time.sleep(0.1)
        os._exit(0)
