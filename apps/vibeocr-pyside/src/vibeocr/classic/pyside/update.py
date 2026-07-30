"""PySide 更新对话框与更新流程编排（Qt 表现层）。

本模块是 ADR Phase 4「去 Qt 化」的落点：原 ``services/update_service.py`` 同时
承载了 UI-free 的下载/校验逻辑与 Qt 对话框/编排逻辑，后者违反了 backend→UI 的
禁止依赖方向。Phase 4 将两者物理拆分——

- 纯逻辑（版本比较、下载、SHA 校验、skip-version）留在 ``services/update_service``，
  供无 Qt 环境（如 ``env_manager`` 的同步下载编排）复用。
- Qt 表现层（``UpdateDialog``、``await_dialog``、``UpdateService`` 编排器）移到本
  模块。``vibeocr.classic.pyside`` 是 Qt 平台壳层（被排除出 backend wheel），是 Qt dialog
  + 编排的正确归属，pyside→services 是 ADR 既定的合法依赖方向。

行为与拆分前完全一致，仅物理位置变化。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from vibeocr.classic.services.update_service import (
    DOWNLOAD_REASON_CANCELLED,
    DOWNLOAD_REASON_EXCEPTION,
    DOWNLOAD_REASON_HTTP_ERROR,
    DOWNLOAD_REASON_RECOVERY_REQUIRED,
    DOWNLOAD_REASON_SHA_MISMATCH,
    DOWNLOAD_REASON_SHA_MISSING,
    REMIND_LATER_SECONDS,
    UpdateInfo,
    check_for_updates,
    download_update,
    is_remind_later_active,
    read_local_version,
    save_remind_later,
    save_skip_version,
    should_skip_version,
)
from vibeocr.classic.ui import theme

# 纯逻辑层（backend）：版本比较、下载、校验、skip-version。pyside→services 合法。
from vibeocr.classic.update_config import GITHUB_RELEASES_BASE
from vibeocr.classic.utils.qt_async import tracked_to_thread

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

__all__ = ["UpdateDialog", "UpdateService", "await_dialog"]


# ---------------------------------------------------------------------------
# 更新日志格式化（供 UpdateDialog 使用，纯字符串处理但仅 Qt 表现层需要）
# ---------------------------------------------------------------------------

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


def _clean_changelog_item(text: str) -> str:
    text = text.strip().strip("#*- ")
    text = re.sub(r"^\[[ xX]\]\s+", "", text)
    text = _CHANGELOG_COMMIT_PREFIX_RE.sub("", text)
    return " ".join(text.split())


def _format_changelog_for_dialog(changelog: str, max_items: int = 10) -> str:
    """Format release notes for the update dialog.

    Release bodies may contain detailed indented explanations intended for
    developers. The dialog should show only compact, top-level user-facing
    items so wrapped continuation lines do not become noisy blank-looking
    entries.
    """
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

    visible_items = items or fallback_items
    return "\n".join(f"· {item}" for item in visible_items[:max_items])


# ---------------------------------------------------------------------------
# 非阻塞模态对话框 await 桥
# ---------------------------------------------------------------------------


async def await_dialog(dialog: QDialog) -> int:
    """非阻塞地运行模态对话框并 await 其结果码。

    替代 ``dialog.exec()``：exec() 会跑一个嵌套 Qt 事件循环，在 qasync 下会让
    事件循环唤醒其它 asyncio 任务并对其 ``_enter_task``，而当前任务仍处于
    「已 enter」状态 → CPython ``asyncio.tasks._enter_task`` 重入保护抛
    ``RuntimeError: Cannot enter into task ... while another task ... is being
    executed``。

    改用 ``show()``（非阻塞，不跑嵌套循环）+ ``finished`` 信号桥到
    ``asyncio.Future``：整个过程中外层 qasync 事件循环正常转动，其它任务可正常
    enter/leave，不再触发重入。返回值与 ``dialog.exec()`` 一致（结果码）。

    ``QDialog`` 及其子类（含 ``QMessageBox``）都有 ``finished(int)`` 信号，
    关闭路径（点按钮、标题栏 X、ESC）都会触发它，故 Future 总能 resolve。

    防护：_on_finished 检查 fut.done()，避免 Future 被取消后迟到的
    finished 信号 set_result 到已完成 Future（InvalidStateError）；
    finally 中断开信号连接避免泄漏。
    """
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[int] = loop.create_future()

    def _on_finished(result_code: int) -> None:
        if not fut.done():
            fut.set_result(result_code)

    dialog.finished.connect(_on_finished)
    dialog.show()
    try:
        return await fut
    finally:
        try:
            dialog.finished.disconnect(_on_finished)
        except (RuntimeError, TypeError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# 更新提示对话框
# ---------------------------------------------------------------------------


class UpdateDialog(QDialog):
    """更新提示对话框"""

    def __init__(
        self,
        update_info: UpdateInfo,
        current_version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(420)
        self._action: str = "cancel"
        self._setup_ui(update_info, current_version)

    def _setup_ui(self, info: UpdateInfo, current_version: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        version_label = QLabel(
            f"当前版本: v{current_version}\n最新版本: v{info.version}"
        )
        version_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(version_label)

        changelog_text = _format_changelog_for_dialog(info.changelog)
        if changelog_text:
            changelog_label = QLabel("更新内容:")
            changelog_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(changelog_label)

            cl_label = QLabel(changelog_text)
            cl_label.setWordWrap(True)
            layout.addWidget(cl_label)

        if info.file_size > 0:
            size_mb = info.file_size / (1024 * 1024)
            size_label = QLabel(f"更新包大小: {size_mb:.1f} MB")
            size_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
            layout.addWidget(size_label)

        layout.addSpacing(8)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._update_btn = QPushButton("立即更新")
        self._update_btn.setDefault(True)
        self._update_btn.clicked.connect(self._on_update)
        btn_layout.addWidget(self._update_btn)

        later_btn = QPushButton("稍后提醒")
        later_btn.clicked.connect(self._on_later)
        btn_layout.addWidget(later_btn)

        skip_btn = QPushButton("跳过此版本")
        skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(skip_btn)

        layout.addLayout(btn_layout)

    def _on_update(self) -> None:
        self._action = "update"
        self.accept()

    def _on_later(self) -> None:
        # 「稍后提醒」：语义化 action="later"，由 check_and_prompt 持久化为
        # remind_later_until（暂缓 1 天）。早期版本误用 "cancel"（与 reject 的结果码
        # 语义混淆），且 check_and_prompt 不处理 "cancel" 分支，导致按钮无任何效果。
        self._action = "later"
        self.reject()

    def _on_skip(self) -> None:
        self._action = "skip"
        self.reject()

    @property
    def user_action(self) -> str:
        return self._action


# ---------------------------------------------------------------------------
# 失败原因 → 用户文案映射
# ---------------------------------------------------------------------------
# reason → 进度框换源提示里的简短短语
_DOWNLOAD_REASON_HINTS: dict[str, str] = {
    DOWNLOAD_REASON_HTTP_ERROR: "连接失败",
    DOWNLOAD_REASON_SHA_MISSING: "缺少校验文件",
    DOWNLOAD_REASON_SHA_MISMATCH: "校验失败",
    DOWNLOAD_REASON_EXCEPTION: "失败",
    DOWNLOAD_REASON_RECOVERY_REQUIRED: "需要人工恢复",
}


def _format_failure_message(fail_reasons: list[str]) -> str:
    """把各源失败原因汇总成给用户的分桶文案，按「最坏」原因决定主语义。

    优先级：完整性校验失败 > 缺少校验文件 > 连接/异常。
    这样镜像被篡改/损坏（sha_mismatch）会优先明确告知用户，
    而不是淹没在「网络问题」里——避免装作成功式的敷衍。
    """
    manual_url = GITHUB_RELEASES_BASE
    tail = f"\n\n如持续失败，可前往手动下载（覆盖安装前请先退出本程序）：\n{manual_url}"

    if DOWNLOAD_REASON_RECOVERY_REQUIRED in fail_reasons:
        return (
            "检测到上次更新留下的人工恢复备份。为避免覆盖唯一备份，已停止下载。\n"
            "请先按更新日志中的恢复提示处理 data/cache/update/_backup。"
        )
    if DOWNLOAD_REASON_SHA_MISMATCH in fail_reasons:
        return (
            "更新包完整性校验失败，下载源文件可能损坏或被篡改。\n"
            "请稍后重试，或手动下载。" + tail
        )
    if DOWNLOAD_REASON_SHA_MISSING in fail_reasons:
        return "服务端缺少 SHA256 校验文件，更新暂不可用。请稍后重试。" + tail
    # 全是连接/异常类
    return "下载更新包失败（无法连接服务器）。请检查网络后重试。" + tail


# ---------------------------------------------------------------------------
# UpdateService 编排器
# ---------------------------------------------------------------------------


class UpdateService:
    """应用更新服务编排器"""

    # check_and_prompt 的进程级互斥锁（类属性，跨实例共享）。
    #
    # 两个调用点各自 ensure_future 起 check_and_prompt——
    #   1) main._check_update：frozen 态 loop.call_later(5) 启动自动检查；
    #   2) AboutTab._on_check_update：用户点「检查更新」按钮。
    # 用类级（而非实例级）锁：两处调用点各 new 出独立 UpdateService 实例，
    # 实例锁无法互斥；必须进程级共享。惰性创建：asyncio.Lock() 在构造时
    # 绑定当前事件循环，模块 import 阶段尚无运行循环，故延后到首次使用。
    #
    # 注：历史根因「Cannot enter into task ...」重入错误已由 await_dialog
    # （非阻塞模态）根治，此锁现仅负责串行化两个调用点，避免并发弹出两个对话框。
    _check_lock: asyncio.Lock | None = None

    # --- 下载阶段类级共享状态（跨实例，因两调用点各 new 独立实例）---
    #
    # _active_cancel_event：当前活跃下载的取消令牌。下载开始时 set 为新 Event，
    #   结束（成功/取消/异常）清回 None。关于页「取消下载」按钮调 request_cancel
    #   → 此 event.set() → 下载协程在各检查点中止。
    # _download_state："idle" | "downloading"。驱动关于页按钮状态机（检查更新 ↔
    #   取消下载）。
    # _state_listeners：状态变更回调（关于页注册）。监听器用弱引用包装，AboutTab
    #   被回收时自动变 no-op，无需显式注销。
    _active_cancel_event: asyncio.Event | None = None
    _download_state: str = "idle"
    _state_listeners: list[Callable[[str], None]] = []

    @classmethod
    def _get_check_lock(cls) -> asyncio.Lock:
        """惰性创建进程级互斥锁。绑定首次调用时的运行事件循环（qasync）。"""
        if cls._check_lock is None:
            cls._check_lock = asyncio.Lock()
        return cls._check_lock

    @classmethod
    def request_cancel(cls) -> None:
        """关于页「取消下载」按钮调用。

        None 守卫防 idle 态竞态（无活跃下载时安全跳过）。

        **立即切 idle**：除 set 取消 event 外，同步把 ``_download_state`` 切回
        ``"idle"``。历史 bug：旧版只 set event，按钮从「取消下载」切回「检查更新」
        完全依赖 ``_do_download_and_update`` 协程跑到 ``finally``——若下载正卡在
        SHA 预检（httpx get 不接受协作取消，read timeout 15s）或 verify_sha256
        （to_thread 不可中断的 170MB 哈希），按钮要等这些阻塞段结束才变。此处
        在用户点击瞬间立即切 idle，按钮即时响应；下游协程随后取消/返回时 finally
        再 set idle（幂等无害）。

        注：idle 语义是「按钮可点检查更新」，此时旧协程仍在收尾——若用户立刻又点
        「检查更新」，会 new 一个 UpdateService 实例跑 check_and_prompt，受
        ``_check_lock`` 串行化（与现状一致），不会并发。
        """
        if cls._active_cancel_event is not None:
            cls._active_cancel_event.set()
            cls._set_download_state("idle")

    @classmethod
    def register_state_listener(cls, fn: Callable[[str], None]) -> None:
        """注册下载状态监听器，并立即同步当前状态。

        立即同步是关键：AboutTab 懒加载，若启动自动检查已触发下载，用户稍后才
        打开关于页，此时只订阅未来变更会错过当前状态。注册即调 fn(当前 state)
        确保按钮初始就正确。
        """
        cls._state_listeners.append(fn)
        fn(cls._download_state)

    @classmethod
    def unregister_state_listener(cls, fn: Callable[[str], None]) -> None:
        try:
            cls._state_listeners.remove(fn)
        except ValueError:
            pass

    @classmethod
    def _set_download_state(cls, state: str) -> None:
        cls._download_state = state
        for fn in list(cls._state_listeners):
            try:
                fn(state)
            except Exception:
                logger.exception("下载状态监听器异常")

    def __init__(
        self,
        app_dir: Path,
        status_callback: Callable[[str, int], None] | None = None,
    ) -> None:
        self._app_dir = app_dir
        self._version_json_path = app_dir / "version.json"
        self._updater_path = (
            app_dir / "updater.exe" if os.name == "nt" else app_dir / "updater"
        )
        from vibeocr.classic.update_config import (
            get_update_cache_dir,
            get_update_settings_path,
        )

        self._cache_dir = get_update_cache_dir()
        self._settings_path = get_update_settings_path()
        # 状态栏文本回调（复用 main_window 的 self._statusbar.showMessage 约定，
        # 与 ClipboardController / SettingsPageController 一致）。无 callback 时
        # （如单测）_status 静默跳过。
        self._status_callback = status_callback

    def _status(self, text: str, timeout: int = 0) -> None:
        """状态栏文本薄包装：无 callback 时静默跳过。"""
        if self._status_callback is not None:
            self._status_callback(text, timeout)

    @staticmethod
    def _fmt_progress(downloaded: int, total: int) -> str:
        dl = downloaded / (1024 * 1024)
        if total > 0:
            pct = int(downloaded / total * 100)
            tot = total / (1024 * 1024)
            return f"正在下载更新 {dl:.1f} / {tot:.1f} MB ({pct}%)"
        return f"正在下载更新 {dl:.1f} MB"

    def _make_progress_cb(self) -> Callable[[int, int], None]:
        """构造下载进度回调：写入状态栏纯文本（替代 DownloadProgressDialog）。"""

        def progress_cb(downloaded: int, total: int) -> None:
            self._status(self._fmt_progress(downloaded, total), 0)

        return progress_cb

    def _make_source_switch_cb(self) -> Callable[[str, str], None]:
        """构造换源回调：状态栏显示「X 失败，切换备用源…」。"""

        def on_source_switch(source_name: str, reason: str) -> None:
            hint = _DOWNLOAD_REASON_HINTS.get(reason, "失败")
            self._status(f"正在下载更新…（{source_name} {hint}，正在切换备用源…）", 0)

        return on_source_switch

    async def check_and_prompt(
        self,
        parent: QWidget | None = None,
        *,
        manual: bool = False,
        now: float | None = None,
    ) -> None:
        """异步检查更新并提示用户

        临界区（网络拉取 + 模态对话框）受类级 ``_check_lock`` 保护，串行化两个
        调用点（启动自动检查、关于页按钮检查），避免并发弹出两个对话框。

        所有模态对话框（``QMessageBox`` / ``UpdateDialog``）经 ``await_dialog``
        非阻塞 await（而非 ``exec()``），避免 qasync 嵌套事件循环触发 asyncio
        ``_enter_task`` 重入 ``RuntimeError``（详见 ``await_dialog`` 文档）。

        Args:
            manual: True 表示用户主动点「检查更新」按钮触发。手动检查始终弹窗，
                忽略「稍后提醒」暂缓（用户主动请求）；自动检查（False）命中暂缓
                窗口则静默跳过。修复历史 bug：原「稍后提醒」按钮无任何持久化，
                点击后下次检查立刻再次弹窗。
            now: 当前时间戳，仅用于测试注入固定时刻判定暂缓窗口；None 取
                ``time.time()``。
        """
        async with self._get_check_lock():
            self._status("正在检查更新…", 0)
            current = read_local_version(self._version_json_path)
            if current == "0.0.0":
                logger.debug("无法读取本地版本，跳过更新检查")
                self._status("无法读取当前版本，已跳过更新检查", 5000)
                return

            update_info, fetch_ok = await check_for_updates(current)

            # 自动检查失败：提示用户去下载页手动下载并覆盖安装（需先退出程序）。
            if not fetch_ok:
                self._status("检查更新失败，请检查网络", 5000)
                manual_url = GITHUB_RELEASES_BASE
                await await_dialog(
                    QMessageBox(
                        QMessageBox.Icon.Warning,
                        "检查更新",
                        "自动检查更新失败，可能是网络问题。\n\n"
                        "可前往 GitHub 手动下载对应版本，"
                        "覆盖安装前请先退出本程序：\n"
                        f"{manual_url}",
                        QMessageBox.StandardButton.Ok,
                        parent,
                    )
                )
                return

            if update_info is None:
                self._status("当前已是最新版本", 3000)
                return

            # 自动检查（非用户主动）命中「稍后提醒」暂缓窗口：静默跳过。
            # 手动检查（manual=True）忽略暂缓——用户主动点按钮即表示现在想看。
            if not manual and is_remind_later_active(self._settings_path, now=now):
                logger.debug("更新提醒处于「稍后提醒」暂缓窗口内，跳过自动弹窗")
                self._status("已暂缓更新提醒，稍后再试", 3000)
                return

            if should_skip_version(update_info.version, self._settings_path):
                logger.debug(f"用户已跳过版本 {update_info.version}")
                self._status(f"已跳过版本 v{update_info.version}", 3000)
                return

            dialog = UpdateDialog(update_info, current, parent)
            await await_dialog(dialog)

            if dialog.user_action == "skip":
                save_skip_version(update_info.version, self._settings_path)
                return

            if dialog.user_action == "later":
                # 「稍后提醒」：持久化暂缓到期时间戳，1 天内自动检查不再弹窗。
                # （手动检查仍弹——见上文 manual 分支。）
                current_now = time.time() if now is None else now
                save_remind_later(
                    current_now + REMIND_LATER_SECONDS, self._settings_path
                )
                self._status("将在明天再次提醒", 3000)
                return

            if dialog.user_action == "update":
                await self._do_download_and_update(update_info, parent)

    async def _do_download_and_update(
        self, info: UpdateInfo, parent: QWidget | None
    ) -> None:
        # 重试上限，防用户连点导致无限下载循环；用户可在失败框主动取消。
        max_attempts = 3
        cls = type(self)
        # 进程内取消令牌：关于页「取消下载」按钮 → request_cancel() → set 此 event →
        # download_update / _download_zip_with_sha 检查后中止下载。
        # 用 asyncio.Event 而非 threading.Event：下载是 async 协程，Event 在
        # 同一事件循环内 set/is_set 无需锁，且 is_set() 在协程 await 点自然可见。
        # 存到类级 _active_cancel_event：两调用点各 new 独立实例，关于页需跨实例
        # 访问当前活跃下载的取消令牌。
        cancel_event = asyncio.Event()
        cls._active_cancel_event = cancel_event
        cls._set_download_state("downloading")
        # 回调在循环外创建一次（不依赖每次迭代的对话框），闭包内无循环变量。
        progress_cb = self._make_progress_cb()
        switch_cb = self._make_source_switch_cb()
        try:
            for _attempt in range(1, max_attempts + 1):
                # 重试入口检查取消（上一次重试框用户可能已点取消并触发 set）
                if cancel_event.is_set():
                    self._status("已取消下载更新", 3000)
                    return
                zip_path, fail_reasons = await download_update(
                    info,
                    self._cache_dir,
                    progress_callback=progress_cb,
                    source_switch_callback=switch_cb,
                    cancel_event=cancel_event,
                )

                # 用户主动取消：直接退出整个更新流程，不弹重试框、不弹任何后续消息。
                # 判定双保险：cancel_event.is_set()（按钮触发）或 fail_reasons 含 cancelled
                # （download_update 因 event 跳出多源循环返回的语义原因）。
                if cancel_event.is_set() or (
                    zip_path is None and DOWNLOAD_REASON_CANCELLED in fail_reasons
                ):
                    logger.info("用户取消下载，退出更新流程")
                    self._status("已取消下载更新", 3000)
                    return

                if zip_path is not None:
                    break

                # 全失败：按真实原因分桶，给出重试 / 取消
                msg = _format_failure_message(fail_reasons)
                retry_btn = QMessageBox.StandardButton.Retry
                cancel_btn = QMessageBox.StandardButton.Cancel
                reply = await await_dialog(
                    QMessageBox(
                        QMessageBox.Icon.Warning,
                        "更新失败",
                        msg,
                        retry_btn | cancel_btn,
                        parent,
                    )
                )
                if reply != retry_btn:
                    return
            else:
                # 重试用尽仍未成功
                self._status("下载更新失败，请稍后重试", 5000)
                return

            # 下载成功：立即切回 idle + 清取消令牌。后续 testzip/extract/launch 期间
            # 关于页按钮应已恢复「检查更新」，不能误显「取消下载」（否则点击会 set
            # 一个无人检查的 event，且按钮语义与实际状态不符）。
            cls._active_cancel_event = None
            cls._set_download_state("idle")

            self._status("更新已就绪，请在弹出的提示中重启安装", 5000)
            reply = await await_dialog(
                QMessageBox(
                    QMessageBox.Icon.Information,
                    "更新已下载",
                    "更新包已下载完成，点击确定重启应用以完成更新。",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    parent,
                )
            )
            if reply != QMessageBox.StandardButton.Ok:
                return

            # 新架构（黄金法则）：旧主程序只"递送"——testzip + 抽取新 updater，
            # 由新 updater（新代码）完成部署。旧主程序不解释新格式。
            #
            # 下载完成后的 testzip → extract → handshake 各阶段虽已 tracked_to_thread
            # 派发（不冻结事件循环），但此前无任何 UI 反馈：用户点「确定」后状态栏
            # 残留的「更新已就绪」5 秒后自动清空，随后最长 15 秒空白（onefile updater
            # 解压 + Python 初始化 + 杀软扫描），用户无法区分「正在工作」与「卡死」。
            # 各阶段前发持久状态栏消息（timeout=0 不自动清空）消除这一感知性卡死。
            # 1. testzip 确保能安全读出 updater 条目
            # 经 tracked_to_thread 派发：testzip 同步读整个 zip（~50-170MB）做 CRC 校验，
            # 在 qasync 事件循环里直接调用会冻结 UI（历史 bug：下载完成后无响应退出）。
            # 与 _download_zip_with_sha 里 verify_sha256 的处理一致。
            self._status("正在校验更新包完整性…", 0)
            if not await tracked_to_thread(self._verify_zip_integrity, zip_path):
                await await_dialog(
                    QMessageBox(
                        QMessageBox.Icon.Critical,
                        "更新失败",
                        "更新包已损坏（zip 校验失败）。\n\n请重新检查更新或手动下载最新版：\n"
                        f"{GITHUB_RELEASES_BASE}",
                        QMessageBox.StandardButton.Ok,
                        parent,
                    )
                )
                return

            # 2. 从 zip 抽取新 updater 到暂存目录 data/cache/update/updater.exe
            # 经 tracked_to_thread 派发：zf.read + write_bytes 是同步 I/O，与 testzip 同属
            # 下载后冻结事件循环的嫌疑点（见 1. testzip 注释）。updater.exe 虽仅 ~8-12MB，
            # 但在 qasync 协程里同步读写仍会阻塞，统一上 to_thread 保持一致。
            self._status("正在准备更新器…", 0)
            try:
                staged_updater = await tracked_to_thread(
                    self._extract_updater_from_zip, zip_path
                )
            except RuntimeError as e:
                await await_dialog(
                    QMessageBox(
                        QMessageBox.Icon.Critical,
                        "更新失败",
                        f"{e}\n\n请手动下载最新版，覆盖安装前请先退出本程序：\n"
                        f"{GITHUB_RELEASES_BASE}",
                        QMessageBox.StandardButton.Ok,
                        parent,
                    )
                )
                return

            # 3. 启动暂存的新 updater，握手确认它"活着"再退出。
            # 握手两态：ready → 确认接管 → 退出释放文件锁。
            #          crashed → 未接管/超时 → 保留旧程序并提示（不走 self-update 兜底，
            #                        因 self-update 本身违反黄金法则：旧主程序代码部署新代码）。
            # onefile updater 解压 + Python 初始化在慢机/杀软扫描下可达数秒~15s
            # （_HANDSHAKE_TIMEOUT 上限）。持久消息让用户知道主程序正在等更新器接管。
            self._status("正在启动更新器，即将重启…", 0)
            result = await self._launch_updater(zip_path, staged_updater)
            if result == "ready":
                self._force_quit()

            # 新 updater 确认崩溃 → 提示手动重装（无 self-update 兜底）。
            logger.warning("新 updater 握手失败（crashed），提示用户手动重装")
            await await_dialog(
                QMessageBox(
                    QMessageBox.Icon.Critical,
                    "更新失败",
                    "更新助手无法启动。\n\n请手动下载最新版，覆盖安装前请先退出本程序：\n"
                    f"{GITHUB_RELEASES_BASE}",
                    QMessageBox.StandardButton.Ok,
                    parent,
                )
            )
        finally:
            # 兜底：异常路径确保取消令牌与按钮状态复位（成功路径已在上文提前
            # 切 idle，此处 set idle 幂等无害；_force_quit 经 os._exit 不返回时 finally
            # 不执行，但此时进程已退出，状态复位无意义）。
            cls._active_cancel_event = None
            cls._set_download_state("idle")

    def _verify_zip_integrity(self, zip_path: Path) -> bool:
        """校验 zip 完整性（testzip），确保能安全读出 updater 条目。

        旧主程序作为"递送员"，只做这个通用校验（不违反黄金法则——testzip 是格式
        无关的完整性检查）。真正的 SHA256 完整性校验留给新 updater（新代码校验
        自己要部署的包）。

        Args:
            zip_path: 已下载的更新包 zip。

        Returns:
            True 表示 zip 结构完整可读；False 表示损坏/不存在。
        """
        if not zip_path.exists():
            logger.error(f"zip 文件不存在: {zip_path}")
            return False
        import zipfile

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    logger.error(f"zip 损坏，损坏条目: {bad}")
                    return False
            return True
        except zipfile.BadZipFile:
            logger.error(f"无效 zip 文件: {zip_path}")
            return False

    def _extract_updater_from_zip(self, zip_path: Path) -> Path:
        """从 zip 按 arcname 抽取新 updater 到暂存目录。

        新架构（黄金法则）核心：旧主程序不解压整包、不解释新格式，只把新版 updater
        从 zip 里取出来放到 ``data/cache/update/updater.exe``，由它（新代码）完成部署。

        zip 内 updater 在 ``VibeOCR/updater.exe``（与 VibeOCR.exe 同层，一层 VibeOCR/ 根目录）。
        只抽这一个条目，不解压整包（避免与 updater 端 extract 重复 I/O）。

        Args:
            zip_path: 已下载并通过 testzip 的更新包 zip。

        Returns:
            暂存 updater 路径 ``self._cache_dir / "updater.exe"``。

        Raises:
            RuntimeError: zip 内找不到 ``VibeOCR/updater.exe`` 条目。
        """
        import zipfile

        arcname = "VibeOCR/updater.exe"
        dest = self._cache_dir / "updater.exe"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # 先确认条目存在（namelist 比 getinfo 容错好）
                if arcname not in zf.namelist():
                    raise RuntimeError(
                        f"更新包内未找到 {arcname}，无法提取更新器。请手动下载最新版重装。"
                    )
                # zf.read 一次性读入内存——updater.exe 是 onefile 约 8-12MB，可接受。
                # 不用 extract(member)（会按 arcname 写到 cache_dir/VibeOCR/updater.exe），
                # 而是直接写到目标路径 cache_dir/updater.exe（扁平化）。
                dest.write_bytes(zf.read(arcname))
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"提取更新器失败: {e}") from e
        logger.info(f"已提取新 updater 到暂存目录: {dest}")
        return dest

    def _force_quit(self) -> None:
        """强制退出主程序，把 VibeOCR.exe 及 _internal/*.dll 的文件锁释放给替换器。

        不能用 ``sys.exit``：本方法运行于 qasync 调度的 asyncio Task 内
        （``_do_download_and_update`` 由 ``asyncio.ensure_future`` 挂载）。``SystemExit``
        被 Task 当成普通异常吞进 ``Task.exception()``，进程不会终止——日志中
        「删除 VibeOCR.exe 失败: WinError 5」的根因正是主程序没退出、文件锁未释放，
        替换器在锁定状态下替换必然失败且回滚也失败。

        用 ``os._exit(0)`` 跳过解释器常规关闭流程（与 main.launch_application 末尾的
        ``os._exit(0)`` 一致），确保进程立即终止、句柄立刻释放。Qt 对象不析构无妨：
        替换器会覆盖整个应用目录，旧实例的资源回收没有意义。
        """
        logger.info("握手成功，主程序退出以释放文件锁，交给替换器完成更新")
        # 先尝试关闭事件循环，给 Qt/asyncio 一个快速收尾机会；再 os._exit 兜底。
        try:
            loop = asyncio.get_event_loop()
            loop.stop()
        except Exception:
            pass
        # 短暂让出 CPU，确保子进程（替换器）已真正接管；随后硬退出。
        time.sleep(0.1)
        os._exit(0)

    # 握手超时（秒）：替换器需在此窗口内写出就绪信号文件。
    # onefile 解压 + Python 初始化在慢机器上可能数秒，给 15s 余量。
    _HANDSHAKE_TIMEOUT = 15.0
    _HANDSHAKE_POLL_INTERVAL = 0.2

    async def _launch_updater(self, zip_path: Path, staged_updater: Path) -> str:
        """启动暂存的新 updater 并握手确认安全接管。返回握手两态。

        新架构：启动目标是暂存目录的新 updater（由 _extract_updater_from_zip 抽取），
        而非 app_dir 的旧 updater。新 updater 是新代码，负责部署新版本（黄金法则）。
        """
        if not staged_updater.exists():
            logger.error(f"暂存 updater 不存在: {staged_updater}")
            return "crashed"

        return await self._handshake_launch(
            exe_path=staged_updater,
            extra_args=[
                "--update",
                str(zip_path),
                "--app-dir",
                str(self._app_dir),
                "--entry",
                "VibeOCR.exe",
                "--health-file",
                str(self._cache_dir / "startup.health"),
            ],
            ready_filename="updater.ready",
            label="updater.exe (staged)",
        )

    async def _handshake_launch(
        self,
        exe_path: Path,
        extra_args: list[str],
        ready_filename: str,
        label: str,
    ) -> str:
        """通用握手启动：清理旧 ready → 启动进程 → 轮询 ready 文件 + 进程存活。

        - ``"ready"``：就绪信号文件出现 → 替换器确认活着，调用方 sys.exit 放心。
        - ``"crashed"``：进程退出或超时且无就绪信号 → 未接管，保留旧程序并提示。

        Args:
            exe_path: 要启动的替换器 exe（暂存目录的新 updater.exe）。
            extra_args: 传给 exe 的参数（不含 exe 本身）。
            ready_filename: 替换器写出的就绪信号文件名（updater.ready）。
            label: 日志/UI 中的人类可读标签。
        """
        ready_path = self._cache_dir / ready_filename
        try:
            ready_path.unlink(missing_ok=True)  # 清理上次残留，避免读到旧信号误判
        except OSError:
            pass

        detached = 0x8 if os.name == "nt" else 0
        cmd = [str(exe_path), *extra_args]
        logger.info(f"启动 {label}：{cmd}")
        try:
            proc = subprocess.Popen(cmd, creationflags=detached)
        except OSError as e:
            logger.error(f"启动 {label} 失败: {e}")
            return "crashed"

        # 轮询放后台线程，主事件循环不阻塞。
        return await tracked_to_thread(
            self._poll_ready, proc, ready_path, label, self._HANDSHAKE_TIMEOUT
        )

    def _poll_ready(
        self, proc: subprocess.Popen, ready_path: Path, label: str, timeout: float
    ) -> str:
        """阻塞轮询 ready 文件 + 进程存活，返回两态（见 _handshake_launch 文档）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready_path.exists():
                logger.info(f"{label} 握手成功（就绪信号已收到）")
                return "ready"
            if proc.poll() is not None:
                # 进程已退出且无就绪信号：替换器崩溃/起不来，确认坏了。
                logger.warning(
                    f"{label} 启动后立即退出（退出码 {proc.returncode}），确认握手失败"
                )
                return "crashed"
            time.sleep(self._HANDSHAKE_POLL_INTERVAL)
        # 没有真实 ready 就绝不能退出旧程序。终止仍在 pre-ready 阶段的 updater，
        # 让旧 UI 保持可用；下一次重试会重新校验完整包。
        logger.warning(
            f"{label} 握手超时（{timeout}s 未收到就绪信号，但进程仍在运行），"
            "终止 updater 并保留旧程序"
        )
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return "crashed"
