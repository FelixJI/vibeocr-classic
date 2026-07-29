"""Preview widget for image display, file loading and screenshot trigger"""

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vibeocr.backend.models.ocr_result import DISCARDED_BLOCK_TYPES, TextBlock
from vibeocr.classic.ui import theme
from vibeocr.classic.utils.image_jobs import GenerationImageJobs, decode_image_file

logger = logging.getLogger(__name__)

# 置信度阈值
LOW_CONFIDENCE_THRESHOLD = 0.80


def _render_pdf_page(
    file_path: str, page_index: int, cancel_event
) -> tuple[str, int, int, object]:
    """Load and render one PDF page entirely inside a thread-pool invocation."""
    from PySide6.QtPdf import QPdfDocument

    document = QPdfDocument()
    try:
        error = document.load(file_path)
        if error != QPdfDocument.Error.None_:
            raise RuntimeError(f"无法加载 PDF: {file_path}")
        if cancel_event.is_set():
            raise RuntimeError("已取消 PDF 预览")
        page_count = document.pageCount()
        if page_index < 0 or page_index >= page_count:
            raise RuntimeError(f"PDF 页码越界: {page_index}")
        page_size = document.pagePointSize(page_index)
        image = document.render(page_index, (page_size * 2.0).toSize())
        if cancel_event.is_set():
            raise RuntimeError("已取消 PDF 预览")
        return file_path, page_index, page_count, image
    finally:
        document.close()

# 无真实文本置信度的块类型：结构识别（表格/图片/图表/印章）与公式管道
# 在 pipeline 里 score 是占位值（0.9 / 1.0），不应在 tooltip 里显示为
# 误导性的百分比。与 base_tab._build_content_list 的白名单保持一致，
# 并补充 formula（score=1.0 占位）。键取自 TextBlock.label。
NO_CONFIDENCE_LABELS = frozenset(
    {"table", "image", "figure", "chart", "seal", "formula"}
)

# 置信度着色颜色
HIGH_CONF_FILL = QColor(76, 175, 80, 40)  # 淡绿色填充
HIGH_CONF_BORDER = QColor(76, 175, 80, 160)  # 淡绿色边框
LOW_CONF_FILL = QColor(244, 67, 54, 60)  # 红色填充
LOW_CONF_BORDER = QColor(244, 67, 54, 200)  # 红色边框
EDIT_FILL = QColor(255, 193, 7, 40)  # 琥珀色填充（手动修改）
EDIT_BORDER = QColor(255, 152, 0, 200)  # 橙色边框（手动修改）

# 块类型着色常量（来自 FilePreviewWidget）
BBOX_NORM = 1000.0
# Painting tens of thousands of translucent Qt paths in one frame can monopolize
# the GUI thread for seconds.  Keep a bounded interactive working set for the
# current page; the full OCR model remains available to the result view/export.
MAX_INTERACTIVE_OVERLAY_BLOCKS = 2000

BLOCK_COLORS = {
    "text": QColor(59, 130, 246, 30),
    "title": QColor(239, 68, 68, 30),
    "table": QColor(34, 197, 94, 30),
    "image": QColor(168, 85, 247, 30),
    "figure": QColor(168, 85, 247, 30),
    "chart": QColor(236, 72, 153, 30),
    "equation": QColor(249, 115, 22, 30),
    "interline_equation": QColor(249, 115, 22, 30),
    "inline_equation": QColor(249, 115, 22, 30),
    # PaddleX 公式管道（pipeline_formula）输出 label/type="formula"，
    # 归一到橙色（与 equation 一致），避免回退到蓝色文本色与文字混淆。
    "formula": QColor(249, 115, 22, 30),
    "list": QColor(6, 182, 212, 30),
    "code": QColor(139, 92, 246, 30),
    "seal": QColor(107, 114, 128, 30),
}

BLOCK_BORDER_COLORS = {
    "text": QColor(59, 130, 246, 200),
    "title": QColor(239, 68, 68, 200),
    "table": QColor(34, 197, 94, 200),
    "image": QColor(168, 85, 247, 200),
    "figure": QColor(168, 85, 247, 200),
    "chart": QColor(236, 72, 153, 200),
    "equation": QColor(249, 115, 22, 200),
    "interline_equation": QColor(249, 115, 22, 200),
    "inline_equation": QColor(249, 115, 22, 200),
    "formula": QColor(249, 115, 22, 200),
    "list": QColor(6, 182, 212, 200),
    "code": QColor(139, 92, 246, 200),
    "seal": QColor(107, 114, 128, 200),
}

BLOCK_TYPE_LABELS = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "chart": "图表",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
    "formula": "公式",
    "list": "列表",
    "code": "代码",
    "seal": "印章",
}


class UnifiedBBoxOverlay(QWidget):
    """统一 BBox 覆盖层，支持置信度着色和块类型着色两种模式"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 置信度模式数据: list of (x, y, w, h, score, text, is_manually_edited, polygon)
        self._conf_rects: list[
            tuple[float, float, float, float, float, str, bool, QPolygonF | None]
        ] = []
        # 块类型模式数据: list of (content_index, rect, block_type, fill, border, confidence)
        self._type_rects: list[
            tuple[int, QRectF, str, QColor, QColor, float | None]
        ] = []
        self._mode: str = "confidence"  # "confidence" or "block_type"
        self._hovered_index: int = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_confidence_blocks(self, rects) -> None:
        self._mode = "confidence"
        self._conf_rects = rects
        self._hovered_index = -1
        self.update()

    def set_type_blocks(self, rects) -> None:
        self._mode = "block_type"
        self._type_rects = rects
        self._hovered_index = -1
        self.update()

    def set_hovered(self, index: int) -> None:
        if index != self._hovered_index:
            self._hovered_index = index
            self.update()

    def clear(self) -> None:
        self._conf_rects.clear()
        self._type_rects.clear()
        self._hovered_index = -1
        self.update()

    def paintEvent(self, event) -> None:
        if self._mode == "confidence":
            self._paint_confidence()
        else:
            self._paint_block_type()

    def _paint_confidence(self) -> None:
        if not self._conf_rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i, (x, y, w, h, score, _text, is_manually_edited, poly) in enumerate(
            self._conf_rects
        ):
            rect = QRectF(x, y, w, h)
            is_low = score < LOW_CONFIDENCE_THRESHOLD
            is_hovered = i == self._hovered_index

            if is_manually_edited:
                fill = EDIT_FILL
                border = EDIT_BORDER
            elif is_low:
                fill = LOW_CONF_FILL
                border = LOW_CONF_BORDER
            else:
                fill = HIGH_CONF_FILL
                border = HIGH_CONF_BORDER

            if is_hovered:
                fill = QColor(fill)
                fill.setAlpha(min(fill.alpha() + 80, 200))

            pen = QPen(border, 2)
            if poly is not None and len(poly) >= 3:
                # 旋转/倾斜文本：画贴合的平行四边形（drawPolygon 同时填色+描边）
                painter.setBrush(fill)
                painter.setPen(pen)
                painter.drawPolygon(poly)
            else:
                # 轴对齐矩形（AABB）：fillRect 忽略 pen，单独描边
                painter.fillRect(rect, fill)
                painter.setPen(pen)
                painter.drawRect(rect)

        # 置信度模式下：若存在手动修改的块，绘制图例说明橙色含义
        # （橙色 = 手动修改）。普通高/低置信度颜色固定且语义明显，不入图例。
        self._paint_type_legend(painter)
        painter.end()

    def _paint_block_type(self) -> None:
        if not self._type_rects:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for (
            cl_idx,
            rect,
            _block_type,
            fill_color,
            border_color,
            confidence,
        ) in self._type_rects:
            is_hovered = cl_idx == self._hovered_index
            is_low_conf = (
                confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
            )

            if is_low_conf:
                fill = QColor(LOW_CONF_FILL)
                border = QColor(LOW_CONF_BORDER)
            elif is_hovered:
                fill = QColor(fill_color)
                fill.setAlpha(min(fill.alpha() + 100, 220))
                border = QColor(border_color)
                border.setAlpha(255)
            else:
                fill = fill_color
                border = border_color

            painter.fillRect(rect, fill)
            pen = QPen(border, 2 if is_low_conf else 1)
            painter.setPen(pen)
            painter.drawRect(rect)

        # 类型用边框颜色编码，文字标识集中在右上角图例中，避免遮挡框选内容
        self._paint_type_legend(painter)
        painter.end()

    def _legend_entries(self) -> list[tuple[str, QColor]]:
        """计算图例条目：(标签, 色块颜色)。

        - 块类型模式：按中文标签去重收集当前画面出现的类型颜色。
        - 若存在任一手动修改块（置信度模式 _conf_rects 的 is_manually_edited），
          追加一项"修改后"（橙色 EDIT_BORDER），解释橙色含义。
        """
        seen: set[str] = set()
        entries: list[tuple[str, QColor]] = []
        for _idx, _rect, block_type, _fill, border_color, _conf in self._type_rects:
            if block_type in seen:
                continue
            seen.add(block_type)
            label = BLOCK_TYPE_LABELS.get(block_type, block_type)
            # figure/image 等同色同名的合并：按中文标签去重
            if any(lbl == label for lbl, _ in entries):
                continue
            swatch = QColor(border_color)
            swatch.setAlpha(255)
            entries.append((label, swatch))

        # 追加"修改后"图例：只要存在任一手动修改块就显示。
        # 置信度模式 _conf_rects 的第 7 项（index 6）是 is_manually_edited。
        if any(r[6] for r in self._conf_rects):
            edited_swatch = QColor(EDIT_BORDER)
            edited_swatch.setAlpha(255)
            entries.append(("修改后", edited_swatch))
        return entries

    def _paint_type_legend(self, painter: QPainter) -> None:
        """在画布右上角绘制类型图例，仅列出当前画面中出现的类型（按中文标签去重）。

        除块类型颜色外，若画面上存在"手动修改"的块（橙色 EDIT_BORDER），
        追加一项"修改后"图例，避免用户不知橙色含义。
        """
        entries = self._legend_entries()
        if not entries:
            return

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        padding = 6
        swatch_size = 10
        swatch_gap = 5
        line_height = max(metrics.height(), swatch_size) + 2
        max_label_w = max(metrics.horizontalAdvance(label) for label, _ in entries)
        legend_w = padding * 2 + swatch_size + swatch_gap + max_label_w
        legend_h = padding * 2 + line_height * len(entries)

        margin = 8
        # 右上角，若空间不足则退到左上角
        legend_x = self.width() - margin - legend_w
        if legend_x < margin:
            legend_x = margin
        legend_y = margin
        legend_rect = QRectF(legend_x, legend_y, legend_w, legend_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(legend_rect, 4, 4)

        text_pen = QPen(QColor(255, 255, 255))
        for i, (label, color) in enumerate(entries):
            row_y = legend_y + padding + i * line_height
            sx = legend_x + padding
            sy = row_y + (line_height - swatch_size) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(sx, sy, swatch_size, swatch_size))
            tx = sx + swatch_size + swatch_gap
            text_rect = QRectF(
                tx, row_y, legend_x + legend_w - padding - tx, line_height
            )
            painter.setPen(text_pen)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )


class ImageViewerDialog(QDialog):
    """原图查看对话框，支持滚轮缩放和拖动滚动。"""

    _MIN_SCALE = 0.1
    _MAX_SCALE = 10.0

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查看原图")
        self.setMinimumSize(640, 480)

        self._pixmap = pixmap
        self._scale = 1.0  # 1.0 = 原始尺寸

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)

        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.setFixedWidth(30)
        self._zoom_out_btn.setToolTip("缩小")
        self._zoom_out_btn.clicked.connect(lambda: self._adjust_scale(0.8))

        self._zoom_label = QLabel("100%")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(60)

        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(30)
        self._zoom_in_btn.setToolTip("放大")
        self._zoom_in_btn.clicked.connect(lambda: self._adjust_scale(1.25))

        self._fit_btn = QPushButton("适应")
        self._fit_btn.setFixedWidth(50)
        self._fit_btn.setToolTip("适应窗口")
        self._fit_btn.clicked.connect(self._fit_to_window)

        self._orig_btn = QPushButton("1:1")
        self._orig_btn.setFixedWidth(40)
        self._orig_btn.setToolTip("原始大小")
        self._orig_btn.clicked.connect(lambda: self._set_scale(1.0))

        tb_layout.addWidget(self._zoom_out_btn)
        tb_layout.addWidget(self._zoom_label)
        tb_layout.addWidget(self._zoom_in_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self._fit_btn)
        tb_layout.addWidget(self._orig_btn)

        layout.addWidget(toolbar)

        # 滚动区域 + 图片标签
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._img_label)

        layout.addWidget(self._scroll, stretch=1)

        # 初始按窗口大小适应
        QTimer.singleShot(0, self._fit_to_window)

    def _update_display(self) -> None:
        scaled_w = int(self._pixmap.width() * self._scale)
        scaled_h = int(self._pixmap.height() * self._scale)
        scaled = self._pixmap.scaled(
            scaled_w,
            scaled_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)
        self._img_label.resize(scaled.size())
        self._zoom_label.setText(f"{self._scale:.0%}")

    def _set_scale(self, scale: float) -> None:
        self._scale = max(self._MIN_SCALE, min(self._MAX_SCALE, scale))
        self._update_display()

    def _adjust_scale(self, factor: float) -> None:
        self._set_scale(self._scale * factor)

    def _fit_to_window(self) -> None:
        vw = self._scroll.viewport().width()
        vh = self._scroll.viewport().height()
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        self._set_scale(min(vw / pw, vh / ph))

    def wheelEvent(self, event) -> None:
        """滚轮缩放。"""
        delta = event.angleDelta().y()
        if delta > 0:
            self._adjust_scale(1.15)
        elif delta < 0:
            self._adjust_scale(1 / 1.15)


class PreviewWidget(QWidget):
    """统一图片预览组件

    支持图片/PDF 加载、截图触发、BBox 高亮、翻页导航、滚轮缩放。
    支持两种覆盖层模式：
    - 置信度模式（单次识别）：通过 set_text_blocks 设置
    - 块类型模式（批量识别/文档解析）：通过 set_content_list 设置
    """

    # 用户缩放倍数范围（叠加在 fit_scale 之上）
    _MIN_USER_SCALE = 0.1
    _MAX_USER_SCALE = 8.0

    screenshot_requested = Signal()
    file_open_requested = Signal()
    image_changed = Signal()
    block_clicked = Signal(int)
    block_text_edited = Signal(int, str)
    block_hovered = Signal(int)
    block_unhovered = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        empty_text: str = "左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式",
    ) -> None:
        super().__init__(parent)
        self._empty_text = empty_text
        self._pixmap: QPixmap | None = None
        self._original_pixmap: QPixmap | None = None
        self._img_w: int = 0
        self._img_h: int = 0
        self._text_blocks: list[TextBlock] = []
        self._text_page_indices: dict[int, list[int]] = {}
        self._text_by_content_index: dict[int, int] = {}
        self._confidence_overlay_indices: list[int] = []
        self._confidence_overlay_local_by_source: dict[int, int] = {}
        self._block_screen_rects: (
            dict[int, tuple[float, float, float, float]]
            | list[tuple[float, float, float, float]]
        ) = {}
        # 多边形屏幕坐标（与 _block_screen_rects 同序，None 表示该块用 AABB）
        self._block_screen_polys: (
            dict[int, QPolygonF | None] | list[QPolygonF | None]
        ) = {}
        # 块类型模式的命中矩形：list of (content_index, screen_rect, block_type)
        self._type_screen_rects: list[tuple[int, QRectF, str]] = []
        self._hovered_block: int | str = -1
        self._editing_index: int = -1
        self._content_list: list[dict] = []
        self._content_page_indices: dict[int, list[int]] = {}
        self._current_file: str = ""
        self._is_pdf = False
        self._highlight_block_index: int = -1

        # PDF
        self._pdf_doc = None  # compatibility marker; PDF work is never GUI-owned
        self._current_page: int = 0
        self._total_pages: int = 0

        # 缩放：_fit_scale 是 fit-to-window 的基础比例；_scale 是用户在其上
        # 叠加的倍数（1.0=fit）。总缩放 = _fit_scale * _scale。分离二者是为了
        # 让“适应窗口”按钮能精确回到 fit，而 wheel 在 fit 基础上放大/缩小。
        self._fit_scale: float = 1.0
        self._scale: float = 1.0
        self._display_cache_key: tuple[int, int, int, int] | None = None
        self._display_cache_pixmap: QPixmap | None = None
        self._last_resize_viewport_size: tuple[int, int, int] | None = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(24)
        self._resize_timer.timeout.connect(self._apply_debounced_resize)
        self._closing = False
        self._image_load_jobs = GenerationImageJobs(self)
        self._image_load_jobs.completed.connect(self._on_image_file_loaded)
        self._image_load_jobs.failed.connect(self._on_image_file_load_failed)
        self._pdf_jobs = GenerationImageJobs(self)
        self._pdf_jobs.completed.connect(self._on_pdf_page_loaded)
        self._pdf_jobs.failed.connect(self._on_pdf_page_failed)
        self._block_index_jobs = GenerationImageJobs(self)
        self._block_index_jobs.completed.connect(self._on_block_indexes_ready)
        self._block_index_jobs.failed.connect(self._on_block_indexes_failed)
        self._content_index_jobs = GenerationImageJobs(self)
        self._content_index_jobs.completed.connect(self._on_content_indexes_ready)
        self._content_index_jobs.failed.connect(self._on_content_indexes_failed)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 翻页导航栏
        self._nav_bar = QWidget()
        nav_layout = QHBoxLayout(self._nav_bar)
        nav_layout.setContentsMargins(4, 0, 4, 0)

        self._prev_btn = QPushButton("<")
        self._prev_btn.setFixedWidth(30)
        self._prev_btn.setEnabled(False)

        self._page_label = QLabel("0 / 0")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._next_btn = QPushButton(">")
        self._next_btn.setFixedWidth(30)
        self._next_btn.setEnabled(False)

        nav_layout.addWidget(self._prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._page_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_btn)

        # 缩放控件（翻页栏右侧）
        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.setToolTip("缩小")
        self._zoom_out_btn.setEnabled(False)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.setToolTip("放大")
        self._zoom_in_btn.setEnabled(False)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(52)
        self._zoom_label.setEnabled(False)
        self._fit_btn = QPushButton("适应")
        self._fit_btn.setFixedWidth(46)
        self._fit_btn.setToolTip("适应窗口")
        self._fit_btn.setEnabled(False)
        nav_layout.addWidget(self._zoom_out_btn)
        nav_layout.addWidget(self._zoom_label)
        nav_layout.addWidget(self._zoom_in_btn)
        nav_layout.addWidget(self._fit_btn)

        layout.addWidget(self._nav_bar)

        # 预览区域（带滚动）
        self._scroll_area = QScrollArea()
        # setWidgetResizable(False)：label 尺寸由我们按缩放显式 resize，可超出
        # viewport 触发滚动。overlay 挂在 label 上，滚动时随 label 平移——
        # 框坐标是 label-relative，故天然无漂移（无需读 scrollbar 值）。
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        self._image_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 2px dashed {theme.Colors.border}; }}"
        )
        self._image_label.setText(self._empty_text)
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click  # type: ignore[method-assign]
        self._image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_label.customContextMenuRequested.connect(self._on_context_menu)
        self._scroll_area.setWidget(self._image_label)

        layout.addWidget(self._scroll_area, stretch=1)

        # 覆盖层：挂在 image_label 上（而非 viewport），随 label 滚动；
        # 几何设为 pixmap 在 label 内的显示矩形（见 _apply_overlay_geometry）。
        self._overlay = UnifiedBBoxOverlay(self._image_label)

        # 内联文本编辑器
        self._inline_editor = QLineEdit(self._image_label)
        self._inline_editor.setStyleSheet(
            f"QLineEdit {{ background-color: rgba(255,255,255,0.95);"
            f" border: 2px solid {theme.Colors.warning}; border-radius: 4px;"
            f" padding: 2px 6px; font-size: 13px; }}"
        )
        self._inline_editor.setFrame(False)
        self._inline_editor.hide()
        self._inline_editor.editingFinished.connect(self._on_inline_edit_finished)
        self._inline_editor.installEventFilter(self)

        # 事件过滤器用于悬停和点击检测
        self._image_label.setMouseTracking(True)
        self._image_label.installEventFilter(self)

        # 翻页信号
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._next_btn.clicked.connect(self._on_next_page)
        # 缩放信号
        self._zoom_in_btn.clicked.connect(lambda: self._zoom_by(1.25))
        self._zoom_out_btn.clicked.connect(lambda: self._zoom_by(1 / 1.25))
        self._fit_btn.clicked.connect(self._zoom_fit)

    # ── 事件过滤器 ──

    def eventFilter(self, obj, event) -> bool:
        if obj == self._image_label and self._pixmap:
            if event.type() == event.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_label_double_click(event.pos())
            elif self._text_blocks:
                if event.type() == event.Type.MouseMove:
                    self._on_mouse_move(event.pos())
                elif event.type() == event.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._on_block_click(event.pos())
        elif obj == self._inline_editor and event.type() == event.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent

            key_event: QKeyEvent = event
            if key_event.key() == Qt.Key.Key_Escape:
                self._cancel_inline_edit()
                return True
        return super().eventFilter(obj, event)

    def _on_mouse_move(self, pos) -> None:
        # 统一悬停键：置信度模式用 text_block 下标，块类型模式用
        # "t:" + content_list 索引，避免两种模式命中互相串扰。
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            hover_key = idx
        elif self._content_list:
            # 块类型模式回退：表格/公式等结构识别管道左侧在块类型模式渲染，
            # 置信度命中测试恒返回 -1，需用 _hit_test_type_block 命中 content_list。
            cl_idx, _bt = self._hit_test_type_block(pos.x(), pos.y())
            hover_key = f"t:{cl_idx}" if cl_idx >= 0 else -1
        else:
            hover_key = -1

        if hover_key != self._hovered_block:
            self._hovered_block = hover_key
            if idx >= 0:
                # 置信度模式命中
                self._overlay.set_hovered(
                    self._confidence_overlay_local_by_source.get(idx, -1)
                )
                self.block_hovered.emit(idx)
                block = self._text_blocks[idx]
                self._image_label.setToolTip(
                    self._build_block_tooltip(
                        getattr(block, "label", "text"),
                        block.text,
                        block.score,
                        block.is_manually_edited,
                    )
                )
            elif isinstance(hover_key, str) and hover_key.startswith("t:"):
                # 块类型模式命中
                cl_idx = int(hover_key[2:])
                self._overlay.set_hovered(cl_idx)
                self.block_hovered.emit(cl_idx)
                tb_idx = self._find_text_block_by_content_index(cl_idx)
                block = self._text_blocks[tb_idx] if tb_idx >= 0 else None
                if block is not None:
                    self._image_label.setToolTip(
                        self._build_block_tooltip(
                            getattr(block, "label", "text"),
                            block.text,
                            block.score,
                            block.is_manually_edited,
                        )
                    )
                else:
                    # 无对应 text_block（如纯图片块）：用 content_list 元信息
                    cl_block = (
                        self._content_list[cl_idx]
                        if 0 <= cl_idx < len(self._content_list)
                        else {}
                    )
                    self._image_label.setToolTip(
                        self._build_block_tooltip(
                            cl_block.get("type", "text"),
                            cl_block.get("text", ""),
                            None,
                            False,
                        )
                    )
            else:
                self._overlay.set_hovered(-1)
                self.block_unhovered.emit()
                self._image_label.setToolTip("")

    @staticmethod
    def _build_block_tooltip(
        label: str, text: str, score: float | None, is_edited: bool
    ) -> str:
        """构造 bbox 悬停 tooltip。

        表格/图片/公式等结构识别块的 score 是占位值（0.9/1.0），显示为百分比
        会误导（如表格显示"90%"），改为"无置信度"；普通文本块保留真实百分比。
        """
        if label in NO_CONFIDENCE_LABELS or score is None:
            conf_line = "置信度: 无置信度"
        else:
            conf_line = f"置信度: {score:.1%}"
        tooltip = f"{(text or '')[:50]}\n{conf_line}"
        if is_edited:
            tooltip += "\n[手动修改]"
        return tooltip

    def _on_block_click(self, pos) -> None:
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            self.block_clicked.emit(idx)

    def _on_label_double_click(self, pos) -> None:
        """双击处理：优先 bbox 内联编辑，空白区域打开原图查看器。

        表格块（label/type=="table"）的 text 是原始 HTML，走内联 QLineEdit
        会把标签当纯文本显示，故表格块双击不做内联编辑（请在右侧结果视图
        编辑表格）。
        """
        # 优先置信度模式（单次识别结果）命中
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            block = self._text_blocks[idx]
            if getattr(block, "label", "") != "table":
                self._start_inline_edit(idx)
            return

        # 回退块类型模式（content_list）
        cl_idx, block_type = self._hit_test_type_block(pos.x(), pos.y())
        if cl_idx >= 0:
            if block_type != "table":
                # 块类型模式下普通文本块：尝试定位到对应 text_block 做内联编辑
                tb_idx = self._find_text_block_by_content_index(cl_idx)
                if tb_idx >= 0:
                    self._start_inline_edit(tb_idx)
            return

        # 未命中任何 bbox → 打开原图查看器
        self._show_original_image()

    def _show_original_image(self) -> None:
        """弹出原图查看对话框。"""
        pm = self._original_pixmap
        if pm is None or pm.isNull():
            return
        dialog = ImageViewerDialog(pm, self)
        dialog.resize(min(pm.width() + 40, 1200), min(pm.height() + 80, 900))
        dialog.exec()

    def _start_inline_edit(self, index: int) -> None:
        screen_rect = self._block_screen_rect_at(index)
        if screen_rect is None or index < 0 or index >= len(self._text_blocks):
            return
        bx, by, bw, bh = screen_rect
        block = self._text_blocks[index]
        self._editing_index = index
        self._inline_editor.setText(block.text)
        self._inline_editor.setGeometry(
            int(bx), int(by), max(int(bw), 120), max(int(bh) + 4, 28)
        )
        self._inline_editor.show()
        self._inline_editor.setFocus()
        self._inline_editor.selectAll()

    def _on_inline_edit_finished(self) -> None:
        if self._editing_index < 0:
            return
        index = self._editing_index
        new_text = self._inline_editor.text()
        old_text = self._text_blocks[index].text
        self._inline_editor.hide()
        self._editing_index = -1
        if new_text != old_text:
            self.block_text_edited.emit(index, new_text)

    def _cancel_inline_edit(self) -> None:
        self._inline_editor.hide()
        self._editing_index = -1

    def _block_screen_rect_at(
        self, index: int
    ) -> tuple[float, float, float, float] | None:
        if isinstance(self._block_screen_rects, dict):
            return self._block_screen_rects.get(index)
        if 0 <= index < len(self._block_screen_rects):
            return self._block_screen_rects[index]
        return None

    def _block_screen_poly_at(self, index: int) -> QPolygonF | None:
        if isinstance(self._block_screen_polys, dict):
            return self._block_screen_polys.get(index)
        if 0 <= index < len(self._block_screen_polys):
            return self._block_screen_polys[index]
        return None

    def _hit_test_block(self, x: int, y: int) -> int:
        # 有多边形的块用 polygon 精确命中（贴合旋转/倾斜文字）；
        # 无多边形的块回退 AABB rect 命中。AABB 比 polygon 大，故已有多边形
        # 的块不再参与 rect 命中，避免重叠块被外接矩形误命中。
        indices = self._confidence_overlay_indices or range(
            len(self._block_screen_rects)
        )
        for i in indices:
            screen_rect = self._block_screen_rect_at(i)
            if screen_rect is None:
                continue
            bx, by, bw, bh = screen_rect
            poly = self._block_screen_poly_at(i)
            if poly is not None and len(poly) >= 3:
                if poly.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill):
                    return i
            elif bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _hit_test_type_block(self, x: int, y: int) -> tuple[int, str]:
        """块类型模式命中测试，返回 (content_list 索引, block_type)。

        未命中返回 (-1, "")。用于双击表格块进入网格编辑、或双击普通文本块
        定位到对应 text_block 做内联编辑。
        """
        for cl_idx, rect, block_type in self._type_screen_rects:
            if rect.contains(x, y):
                return cl_idx, block_type
        return -1, ""

    def _find_text_block_by_content_index(self, cl_idx: int) -> int:
        """按 content_index 反查 text_blocks 的下标（用于块类型模式下
        命中普通文本块后复用置信度模式的内联编辑）。"""
        if cl_idx < 0:
            return -1
        return self._text_by_content_index.get(cl_idx, -1)

    # ── 标签点击（空状态触发截图/文件选择）──

    def _on_label_click(self, event) -> None:
        if self._pixmap is None and self._original_pixmap is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.screenshot_requested.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.file_open_requested.emit()

    def _on_context_menu(self, pos) -> None:
        if self._pixmap is not None or self._original_pixmap is not None:
            return
        menu = QMenu(self._image_label)
        action_screenshot = QAction("截图识别", menu)
        action_open_file = QAction("选择文件（图片/PDF）", menu)
        action_screenshot.triggered.connect(self.screenshot_requested.emit)
        action_open_file.triggered.connect(self.file_open_requested.emit)
        menu.addAction(action_screenshot)
        menu.addAction(action_open_file)
        menu.exec(self._image_label.mapToGlobal(pos))

    # ── 图片设置 ──

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """设置预览图片（截图或打开图片）"""
        self._image_load_jobs.cancel_current()
        self._pdf_jobs.cancel_current()
        self._block_index_jobs.cancel_current()
        self._content_index_jobs.cancel_current()
        self._text_blocks = []
        self._text_page_indices = {}
        self._text_by_content_index = {}
        self._confidence_overlay_indices = []
        self._confidence_overlay_local_by_source = {}
        self._block_screen_rects = []
        self._block_screen_polys = []
        self._content_list = []
        self._content_page_indices = {}
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._scale = 1.0  # 新图重置用户缩放
        self._overlay.clear()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)
        self._pixmap = pixmap
        self._original_pixmap = pixmap
        self._invalidate_display_cache()
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._total_pages = 1
        self._current_page = 0
        self._update_display()
        self._update_nav()
        self.image_changed.emit()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def original_pixmap(self) -> QPixmap | None:
        """返回原始图片（未预处理、未缩放）。

        OCR 预处理可能把 _pixmap（显示用）替换为预处理后图像，
        但 _original_pixmap 始终保留原图，供"复制原图"使用。
        """
        return self._original_pixmap

    def set_text_blocks(self, blocks: list[TextBlock]) -> None:
        """设置文本块用于置信度模式高亮"""
        if self._closing:
            return
        if blocks is self._text_blocks:
            if self._content_list:
                self._update_type_overlay()
            else:
                self._update_block_overlay()
            return

        self._text_blocks = blocks
        if len(blocks) > MAX_INTERACTIVE_OVERLAY_BLOCKS:
            # Seed only the bounded interactive set synchronously.  Full page
            # and content-index maps are pure data and can be built off-thread.
            seed_count = min(len(blocks), MAX_INTERACTIVE_OVERLAY_BLOCKS)
            page_indices, content_lookup = self._build_text_block_indexes(
                blocks, None, limit=seed_count
            )
            self._text_page_indices = page_indices
            self._text_by_content_index = content_lookup
            self._update_block_overlay()
            self._block_index_jobs.submit(
                lambda cancel_event: (
                    blocks,
                    *self._build_text_block_indexes(blocks, cancel_event),
                )
            )
            return

        self._block_index_jobs.cancel_current()
        page_indices, content_lookup = self._build_text_block_indexes(blocks, None)
        self._text_page_indices = page_indices
        self._text_by_content_index = content_lookup
        self._update_block_overlay()

    def set_text_content_index(self, text_index_by_content: dict[int, int]) -> None:
        """Install a worker-prepared reverse index without a GUI-thread copy."""
        if not self._closing:
            self._text_by_content_index = text_index_by_content

    @staticmethod
    def _build_text_block_indexes(
        blocks: list[TextBlock], cancel_event, *, limit: int | None = None
    ) -> tuple[dict[int, list[int]], dict[int, int]]:
        page_indices: dict[int, list[int]] = {}
        content_lookup: dict[int, int] = {}
        block_count = len(blocks) if limit is None else min(len(blocks), limit)
        for index in range(block_count):
            if (
                cancel_event is not None
                and index % 256 == 0
                and cancel_event.is_set()
            ):
                return {}, {}
            block = blocks[index]
            page_indices.setdefault(getattr(block, "page_idx", None) or 0, []).append(
                index
            )
            content_index = getattr(block, "content_index", None)
            if content_index is not None:
                content_lookup[content_index] = index
        return page_indices, content_lookup

    @Slot(int, object)
    def _on_block_indexes_ready(self, _generation: int, payload: object) -> None:
        if self._closing or not isinstance(payload, tuple) or len(payload) != 3:
            return
        blocks, page_indices, content_lookup = payload
        if blocks is not self._text_blocks:
            return
        self._text_page_indices = page_indices
        self._text_by_content_index = content_lookup
        if self._content_list:
            self._update_type_overlay()
        else:
            self._update_block_overlay()

    @Slot(int, str)
    def _on_block_indexes_failed(self, _generation: int, error: str) -> None:
        if not self._closing:
            logger.error("后台构建 OCR 文本块索引失败: %s", error)

    # ── 文件加载（PDF/图片）──

    def load_file(self, file_path: str) -> None:
        """从文件路径加载（自动检测 PDF/图片）"""
        if self._closing:
            return
        self._image_load_jobs.cancel_current()
        self._pdf_jobs.cancel_current()
        self._current_file = file_path
        ext = Path(file_path).suffix.lower()
        self._is_pdf = ext == ".pdf"

        self._block_index_jobs.cancel_current()
        self._content_index_jobs.cancel_current()
        self._text_blocks = []
        self._text_page_indices = {}
        self._text_by_content_index = {}
        self._confidence_overlay_indices = []
        self._confidence_overlay_local_by_source = {}
        self._block_screen_rects = []
        self._block_screen_polys = []
        self._content_list = []
        self._content_page_indices = {}
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._scale = 1.0  # 新文件重置用户缩放
        self._overlay.clear()

        if self._is_pdf:
            self._load_pdf(file_path)
        else:
            self._load_image_file(file_path)

    def _load_pdf(self, file_path: str) -> None:
        self._pixmap = None
        self._original_pixmap = None
        self._invalidate_display_cache()
        self._total_pages = 0
        self._current_page = 0
        self._render_current_page()
        self._update_nav()

    def _load_image_file(self, file_path: str) -> None:
        self._total_pages = 1
        self._current_page = 0
        self._pdf_jobs.cancel_current()
        self._pixmap = None
        self._original_pixmap = None
        self._invalidate_display_cache()
        self._image_label.clear()
        self._image_label.setText(f"正在加载图片: {Path(file_path).name}...")
        self._update_nav()
        self._image_load_jobs.submit(
            lambda cancel_event: (
                file_path,
                decode_image_file(file_path, cancel_event),
            )
        )

    @Slot(int, object)
    def _on_image_file_loaded(self, _generation: int, result: object) -> None:
        if self._closing or not isinstance(result, tuple) or len(result) != 2:
            return
        file_path, image = result
        if file_path != self._current_file or image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_image_file_load_failed(_generation, f"无法显示图片: {file_path}")
            return
        self._original_pixmap = pixmap
        self._pixmap = pixmap
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._invalidate_display_cache()
        self._update_display()
        self.image_changed.emit()

    @Slot(int, str)
    def _on_image_file_load_failed(self, _generation: int, error: str) -> None:
        if self._closing:
            return
        self._image_label.clear()
        self._image_label.setText(error)

    def _render_current_page(self) -> None:
        if self._closing or not self._current_file or not self._is_pdf:
            return
        file_path = self._current_file
        page_index = self._current_page
        self._image_label.setText(
            f"正在加载 PDF: {Path(file_path).name} 第 {page_index + 1} 页..."
        )
        self._pdf_jobs.submit(
            lambda cancel_event: _render_pdf_page(
                file_path, page_index, cancel_event
            )
        )

    @Slot(int, object)
    def _on_pdf_page_loaded(self, _generation: int, result: object) -> None:
        if self._closing or not isinstance(result, tuple) or len(result) != 4:
            return
        file_path, page_index, page_count, image = result
        if file_path != self._current_file or page_index != self._current_page:
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_pdf_page_failed(_generation, f"无法显示 PDF: {file_path}")
            return
        self._total_pages = int(page_count)
        self._original_pixmap = pixmap
        self._pixmap = self._original_pixmap
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._invalidate_display_cache()
        self._update_display()
        self._update_nav()
        self._reapply_highlight()
        self.image_changed.emit()

    @Slot(int, str)
    def _on_pdf_page_failed(self, _generation: int, error: str) -> None:
        if self._closing:
            return
        self._image_label.clear()
        self._image_label.setText(error)
        if self._total_pages <= 0:
            self._update_nav()

    # ── 翻页 ──

    def _on_prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _on_next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _update_nav(self) -> None:
        has_pages = self._total_pages > 1
        self._prev_btn.setEnabled(has_pages and self._current_page > 0)
        self._next_btn.setEnabled(
            has_pages and self._current_page < self._total_pages - 1
        )
        self._page_label.setText(f"{self._current_page + 1} / {self._total_pages}")
        self._nav_bar.setVisible(self._total_pages > 0)

    def current_page(self) -> int:
        return self._current_page

    def page_count(self) -> int:
        return self._total_pages

    # ── content_list 和块类型着色 ──

    def set_content_list(self, content_list: list[dict]) -> None:
        """设置 content_list 用于块类型着色覆盖"""
        if self._closing:
            return
        if content_list is self._content_list:
            self._update_type_overlay()
            return

        self._content_list = content_list
        if len(content_list) > MAX_INTERACTIVE_OVERLAY_BLOCKS:
            self._content_page_indices = self._build_content_page_indexes(
                content_list, None, limit=MAX_INTERACTIVE_OVERLAY_BLOCKS
            )
            self._update_type_overlay()
            self._content_index_jobs.submit(
                lambda cancel_event: (
                    content_list,
                    self._build_content_page_indexes(content_list, cancel_event),
                )
            )
            return

        self._content_index_jobs.cancel_current()
        self._content_page_indices = self._build_content_page_indexes(
            content_list, None
        )
        self._update_type_overlay()

    @staticmethod
    def _build_content_page_indexes(
        content_list: list[dict], cancel_event, *, limit: int | None = None
    ) -> dict[int, list[int]]:
        page_indices: dict[int, list[int]] = {}
        block_count = (
            len(content_list) if limit is None else min(len(content_list), limit)
        )
        for index in range(block_count):
            if (
                cancel_event is not None
                and index % 256 == 0
                and cancel_event.is_set()
            ):
                return {}
            block = content_list[index]
            if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                continue
            page_indices.setdefault(block.get("page_idx", 0) or 0, []).append(index)
        return page_indices

    @Slot(int, object)
    def _on_content_indexes_ready(self, _generation: int, payload: object) -> None:
        if self._closing or not isinstance(payload, tuple) or len(payload) != 2:
            return
        content_list, page_indices = payload
        if content_list is not self._content_list:
            return
        self._content_page_indices = page_indices
        self._update_type_overlay()

    @Slot(int, str)
    def _on_content_indexes_failed(self, _generation: int, error: str) -> None:
        if not self._closing:
            logger.error("后台构建 OCR 内容块索引失败: %s", error)

    def _update_type_overlay(self) -> None:
        """绘制所有 content_list 块的 bbox 覆盖层"""
        if not self._content_list or self._original_pixmap is None:
            self._overlay.set_type_blocks([])
            return

        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
        if disp_w <= 0 or disp_h <= 0:
            self._overlay.set_type_blocks([])
            return

        overlay_rects = []
        type_screen_rects: list[tuple[int, QRectF, str]] = []
        page_indices = self._content_page_indices.get(self._current_page, [])
        visible_indices = page_indices[:MAX_INTERACTIVE_OVERLAY_BLOCKS]
        if (
            self._highlight_block_index in page_indices
            and self._highlight_block_index not in visible_indices
        ):
            visible_indices = [*visible_indices, self._highlight_block_index]
        for i in visible_indices:
            block = self._content_list[i]
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            block_type = block.get("type", "text")
            # MinerU content_list v1: titles are {"type": "text", "text_level": N}
            if block_type == "text" and "text_level" in block:
                block_type = "title"
            fill_color = BLOCK_COLORS.get(block_type, BLOCK_COLORS["text"])
            border_color = BLOCK_BORDER_COLORS.get(
                block_type, BLOCK_BORDER_COLORS["text"]
            )
            screen_rect = QRectF(
                bbox[0] / BBOX_NORM * disp_w + offset_x,
                bbox[1] / BBOX_NORM * disp_h + offset_y,
                (bbox[2] - bbox[0]) / BBOX_NORM * disp_w,
                (bbox[3] - bbox[1]) / BBOX_NORM * disp_h,
            )
            overlay_rects.append(
                (
                    i,
                    screen_rect,
                    block_type,
                    fill_color,
                    border_color,
                    block.get("confidence"),
                )
            )
            # 同步记录命中矩形，供块类型模式下的双击编辑命中测试使用
            type_screen_rects.append((i, screen_rect, block_type))

        self._type_screen_rects = type_screen_rects
        self._overlay.set_type_blocks(overlay_rects)
        self._apply_overlay_geometry()

    # ── 高亮 ──

    def highlight_block(self, index: int) -> None:
        """高亮指定块（同时支持置信度模式和块类型模式）"""
        # 块类型模式：查找 content_list 中的 bbox，翻页到对应页
        if self._content_list and 0 <= index < len(self._content_list):
            block = self._content_list[index]
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                self._overlay.set_hovered(-1)
                return
            page_idx = block.get("page_idx", 0)
            if page_idx != self._current_page:
                self._current_page = page_idx
                if self._is_pdf:
                    self._render_current_page()
                self._update_nav()
                self._update_type_overlay()
            self._highlight_block_index = index
            self._overlay.set_hovered(index)
            return

        # 置信度模式：直接设置 overlay hovered index
        self._overlay.set_hovered(
            self._confidence_overlay_local_by_source.get(index, -1)
        )

    def clear_highlight(self) -> None:
        """清除悬停高亮（保留永久覆盖层）"""
        self._overlay.set_hovered(-1)
        self._highlight_block_index = -1

    def _reapply_highlight(self) -> None:
        """翻页后重新应用高亮和全块覆盖"""
        if self._content_list:
            self._update_type_overlay()
        else:
            self._update_block_overlay()
        if self._highlight_block_index >= 0:
            self.highlight_block(self._highlight_block_index)

    # ── 清除 ──

    def clear(self) -> None:
        """清除图片"""
        self._image_load_jobs.cancel_current()
        self._pdf_jobs.cancel_current()
        self._block_index_jobs.cancel_current()
        self._content_index_jobs.cancel_current()
        self._pixmap = None
        self._original_pixmap = None
        self._invalidate_display_cache()
        self._text_blocks = []
        self._text_page_indices = {}
        self._text_by_content_index = {}
        self._confidence_overlay_indices = []
        self._confidence_overlay_local_by_source = {}
        self._block_screen_rects = []
        self._block_screen_polys = []
        self._content_list = []
        self._content_page_indices = {}
        self._type_screen_rects = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._overlay.clear()
        self._image_label.clear()
        self._image_label.setText(self._empty_text)
        self._image_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 2px dashed {theme.Colors.border}; }}"
        )
        self._current_file = ""
        self._total_pages = 0
        self._current_page = 0
        self._update_nav()
        self.image_changed.emit()

    # ── 显示更新 ──

    def _compute_scale_factor(self) -> tuple[float, float, float, float]:
        """基于 _original_pixmap 和当前总缩放计算 pixmap 显示尺寸与偏移

        总缩放 = _fit_scale（fit-to-window 基础）× _scale（用户倍数）。
        disp_w/disp_h 是 pixmap 在 label 内的实际像素尺寸；offset 是 pixmap
        相对 label 原点的居中偏移（label-relative，overlay 挂在 label 上故直接生效）。

        Returns: (disp_w, disp_h, offset_x, offset_y)
        """
        if not self._original_pixmap or self._original_pixmap.isNull():
            return 0, 0, 0, 0
        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()
        if img_w <= 0 or img_h <= 0:
            return 0, 0, 0, 0
        scale = self._fit_scale * self._scale
        disp_w = img_w * scale
        disp_h = img_h * scale
        # label 已被 resize 到 disp_w/disp_h（见 _update_display），故居中偏移为 0；
        # 但当 pixmap 因 KeepAspectRatio 与 label 尺寸不完全一致时仍需居中。
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        offset_x = max((label_w - disp_w) / 2, 0)
        offset_y = max((label_h - disp_h) / 2, 0)
        return disp_w, disp_h, offset_x, offset_y

    # ── 缩放 ──

    def _polygon_to_screen(
        self,
        polygon: tuple[float, ...] | None,
        disp_w: float,
        disp_h: float,
        offset_x: float,
        offset_y: float,
    ) -> QPolygonF | None:
        """把归一化 [0,1000] 的扁平多边形 [x0,y0,x1,y1,...] 转成屏幕坐标 QPolygonF。

        与 bbox 换算同口径（/1000 × disp + offset）。点数 < 3 或解析失败返回 None，
        调用方回退到 AABB rect。
        """
        if not polygon or len(polygon) < 6 or len(polygon) % 2 != 0:
            return None
        pts = QPolygonF()
        for i in range(0, len(polygon), 2):
            px = polygon[i] / 1000.0 * disp_w + offset_x
            py = polygon[i + 1] / 1000.0 * disp_h + offset_y
            pts.append(QPointF(px, py))
        return pts if len(pts) >= 3 else None

    def _compute_fit_scale(self) -> float:
        """计算 fit-to-window 的基础缩放比例（让 pixmap 适应 viewport）。"""
        if not self._original_pixmap or self._original_pixmap.isNull():
            return 1.0
        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()
        viewport = self._scroll_area.viewport()
        max_w = max(viewport.width() - 20, 1)
        max_h = max(viewport.height() - 20, 1)
        if img_w <= 0 or img_h <= 0:
            return 1.0
        return min(max_w / img_w, max_h / img_h)

    def _current_total_scale(self) -> float:
        """总缩放（fit × user）。"""
        return self._fit_scale * self._scale

    def _zoom_by(self, factor: float) -> None:
        """以当前缩放为中心按 factor 放大/缩小（用户倍数层）。"""
        if self._pixmap is None:
            return
        new_scale = max(
            self._MIN_USER_SCALE, min(self._MAX_USER_SCALE, self._scale * factor)
        )
        if new_scale == self._scale:
            return
        self._scale = new_scale
        self._after_zoom()

    def _zoom_fit(self) -> None:
        """回到 fit-to-window（用户倍数 = 1.0）。"""
        if self._pixmap is None:
            return
        self._scale = 1.0
        self._fit_scale = self._compute_fit_scale()
        self._after_zoom()

    def _after_zoom(self) -> None:
        """缩放变化后刷新显示、框、命中矩形与控件状态。"""
        self._update_display()
        self._reapply_highlight()
        self._update_zoom_controls()

    def _update_zoom_controls(self) -> None:
        """同步缩放按钮/标签的启用状态与百分比文本。"""
        has_img = self._pixmap is not None
        for w in (
            self._zoom_in_btn,
            self._zoom_out_btn,
            self._fit_btn,
            self._zoom_label,
        ):
            w.setEnabled(has_img)
        if has_img:
            self._zoom_label.setText(f"{self._current_total_scale():.0%}")

    def _apply_overlay_geometry(self) -> None:
        """把 overlay 几何对齐到 label 内容区。

        overlay 挂在 image_label 上，随 label 一起被 QScrollArea 滚动，
        故框坐标（label-relative）天然跟随，滚动时无漂移。overlay 覆盖整个
        label；pixmap 在 label 内居中，框换算用 _compute_scale_factor 的 offset。
        """
        self._overlay.setGeometry(self._image_label.rect())

    def wheelEvent(self, event) -> None:
        """滚轮缩放（Ctrl 修饰时），否则交给默认（不拦截）。"""
        if (
            self._pixmap is not None
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom_by(1.15)
            elif delta < 0:
                self._zoom_by(1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def _update_display(self) -> None:
        if self._pixmap:
            dpr = self.devicePixelRatio()
            # 重新计算 fit_scale（viewport 可能已变化），再叠加用户倍数。
            self._fit_scale = self._compute_fit_scale()
            total = self._current_total_scale()
            disp_w = int(self._img_w * total)
            disp_h = int(self._img_h * total)

            target_w = max(int(disp_w * dpr), 1)
            target_h = max(int(disp_h * dpr), 1)
            cache_key = (
                int(self._pixmap.cacheKey()),
                target_w,
                target_h,
                int(dpr * 1000),
            )
            if cache_key != self._display_cache_key:
                scaled = self._pixmap.scaled(
                    target_w,
                    target_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(dpr)
                self._display_cache_key = cache_key
                self._display_cache_pixmap = scaled
            else:
                scaled = self._display_cache_pixmap
            if scaled is None:
                return
            self._image_label.setPixmap(scaled)
            # label 尺寸 = 缩放后 pixmap 尺寸；超出 viewport 时 QScrollArea 自动滚动。
            # setWidgetResizable(False) 让此 resize 生效（不被 viewport 强制夹住）。
            self._image_label.resize(max(disp_w, 200), max(disp_h, 200))
            self._image_label.setStyleSheet(
                f"QLabel {{ background-color: {theme.Colors.surface};"
                f" border: 1px solid {theme.Colors.border}; }}"
            )
            QTimer.singleShot(0, self._update_overlay_deferred)

    def _invalidate_display_cache(self) -> None:
        self._display_cache_key = None
        self._display_cache_pixmap = None

    def _apply_debounced_resize(self) -> None:
        viewport = self._scroll_area.viewport().size()
        size_key = (
            viewport.width(),
            viewport.height(),
            int(self.devicePixelRatio() * 1000),
        )
        if size_key == self._last_resize_viewport_size:
            return
        self._last_resize_viewport_size = size_key
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._update_display()
            self._reapply_highlight()
        else:
            self._apply_overlay_geometry()

    def _update_overlay_deferred(self) -> None:
        """延迟一帧更新 overlay，确保布局已完成"""
        if self._content_list:
            self._update_type_overlay()
        elif self._text_blocks:
            self._update_block_overlay()
        self._apply_overlay_geometry()
        self._update_zoom_controls()

    def _update_block_overlay(self) -> None:
        """根据当前文本块和图片显示计算置信度模式覆盖矩形"""
        self._overlay.clear()
        self._block_screen_rects = {}
        self._block_screen_polys = {}
        self._confidence_overlay_indices = []
        self._confidence_overlay_local_by_source = {}
        self._type_screen_rects = []

        if not self._pixmap or not self._text_blocks:
            return

        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
        if disp_w <= 0 or disp_h <= 0:
            return

        candidates = self._text_page_indices.get(self._current_page, [])[
            :MAX_INTERACTIVE_OVERLAY_BLOCKS
        ]
        overlay_rects = []
        for source_index in candidates:
            block = self._text_blocks[source_index]
            if block.bbox is None:
                continue
            x0, y0, x1, y1 = block.bbox
            sx = x0 / 1000.0 * disp_w + offset_x
            sy = y0 / 1000.0 * disp_h + offset_y
            sw = (x1 - x0) / 1000.0 * disp_w
            sh = (y1 - y0) / 1000.0 * disp_h
            self._block_screen_rects[source_index] = (sx, sy, sw, sh)
            # 多边形：若有则转成屏幕坐标 QPolygonF，让 overlay 画贴合的平行四边形
            # （旋转/倾斜文字不再用过大的 AABB）；否则 None，回退到 AABB rect。
            poly = self._polygon_to_screen(
                block.polygon, disp_w, disp_h, offset_x, offset_y
            )
            self._block_screen_polys[source_index] = poly
            self._confidence_overlay_local_by_source[source_index] = len(overlay_rects)
            self._confidence_overlay_indices.append(source_index)
            overlay_rects.append(
                (
                    sx,
                    sy,
                    sw,
                    sh,
                    block.score,
                    block.text,
                    block.is_manually_edited,
                    poly,
                )
            )

        self._overlay.set_confidence_blocks(overlay_rects)
        self._apply_overlay_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_timer.start()

    def closeEvent(self, event) -> None:
        self.request_shutdown()
        super().closeEvent(event)

    def request_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._resize_timer.stop()
        self._image_load_jobs.close()
        self._pdf_jobs.close()
        self._block_index_jobs.close()
        self._content_index_jobs.close()

    def drain(self, timeout_ms: int = 0) -> bool:
        import time

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        if not self._image_load_jobs.drain(max(0, timeout_ms)):
            return False
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if not self._pdf_jobs.drain(remaining_ms):
            return False
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if not self._block_index_jobs.drain(remaining_ms):
            return False
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        return self._content_index_jobs.drain(remaining_ms)

    def is_drained(self) -> bool:
        return self.drain(0)
