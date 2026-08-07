"""ScreenCaptureOverlay — 统一的截图+编辑覆盖层

替代原有的 ScreenshotWidget + ScreenshotEditWindow 双窗口流程，
使用状态机管理 CAPTURING → EDITING 两个阶段。

状态机:
  CAPTURING: 全屏透明覆盖层，截图捕获，选区绘制，放大镜
  EDITING:   内联画布 + 工具栏 + 识别面板
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QBuffer,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRegion,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QWidget,
)

from vibeocr.classic.ui import theme
from vibeocr.classic.utils.image_jobs import (
    ClipboardPngResult,
    GenerationImageJobs,
    compose_screen_images,
    delete_files,
    save_image_file,
    write_clipboard_png,
)
from vibeocr.classic.widgets.editor.annotation_items import (
    BlurItem,
    MosaicItem,
    TextAnnotation,
)
from vibeocr.classic.widgets.inline_edit_canvas import InlineEditCanvas
from vibeocr.classic.widgets.inline_recognition_panel import InlineRecognitionPanel
from vibeocr.classic.widgets.inline_toolbar import InlineToolbar
from vibeocr.classic.widgets.magnifier_overlay import MagnifierOverlay
from vibeocr.classic.widgets.screen_coordinate_mapper import (
    ScreenCoordinateMapper,
    ScreenInfo,
)
from vibeocr.classic.widgets.selection_resize_frame import SelectionResizeFrame

try:
    from vibeocr.classic.widgets.window_detector import WindowDetector
except ImportError:
    WindowDetector = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# 已经由用户确认的保存任务不能随着覆盖层的下一次 capture generation 失效。
# 模块级引用还确保覆盖层关闭后，QRunnable 的完成回调对象不会被提前回收。
_ACTIVE_SAVE_JOB_CONTROLLERS: set[GenerationImageJobs] = set()


class ScreenCaptureOverlay(QWidget):
    """统一的截图+编辑覆盖层

    Signals:
        confirmed(QPixmap, object): 确认识别，传递截图和 OCROptions
        copied(QPixmap): 复制到剪贴板
        saved(str): 另存为文件路径
        save_failed(str): 已确认的另存为任务失败原因
        cancelled(): 取消
    """

    confirmed = Signal(QPixmap, object)
    copied = Signal(QPixmap)
    saved = Signal(str)
    save_failed = Signal(str)
    cancelled = Signal()

    # 最小选区尺寸
    MIN_SELECTION_SIZE = 5

    # 放大倍数选项
    ZOOM_LEVELS = [2, 4, 8]

    # 面板定位阈值
    _PANEL_MIN_WIDTH = 120
    _TOOLBAR_MIN_HEIGHT = 48

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # 窗口标志：无边框、置顶、工具窗口
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")

        # 状态
        self._state: str = "CAPTURING"

        # 临时剪贴板文件管理：复制截图时写入 temp 供资源管理器粘贴（CF_HDROP），
        # 维护进程内列表以滚动清理，避免常驻进程长期堆积临时文件。
        self._temp_clip_files: list[Path] = []
        self._temp_clip_max = 10
        self._closing = False
        self._capture_jobs = GenerationImageJobs(self)
        self._capture_jobs.completed.connect(self._on_screen_composed)
        self._capture_jobs.failed.connect(self._on_background_job_failed)
        self._clipboard_jobs = GenerationImageJobs(self)
        self._clipboard_jobs.completed.connect(self._on_clipboard_job_completed)
        self._clipboard_jobs.failed.connect(self._on_background_job_failed)
        self._save_jobs: dict[
            GenerationImageJobs, tuple[str, threading.Event]
        ] = {}
        self._save_jobs_lock = threading.Lock()
        self._save_shutdown_requested = False
        self._cleanup_jobs = GenerationImageJobs(self)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._schedule_temp_clip_cleanup)

        # 截图相关
        self._start_pos: QPoint | None = None
        self._end_pos: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._screen_pixmap: QPixmap | None = None
        self._virtual_geometry = QRect()
        self._mapper: ScreenCoordinateMapper | None = None

        # HOVER/DRAG 子状态
        self._sub_state: str = "HOVER"
        self._detected_rect: QRect | None = None
        self._window_detector: Any = None
        self._last_detect_pos: QPoint = QPoint()

        # 放大镜相关
        self._current_mouse_pos: QPoint | None = None
        self._magnifier_zoom: int = 4
        self._zoom_index: int = 1

        # EDITING 模式子组件
        self._canvas: InlineEditCanvas | None = None
        self._toolbar: InlineToolbar | None = None
        self._recognition_panel: InlineRecognitionPanel | None = None
        self._captured_pixmap: QPixmap | None = None
        self._resize_frame: SelectionResizeFrame | None = None

        # 管道快捷截图：设置后选区完成直接识别，跳过编辑界面
        self._pending_pipeline: str | None = None

    # ==================== CAPTURING 模式 ====================

    def _logical_rect_to_physical(self, rect: QRect) -> QRect:
        """将逻辑坐标矩形转换为物理坐标矩形，优先使用 mapper，否则回退标量 DPR"""
        if self._mapper is not None:
            return self._mapper.logical_to_screenshot_physical(rect)
        dpr = 1.0
        return QRect(
            int(rect.x() * dpr),
            int(rect.y() * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )

    def start_capture(self) -> None:
        """开始截图（支持多屏幕和高DPI）"""
        if self._closing:
            return
        self._clipboard_jobs.cancel_current()
        screens = QGuiApplication.screens()
        if not screens:
            return

        # 防御性清空上一轮可能残留的选区/检测状态（异常退出路径下 _cleanup 未必执行），
        # 避免本窗口变为可见时 paintEvent 短暂绘制上一轮的选区（即「一闪而过」）。
        self._selection_rect = None
        self._detected_rect = None
        self._start_pos = None
        self._end_pos = None
        self._sub_state = "HOVER"
        self._state = "CAPTURING"
        # 注意：不清空 _pending_pipeline，由外部在 start_capture 前设置

        # 计算虚拟桌面几何
        virtual_geometry = screens[0].geometry()
        for screen in screens[1:]:
            virtual_geometry = virtual_geometry.united(screen.geometry())

        max_dpr = max(screen.devicePixelRatio() for screen in screens)

        # QScreen.grabWindow 必须留在 GUI 线程；每块屏幕只抓取一次，mapper 与
        # 后台 QImage 合成都复用同一份结果。
        grabs = [(screen, screen.grabWindow(0)) for screen in screens]

        # 构建 per-screen info for mapper
        screen_infos = []
        image_parts = []
        for screen, grab in grabs:
            sg = screen.geometry()
            offset = sg.topLeft() - virtual_geometry.topLeft()
            screen_infos.append(
                ScreenInfo(
                    geometry=QRect(
                        offset.x(),
                        offset.y(),
                        sg.width(),
                        sg.height(),
                    ),
                    dpr=screen.devicePixelRatio(),
                    grab=grab,
                    offset=offset,
                )
            )
            image_parts.append((offset, grab.toImage().copy()))

        self._mapper = ScreenCoordinateMapper(screen_infos)
        self._virtual_geometry = virtual_geometry

        # 合并大画布交给后台 QImage job；GUI 完成后只做 fromImage。
        physical_size = virtual_geometry.size() * max_dpr
        # 设置窗口大小为虚拟桌面大小
        self.setGeometry(virtual_geometry)
        self.setMouseTracking(True)

        # 初始化窗口检测器
        hwnd = int(self.winId())
        if WindowDetector is not None:
            self._window_detector = WindowDetector(hwnd)

        self._capture_jobs.submit(
            lambda cancel_event: compose_screen_images(
                image_parts, physical_size, max_dpr, cancel_event
            )
        )

    @Slot(int, object)
    def _on_screen_composed(self, _generation: int, image: object) -> None:
        if self._closing or not hasattr(image, "isNull") or image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._screen_pixmap = pixmap

        # 新原生窗口（由 MainWindow._start_fresh_overlay_capture 每次新建）天然无
        # 残留后备存储，show() 直接以本次截图上屏，无「一闪而过」。
        self.show()
        self.activateWindow()
        self.grabMouse()

    def paintEvent(self, _event) -> None:
        """绘制冻结截图背景、遮罩（CAPTURING/EDITING 共用）和放大镜"""
        if not self._screen_pixmap:
            return

        painter = QPainter(self)

        # 1. 绘制冻结截图背景
        painter.drawPixmap(QPoint(0, 0), self._screen_pixmap)

        # 2. 创建遮罩（减去选区，镂空效果）
        mask_region = QRegion(self.rect())
        if self._selection_rect:
            mask_region = mask_region.subtracted(QRegion(self._selection_rect))

        # 3. 非选区绘制半透明遮罩
        painter.setClipRegion(mask_region)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
        painter.setClipping(False)

        if self._state != "CAPTURING":
            return

        # --- 以下仅 CAPTURING 模式 ---

        # HOVER 模式绘制检测高亮
        if self._sub_state == "HOVER" and self._detected_rect:
            painter.fillRect(self._detected_rect, QColor(0, 120, 215, 40))
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._detected_rect)

        # 4. 绘制选区边框和尺寸
        if self._selection_rect:
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection_rect)

            size_text = (
                f"{self._selection_rect.width()} x {self._selection_rect.height()}"
            )
            painter.drawText(self._selection_rect.topLeft() + QPoint(5, -5), size_text)

        # 5. 放大镜和像素信息
        if self._current_mouse_pos is not None and self._mapper is not None:
            mag_rect = MagnifierOverlay.draw_magnifier(
                painter,
                self._current_mouse_pos,
                self._screen_pixmap,
                self._virtual_geometry,
                self._magnifier_zoom,
                self._mapper,
                self.rect(),
            )
            MagnifierOverlay.draw_pixel_info(
                painter,
                self._current_mouse_pos,
                self._selection_rect,
                self._virtual_geometry,
                self._mapper,
                mag_rect,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: HOVER 点击选中窗口 / DRAG 开始拖拽 / 右键状态相关退出"""
        if self._state != "CAPTURING":
            return
        # 右键：框选前退出，框选后重新框选（与 ESC 逻辑一致）
        if event.button() == Qt.MouseButton.RightButton:
            self.releaseMouse()
            self._handle_capturing_abort()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._sub_state == "HOVER" and self._detected_rect is not None:
            # 检测到窗口，直接选中
            self._selection_rect = self._detected_rect
            self.releaseMouse()
            physical_rect = self._logical_rect_to_physical(self._selection_rect)
            if self._screen_pixmap is None:
                return
            captured = self._screen_pixmap.copy(physical_rect)
            self._captured_pixmap = captured
            self._enter_editing()
            return

        # 无检测窗口或 DRAG 模式：切换到 DRAG
        self._sub_state = "DRAG"
        self._start_pos = event.pos()
        self._selection_rect = QRect(self._start_pos, self._start_pos)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标移动 — HOVER 检测或 DRAG 更新选区"""
        if self._state != "CAPTURING":
            return
        self._current_mouse_pos = event.pos()

        if self._sub_state == "DRAG":
            if self._start_pos:
                self._end_pos = event.pos()
                self._selection_rect = QRect(
                    self._start_pos, self._end_pos
                ).normalized()
            self.update()
            return

        # HOVER: 窗口检测
        if self._window_detector:
            delta = event.pos() - self._last_detect_pos
            if delta.x() * delta.x() + delta.y() * delta.y() >= 9:
                mapper = self._mapper
                if mapper is not None:
                    self._detected_rect = self._window_detector.detect_at(
                        event.pos(),
                        mapper,
                    )
                self._last_detect_pos = event.pos()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标释放完成选区，进入 EDITING 模式"""
        if self._state != "CAPTURING":
            return
        if event.button() == Qt.MouseButton.LeftButton and self._selection_rect:
            self.releaseMouse()
            if (
                self._screen_pixmap
                and self._selection_rect.width() > self.MIN_SELECTION_SIZE
                and self._selection_rect.height() > self.MIN_SELECTION_SIZE
            ):
                # QPixmap.copy() 操作物理像素，需将逻辑坐标转换为物理坐标
                # 通过 mapper 自动处理多屏 DPR 差异
                physical_rect = self._logical_rect_to_physical(self._selection_rect)
                captured = self._screen_pixmap.copy(physical_rect)
                self._captured_pixmap = captured

                # 进入 EDITING 模式
                self._enter_editing()
                return

            # 选区太小，重置
            self._reset_capturing()
            self.hide()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """CAPTURING: 滚轮切换放大倍数"""
        if self._state != "CAPTURING":
            return
        if event.angleDelta().y() > 0:
            self._zoom_index = (self._zoom_index + 1) % len(self.ZOOM_LEVELS)
        else:
            self._zoom_index = (self._zoom_index - 1) % len(self.ZOOM_LEVELS)
        self._magnifier_zoom = self.ZOOM_LEVELS[self._zoom_index]
        self.update()

    def keyPressEvent(self, event) -> None:
        """ESC：CAPTURING 阶段有选区则重新框选，无选区则退出；EDITING 阶段退出。"""
        if event.key() == Qt.Key.Key_Escape:
            if self._state == "CAPTURING":
                self.releaseMouse()
                self._handle_capturing_abort()
                return
            self._do_cancel()

    def _handle_capturing_abort(self) -> None:
        """CAPTURING 下 ESC/右键：有选区则重新框选，无选区则退出。

        - 框选前（无选区）：取消整个截图
        - 框选后（已有选区 / DRAG 进行中）：清除选区，回到 HOVER 继续框选
        """
        if self._selection_rect is not None:
            self._reset_selection_for_re_capture()
        else:
            self._do_cancel()

    # ==================== EDITING 模式 ====================

    def set_pending_pipeline(self, pipeline_name: str) -> None:
        """设置快捷管道名称，下次截图选区完成后直接识别（跳过编辑界面）"""
        self._pending_pipeline = pipeline_name

    def _build_pipeline_options(self, pipeline_name: str) -> Any:
        """为快捷管道构建 OCROptions，优先使用 screenshot 源的持久化配置"""
        try:
            from vibeocr.classic.recognition_settings import OCROptions
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences
            from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

            pipeline_enum = OCRPipeline(pipeline_name)
            prefs = OCRPreferences.instance()
            return prefs.get_pipeline_options("screenshot", pipeline_enum)
        except Exception:
            from vibeocr.classic.recognition_settings import OCROptions
            from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

            try:
                pipeline_enum = OCRPipeline(pipeline_name)
            except ValueError:
                pipeline_enum = OCRPipeline.OCR
            return OCROptions(pipeline=pipeline_enum)

    def _enter_editing(self) -> None:
        """进入 EDITING 模式，创建子组件

        若 _pending_pipeline 已设置（工具栏快捷管道按钮触发），则跳过编辑界面，
        直接用对应管道选项确认识别。
        """
        self._state = "EDITING"
        self.setMouseTracking(False)

        if not self._captured_pixmap:
            return

        sel_rect = self._selection_rect
        if sel_rect is None:
            return

        # 快捷管道：跳过编辑界面，直接确认识别
        if self._pending_pipeline is not None:
            pipeline_name = self._pending_pipeline
            self._pending_pipeline = None
            options = self._build_pipeline_options(pipeline_name)
            # DPR 归一化（与 InlineEditCanvas.export_image 行为一致）
            pixmap = self._captured_pixmap
            if pixmap.devicePixelRatio() != 1.0:
                pixmap = QPixmap(pixmap)
                pixmap.setDevicePixelRatio(1.0)
            logger.debug(f"[快捷管道] 直接确认识别，管道: {pipeline_name}")
            self.confirmed.emit(pixmap, options)
            self._reset_capturing()
            self.hide()
            return

        # 创建画布
        self._canvas = InlineEditCanvas(self)
        self._canvas.set_background(
            self._captured_pixmap,
            QPointF(sel_rect.x(), sel_rect.y()),
            self._mapper,
        )

        # 创建工具栏
        self._toolbar = InlineToolbar(self)

        # 创建识别面板
        self._recognition_panel = InlineRecognitionPanel(self)

        # 创建 resize 框架
        self._resize_frame = SelectionResizeFrame(
            self,
            virtual_geometry=self._virtual_geometry,
            min_size=self.MIN_SELECTION_SIZE,
            forward_target=self._canvas,
        )
        self._resize_frame.selection_changed.connect(self._on_selection_changed)
        self._resize_frame.selection_finalized.connect(self._on_selection_finalized)

        # 定位子组件

        # 定位子组件
        self._position_editing_widgets()

        # 连接信号
        self._connect_editing_signals()

        # 显示子组件
        self._canvas.show()
        self._toolbar.show()
        self._recognition_panel.show()
        self._resize_frame.set_initial_selection(sel_rect)
        self._resize_frame.show()
        self._toolbar.raise_()
        self._recognition_panel.raise_()

        # 重绘覆盖层（EDITING 模式下 paintEvent 不绘制）
        self.update()

    def _position_editing_widgets(self) -> None:
        """定位 EDITING 模式的子组件"""
        if (
            not self._selection_rect
            or not self._canvas
            or not self._toolbar
            or not self._recognition_panel
        ):
            return

        sel = self._selection_rect

        # 画布定位在选区位置
        self._canvas.setGeometry(sel)

        # 工具栏几何
        toolbar_geo = self._calc_toolbar_geometry(sel)
        self._toolbar.setGeometry(toolbar_geo)

        # 识别面板几何
        panel_geo = self._calc_recognition_panel_geometry(sel)
        self._recognition_panel.setGeometry(panel_geo)
        self._recognition_panel.setFixedWidth(panel_geo.width())

    def _connect_editing_signals(self) -> None:
        """连接工具栏信号"""
        if not self._toolbar or not self._canvas:
            return

        # 工具切换
        self._toolbar.tool_changed.connect(self._canvas.set_tool)
        self._toolbar.tool_changed.connect(lambda _: self._reposition_toolbar())

        # 属性变更（画布全局属性 + 选中项属性）
        props = self._toolbar.properties_bar
        props.color_changed.connect(self._on_color_changed)
        props.line_width_changed.connect(self._on_line_width_changed)
        props.fill_enabled_changed.connect(self._on_fill_enabled_changed)
        props.fill_color_changed.connect(self._on_fill_color_changed)
        props.fill_opacity_changed.connect(self._on_fill_opacity_changed)
        props.fill_linked_changed.connect(self._on_fill_linked_changed)
        props.mosaic_strength_changed.connect(self._on_mosaic_strength_changed)
        props.blur_radius_changed.connect(self._on_blur_radius_changed)
        props.font_changed.connect(self._canvas.set_font)
        props.font_size_changed.connect(self._canvas.set_font_size)
        props.bold_changed.connect(self._canvas.set_bold)
        props.italic_changed.connect(self._canvas.set_italic)

        # 撤销/重做
        self._toolbar.undo_requested.connect(self._canvas.undo_stack.undo)
        self._toolbar.redo_requested.connect(self._canvas.undo_stack.redo)
        self._canvas.undo_stack.canUndoChanged.connect(self._toolbar.set_undo_enabled)
        self._canvas.undo_stack.canRedoChanged.connect(self._toolbar.set_redo_enabled)

        # 选中变化 → 属性条更新
        self._canvas._scene.selectionChanged.connect(
            self._on_annotation_selection_changed
        )

        # 操作按钮
        self._toolbar.copy_requested.connect(self._on_copy)
        self._toolbar.save_requested.connect(self._on_save)
        self._toolbar.cancel_requested.connect(self._do_cancel)

        # 识别面板
        if self._recognition_panel:
            self._recognition_panel.recognize_requested.connect(self._on_confirm)

    def _on_selection_changed(self, new_rect: QRect) -> None:
        """选区 resize/move 过程中持续更新"""
        if not self._canvas or not self._screen_pixmap or not self._mapper:
            return

        # 批量更新：禁止中间状态重绘，避免波纹
        self.setUpdatesEnabled(False)
        try:
            self._selection_rect = new_rect

            self._canvas.update_crop_region(self._screen_pixmap, new_rect, self._mapper)

            self._canvas.setGeometry(new_rect)
            if self._resize_frame:
                self._resize_frame.sync_selection(new_rect)

            toolbar_geo = self._calc_toolbar_geometry(new_rect)
            if self._toolbar:
                self._toolbar.setGeometry(toolbar_geo)

            panel_geo = self._calc_recognition_panel_geometry(new_rect)
            if self._recognition_panel:
                self._recognition_panel.setGeometry(panel_geo)
                self._recognition_panel.setFixedWidth(panel_geo.width())

            self.update()
        finally:
            self.setUpdatesEnabled(True)

    def _on_selection_finalized(self) -> None:
        """选区拖拽结束"""

    def _on_annotation_selection_changed(self) -> None:
        """选中标注项时更新属性条"""
        if not self._toolbar or not self._canvas:
            return

        item = self._canvas.selected_annotation
        props = self._toolbar.properties_bar

        if item:
            props.update_for_selection(item)
        else:
            props.clear_selection()

    def _on_color_changed(self, color) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_pen_color(color)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_pen_color"):
            item.set_pen_color(color)
        elif isinstance(item, TextAnnotation):
            item.set_text_color(color)
        if canvas._fill_linked:
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(color)

    def _on_line_width_changed(self, width) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_pen_width(width)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_pen_width"):
            item.set_pen_width(width)

    def _on_fill_enabled_changed(self, enabled) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_enabled(enabled)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_enabled"):
            item.set_fill_enabled(enabled, canvas._fill_color, canvas._fill_opacity)

    def _on_fill_color_changed(self, color) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_color(color)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_color"):
            item.set_fill_color(color)

    def _on_fill_opacity_changed(self, opacity) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_opacity(opacity)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_opacity"):
            item.set_fill_opacity(opacity)

    def _on_fill_linked_changed(self, linked) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_linked(linked)
        if linked:
            item = canvas.selected_annotation
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(canvas._pen_color)

    def _on_mosaic_strength_changed(self, value) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_mosaic_strength(value)
        item = canvas.selected_annotation
        if isinstance(item, MosaicItem):
            item.set_strength(value)

    def _on_blur_radius_changed(self, value) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_blur_radius(value)
        item = canvas.selected_annotation
        if isinstance(item, BlurItem):
            item.set_radius(value)

    def _on_confirm(self) -> None:
        """确认识别"""
        if not self._canvas:
            return
        pixmap = self._canvas.export_image()
        options = None
        if self._recognition_panel:
            options = self._recognition_panel.get_options()
        self.confirmed.emit(pixmap, options)
        self._cleanup()

    def _on_copy(self) -> None:
        """复制到剪贴板

        Windows 下同时写入位图格式（供微信/画图等粘贴）和文件格式（CF_HDROP，
        供资源管理器粘贴到文件夹）；其它平台保持原有位图写入。
        """
        if not self._canvas:
            return
        pixmap = self._canvas.export_image()
        clipboard = QApplication.clipboard()
        if sys.platform == "win32":
            # 位图剪贴板必须在 GUI 线程；PNG 编码/临时文件写入完成后再补
            # CF_HDROP，本次先让普通应用立即可粘贴图片。
            image = pixmap.toImage().copy()
            clipboard.setImage(image)
            existing = list(self._temp_clip_files)
            max_files = self._temp_clip_max
            self._clipboard_jobs.submit(
                lambda cancel_event: write_clipboard_png(
                    image, existing, max_files, cancel_event
                )
            )
        else:
            clipboard.setPixmap(pixmap)

        self.copied.emit(pixmap)
        self._cleanup()

    @Slot(int, object)
    def _on_clipboard_job_completed(self, _generation: int, result: object) -> None:
        """在 GUI 线程应用最新一次复制任务生成的剪贴板 MIME。"""
        if self._closing:
            if hasattr(result, "discard"):
                result.discard()
            return
        if isinstance(result, ClipboardPngResult):
            self._temp_clip_files = result.kept_paths
            mime_data = QMimeData()
            mime_data.setImageData(result.image)
            mime_data.setUrls([QUrl.fromLocalFile(str(result.path))])
            QApplication.clipboard().setMimeData(mime_data)

    @Slot(int, str)
    def _on_background_job_failed(self, _generation: int, error: str) -> None:
        if not self._closing:
            logger.warning("截图后台任务失败: %s", error)

    @staticmethod
    def _pixmap_to_png(pixmap: QPixmap) -> bytes | None:
        """将 QPixmap 编码为 PNG 字节；失败返回 None。"""
        try:
            image = pixmap.toImage()
            buffer = QBuffer()
            buffer.open(QBuffer.OpenModeFlag.WriteOnly)
            ok = image.save(buffer, "PNG")  # type: ignore[call-overload,arg-type]
            buffer.close()
            if not ok:
                return None
            return bytes(buffer.data())  # type: ignore[arg-type]
        except Exception:  # 编码失败不应阻断复制流程
            logger.exception("编码 PNG 失败")
            return None

    def _write_temp_clip_file(self, png_bytes: bytes | None) -> Path | None:
        """写入临时 PNG 文件并登记到进程内列表；失败返回 None。"""
        if png_bytes is None:
            return None
        try:
            fd, name = tempfile.mkstemp(
                prefix="vibeocr_clip_", suffix=".png", dir=tempfile.gettempdir()
            )
            path = Path(name)
            with os.fdopen(fd, "wb") as f:
                f.write(png_bytes)
            self._temp_clip_files.append(path)
            return path
        except Exception:  # 临时文件失败不应阻断复制
            logger.exception("写入临时剪贴板文件失败")
            return None

    def _prune_temp_clip_files(self) -> None:
        """惰性校验 + 滚动清理临时剪贴板文件。

        先剔除被外部删除的幽灵条目（仅 stat），再在超限时删除最旧的若干文件，
        保留最近 _temp_clip_max 个。整个过程不扫描磁盘目录。
        """
        try:
            # 惰性校验：剔除已不存在的条目，保证计数器准确
            self._temp_clip_files = [p for p in self._temp_clip_files if p.exists()]
            # 滚动清理：保留最近 N 个
            overflow = len(self._temp_clip_files) - self._temp_clip_max
            for _ in range(max(0, overflow)):
                oldest = self._temp_clip_files.pop(0)
                try:
                    oldest.unlink(missing_ok=True)
                except OSError:
                    logger.warning("删除临时剪贴板文件失败: %s", oldest)
        except Exception:  # 清理失败不应阻断复制
            logger.exception("清理临时剪贴板文件失败")

    def _cleanup_temp_clip_files(self) -> None:
        """应用退出时兜底清理所有临时剪贴板文件。"""
        try:
            for path in self._temp_clip_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("退出清理临时剪贴板文件失败: %s", path)
            self._temp_clip_files.clear()
        except Exception:
            logger.exception("退出清理临时剪贴板文件失败")

    def _on_save(self) -> None:
        """另存为"""
        if not self._canvas or self._save_shutdown_requested:
            return
        from PySide6.QtWidgets import QFileDialog

        pixmap = self._canvas.export_image()
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "", "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)"
        )
        if path:
            image = pixmap.toImage().copy()
            self._submit_confirmed_save(image, path)
        self._cleanup()

    def _submit_confirmed_save(self, image: QImage, path: str) -> None:
        """启动独立保存；后续截图、复制或保存均不得取消该任务。"""
        controller = GenerationImageJobs()
        completed_event = threading.Event()
        with self._save_jobs_lock:
            self._save_jobs[controller] = (path, completed_event)
        _ACTIVE_SAVE_JOB_CONTROLLERS.add(controller)
        controller.completed.connect(
            lambda _generation, result, controller=controller: (
                self._on_confirmed_save_completed(controller, result)
            )
        )
        controller.failed.connect(
            lambda _generation, error, controller=controller: (
                self._on_confirmed_save_failed(controller, error)
            )
        )
        controller.submit(
            lambda cancel_event: save_image_file(image, path, cancel_event)
        )

    def _save_job_path(self, controller: GenerationImageJobs) -> str:
        with self._save_jobs_lock:
            entry = self._save_jobs.get(controller)
        return entry[0] if entry is not None else ""

    def _release_save_controller(self, controller: GenerationImageJobs) -> None:
        with self._save_jobs_lock:
            entry = self._save_jobs.pop(controller, None)
        completed_event = entry[1] if entry is not None else None
        _ACTIVE_SAVE_JOB_CONTROLLERS.discard(controller)
        controller.deleteLater()
        if completed_event is not None:
            completed_event.set()

    def _on_confirmed_save_completed(
        self, controller: GenerationImageJobs, result: object
    ) -> None:
        path = self._save_job_path(controller)
        try:
            if isinstance(result, str) and result:
                self.saved.emit(result)
                return
            message = f"保存失败：{path or '未知路径'}"
            logger.warning(message)
            self.save_failed.emit(message)
        finally:
            self._release_save_controller(controller)

    def _on_confirmed_save_failed(
        self, controller: GenerationImageJobs, error: str
    ) -> None:
        path = self._save_job_path(controller)
        message = f"保存失败（{path or '未知路径'}）：{error}"
        logger.warning(message)
        try:
            self.save_failed.emit(message)
        finally:
            self._release_save_controller(controller)

    def _schedule_temp_clip_cleanup(self) -> None:
        """异步清理临时剪贴板文件，不等待 worker。"""
        paths = list(self._temp_clip_files)
        self._temp_clip_files.clear()
        if paths:
            self._cleanup_jobs.submit(
                lambda cancel_event: delete_files(paths, cancel_event)
            )

    def request_save_shutdown(self) -> None:
        """停止接受新的保存请求，但不取消任何已由用户确认的保存。"""
        self._save_shutdown_requested = True

    def request_shutdown(self) -> None:
        """Freeze interaction and cancel disposable image jobs without waiting."""
        if self._closing:
            return
        self._closing = True
        self.request_save_shutdown()
        self._capture_jobs.close()
        self._clipboard_jobs.close()
        self.finish_capture()
        self._schedule_temp_clip_cleanup()
        self._cleanup_jobs.close()

    def is_drained(self) -> bool:
        """Non-blocking probe for every overlay-owned background boundary."""
        return (
            self._capture_jobs.drain(0)
            and self._clipboard_jobs.drain(0)
            and self._cleanup_jobs.drain(0)
            and self.drain_saves(0)
        )

    def drain_saves(self, timeout_ms: int) -> bool:
        """供非 GUI 关闭协调器等待已确认保存完成及完成/失败通知。"""
        with self._save_jobs_lock:
            completed_events = [entry[1] for entry in self._save_jobs.values()]
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for completed_event in completed_events:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not completed_event.wait(remaining):
                return False
        return True

    def closeEvent(self, event) -> None:
        """仅请求取消；绝不在 GUI 线程等待编码/写盘任务。"""
        self.request_shutdown()
        super().closeEvent(event)

    def _do_cancel(self) -> None:
        """取消操作"""
        self.cancelled.emit()
        self._cleanup()

    def _cleanup(self) -> None:
        """清理子组件并重置"""
        # 销毁子组件
        if self._canvas:
            self._canvas.deleteLater()
            self._canvas = None
        if self._toolbar:
            self._toolbar.deleteLater()
            self._toolbar = None
        if self._recognition_panel:
            self._recognition_panel.deleteLater()
            self._recognition_panel = None
        if self._resize_frame:
            self._resize_frame.deleteLater()
            self._resize_frame = None

        self._captured_pixmap = None
        self._reset_capturing()
        self.hide()

    # ==================== 智能定位 ====================

    def _calc_panel_positions(self, selection: QRect) -> dict:
        """计算面板和工具栏的方向

        Returns:
            dict: {"panel_side": "left"|"right", "toolbar_side": "top"|"bottom"}
        """
        vg = self._virtual_geometry

        # 识别面板：默认右侧，右侧空间不足则翻到左侧
        right_space = vg.right() - selection.right()
        panel_side = "right" if right_space >= self._PANEL_MIN_WIDTH else "left"

        # 工具栏：默认底部，底部空间不足则翻到顶部
        bottom_space = vg.bottom() - selection.bottom()
        toolbar_side = "bottom" if bottom_space >= self._TOOLBAR_MIN_HEIGHT else "top"

        return {"panel_side": panel_side, "toolbar_side": toolbar_side}

    def _calc_toolbar_geometry(self, selection: QRect) -> QRect:
        """计算工具栏的几何位置——靠选区右下角"""
        if self._toolbar:
            toolbar_h = self._toolbar.sizeHint().height()
            toolbar_w = self._toolbar.sizeHint().width()
        else:
            toolbar_h = theme.Layout.toolbar_height
            toolbar_w = 400
        vg = self._virtual_geometry

        # 右对齐选区，下方 4px
        x = selection.right() - toolbar_w
        y = selection.bottom() + 4

        # 边界约束
        x = max(vg.left(), min(x, vg.right() - toolbar_w))
        if y + toolbar_h > vg.bottom():
            y = selection.top() - toolbar_h - 4

        return QRect(x, y, toolbar_w, toolbar_h)

    def _reposition_toolbar(self) -> None:
        """工具切换后重新定位工具栏（属性条显隐改变高度）"""
        if self._toolbar and self._selection_rect:
            geo = self._calc_toolbar_geometry(self._selection_rect)
            self._toolbar.setGeometry(geo)

    def _calc_recognition_panel_geometry(self, selection: QRect) -> QRect:
        """计算识别面板的几何位置

        面板底部对齐选区下沿，高度仅容纳按钮。
        """
        positions = self._calc_panel_positions(selection)
        side = positions["panel_side"]

        panel_width = 120

        # 紧凑高度：仅够容纳按钮
        if self._recognition_panel:
            panel_height = max(self._recognition_panel.sizeHint().height(), 100)
        else:
            panel_height = 200

        if side == "right":
            x = selection.right() + 4
        else:
            x = selection.left() - panel_width - 4

        # 底部对齐选区下沿
        y = selection.bottom() - panel_height

        return QRect(x, y, panel_width, panel_height)

    # ==================== 阴影效果 ====================

    @staticmethod
    def _add_shadow(widget: QWidget) -> None:
        """为控件添加阴影效果"""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(theme.Layout.shadow_blur)
        effect.setOffset(theme.Layout.shadow_offset_y)
        effect.setColor(QColor(theme.Layout.shadow_color))
        widget.setGraphicsEffect(effect)

    # ==================== 状态重置 ====================

    def _reset_selection_for_re_capture(self) -> None:
        """清除当前选区但保留截图底图，使 ESC/右键能重新框选。

        与 _reset_capturing 的区别：不清空 _screen_pixmap / _mapper /
        _virtual_geometry（重新框选仍需底图），也不改 _state / _pending_pipeline。
        """
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._detected_rect = None
        self._sub_state = "HOVER"
        self._last_detect_pos = QPoint()
        self.update()

    def _reset_capturing(self) -> None:
        """重置 CAPTURING 状态"""
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._screen_pixmap = None
        self._virtual_geometry = QRect()
        self._mapper = None
        self._current_mouse_pos = None
        self._sub_state = "HOVER"
        self._detected_rect = None
        self._last_detect_pos = QPoint()
        self._state = "CAPTURING"
        self._pending_pipeline = None
        self.update()

    def finish_capture(self) -> None:
        """完成截图（供外部调用）"""
        self._cleanup()
