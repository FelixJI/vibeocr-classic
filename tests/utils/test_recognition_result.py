"""Classic-owned OCR presentation model 的公开行为契约。"""

from __future__ import annotations

import pytest
from vibeocr.classic.recognition_result import (
    DISCARDED_BLOCK_TYPES,
    OCRResult,
    TextBlock,
    normalize_bbox,
    ocr_result_from_payload,
)
from vibeocr.runtime_contracts import ContractError


def test_presentation_result_keeps_backend_compatible_mutable_fields() -> None:
    block = TextBlock("原文", 0.91, (10.0, 20.0, 30.0, 40.0))
    result = OCRResult(
        raw_text="原文",
        markdown_text="**原文**",
        html_text="<strong>原文</strong>",
        text_with_scores=[("原文", 0.91)],
        avg_score=0.91,
        low_confidence_items=[("待复核", 0.7)],
        pipeline_type="OCR",
        images={"figure": {"present": True}},
        content_list=[{"type": "text", "text": "原文"}],
        text_blocks=[block],
        image_width=1200,
        image_height=800,
        preproc_angle=90,
        preprocessed_image=b"png",
        preproc_img_w=600,
        preproc_img_h=400,
    )

    result.raw_text = "已编辑"
    result.text_blocks[0].text = "已编辑块"
    result.content_list[0]["text"] = "已编辑内容"

    assert result.raw_text == "已编辑"
    assert result.text_blocks[0].text == "已编辑块"
    assert result.content_list[0]["text"] == "已编辑内容"
    assert result.has_rich_content is True
    assert result.has_content_list is True
    assert result.display_text == "<strong>原文</strong>"
    assert result.copy_text == "**原文**"
    assert result.preprocessed_image == b"png"
    assert result.preproc_img_w == 600
    assert result.preproc_img_h == 400


def test_protocol_payload_becomes_mutable_classic_presentation_graph() -> None:
    result = ocr_result_from_payload(
        "ocr.v1",
        {
            "raw_text": "正文",
            "markdown_text": "**正文**",
            "html_text": "<strong>正文</strong>",
            "avg_score": 0.88,
            "pipeline_type": "PP-StructureV3",
            "preproc_angle": 180,
            "content_list": [{"type": "text", "text": "正文"}],
            "text_with_scores": [["正文", 0.88]],
            "low_confidence_items": [["待复核", 0.6]],
            "text_blocks": [
                {
                    "text": "正文",
                    "score": 0.88,
                    "bbox": [10, 20, 30, 40],
                    "polygon": [10, 20, 30, 20, 30, 40, 10, 40],
                    "page_idx": 2,
                    "is_manually_edited": True,
                    "content_index": 0,
                    "content_id": "block-0",
                    "label": "text",
                    "order": 7,
                }
            ],
            "images": {"figure": {"present": True, "size": 12}},
            "image_width": 1920,
            "image_height": 1080,
        },
    )

    result.text_blocks[0].text = "修订"
    result.images["figure"]["size"] = 13

    assert result.raw_text == "正文"
    assert result.pipeline_type == "PP-StructureV3"
    assert result.preproc_angle == 180
    assert result.text_with_scores == [("正文", 0.88)]
    assert result.low_confidence_items == [("待复核", 0.6)]
    assert result.text_blocks[0] == TextBlock(
        text="修订",
        score=0.88,
        bbox=(10.0, 20.0, 30.0, 40.0),
        polygon=(10.0, 20.0, 30.0, 20.0, 30.0, 40.0, 10.0, 40.0),
        page_idx=2,
        is_manually_edited=True,
        content_index=0,
        content_id="block-0",
        label="text",
        order=7,
    )
    assert result.content_list == [{"type": "text", "text": "正文"}]
    assert result.images == {"figure": {"present": True, "size": 13}}
    assert result.image_width == 1920
    assert result.image_height == 1080


def test_protocol_adapter_preserves_legacy_text_fallback() -> None:
    result = ocr_result_from_payload("ocr.v1", {"text": "旧版纯文本"})

    assert result.raw_text == "旧版纯文本"
    assert result.markdown_text == ""
    assert result.text_blocks == []


def test_protocol_adapter_rejects_unknown_payload_type() -> None:
    with pytest.raises(ContractError, match="payload_type"):
        ocr_result_from_payload("ocr.v2", {})


def test_presentation_geometry_helpers_keep_classic_normalization_semantics() -> None:
    assert normalize_bbox([0, 0.5, 1, 0.25]) == (0.0, 500.0, 1000.0, 250.0)
    assert normalize_bbox([0, 0, 1920, 1080], 1920, 1080) == (
        0.0,
        0.0,
        1000.0,
        1000.0,
    )
    assert normalize_bbox([10, 20, 30, 40]) == (10.0, 20.0, 30.0, 40.0)
    assert DISCARDED_BLOCK_TYPES == {
        "header",
        "footer",
        "page_number",
        "page_footnote",
        "aside_text",
    }
