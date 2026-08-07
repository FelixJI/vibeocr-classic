"""pdf_coords 纯坐标变换测试。

覆盖 bbox_to_pixel 的所有 source/rotation/page_rect 组合分支。
"""

from __future__ import annotations

import pytest

from vibeocr.classic.utils.pdf_coords import bbox_to_pixel


class TestBboxToPixelPageRectShapes:
    """page_rect 接受 4-tuple 或带 .width/.height 的对象。"""

    def test_tuple_page_rect(self):
        result = bbox_to_pixel(
            (10, 20, 30, 40), (0, 0, 100, 200), 72, source="normalized"
        )
        # normalized [0,1000] → page_rect: 10/1000*100=1.0, 20/1000*200=4.0, ...
        assert result == pytest.approx((1.0, 4.0, 3.0, 8.0))

    def test_object_with_width_height(self):
        class _Rect:
            width = 1000.0
            height = 2000.0

        result = bbox_to_pixel((500, 1000, 500, 1000), _Rect(), 72, source="normalized")
        # 500/1000*1000=500, 1000/1000*2000=2000
        assert result == pytest.approx((500.0, 2000.0, 500.0, 2000.0))


class TestBboxToPixelNormalized:
    """source='normalized' 分支。"""

    def test_normalized_basic_scaling(self):
        # bbox [0,1000] 归一化 → page_rect 全尺寸
        result = bbox_to_pixel(
            (0, 0, 1000, 1000), (0, 0, 200, 400), 72, source="normalized"
        )
        assert result == pytest.approx((0.0, 0.0, 200.0, 400.0))

    def test_normalized_with_render_dpi(self):
        # DPI=144 → scale=2.0
        result = bbox_to_pixel(
            (500, 500, 500, 500), (0, 0, 100, 100), 144, source="normalized"
        )
        # 500/1000*100=50, *2.0=100
        assert result == pytest.approx((100.0, 100.0, 100.0, 100.0))


class TestBboxToPixelPdfRotation:
    """source='pdf' + rotation 各角度分支。"""

    def test_pdf_rotation_0_no_mediabox(self):
        # rotation=0, 无 mediabox → mb_w=pw, mb_h=ph, bbox 透传
        result = bbox_to_pixel(
            (10, 20, 30, 40), (0, 0, 100, 200), 72, source="pdf", rotation=0
        )
        assert result == pytest.approx((10.0, 20.0, 30.0, 40.0))

    def test_pdf_rotation_90_uses_swapped_dims(self):
        # rotation=90, 无 mediabox → mb_w=ph, mb_h=pw
        # page_rect=(0,0,100,200) → pw=100, ph=200; rot90 → mb_w=200, mb_h=100
        # (bx0,by0,bx1,by1)=(10,20,30,40)
        # x0=mb_h-by1=100-40=60, y0=bx0=10, x1=mb_h-by0=100-20=80, y1=bx1=30
        result = bbox_to_pixel(
            (10, 20, 30, 40), (0, 0, 100, 200), 72, source="pdf", rotation=90
        )
        assert result == pytest.approx((60.0, 10.0, 80.0, 30.0))

    def test_pdf_rotation_180(self):
        # rotation=180, 无 mediabox → mb_w=pw=100, mb_h=ph=200
        # x0=mb_w-bx1=100-30=70, y0=mb_h-by1=200-40=160
        # x1=mb_w-bx0=100-10=90, y1=mb_h-by0=200-20=180
        result = bbox_to_pixel(
            (10, 20, 30, 40), (0, 0, 100, 200), 72, source="pdf", rotation=180
        )
        assert result == pytest.approx((70.0, 160.0, 90.0, 180.0))

    def test_pdf_rotation_270(self):
        # rotation=270, 无 mediabox → mb_w=ph=200, mb_h=pw=100
        # x0=by0=20, y0=mb_w-bx1=200-30=170, x1=by1=40, y1=mb_w-bx0=200-10=190
        result = bbox_to_pixel(
            (10, 20, 30, 40), (0, 0, 100, 200), 72, source="pdf", rotation=270
        )
        assert result == pytest.approx((20.0, 170.0, 40.0, 190.0))

    def test_pdf_rotation_90_with_explicit_mediabox(self):
        # 提供 mediabox → 不走 page_rect 互换推断
        # mediabox=(0,0,50,80) → mb_w=50, mb_h=80
        # rot90: x0=mb_h-by1=80-40=40, y0=bx0=10, x1=mb_h-by0=80-20=60, y1=bx1=30
        result = bbox_to_pixel(
            (10, 20, 30, 40),
            (0, 0, 100, 200),
            72,
            source="pdf",
            rotation=90,
            mediabox=(0, 0, 50, 80),
        )
        assert result == pytest.approx((40.0, 10.0, 60.0, 30.0))
