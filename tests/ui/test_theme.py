# tests/ui/test_theme.py
"""theme 模块 token 与 QSS 生成函数测试"""

from vibeocr.classic.ui import theme


class TestColors:
    def test_all_colors_are_hex_or_rgba(self):
        for name in (
            "bg",
            "surface",
            "surface_alt",
            "text",
            "text_muted",
            "text_subtle",
            "border",
            "border_strong",
            "accent",
            "accent_hover",
            "accent_soft",
            "success",
            "danger",
        ):
            val = getattr(theme.Colors, name)
            assert val.startswith("#"), f"{name}={val} 应为十六进制"

    def test_transparent_overlays(self):
        assert theme.Colors.overlay.startswith("rgba")
        assert theme.Colors.hover_bg.startswith("#")
        assert theme.Colors.pressed_bg.startswith("#")


class TestSpacingScale:
    def test_scale_is_4_multiples(self):
        assert theme.Spacing.xs == 4
        assert theme.Spacing.sm == 8
        assert theme.Spacing.md == 12
        assert theme.Spacing.lg == 16
        assert theme.Spacing.xl == 24
        assert theme.Spacing.xxl == 32


class TestLayoutSizes:
    def test_toolbar_and_panel_dims(self):
        assert theme.Layout.toolbar_height == 48
        assert theme.Layout.panel_width == 280
        assert theme.Layout.panel_min_width == 180

    def test_shadow(self):
        assert theme.Layout.shadow_blur == 12
        assert theme.Layout.shadow_color.startswith("rgba")


class TestGlobalQss:
    def test_global_qss_returns_str(self):
        qss = theme.global_qss()
        assert isinstance(qss, str)
        assert len(qss) > 0

    def test_global_qss_covers_core_widgets(self):
        qss = theme.global_qss()
        for selector in (
            "QWidget",
            "QPushButton",
            "QLineEdit",
            "QGroupBox",
            "QTabBar::tab",
            "QProgressBar",
        ):
            assert selector in qss, f"全局 QSS 缺少 {selector}"

    def test_global_qss_uses_token_colors(self):
        qss = theme.global_qss()
        assert theme.Colors.accent in qss
        assert theme.Colors.bg in qss


class TestCardQss:
    def test_card_qss(self):
        qss = theme.card_qss()
        assert "border-radius" in qss
        assert theme.Colors.border in qss


class TestButtonQss:
    def test_primary_button(self):
        qss = theme.button_qss("primary")
        assert theme.Colors.accent in qss
        assert "QPushButton" in qss

    def test_default_button(self):
        qss = theme.button_qss("default")
        # default 返回空串（由全局 QSS 接管），仅校验类型
        assert isinstance(qss, str)

    def test_invalid_variant_raises(self):
        import pytest

        with pytest.raises(ValueError):
            theme.button_qss("nonexistent")


class TestToolbarButtonQss:
    def test_toolbar_button(self):
        qss = theme.toolbar_button_qss()
        assert "QToolButton" in qss
        assert ":hover" in qss
        assert ":checked" in qss


class TestPanelQss:
    def test_panel_qss(self):
        qss = theme.panel_qss()
        assert isinstance(qss, str)
        assert len(qss) > 0
