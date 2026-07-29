"""二维码生成与识别标签页"""

import asyncio
import io
import logging
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.utils.export_jobs import (
    ExportSaveJob,
    save_bitmap_operation,
    save_svg_operation,
)
from vibeocr.classic.utils.image_jobs import GenerationImageJobs, decode_image_file

logger = logging.getLogger(__name__)


# Cancelling the asyncio waiter does not stop work already submitted by
# asyncio.to_thread(). Keep the owning tab alive and expose the real native
# completion boundary to the shutdown coordinator.
_QR_NATIVE_CALLS_LOCK = threading.Lock()
_ACTIVE_QR_NATIVE_CALLS: dict[threading.Event, object] = {}


def _run_tracked_native_call(
    owner: object,
    done_event: threading.Event,
    schedule_on_gui: Any,
    cleanup_on_gui: Any,
    operation: Any,
    *args: Any,
) -> Any:
    try:
        return operation(*args)
    finally:
        # Do not dereference the QWidget owner from the native worker.  The
        # completion event and module keepalive are sufficient for GUI polling.
        done_event.set()
        with _QR_NATIVE_CALLS_LOCK:
            _ACTIVE_QR_NATIVE_CALLS.pop(done_event, None)
        # Never mutate QWidget-owned bookkeeping from the executor thread.
        try:
            schedule_on_gui(cleanup_on_gui)
        except RuntimeError:
            # The loop may already be closed during interpreter teardown.
            pass

FORMAT_ITEMS = [
    ("QR Code", "qr"),
    ("Code 128", "code128"),
    ("Code 39", "code39"),
    ("EAN-13", "ean13"),
    ("EAN-8", "ean8"),
    ("UPC-A", "upc-a"),
    ("ISBN-13", "isbn13"),
    ("ITF", "itf"),
    ("Codabar", "codabar"),
    ("PZN", "pzn"),
    ("GS1-128", "gs1-128"),
]

LABEL_POS_MAP = {0: "bottom", 1: "top", 2: "none"}


def _pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
    if pil_image.mode == "RGBA":
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(
            data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888
        )
    else:
        data = pil_image.tobytes("raw", "RGB")
        qimage = QImage(
            data, pil_image.width, pil_image.height, QImage.Format.Format_RGB888
        )
    return QPixmap.fromImage(qimage.copy())


def _scale_pixmap_for_label(pixmap: QPixmap, label: QLabel) -> QPixmap:
    """缩放 pixmap 使其完整显示在 label 内，适配高分屏。"""
    dpr = label.devicePixelRatio()
    target_w = int(label.width() * dpr)
    target_h = int(label.height() * dpr)
    scaled = pixmap.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


def _qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """QPixmap → PIL.Image（RGB）。用 PNG 中转，不引入新依赖。"""
    from io import BytesIO

    from PySide6.QtCore import QBuffer

    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    buffer.seek(0)
    img = Image.open(BytesIO(buffer.data().data()))
    buffer.close()
    return img.convert("RGB")


def _decode_type_label(type_str: str) -> str:
    """把 pyzbar 的 type 字符串转成更友好的中文标签。"""
    t = type_str.upper()
    if "QR" in t:
        return "二维码"
    return f"条形码·{type_str}"


def _escape_for_richtext(text: str) -> str:
    """转义用于富文本属性值的字符（防止单引号/HTML 破坏）。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
        .replace('"', "&quot;")
    )


class DropLabel(QLabel):
    """支持拖入图片数据的 QLabel。"""

    imageDropped = Signal(QPixmap)
    fileDropped = Signal(str)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.fileDropped.emit(path)
                event.acceptProposedAction()
                return
        pm = QPixmap(event.mimeData().imageData())
        if not pm.isNull():
            self.imageDropped.emit(pm)
            event.acceptProposedAction()
        else:
            event.ignore()


class DecodeResultWidget(QWidget):
    """单条识别结果展示：序号 + 类型标签 + 内容/链接 + 操作按钮。"""

    open_url_requested = Signal(str)
    copy_requested = Signal(str)

    def __init__(
        self,
        index: int,
        data: str,
        type_label: str,
        is_url: bool,
        safe_data: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._data = data
        href_value = safe_data if safe_data is not None else data

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)

        idx_label = QLabel(f"{index}.")
        idx_label.setFixedWidth(20)
        row.addWidget(idx_label)

        type_tag = QLabel(type_label)
        type_tag.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.hover_bg};"
            f" color: {theme.Colors.text};"
            f" border-radius: 6px; padding: 1px 6px;"
            f" font-size: {theme.Typography.caption}px; }}"
        )
        row.addWidget(type_tag)

        content_label = QLabel()
        content_label.setWordWrap(True)
        display = data if len(data) <= 80 else data[:77] + "..."
        if is_url:
            content_label.setText(
                f"<a href='{href_value}' style='color:{theme.Colors.accent}; text-decoration: underline;'>"
                f"{display}</a>"
            )
            content_label.setOpenExternalLinks(False)
            content_label.linkActivated.connect(self._on_link)

            open_btn = QPushButton("🔗打开")
            open_btn.setFixedHeight(22)
            open_btn.clicked.connect(lambda: self.open_url_requested.emit(self._data))
            row.addWidget(open_btn)
        else:
            content_label.setText(display)
            content_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
        row.addWidget(content_label, stretch=1)

        copy_btn = QPushButton("📋复制")
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._data))
        row.addWidget(copy_btn)

    def _on_link(self, _href: str) -> None:
        # 忽略富文本回传的 href（可能被 _escape_for_richtext 转义），
        # 始终用原始 data，保证含 & 等字符的 URL 正确打开。
        self.open_url_requested.emit(self._data)


class QrcodeTab(QWidget):
    """二维码生成与识别标签页（左侧共享预览 + 右侧生成/识别子标签页）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        backend: object | None = None,
    ) -> None:
        super().__init__(parent)
        # The QR tab talks to its exclusive WorkerHost over RPC (ADR §5.1):
        # the UI never imports backend services directly. The SyncBackendClient
        # owns the worker subprocess and a background asyncio loop. Tests inject
        # a fake backend (duck-typed: generate_qrcode_sync/generate_qrcode_svg_sync/
        # decode_qrcode_sync) to avoid spawning a real worker.
        self._backend = backend
        self._uses_shared_backend = backend is None
        self._current_image: Image.Image | None = None
        self._logo_path: str | None = None

        # 子页预览状态（切换时保存/恢复）
        self._gen_preview_pixmap: QPixmap | None = None
        self._decode_pending_pixmap: QPixmap | None = None
        self._decode_results: list = []  # list[DecodedItem]，由 _on_decode 填充
        self._closing = False
        self._preview_generation = 0
        self._decode_generation = 0
        self._preview_task: asyncio.Task | None = None
        self._decode_task: asyncio.Task | None = None
        self._native_done_events: set[threading.Event] = set()
        self._save_job: ExportSaveJob | None = None
        self._save_generation = 0
        self._file_load_jobs = GenerationImageJobs(self)
        self._file_load_jobs.completed.connect(self._on_decode_image_file_loaded)
        self._file_load_jobs.failed.connect(self._on_decode_image_file_failed)
        self._preview_scale_cache_key: tuple[int, int, int, int, int] | None = None
        self._preview_scale_timer = QTimer(self)
        self._preview_scale_timer.setSingleShot(True)
        self._preview_scale_timer.setInterval(24)
        self._preview_scale_timer.timeout.connect(self._apply_preview_scale)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._setup_ui()
        self._connect_signals()

        # Ctrl+V 粘贴图片快捷键：仅在识别子页激活时启用
        self._decode_paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self._decode_paste_shortcut.setEnabled(False)
        self._decode_paste_shortcut.activated.connect(self._on_paste_image)

        self._on_sub_tab_changed(0)  # 初始：生成子页

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._splitter = QSplitter()

        # ── 左侧：预览区（生成与识别共享） ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._preview_label = DropLabel("输入内容后自动生成预览")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(200, 200)
        self._preview_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.Colors.surface_alt};"
            f" border: 1px solid {theme.Colors.border};"
            f" border-radius: {theme.Radius.sm}px; }}"
        )
        self._preview_label.setAcceptDrops(False)  # 仅识别子页激活时开启
        self._preview_label.imageDropped.connect(self._on_image_input)
        self._preview_label.fileDropped.connect(self._request_decode_image_file)
        left_layout.addWidget(self._preview_label, stretch=1)

        # 生成操作栏（保存/复制）—— 子页切换时显隐
        self._gen_action_bar_widget = QWidget()
        gen_action_bar = QHBoxLayout(self._gen_action_bar_widget)
        gen_action_bar.setContentsMargins(0, 0, 0, 0)
        gen_action_bar.setSpacing(6)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setFixedHeight(28)
        self._btn_copy = QPushButton("复制到剪贴板")
        self._btn_copy.setObjectName("btnCopy")
        self._btn_copy.setFixedHeight(28)

        gen_action_bar.addWidget(self._btn_save)
        gen_action_bar.addWidget(self._btn_copy)
        gen_action_bar.addStretch()
        left_layout.addWidget(self._gen_action_bar_widget)

        # 识别操作栏（粘贴/选择/识别/清空）—— 子页切换时显隐，初始隐藏
        self._decode_action_bar_widget = QWidget()
        dec_action_bar = QHBoxLayout(self._decode_action_bar_widget)
        dec_action_bar.setContentsMargins(0, 0, 0, 0)
        dec_action_bar.setSpacing(6)

        self._btn_paste_img = QPushButton("粘贴图片")
        self._btn_paste_img.setObjectName("btnPasteImg")
        self._btn_paste_img.setFixedHeight(28)
        self._btn_select_img = QPushButton("选择图片...")
        self._btn_select_img.setObjectName("btnSelectImg")
        self._btn_select_img.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_paste_img)
        dec_action_bar.addWidget(self._btn_select_img)
        dec_action_bar.addStretch()
        self._btn_decode = QPushButton("🔍 识别")
        self._btn_decode.setObjectName("btnDecode")
        self._btn_decode.setFixedHeight(28)
        self._btn_decode.setEnabled(False)  # 无图时禁用
        dec_action_bar.addWidget(self._btn_decode)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.setObjectName("btnClear")
        self._btn_clear.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_clear)
        self._decode_action_bar_widget.setVisible(False)
        left_layout.addWidget(self._decode_action_bar_widget)

        self._splitter.addWidget(left_panel)

        # ── 右侧：嵌套子标签页 ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.setObjectName("subTabs")

        # 「生成」子页 = 原 QScrollArea 包裹的参数面板
        self._sub_tabs.addTab(self._build_generate_panel(), "生成")

        # 「识别」子页（Task 5 填充真实内容，先占位）
        self._sub_tabs.addTab(self._build_decode_panel(), "识别")

        self._splitter.addWidget(self._sub_tabs)
        self._splitter.setSizes([500, 300])

        layout.addWidget(self._splitter, stretch=1)

    def _build_generate_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(360)

        params_widget = QWidget()
        params_layout = QVBoxLayout(params_widget)
        params_layout.setContentsMargins(8, 4, 8, 4)
        params_layout.setSpacing(8)

        # ── 1. 输入内容 ──
        params_layout.addWidget(self._create_section_label("输入内容"))

        self._format_combo = QComboBox()
        for name, _ in FORMAT_ITEMS:
            self._format_combo.addItem(name)
        params_layout.addWidget(self._format_combo)

        self._text_input = QPlainTextEdit()
        self._text_input.setPlaceholderText("输入要编码的内容...")
        self._text_input.setMaximumHeight(80)
        params_layout.addWidget(self._text_input)

        self._btn_paste = QPushButton("从剪贴板粘贴")
        self._btn_paste.setFixedHeight(26)
        params_layout.addWidget(self._btn_paste)

        # ── 2. 尺寸与纠错 ──
        params_layout.addWidget(self._create_section_label("尺寸与纠错"))

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("尺寸:"))
        self._size_spin = self._create_spin_box(100, 2000, 600)
        size_row.addWidget(self._size_spin)
        size_row.addStretch()
        params_layout.addLayout(size_row)

        ec_row = QHBoxLayout()
        self._ec_label = QLabel("纠错等级:")
        ec_row.addWidget(self._ec_label)
        self._ec_group = _create_button_group(self)
        for btn in self._ec_group.buttons():
            ec_row.addWidget(btn)
        params_layout.addLayout(ec_row)

        # ── 3. 颜色设置 ──
        params_layout.addWidget(self._create_section_label("颜色设置"))

        color_row = QHBoxLayout()
        self._fg_btn = QPushButton("前景色")
        self._fg_color = "#000000"
        self._fg_btn.setStyleSheet(self._color_btn_style(self._fg_color))
        color_row.addWidget(self._fg_btn)

        self._bg_btn = QPushButton("背景色")
        self._bg_color = "#FFFFFF"
        self._bg_btn.setStyleSheet(self._color_btn_style(self._bg_color))
        color_row.addWidget(self._bg_btn)

        self._invert_check = QCheckBox("反色")
        color_row.addWidget(self._invert_check)
        params_layout.addLayout(color_row)

        # ── 4. Logo 嵌入（仅二维码）──
        params_layout.addWidget(self._create_section_label("Logo 嵌入"))

        logo_row = QHBoxLayout()
        self._logo_check = QCheckBox("启用")
        logo_row.addWidget(self._logo_check)
        self._logo_select_btn = QPushButton("选择图片")
        self._logo_select_btn.setEnabled(False)
        logo_row.addWidget(self._logo_select_btn)
        params_layout.addLayout(logo_row)

        logo_size_row = QHBoxLayout()
        logo_size_row.addWidget(QLabel("Logo 大小比例:"))
        self._logo_ratio_spin = self._create_spin_box(5, 50, 20)
        self._logo_ratio_spin.setSuffix("%")
        logo_size_row.addWidget(self._logo_ratio_spin)
        logo_size_row.addStretch()
        params_layout.addLayout(logo_size_row)
        self._logo_section_widgets = [
            self._logo_check,
            self._logo_select_btn,
            self._logo_ratio_spin,
        ]

        # ── 5. 文字说明 ──
        params_layout.addWidget(self._create_section_label("文字说明"))

        self._label_text_input = QLineEdit()
        self._label_text_input.setPlaceholderText("自定义说明文字（留空使用原始内容）")
        params_layout.addWidget(self._label_text_input)

        label_pos_row = QHBoxLayout()
        label_pos_row.addWidget(QLabel("位置:"))
        self._label_pos_combo = QComboBox()
        self._label_pos_combo.addItems(["下方", "上方", "无"])
        label_pos_row.addWidget(self._label_pos_combo)
        label_pos_row.addStretch()
        params_layout.addLayout(label_pos_row)

        label_font_row = QHBoxLayout()
        label_font_row.addWidget(QLabel("字体大小:"))
        self._label_font_spin = self._create_spin_box(8, 48, 12)
        label_font_row.addWidget(self._label_font_spin)
        label_font_row.addStretch()
        params_layout.addLayout(label_font_row)

        params_layout.addStretch()

        scroll.setWidget(params_widget)
        return scroll

    def _build_decode_panel(self) -> QWidget:
        """构建「识别」子页。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        hint = QLabel(
            "支持粘贴图片 (Ctrl+V)、拖入图片到左侧预览区、\n或点击下方选择文件"
        )
        hint.setStyleSheet(
            f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption}px;"
        )
        layout.addWidget(hint)

        # 识别结果区
        layout.addWidget(self._create_section_label("识别结果"))

        self._decode_result_list = QListWidget()
        self._decode_result_list.setObjectName("decodeResultList")
        layout.addWidget(self._decode_result_list, stretch=1)

        # 底部操作
        bottom_row = QHBoxLayout()
        self._btn_copy_all = QPushButton("复制全部")
        self._btn_copy_all.setObjectName("btnCopyAll")
        self._btn_copy_all.setFixedHeight(26)
        bottom_row.addWidget(self._btn_copy_all)
        bottom_row.addStretch()
        self._result_count_label = QLabel("识别到 0 条结果")
        self._result_count_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        bottom_row.addWidget(self._result_count_label)
        layout.addLayout(bottom_row)

        return panel

    def _connect_signals(self) -> None:
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._text_input.textChanged.connect(self._schedule_refresh)
        self._size_spin.valueChanged.connect(self._schedule_refresh)
        self._ec_group.buttonClicked.connect(self._schedule_refresh)
        self._invert_check.stateChanged.connect(self._schedule_refresh)
        self._logo_check.stateChanged.connect(self._on_logo_check_changed)
        self._logo_select_btn.clicked.connect(self._on_logo_select)
        self._logo_ratio_spin.valueChanged.connect(self._schedule_refresh)
        self._label_text_input.textChanged.connect(self._schedule_refresh)
        self._label_pos_combo.currentIndexChanged.connect(self._schedule_refresh)
        self._label_font_spin.valueChanged.connect(self._schedule_refresh)
        self._fg_btn.clicked.connect(self._on_pick_fg_color)
        self._bg_btn.clicked.connect(self._on_pick_bg_color)
        self._btn_paste.clicked.connect(self._on_paste_from_clipboard)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_copy.clicked.connect(self._on_copy)
        self._sub_tabs.currentChanged.connect(self._on_sub_tab_changed)
        # 识别子页按钮
        self._btn_paste_img.clicked.connect(self._on_paste_image)
        self._btn_select_img.clicked.connect(self._on_select_image)
        self._btn_decode.clicked.connect(self._on_decode)
        self._btn_clear.clicked.connect(self._on_clear_decode)
        self._btn_copy_all.clicked.connect(self._on_copy_all)

    def _on_sub_tab_changed(self, index: int) -> None:
        """切换生成/识别子页时，保存/恢复预览状态并切换操作栏与拖入支持。"""
        is_decode = index == 1

        if is_decode:
            # 恢复识别页预览
            if self._decode_pending_pixmap is not None:
                self._apply_preview_scale(force=True)
            else:
                self._preview_label.clear()
                self._preview_label.setText("粘贴、拖入或选择图片以识别")
        else:
            # 恢复生成页预览
            if self._gen_preview_pixmap is not None:
                self._apply_preview_scale(force=True)
            else:
                self._preview_label.clear()
                self._preview_label.setText("输入内容后自动生成预览")

        self._gen_action_bar_widget.setVisible(not is_decode)
        self._decode_action_bar_widget.setVisible(is_decode)
        self._preview_label.setAcceptDrops(is_decode)
        if hasattr(self, "_decode_paste_shortcut"):
            self._decode_paste_shortcut.setEnabled(is_decode)

    def _active_preview_source(self) -> QPixmap | None:
        if self._sub_tabs.currentIndex() == 1:
            return self._decode_pending_pixmap
        return self._gen_preview_pixmap

    def _apply_preview_scale(self, *, force: bool = False) -> None:
        source = self._active_preview_source()
        if source is None or source.isNull():
            return
        dpr = self._preview_label.devicePixelRatio()
        cache_key = (
            int(source.cacheKey()),
            self._preview_label.width(),
            self._preview_label.height(),
            int(dpr * 1000),
            self._sub_tabs.currentIndex(),
        )
        if not force and cache_key == self._preview_scale_cache_key:
            return
        self._preview_scale_cache_key = cache_key
        self._preview_label.setPixmap(
            _scale_pixmap_for_label(source, self._preview_label)
        )

    # ── helpers ──

    @staticmethod
    def _create_section_label(text: str) -> QLabel:
        label = QLabel(f"<b>{text}</b>")
        label.setContentsMargins(0, 4, 0, 0)
        return label

    @staticmethod
    def _create_spin_box(min_val: int, max_val: int, default: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(min_val, max_val)
        spin.setValue(default)
        spin.setFixedWidth(80)
        return spin

    @staticmethod
    def _color_btn_style(color: str) -> str:
        return f"QPushButton {{ background-color: {color}; border: 1px solid {theme.Colors.border_strong}; padding: 4px; }}"

    # ── 生成子页 slots ──

    def _on_format_changed(self, index: int) -> None:
        is_qr = FORMAT_ITEMS[index][1] == "qr"
        self._ec_label.setVisible(is_qr)
        for btn in self._ec_group.buttons():
            btn.setVisible(is_qr)
        for w in self._logo_section_widgets:
            w.setVisible(is_qr)
        self._schedule_refresh()

    def _on_logo_check_changed(self, state: int) -> None:
        self._logo_select_btn.setEnabled(bool(state))
        self._logo_ratio_spin.setEnabled(bool(state))
        self._schedule_refresh()

    def _on_logo_select(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Logo 图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*)",
        )
        if path:
            self._logo_path = path
            self._logo_select_btn.setText(Path(path).name)
            self._schedule_refresh()

    def _on_pick_fg_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor(self._fg_color), self, "选择前景色")
        if color.isValid():
            self._fg_color = color.name()
            self._fg_btn.setStyleSheet(self._color_btn_style(self._fg_color))
            self._schedule_refresh()

    def _on_pick_bg_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor(self._bg_color), self, "选择背景色")
        if color.isValid():
            self._bg_color = color.name()
            self._bg_btn.setStyleSheet(self._color_btn_style(self._bg_color))
            self._schedule_refresh()

    def _on_paste_from_clipboard(self) -> None:
        from PySide6.QtGui import QGuiApplication

        text = QGuiApplication.clipboard().text()
        if text:
            self._text_input.setPlainText(text)

    def _schedule_refresh(self) -> None:
        self._preview_generation += 1
        if self._preview_task is not None and not self._preview_task.done():
            self._preview_task.cancel()
        self._debounce_timer.start()

    def _build_options(self) -> dict:
        """Build the qrcode.generate RPC options bag from the UI controls."""
        fmt_key = FORMAT_ITEMS[self._format_combo.currentIndex()][1]
        ec_btn = self._ec_group.checkedButton()
        ec_val = ec_btn.property("ec_value") if ec_btn else "M"

        # Map the UI format key to the RPC format + barcode_format fields.
        if fmt_key == "qr":
            rpc_format = "qrcode"
            barcode_format = None
        else:
            rpc_format = "barcode"
            barcode_format = fmt_key

        options: dict = {"format": rpc_format}
        if barcode_format is not None:
            options["barcode_format"] = barcode_format
        options["size"] = self._size_spin.value()
        options["error_correction"] = ec_val
        options["fg_color"] = self._fg_color
        options["bg_color"] = self._bg_color
        options["invert"] = self._invert_check.isChecked()
        logo_path = self._logo_path if self._logo_check.isChecked() else None
        if logo_path:
            options["logo_path"] = logo_path
            options["logo_ratio"] = self._logo_ratio_spin.value() / 100.0
        label_text = self._label_text_input.text()
        if label_text:
            options["label_text"] = label_text
            options["label_position"] = LABEL_POS_MAP.get(
                self._label_pos_combo.currentIndex(), "bottom"
            )
            options["label_font_size"] = self._label_font_spin.value()
        return options

    # -- backend bridge (sync RPC over the exclusive WorkerHost) --------

    def _call_backend_generate(self, text: str, options: dict) -> bytes:
        """Generate QR via v2 supervisor."""
        if self._backend is not None:
            generate = getattr(self._backend, "generate_qrcode_sync", None)
            if callable(generate):
                return generate(text, options=options)
        return self._generate_via_supervisor(text, options)

    def _call_backend_generate_svg(self, text: str, options: dict) -> str:
        """Generate SVG via an injected client or the v2 supervisor."""
        if self._backend is not None:
            generate = getattr(self._backend, "generate_qrcode_svg_sync", None)
            if callable(generate):
                return generate(text, options=options)
        import base64

        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        client = get_supervisor_adapter().inference_sync_client
        if client is None:
            raise RuntimeError("supervisor utility client is unavailable")
        return base64.b64decode(
            client.generate_qrcode(text, fmt="svg", options=options)
        ).decode("utf-8")

    def _call_backend_decode(self, image_bytes: bytes):
        """Decode QR via v2 supervisor."""
        return self._decode_via_supervisor(image_bytes)

    def _generate_via_supervisor(self, text: str, options: dict) -> bytes:
        """Generate QR via supervisor /v2/qrcode/generate (sync, in QThread)."""
        import base64

        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        adapter = get_supervisor_adapter()
        client = adapter.inference_sync_client
        if client is None:
            raise RuntimeError("supervisor utility client is unavailable")
        fmt = "qrcode" if options.get("format", "qr") == "qr" else options.get("format", "qrcode")
        return base64.b64decode(
            client.generate_qrcode(text, fmt=fmt, options=options)
        )

    def _decode_via_supervisor(self, image_bytes: bytes):
        """Decode QR via supervisor /v2/qrcode/decode (sync, in QThread)."""
        from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

        adapter = get_supervisor_adapter()
        client = adapter.inference_sync_client
        if client is None:
            raise RuntimeError("supervisor utility client is unavailable")
        return client.decode_qrcode(image_bytes)

    @staticmethod
    def _pil_to_png_bytes(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _qimage_to_png_bytes(image: QImage) -> bytes:
        """Encode a detached QImage away from the GUI thread."""
        from PySide6.QtCore import QBuffer

        buffer = QBuffer()
        if not buffer.open(QBuffer.OpenModeFlag.ReadWrite):
            raise RuntimeError("cannot open image buffer")
        try:
            if not image.save(buffer, "PNG"):  # pyright: ignore[reportCallIssue, reportArgumentType]
                raise RuntimeError("PNG encoding failed")
            return bytes(buffer.data().data())
        finally:
            buffer.close()

    @staticmethod
    def _load_generated_image(png_bytes: bytes) -> Image.Image:
        """Decode and detach an image from its byte stream in a worker thread."""
        with Image.open(io.BytesIO(png_bytes)) as source:
            image = source.convert("RGBA" if source.mode == "RGBA" else "RGB")
            image.load()
            return image

    @staticmethod
    def _consume_task_exception(task: asyncio.Task) -> None:
        if not task.cancelled():
            try:
                task.exception()
            except Exception:
                pass

    async def _generate_preview_async(self, text: str, options: dict) -> Image.Image:
        return await self._to_thread_tracked(
            self._generate_preview_sync, text, options
        )

    async def _to_thread_tracked(self, operation: Any, *args: Any) -> Any:
        """Run one native call while tracking its lifetime beyond Task.cancel()."""
        loop = asyncio.get_running_loop()
        done_event = threading.Event()
        with _QR_NATIVE_CALLS_LOCK:
            self._native_done_events.add(done_event)
            _ACTIVE_QR_NATIVE_CALLS[done_event] = self

        def cleanup_on_gui() -> None:
            with _QR_NATIVE_CALLS_LOCK:
                self._native_done_events.discard(done_event)

        return await asyncio.to_thread(
            _run_tracked_native_call,
            self,
            done_event,
            loop.call_soon_threadsafe,
            cleanup_on_gui,
            operation,
            *args,
        )

    def _generate_preview_sync(self, text: str, options: dict) -> Image.Image:
        png_bytes = self._call_backend_generate(text, options)
        return self._load_generated_image(png_bytes)

    def _refresh_preview(self) -> None:
        text = self._text_input.toPlainText().strip()
        generation = self._preview_generation
        if not text:
            self._preview_label.setText("输入内容后自动生成预览")
            self._current_image = None
            self._gen_preview_pixmap = None
            self._preview_scale_cache_key = None
            return

        try:
            options = self._build_options()
        except Exception as exc:
            self._on_preview_error(generation, exc)
            return

        from vibeocr.classic.utils.qt_async import get_async_runner

        task = get_async_runner().run(
            self._generate_preview_async(text, options),
            on_complete=lambda image: self._on_preview_ready(generation, image),
            on_error=lambda exc: self._on_preview_error(generation, exc),
        )
        self._preview_task = task

        def _clear_ref(completed: asyncio.Task) -> None:
            self._consume_task_exception(completed)
            if self._preview_task is completed:
                self._preview_task = None

        task.add_done_callback(_clear_ref)

    def _on_preview_ready(self, generation: int, image: Image.Image) -> None:
        if self._closing or generation != self._preview_generation:
            return
        self._current_image = image
        pixmap = _pil_to_qpixmap(image)
        self._gen_preview_pixmap = pixmap
        if self._sub_tabs.currentIndex() == 0:
            self._apply_preview_scale(force=True)

    def _on_preview_error(self, generation: int, exc: Exception) -> None:
        if self._closing or generation != self._preview_generation:
            return
        logger.error("生成预览失败: %s", exc, exc_info=exc)
        self._preview_label.setText(
            f"<span style='color:{theme.Colors.danger};'>生成失败：{exc}</span>"
        )
        self._current_image = None
        self._gen_preview_pixmap = None
        self._preview_scale_cache_key = None

    def _on_save(self) -> None:
        if self._current_image is None or self._save_job is not None or self._closing:
            return

        from PySide6.QtWidgets import QFileDialog

        options = self._build_options()
        is_qr = options["format"] == "qrcode"

        filters = "PNG (*.png);;JPG (*.jpg)"
        if is_qr and not options.get("logo_path"):
            filters += ";;SVG (*.svg)"

        path, _ = QFileDialog.getSaveFileName(self, "保存", "", filters)
        if not path:
            return

        output_path = Path(path)
        if path.lower().endswith(".svg"):
            text = self._text_input.toPlainText().strip()
            svg_options = {
                k: v
                for k, v in options.items()
                if k in ("error_correction", "fg_color", "bg_color")
            }
            operation = save_svg_operation(
                self._backend, text, svg_options, output_path
            )
        else:
            fmt = "JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG"
            # PIL.copy() 在 GUI 线程完成 detached 快照；编码与写盘进入 worker。
            operation = save_bitmap_operation(
                self._current_image.copy(), output_path, fmt
            )

        self._save_generation += 1
        job = ExportSaveJob(operation)
        job.setProperty("generation", self._save_generation)
        self._save_job = job
        self._btn_save.setEnabled(False)
        self._btn_copy.setEnabled(False)
        job.completed.connect(self._on_save_completed)
        job.failed.connect(self._on_save_failed)
        job.stopped.connect(self._on_save_job_finished)
        job.start()

    def _is_current_save_signal(self) -> bool:
        job = self.sender()
        return bool(
            not self._closing
            and job is self._save_job
            and job.property("generation") == self._save_generation
        )

    def _on_save_completed(self, output_path: object) -> None:
        if self._is_current_save_signal():
            logger.info("二维码已保存: %s", output_path)

    def _on_save_failed(self, error: str) -> None:
        if not self._is_current_save_signal():
            return
        logger.error("保存失败: %s", error)
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "保存失败", error)

    def _on_save_job_finished(self, job: ExportSaveJob) -> None:
        if job is not self._save_job:
            return
        self._save_job = None
        enabled = not self._closing and self._current_image is not None
        self._btn_save.setEnabled(enabled)
        self._btn_copy.setEnabled(enabled)
        job.deleteLater()

    def cancel_save(self) -> None:
        """协作式取消保存，并丢弃所有迟到结果。"""
        self._save_generation += 1
        if self._save_job is not None:
            self._save_job.cancel()

    def drain(self, timeout_ms: int = 0) -> bool:
        """只等待保存、图像加载和真实 to_thread 调用；不触碰 GUI/引用。"""
        deadline = time.monotonic() + max(0, timeout_ms) / 1000

        def remaining_ms() -> int:
            return max(0, int((deadline - time.monotonic()) * 1000))

        job = self._save_job
        if job is not None and not job.drain(remaining_ms()):
            return False
        if not self._file_load_jobs.drain(remaining_ms()):
            return False
        with _QR_NATIVE_CALLS_LOCK:
            done_events = tuple(self._native_done_events)
        for done_event in done_events:
            if done_event.is_set():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not done_event.wait(remaining):
                return False
        return True

    def is_drained(self) -> bool:
        """无副作用探测所有 QR 后台原生工作是否已经结束。"""
        with _QR_NATIVE_CALLS_LOCK:
            self._native_done_events = {
                event for event in self._native_done_events if not event.is_set()
            }
        return self.drain(0)

    def _on_copy(self) -> None:
        if self._current_image is None:
            return

        from PySide6.QtGui import QGuiApplication

        pixmap = _pil_to_qpixmap(self._current_image)
        QGuiApplication.clipboard().setPixmap(pixmap)
        logger.debug("二维码已复制到剪贴板")

    # ── 识别子页 slots ──

    def _on_image_input(self, pixmap: QPixmap) -> None:
        """统一的图片输入入口（粘贴/拖入/选择文件）。"""
        self._file_load_jobs.cancel_current()
        self._btn_select_img.setEnabled(not self._closing)
        self._apply_decode_pixmap(pixmap)

    def _apply_decode_pixmap(self, pixmap: QPixmap) -> None:
        """GUI 线程应用已经解码或来自剪贴板的 QPixmap。"""
        if pixmap.isNull():
            return
        self._invalidate_decode_task()
        # 归一化 devicePixelRatio
        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)
        self._decode_pending_pixmap = pixmap
        self._apply_preview_scale(force=True)
        self._btn_decode.setEnabled(True)
        # 清空上次结果
        self._decode_result_list.clear()
        self._decode_results = []
        self._result_count_label.setText("识别到 0 条结果")

    def _on_paste_image(self) -> None:
        clipboard = QGuiApplication.clipboard()
        pm = clipboard.pixmap()
        if not pm.isNull():
            self._on_image_input(pm)

    def _on_select_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
            ";;所有文件 (*)",
        )
        if path:
            self._request_decode_image_file(path)

    @Slot(str)
    def _request_decode_image_file(self, path: str) -> None:
        if self._closing:
            return
        self._invalidate_decode_task()
        self._decode_pending_pixmap = None
        self._btn_decode.setEnabled(False)
        self._btn_select_img.setEnabled(False)
        self._decode_result_list.clear()
        self._decode_results = []
        self._result_count_label.setText("识别到 0 条结果")
        self._preview_label.clear()
        self._preview_label.setText(f"正在加载图片：{Path(path).name}...")
        self._file_load_jobs.submit(
            lambda cancel_event: (path, decode_image_file(path, cancel_event))
        )

    @Slot(int, object)
    def _on_decode_image_file_loaded(self, _generation: int, result: object) -> None:
        if self._closing or not isinstance(result, tuple) or len(result) != 2:
            return
        _path, image = result
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_decode_image_file_failed(_generation, "无法显示所选图片")
            return
        self._btn_select_img.setEnabled(True)
        self._apply_decode_pixmap(pixmap)

    @Slot(int, str)
    def _on_decode_image_file_failed(self, _generation: int, error: str) -> None:
        if self._closing:
            return
        self._btn_select_img.setEnabled(True)
        self._preview_label.clear()
        self._preview_label.setText(
            f"<span style='color:{theme.Colors.danger};'>加载失败：{error}</span>"
        )

    def _on_clear_decode(self) -> None:
        self._file_load_jobs.cancel_current()
        self._invalidate_decode_task()
        self._decode_pending_pixmap = None
        self._preview_scale_cache_key = None
        self._decode_results = []
        self._decode_result_list.clear()
        self._btn_decode.setEnabled(False)
        self._btn_select_img.setEnabled(not self._closing)
        self._result_count_label.setText("识别到 0 条结果")
        self._preview_label.clear()
        self._preview_label.setText("粘贴、拖入或选择图片以识别")

    def _on_decode(self) -> None:
        if (
            self._closing
            or self._decode_pending_pixmap is None
            or (self._decode_task is not None and not self._decode_task.done())
        ):
            return
        self._btn_decode.setEnabled(False)
        self._btn_decode.setText("识别中...")
        self._decode_generation += 1
        generation = self._decode_generation
        image = self._decode_pending_pixmap.toImage().copy()

        from vibeocr.classic.utils.qt_async import get_async_runner

        task = get_async_runner().run(
            self._decode_async(image),
            on_complete=lambda results: self._on_decode_ready(generation, results),
            on_error=lambda exc: self._on_decode_error(generation, exc),
        )
        self._decode_task = task

        def _clear_ref(completed: asyncio.Task) -> None:
            self._consume_task_exception(completed)
            if self._decode_task is completed:
                self._decode_task = None

        task.add_done_callback(_clear_ref)

    async def _decode_async(self, image: QImage):
        return await self._to_thread_tracked(self._decode_image_sync, image)

    def _decode_image_sync(self, image: QImage):
        return self._call_backend_decode(self._qimage_to_png_bytes(image))

    def _on_decode_ready(self, generation: int, results) -> None:
        if self._closing or generation != self._decode_generation:
            return
        self._decode_results = results
        self._decode_result_list.clear()
        if not results:
            hint = QLabel(
                f"<span style='color:{theme.Colors.text_muted};'>未识别到二维码/条形码，请尝试更清晰的图片</span>"
            )
            item = QListWidgetItem()
            self._decode_result_list.addItem(item)
            self._decode_result_list.setItemWidget(item, hint)
            item.setSizeHint(hint.sizeHint())
            for idx, r in enumerate(results, start=1):
                safe_data = _escape_for_richtext(r.data)
                widget = DecodeResultWidget(
                    index=idx,
                    data=r.data,
                    type_label=_decode_type_label(r.format),
                    is_url=r.is_url,
                    safe_data=safe_data,
                )
                widget.open_url_requested.connect(self._on_open_url)
                widget.copy_requested.connect(self._on_copy_single)
                item = QListWidgetItem()
                self._decode_result_list.addItem(item)
                self._decode_result_list.setItemWidget(item, widget)
                item.setSizeHint(widget.sizeHint())

        self._result_count_label.setText(f"识别到 {len(results)} 条结果")
        self._btn_decode.setText("🔍 识别")
        self._btn_decode.setEnabled(self._decode_pending_pixmap is not None)

    def _on_decode_error(self, generation: int, exc: Exception) -> None:
        if self._closing or generation != self._decode_generation:
            return
        logger.error("识别失败: %s", exc, exc_info=exc)
        self._decode_result_list.clear()
        item = QListWidgetItem()
        err_label = QLabel(
            f"<span style='color:{theme.Colors.danger};'>识别失败：{exc}</span>"
        )
        self._decode_result_list.addItem(item)
        self._decode_result_list.setItemWidget(item, err_label)
        item.setSizeHint(err_label.sizeHint())
        self._decode_results = []
        self._result_count_label.setText("识别到 0 条结果")
        self._btn_decode.setText("🔍 识别")
        self._btn_decode.setEnabled(self._decode_pending_pixmap is not None)

    def _invalidate_decode_task(self) -> None:
        self._decode_generation += 1
        if self._decode_task is not None and not self._decode_task.done():
            self._decode_task.cancel()

    def set_closing(self, closing: bool) -> None:
        self._closing = closing
        if not closing:
            self._btn_save.setEnabled(
                self._save_job is None and self._current_image is not None
            )
            self._btn_copy.setEnabled(
                self._save_job is None and self._current_image is not None
            )
            return
        self._debounce_timer.stop()
        self._preview_scale_timer.stop()
        self._file_load_jobs.close()
        self._preview_generation += 1
        self._decode_generation += 1
        for task in (self._preview_task, self._decode_task):
            if task is not None and not task.done():
                task.cancel()
        self.cancel_save()
        self._btn_select_img.setEnabled(False)
        self._btn_save.setEnabled(False)
        self._btn_copy.setEnabled(False)

    def _on_open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _on_copy_single(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _on_copy_all(self) -> None:
        texts = [item.data for item in self._decode_results]
        QGuiApplication.clipboard().setText("\n".join(texts))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._preview_scale_timer.start()

    def closeEvent(self, event) -> None:
        self.set_closing(True)
        super().closeEvent(event)


def _create_button_group(parent: QWidget):
    from PySide6.QtWidgets import QButtonGroup

    group = QButtonGroup(parent)
    for text, val in [("L", "L"), ("M", "M"), ("Q", "Q"), ("H", "H")]:
        rb = QRadioButton(text)
        rb.setProperty("ec_value", val)
        group.addButton(rb)
        if val == "M":
            rb.setChecked(True)
    return group
