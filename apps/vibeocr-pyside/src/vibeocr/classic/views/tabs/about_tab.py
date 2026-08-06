# src/vibeocr/views/tabs/about_tab.py
"""关于标签页 — 展示应用元信息（卡片化布局）"""

from __future__ import annotations

import logging
import weakref
from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic import __version__
from vibeocr.classic.app_paths import (
    get_bundled_changelog_path,
    get_bundled_resources_dir,
    get_install_root,
)
from vibeocr.classic.ui import theme
from vibeocr.classic.update_config import (
    GITEE_REPO_BASE,
    GITHUB_REPO_BASE,
    get_update_progress_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_APP_NAME = "VibeOCR"
_DESCRIPTION = (
    "一款基于 PaddleOCR 的截图文字识别工具，支持表格识别、公式识别、文档解析等功能。"
)
_AUTHOR = "Felix Ji"
# 首版年份固定为 2025，当前年份运行时取系统日期，二者不同时显示为区间。
_FIRST_YEAR = 2025
_current_year = date.today().year
_year_range = (
    str(_FIRST_YEAR)
    if _current_year <= _FIRST_YEAR
    else f"{_FIRST_YEAR}–{_current_year}"
)
_COPYRIGHT = f"© {_year_range} Felix Ji. All rights reserved."
_GITHUB_URL = GITHUB_REPO_BASE
_GITEE_URL = GITEE_REPO_BASE
_CNB_URL = "https://cnb.cool/feljii/VibeOCR"
_TECH_STACK = [
    ("PaddlePaddle / PaddleX", "OCR 引擎"),
    ("MinerU", "文档解析"),
    ("PySide6", "UI 框架"),
]

_CHANGELOG_MARKDOWN_FEATURES = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    | QTextDocument.MarkdownFeature.MarkdownNoHTML
)


def _load_update_progress() -> dict | None:
    """读取上次更新的进度记录（progress.json），不存在/损坏返回 None。

    替换器（updater.exe / self-update）在替换各阶段写入耗时记录（见
    update_replacer._StageTimer），新版启动后由此读取并在关于页展示
    「上次更新各阶段耗时」，方便用户/开发者排查更新慢的瓶颈。

    首次安装或从未更新过的机器上文件不存在 → 返回 None（关于页不显示该卡片）。
    """
    path = get_update_progress_path()
    if not path.exists():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug(f"读取更新进度记录失败: {path}")
        return None


class AboutTab(QWidget):
    """关于标签页，展示应用元信息。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        status_callback: Callable[[str, int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._status_callback = status_callback
        self._setup_ui()
        self._register_update_state_listener()

    def _setup_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        # 居中容器：把 stretch 放进 scroll 的视口里，而非 scroll 外层。
        # 原实现把 scroll 包在 HBox(addStretch + scroll + addStretch) 中，
        # 但 setWidgetResizable=True 时 scroll 会吞掉全部宽度，外层 stretch
        # 失效。这里让 scroll 全宽透明，container 包一层 HBox 左右各 addStretch，
        # 宽屏下 container 才真正水平居中。
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        # 左右两栏布局：左侧品牌/详细信息/上次更新耗时卡片，右侧更新日志 + 检查更新按钮。
        # 容器宽度调到 980 以容纳两栏（旧单列布局 720px 过窄，两栏会挤）。
        container = QWidget()
        container.setMaximumWidth(980)
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(
            theme.Spacing.xxl,
            theme.Spacing.xl,
            theme.Spacing.xxl,
            theme.Spacing.xl,
        )
        container_layout.setSpacing(theme.Spacing.lg)

        # 左栏：品牌卡片 + 详细信息卡片 + 上次更新耗时卡片（如有）
        left_column = QWidget()
        left_column.setObjectName("leftColumn")
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(theme.Spacing.lg)
        left_layout.addWidget(self._create_brand_card())
        left_layout.addWidget(self._create_info_card())
        # 上次更新耗时详情卡片（仅当存在 progress.json 时显示）
        timing_card = self._create_update_timing_card()
        if timing_card is not None:
            left_layout.addWidget(timing_card)
        left_layout.addStretch()

        # 右栏：更新日志卡片 + 检查更新按钮
        right_column = QWidget()
        right_column.setObjectName("rightColumn")
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(theme.Spacing.lg)
        right_layout.addWidget(self._create_changelog_card(), stretch=1)

        # 检查更新按钮：右栏底部右对齐，与更新日志语义相邻。
        # 存为实例属性：下载期间由 _apply_download_state 切换为「取消下载」。
        update_btn = QPushButton("检查更新")
        update_btn.setFixedWidth(160)
        update_btn.setStyleSheet(theme.button_qss("primary"))
        update_btn.clicked.connect(self._on_check_update)
        right_layout.addWidget(update_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self._update_btn = update_btn

        container_layout.addWidget(left_column, stretch=1)
        container_layout.addWidget(right_column, stretch=1)

        # 视口内居中：HBox(左 stretch + container + 右 stretch)
        viewport = QWidget()
        viewport_layout = QHBoxLayout(viewport)
        viewport_layout.setContentsMargins(0, 0, 0, 0)
        viewport_layout.addStretch()
        viewport_layout.addWidget(container)
        viewport_layout.addStretch()
        viewport.setStyleSheet("background: transparent;")

        scroll.setWidget(viewport)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _create_card(self) -> tuple[QFrame, QVBoxLayout]:
        """创建一张卡片容器（QFrame + card_qss）。

        Returns:
            (card_frame, card_layout) 元组。
        """
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(theme.card_qss())
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            theme.Spacing.lg,
            theme.Spacing.lg,
            theme.Spacing.lg,
            theme.Spacing.lg,
        )
        card_layout.setSpacing(theme.Spacing.sm)
        return card, card_layout

    def _create_brand_card(self) -> QFrame:
        """品牌卡片：图标 + 应用名 + 版本徽标 + 简介。"""
        card, card_layout = self._create_card()

        logo = self._create_logo_label(96)
        if logo is not None:
            card_layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(_APP_NAME)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = name_label.font()
        f.setPointSize(theme.Typography.title)
        f.setBold(True)
        name_label.setFont(f)
        self._name_label = name_label
        card_layout.addWidget(name_label)

        # 版本药丸徽标
        version_label = QLabel(f" v{__version__} ")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(
            f"background: {theme.Colors.accent_soft}; color: {theme.Colors.accent};"
            f" border-radius: {theme.Radius.md}px;"
            f" padding: 2px {theme.Spacing.sm}px;"
            f" font-size: {theme.Typography.body}px;"
        )
        self._version_label = version_label
        card_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignCenter)

        card_layout.addSpacing(theme.Spacing.sm)
        desc_label = QLabel(_DESCRIPTION)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        card_layout.addWidget(desc_label)
        return card

    def _create_info_card(self) -> QFrame:
        """详细信息卡片：键值对。"""
        card, card_layout = self._create_card()

        title = QLabel("详细信息")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(theme.Spacing.sm)
        label_style = f"color: {theme.Colors.text_muted};"

        def make_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(label_style)
            return lbl

        tech = " · ".join(name for name, _ in _TECH_STACK)

        def make_link(url: str) -> QLabel:
            lbl = QLabel(
                f'<a href="{url}" style="color:{theme.Colors.accent};">{url}</a>'
            )
            lbl.setOpenExternalLinks(True)
            return lbl

        form.addRow(make_label("作者"), QLabel(_AUTHOR))
        form.addRow(make_label("版权"), QLabel(_COPYRIGHT))
        form.addRow(make_label("技术栈"), QLabel(tech))
        form.addRow(make_label("GitHub"), make_link(_GITHUB_URL))
        form.addRow(make_label("Gitee"), make_link(_GITEE_URL))
        form.addRow(make_label("代码镜像"), make_link(_CNB_URL))
        card_layout.addLayout(form)
        return card

    def _create_changelog_card(self) -> QFrame:
        """更新日志卡片。"""
        card, card_layout = self._create_card()

        title = QLabel("更新日志")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        self._changelog_browser = QTextBrowser()
        self._changelog_browser.setOpenExternalLinks(True)
        # 更新日志位于右栏，让其随右栏自由伸展（不再设固定最大高度，旧版单列布局时的
        # 320px 上限会压缩内容）。右栏整体填满可用高度，浏览器滚动条兜底超长内容。
        self._changelog_browser.setMinimumHeight(280)
        self._changelog_browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._changelog_browser.setStyleSheet("background: transparent;")

        # 打包态 CHANGELOG.md 由 --add-data 捆绑进 _internal/（sys._MEIPASS），
        # 早期用 get_project_root()（exe 同级）找不到，导致客户机显示"暂无更新日志"。
        # 改用 get_bundled_changelog_path 统一解析 dev/frozen 两态。
        changelog_path = get_bundled_changelog_path()
        if changelog_path is not None:
            try:
                raw = changelog_path.read_text(encoding="utf-8")
                # CHANGELOG 是纯 Markdown 文档，不需要原始 HTML。Qt 的 Markdown
                # 解析器遇到正文中的字面量 ``<br>`` 时会错误吞掉后续普通文字，
                # 只留下反引号包裹的代码片段；禁用 HTML 可保留完整正文。
                self._changelog_browser.document().setMarkdown(
                    raw, _CHANGELOG_MARKDOWN_FEATURES
                )
                # setMarkdown 后光标停在文档末尾，CHANGELOG.md 是倒序排列
                # （最新版本在顶部），需把光标移回开头，否则打开关于页默认滚到
                # 最底部，看到的反而是最旧的更新条目。
                cursor = self._changelog_browser.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self._changelog_browser.setTextCursor(cursor)
            except Exception:
                logger.exception("读取 CHANGELOG.md 失败: %s", changelog_path)
                self._changelog_browser.setMarkdown("暂无更新日志")
        else:
            self._changelog_browser.setMarkdown("暂无更新日志")
        card_layout.addWidget(self._changelog_browser)
        return card

    def _create_update_timing_card(self) -> QFrame | None:
        """上次更新耗时详情卡片。

        读取 progress.json（由替换器写入），把各阶段耗时渲染成紧凑的 HTML 表格。
        文件不存在（首次安装/未更新过）时返回 None，_setup_ui 据此跳过添加卡片。

        展示策略：
        - 顶部一行汇总：版本、成功/失败、总耗时；
        - 各阶段按 depth 缩进（子阶段缩进一级），慢阶段（≥10s）标红，失败阶段标红；
        - 用 HTML 而非 QFormLayout：阶段数不定（11~12 行），HTML 表格更紧凑可控。
        """
        data = _load_update_progress()
        if data is None or not data.get("stages"):
            return None

        card, card_layout = self._create_card()

        title = QLabel("上次更新耗时")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        # 汇总行
        version = data.get("version", "")
        success = data.get("success", False)
        total = data.get("total_seconds", 0.0)
        status_text = "成功" if success else "失败"
        status_color = theme.Colors.success if success else theme.Colors.danger
        summary = QLabel(
            f"更新到 v{version} · <span style='color:{status_color};'>{status_text}</span>"
            f" · 总耗时 {total:.1f}s"
        )
        summary.setStyleSheet(f"color: {theme.Colors.text_muted};")
        card_layout.addWidget(summary)

        # 阶段表格（HTML）
        muted = theme.Colors.text_muted
        danger = theme.Colors.danger
        text_color = theme.Colors.text
        rows_html: list[str] = []
        for stage in data["stages"]:
            depth = stage.get("depth", 0)
            name = stage.get("name", "")
            secs = stage.get("seconds", 0.0)
            is_slow = stage.get("slow", False)
            is_failed = stage.get("failed", False)
            # 子阶段缩进（depth=1 缩进 1 个 em）；顶层阶段不缩进
            indent = "&nbsp;&nbsp;&nbsp;&nbsp;" * depth
            color = danger if (is_slow or is_failed) else text_color
            flag = ""
            if is_failed:
                flag = f" <span style='color:{danger};'>[失败]</span>"
            elif is_slow:
                flag = f" <span style='color:{danger};'>[慢]</span>"
            rows_html.append(
                f"<tr>"
                f"<td style='color:{color};padding:1px 8px 1px 0;'>{indent}{name}{flag}</td>"
                f"<td style='color:{muted};text-align:right;'>{secs:.2f}s</td>"
                f"</tr>"
            )
        html = (
            f"<table style='font-size:{theme.Typography.body}px;'>"
            + "".join(rows_html)
            + "</table>"
        )
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMaximumHeight(280)
        browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        browser.setStyleSheet("background: transparent;")
        browser.setHtml(html)
        card_layout.addWidget(browser)
        return card

    @staticmethod
    def _create_logo_label(size: int = 128) -> QLabel | None:
        """创建关于页 Logo 标签。

        通过 QIcon 读取多分辨率 app_icon.ico，由其按目标尺寸自动挑选最
        合适的子图并处理 HiDPI；缺失/加载失败时返回 None（不破坏布局）。

        注：不能用 ``QPixmap(str(ico))`` 直接加载——它只读取 .ico 的第一帧
        （16×16），再放大到 ``size`` 会模糊。QIcon 才会按需挑选高分辨率
        子图（见实测：请求 96 时取 144 这一档）。
        """
        icon_path = get_bundled_resources_dir() / "app_icon.ico"
        if not icon_path.exists():
            logger.warning(f"应用图标不存在: {icon_path}")
            return None

        icon = QIcon(str(icon_path))
        pixmap = icon.pixmap(QSize(size, size))
        if pixmap.isNull():
            logger.warning(f"应用图标加载失败: {icon_path}")
            return None

        label = QLabel()
        label.setPixmap(pixmap)
        return label

    def _on_check_update(self) -> None:
        """手动检查更新"""
        import asyncio

        from PySide6.QtWidgets import QMessageBox

        async def _run():
            from vibeocr.classic.pyside.update import UpdateService

            app_dir = get_install_root()
            service = UpdateService(app_dir, status_callback=self._status_callback)
            # manual=True：用户主动点「检查更新」按钮，忽略「稍后提醒」暂缓，
            # 始终弹窗（用户主动请求即表示现在想看更新信息）。
            await service.check_and_prompt(self, manual=True)

        async def _safe():
            try:
                await _run()
            except Exception:
                # ensure_future 会静默吞掉协程异常，必须显式捕获并提示用户，
                # 否则点"检查更新"按钮出错时毫无反馈。
                logger.exception("检查更新失败")
                QMessageBox.warning(self, "检查更新", "检查更新失败，请查看日志。")

        try:
            _update_task = asyncio.ensure_future(_safe())  # noqa: RUF006
        except Exception:
            logger.exception("启动检查更新失败")

    # ------------------------------------------------------------------
    # 下载状态机：关于页按钮在 idle / downloading 间切换
    # ------------------------------------------------------------------

    def _register_update_state_listener(self) -> None:
        """注册 UpdateService 状态监听器，按钮随下载状态切换文本/样式/槽。

        用 weakref.ref(self) 包装回调：AboutTab 被回收时回调自动变 no-op，
        无需 __del__（QWidget 的 __del__ 不可靠）。
        """
        from vibeocr.classic.pyside.update import UpdateService

        self_ref = weakref.ref(self)

        def _on_state(state: str) -> None:
            tab = self_ref()
            if tab is not None:
                tab._apply_download_state(state)

        UpdateService.register_state_listener(_on_state)
        self._state_listener_fn = _on_state

    def _apply_download_state(self, state: str) -> None:
        """根据下载状态切换按钮文本/样式/连接的槽函数。

        - idle:        「检查更新」(primary) → check_and_prompt
        - downloading: 「取消下载」(danger)  → UpdateService.request_cancel

        用 blocking-signal + disconnect all + reconnect 切换槽，避免 idle 槽
        与 downloading 槽同时连接导致点「取消下载」触发两套逻辑。
        """
        btn = self._update_btn
        btn.blockSignals(True)
        try:
            btn.disconnect()  # type: ignore[call-overload]
        except (RuntimeError, TypeError):
            # 按钮首次连接时尚无连接，disconnect 会抛 RuntimeError
            pass
        btn.blockSignals(False)

        if state == "downloading":
            btn.setText("取消下载")
            btn.setStyleSheet(theme.button_qss("danger"))
            from vibeocr.classic.pyside.update import UpdateService

            btn.clicked.connect(UpdateService.request_cancel)
        else:
            btn.setText("检查更新")
            btn.setStyleSheet(theme.button_qss("primary"))
            btn.clicked.connect(self._on_check_update)
