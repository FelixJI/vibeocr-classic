# tests/views/tabs/test_about_tab.py
"""关于标签页测试"""

import sys

import pytest

from vibeocr.classic import __version__


@pytest.fixture
def about_tab(qtbot):
    from vibeocr.classic.views.tabs.about_tab import AboutTab

    tab = AboutTab()
    qtbot.addWidget(tab)
    return tab


class TestAboutTab:
    def test_version_label_shows_current_version(self, about_tab):
        assert __version__ in about_tab._version_label.text()

    def test_app_name_displayed(self, about_tab):
        text = about_tab._name_label.text()
        assert "VibeOCR" in text

    def test_changelog_browser_exists(self, about_tab):
        assert about_tab._changelog_browser is not None

    def test_changelog_has_content(self, about_tab):
        html = about_tab._changelog_browser.toHtml()
        assert len(html) > 0

    def test_changelog_literal_html_does_not_hide_following_text(
        self, qtbot, monkeypatch, tmp_path
    ):
        """字面量 HTML 标签不能让 Qt 吞掉后续 Markdown 普通文字。"""
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n"
            "- 修复 Markdown 表格 <br>/实体丢失\n"
            "- `_read_free_vram_mb` NVML 失败时增加 "
            "`paddle.device.cuda` 二级兜底读取显存\n"
            "- `OCROptions.use_doc_unwarping` 默认改为 `False`\n",
            encoding="utf-8",
        )

        from vibeocr.classic.views.tabs import about_tab

        monkeypatch.setattr(
            about_tab,
            "get_bundled_changelog_path",
            lambda: changelog,
        )
        tab = about_tab.AboutTab()
        qtbot.addWidget(tab)

        text = tab._changelog_browser.toPlainText()
        assert "<br>" in text
        assert "NVML 失败时增加" in text
        assert "二级兜底读取显存" in text
        assert "默认改为 False" in text

    def test_left_right_columns_exist(self, about_tab):
        """左右两栏布局：左栏（品牌/信息/耗时）+ 右栏（更新日志/按钮）应同时存在。"""
        from PySide6.QtWidgets import QWidget

        left = about_tab.findChild(QWidget, "leftColumn")
        right = about_tab.findChild(QWidget, "rightColumn")
        assert left is not None, "关于页应有左栏容器（objectName=leftColumn）"
        assert right is not None, "关于页应有右栏容器（objectName=rightColumn）"

    def test_changelog_is_in_right_column(self, about_tab):
        """更新日志浏览器应位于右栏（不在左栏）。"""
        from PySide6.QtWidgets import QWidget

        right = about_tab.findChild(QWidget, "rightColumn")
        assert right is not None
        # _changelog_browser 的祖先链中应包含 right（它被 add 到右栏）
        ancestor = about_tab._changelog_browser.parentWidget()
        while ancestor is not None:
            if ancestor is right:
                break
            ancestor = ancestor.parentWidget()
        assert ancestor is right, "更新日志应位于右栏容器内"


class TestAboutTabFrozen:
    """打包态（PyInstaller frozen）回归测试。

    客户端安装后关于页显示"暂无更新日志"的根因：CHANGELOG.md 由 --add-data
    打入 sys._MEIPASS（_internal/），而旧代码用 get_project_root()（exe 同级）
    查找，永远找不到。这里模拟 frozen 态验证走 _MEIPASS 能正确读到内容。
    """

    def test_changelog_loaded_from_meipass(self, qtbot, monkeypatch, tmp_path):
        # 把假 CHANGELOG.md 放进模拟的 _MEIPASS
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [9.9.9] - 2099-01-01\n\n### Added\n- frozen-test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        from vibeocr.classic.views.tabs.about_tab import AboutTab

        tab = AboutTab()
        qtbot.addWidget(tab)

        html = tab._changelog_browser.toHtml()
        assert "9.9.9" in html, "打包态应从 _MEIPASS 读到 CHANGELOG，而非显示占位文案"
        assert "frozen-test" in html

    def test_changelog_shows_placeholder_when_absent(
        self, qtbot, monkeypatch, tmp_path
    ):
        """打包态且各处都无 CHANGELOG.md 时回退占位文案。"""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        from vibeocr.classic.views.tabs.about_tab import AboutTab

        tab = AboutTab()
        qtbot.addWidget(tab)

        text = tab._changelog_browser.toPlainText()
        assert "暂无更新日志" in text


class TestAboutTabRepoUrls:
    """关于页 URL 应来自 env_config SSOT，且指向正确的 owner/repo"""

    def test_github_url_points_to_repo_root(self):
        """关于页 GitHub 链接应指向仓库主页（无 /releases 后缀）"""
        from vibeocr.classic.views.tabs import about_tab

        assert about_tab._GITHUB_URL == "https://github.com/FelixJI/vibeocr-classic"

    def test_gitee_url_points_to_repo_root(self):
        """关于页 Gitee 链接应指向仓库主页（无 /releases 后缀）"""
        from vibeocr.classic.views.tabs import about_tab

        assert about_tab._GITEE_URL == "https://gitee.com/felixjii/vibeocr"

    def test_urls_imported_from_env_config_ssot(self):
        """URL 常量应与 Classic update_config SSOT 完全一致"""
        from vibeocr.classic.update_config import (
            GITEE_REPO_BASE,
            GITHUB_REPO_BASE,
        )
        from vibeocr.classic.views.tabs import about_tab

        assert about_tab._GITHUB_URL == GITHUB_REPO_BASE
        assert about_tab._GITEE_URL == GITEE_REPO_BASE
