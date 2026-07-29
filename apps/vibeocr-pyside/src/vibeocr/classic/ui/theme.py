# src/vibeocr/ui/theme.py
"""唯一设计 token 源 + QSS 生成器（浅色主题）。

所有颜色、间距、圆角、字号、布局尺寸在此集中定义，QSS 通过 f-string 引用
token 生成，确保全应用配色一致。
"""

from __future__ import annotations


class Colors:
    """语义色 token（浅色单一套）"""

    # 背景层
    bg = "#f3f4f6"
    surface = "#ffffff"
    surface_alt = "#f9fafb"

    # 文字
    text = "#1f2937"
    text_muted = "#6b7280"
    text_subtle = "#9ca3af"

    # 边框
    border = "#e5e7eb"
    border_strong = "#d1d5db"

    # 强调
    accent = "#0078d4"
    accent_hover = "#106ebe"
    accent_soft = "#e3f2fd"

    # 语义
    success = "#107c10"
    success_hover = "#0b6a0b"
    warning = "#f7630c"
    danger = "#c83232"
    danger_hover = "#d6550a"

    # 透明叠加
    overlay = "rgba(0,0,0,0.30)"
    hover_bg = "#e8e8e8"
    pressed_bg = "#dcdcdc"


class Spacing:
    """间距 scale（4 的倍数）"""

    xs, sm, md, lg, xl, xxl = 4, 8, 12, 16, 24, 32


class Radius:
    sm, md, lg = 4, 6, 8


class Typography:
    title = 24
    h1 = 16
    body = 14
    small = 12
    caption = 11
    weight_bold = 700
    weight_medium = 500


class Shadow:
    blur, offset_y, color = 12, 2, "rgba(0,0,0,0.08)"


class Layout:
    """布局尺寸 token（承接原 EditorStyles/InlineStyles 的尺寸常量）"""

    toolbar_height = 48
    panel_width = 280
    panel_min_width = 180
    shadow_blur = 12
    shadow_offset_y = 2
    shadow_color = "rgba(0,0,0,0.15)"


def global_qss() -> str:
    """全局基础样式（控件级），由 main.py 加载一次。"""
    c, s, r, t = Colors, Spacing, Radius, Typography
    return f"""
    QWidget        {{ background: {c.bg}; color: {c.text}; font-size: {t.body}px; }}
    QToolTip       {{ background: {c.text}; color: {c.surface}; border: none;
                     padding: {s.xs}px; border-radius: {r.sm}px; }}

    QPushButton    {{ background: {c.surface}; color: {c.text};
                     border: 1px solid {c.border}; border-radius: {r.md}px;
                     padding: 6px 14px; }}
    QPushButton:hover    {{ background: {c.hover_bg}; border-color: {c.border_strong}; }}
    QPushButton:pressed  {{ background: {c.pressed_bg}; }}
    QPushButton:disabled {{ color: {c.text_subtle}; background: {c.surface_alt};
                            border-color: {c.border}; }}

    QLineEdit, QSpinBox, QFontComboBox, QComboBox {{
        background: {c.surface}; color: {c.text};
        border: 1px solid {c.border}; border-radius: {r.sm}px; padding: 4px 8px;
    }}
    QLineEdit:focus, QSpinBox:focus, QFontComboBox:focus, QComboBox:focus {{
        border-color: {c.accent};
    }}
    QComboBox QAbstractItemView {{ background: {c.surface};
        selection-background-color: {c.accent_soft}; selection-color: {c.text}; }}

    QGroupBox {{ background: {c.surface}; border: 1px solid {c.border};
                 border-radius: {r.md}px; margin-top: {s.md}px;
                 padding-top: {s.sm}px; }}
    QGroupBox::title {{ color: {c.text_muted}; subcontrol-origin: margin;
                        left: {s.sm}px; padding: 0 {s.xs}px; }}

    QListWidget, QScrollArea {{ background: {c.surface}; border: 1px solid {c.border}; }}
    QListWidget::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
    QListWidget::item:hover    {{ background: {c.surface_alt}; }}

    QTabWidget::pane   {{ border: 1px solid {c.border}; top: -1px; }}
    QTabBar::tab       {{ padding: 8px {s.lg}px; border: 1px solid {c.border};
                          border-bottom: none; background: {c.surface_alt};
                          border-top-left-radius: {r.sm}px;
                          border-top-right-radius: {r.sm}px; }}
    QTabBar::tab:selected {{ background: {c.surface};
                              border-bottom: 2px solid {c.accent}; }}

    QProgressBar       {{ background: {c.surface_alt}; border: 1px solid {c.border};
                          border-radius: {r.sm}px; text-align: center;
                          color: {c.text}; height: 20px; }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: {r.sm}px; }}

    QCheckBox, QRadioButton {{ color: {c.text}; spacing: {s.xs}px; }}

    QScrollBar:vertical {{ background: {c.surface}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {c.border_strong}; border-radius: {r.sm}px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {c.text_subtle}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: {c.surface}; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {c.border_strong}; border-radius: {r.sm}px; min-width: 24px; }}
    """


def card_qss() -> str:
    """卡片容器样式（关于页 / 设置页用）。"""
    c, r = Colors, Radius
    return f"""
    QFrame#card {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.lg}px;
    }}
    """


def button_qss(variant: str = "default") -> str:
    """生成按钮样式。

    Args:
        variant: "default" | "primary" | "danger"
    """
    c, r = Colors, Radius
    if variant == "primary":
        return f"""
        QPushButton {{
            background: {c.accent}; color: white;
            border: none; border-radius: {r.md}px; padding: 8px 20px;
            font-weight: {Typography.weight_medium};
        }}
        QPushButton:hover {{ background: {c.accent_hover}; }}
        QPushButton:pressed {{ background: {c.accent_hover}; }}
        QPushButton:disabled {{ background: {c.text_subtle}; color: white; }}
        """
    if variant == "danger":
        return f"""
        QPushButton {{
            background: {c.danger}; color: white;
            border: none; border-radius: {r.md}px; padding: 8px 20px;
        }}
        QPushButton:hover {{ background: {c.danger_hover}; }}
        """
    if variant == "default":
        return ""  # 由全局 QSS 接管
    raise ValueError(f"未知按钮 variant: {variant}")


def toolbar_button_qss() -> str:
    """QToolButton 统一样式（编辑器 + 内联浮窗共用，浅色）。"""
    c, r = Colors, Radius
    return f"""
    QToolButton {{
        background: transparent; color: {c.text};
        border: none; border-radius: {r.sm}px; padding: 4px 8px;
    }}
    QToolButton:hover    {{ background: {c.hover_bg}; }}
    QToolButton:pressed  {{ background: {c.pressed_bg}; }}
    QToolButton:checked  {{ background: {c.accent}; color: white; }}
    QToolButton:checked:hover {{ background: {c.accent_hover}; }}
    QToolButton:disabled {{ color: {c.text_subtle}; }}
    """


def panel_qss(object_name: str = "recognitionPanel") -> str:
    """右侧识别面板样式（原 EditorStyles.panel_style，现为浅色）。

    Args:
        object_name: 面板 widget 的 objectName，用于 QSS 选择器作用域。
    """
    c, s = Colors, Spacing
    return f"""
    QWidget#{object_name} {{
        background: {c.surface};
        border-left: 1px solid {c.border};
    }}
    QWidget#{object_name} QLabel#panelTitle {{
        color: {c.text}; font-size: {Typography.h1}px;
        font-weight: {Typography.weight_bold}; padding: {s.sm}px;
    }}
    """
