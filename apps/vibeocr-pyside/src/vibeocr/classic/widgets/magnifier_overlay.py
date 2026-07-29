"""放大镜和像素信息覆盖绘制辅助类

纯绘制辅助类（非 QWidget），供 ScreenshotWidget.paintEvent 调用。
"""

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

from vibeocr.classic.widgets.screen_coordinate_mapper import ScreenCoordinateMapper


class MagnifierOverlay:
    """放大镜和像素信息绘制辅助"""

    # 放大镜尺寸（逻辑像素，奇数保证中心像素对齐）
    MAGNIFIER_SIZE = 121
    # 放大镜距离鼠标的偏移
    OFFSET = 20
    # 像素信息面板宽度
    INFO_WIDTH = 160
    # 像素信息面板高度
    INFO_HEIGHT = 90

    @staticmethod
    def draw_magnifier(
        painter: QPainter,
        mouse_pos: QPoint,
        screen_pixmap: QPixmap,
        virtual_geometry: QRect,
        zoom_level: int,
        mapper: ScreenCoordinateMapper,
        widget_rect: QRect,
    ) -> QRect:
        """绘制放大镜视图

        Args:
            painter: 当前 QPainter
            mouse_pos: 鼠标在 widget 中的逻辑坐标
            screen_pixmap: 全屏截图（物理像素，已设置 dpr）
            virtual_geometry: 虚拟桌面几何（逻辑像素）
            zoom_level: 放大倍数（2/4/8）
            mapper: 屏幕坐标映射器
            widget_rect: widget 的矩形区域

        Returns:
            放大镜绘制区域的矩形（用于定位信息面板）
        """
        mag_size = MagnifierOverlay.MAGNIFIER_SIZE
        offset = MagnifierOverlay.OFFSET

        # 计算放大镜位置（默认鼠标右下方）
        mag_x = mouse_pos.x() + offset
        mag_y = mouse_pos.y() + offset

        # 如果超出屏幕右边界，移到左侧
        if mag_x + mag_size > widget_rect.right():
            mag_x = mouse_pos.x() - offset - mag_size
        # 如果超出屏幕下边界，移到上方
        if mag_y + mag_size > widget_rect.bottom():
            mag_y = mouse_pos.y() - offset - mag_size

        mag_rect = QRect(mag_x, mag_y, mag_size, mag_size)

        # 计算从截图中取样的源矩形
        # 注意：screen_pixmap 是合并截图，在 start_capture 中统一按 max_dpr
        # 渲染（每块屏的像素被等比例拉伸到 max_dpr 空间），因此取样坐标必须
        # 用统一的 screenshot_dpr 换算，而非 per-screen dpr。否则在混合 DPR
        # 多屏下，低 DPR 屏上的鼠标位置会被映射到错误的物理位置，放大镜显示
        # 内容与鼠标实际位置错位。
        phys = mapper.logical_to_screenshot_physical_point(mouse_pos)
        phys_x, phys_y = phys.x(), phys.y()
        dpr = mapper.screenshot_dpr

        # 取样区域大小（物理像素）
        sample_size = int(mag_size * dpr / zoom_level)
        half_sample = sample_size // 2

        src_rect = QRect(
            phys_x - half_sample,
            phys_y - half_sample,
            sample_size,
            sample_size,
        )

        # 裁剪到截图范围内
        pixmap_rect = QRect(0, 0, screen_pixmap.width(), screen_pixmap.height())
        src_rect = src_rect.intersected(pixmap_rect)

        if src_rect.isEmpty():
            return mag_rect

        # 绘制放大镜背景
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        # 绘制放大的截图区域
        # 注意：screen_pixmap 设置了 dpr，直接用 drawPixmap 的 source rect 需要物理像素
        painter.drawPixmap(
            QRectF(mag_rect),
            screen_pixmap,
            QRectF(src_rect),
        )

        painter.restore()

        # 绘制放大镜边框
        painter.save()
        pen = QPen(QColor(0, 120, 215), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(mag_rect)

        # 绘制十字准星
        center_x = mag_rect.center().x()
        center_y = mag_rect.center().y()
        crosshair_size = 10
        pen.setWidth(1)
        pen.setColor(QColor(255, 0, 0, 200))
        painter.setPen(pen)
        painter.drawLine(
            center_x - crosshair_size,
            center_y,
            center_x + crosshair_size,
            center_y,
        )
        painter.drawLine(
            center_x,
            center_y - crosshair_size,
            center_x,
            center_y + crosshair_size,
        )

        # 绘制倍数标签
        font = QFont("Microsoft YaHei", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        label_text = f"{zoom_level}x"
        painter.drawText(mag_rect.left() + 4, mag_rect.top() + 14, label_text)

        painter.restore()

        return mag_rect

    @staticmethod
    def draw_pixel_info(
        painter: QPainter,
        mouse_pos: QPoint,
        selection_rect: QRect | None,
        virtual_geometry: QRect,
        mapper: ScreenCoordinateMapper,
        magnifier_rect: QRect,
    ) -> None:
        """绘制像素信息面板

        Args:
            painter: 当前 QPainter
            mouse_pos: 鼠标在 widget 中的逻辑坐标
            selection_rect: 当前选区矩形（逻辑像素），可为 None
            virtual_geometry: 虚拟桌面几何（逻辑像素）
            mapper: 屏幕坐标映射器
            magnifier_rect: 放大镜矩形区域（用于定位）
        """
        info_w = MagnifierOverlay.INFO_WIDTH
        info_h = MagnifierOverlay.INFO_HEIGHT

        # 信息面板位于放大镜下方
        info_x = magnifier_rect.left()
        info_y = magnifier_rect.bottom() + 4

        info_rect = QRect(info_x, info_y, info_w, info_h)

        # 通过 mapper 采样鼠标位置对应的像素颜色
        pixel_color = mapper.sample_pixel(mouse_pos)

        r, g, b = pixel_color.red(), pixel_color.green(), pixel_color.blue()
        hex_color = f"#{r:02X}{g:02X}{b:02X}"

        # 绝对坐标（逻辑像素，相对于虚拟桌面原点）
        vg_offset = virtual_geometry.topLeft()
        abs_x = mouse_pos.x() + vg_offset.x()
        abs_y = mouse_pos.y() + vg_offset.y()

        # 相对选区坐标
        rel_text = ""
        if selection_rect and not selection_rect.isEmpty():
            rel_x = mouse_pos.x() - selection_rect.x()
            rel_y = mouse_pos.y() - selection_rect.y()
            rel_text = f"({rel_x}, {rel_y})"

        # 绘制背景面板
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))
        painter.drawRoundedRect(info_rect, 4, 4)

        # 绘制文字信息
        font = QFont("Consolas", 9)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))

        text_x = info_rect.left() + 8
        line_height = 16
        y_start = info_rect.top() + 14

        # 坐标信息
        painter.drawText(text_x, y_start, f"pos: ({abs_x}, {abs_y})")
        if rel_text:
            painter.drawText(text_x, y_start + line_height, f"rel: {rel_text}")

        # RGB 颜色值
        color_y = y_start + line_height * 2
        painter.drawText(text_x, color_y, f"RGB: ({r}, {g}, {b})")

        # HEX 颜色值
        hex_y = color_y + line_height
        painter.drawText(text_x, hex_y, f"HEX: {hex_color}")

        # 绘制颜色预览色块
        swatch_size = 14
        swatch_x = info_rect.right() - swatch_size - 8
        swatch_y = hex_y - swatch_size + 2
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(pixel_color)
        painter.drawRect(swatch_x, swatch_y, swatch_size, swatch_size)

        painter.restore()
