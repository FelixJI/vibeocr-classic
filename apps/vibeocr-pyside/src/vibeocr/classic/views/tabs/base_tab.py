"""Tab 基类

提供所有 OCR Tab 的基础功能。
"""

import logging
from abc import abstractmethod
from typing import Any

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QWidget

from vibeocr.classic.recognition_result import normalize_bbox
from vibeocr.classic.utils.image_jobs import GenerationImageJobs

logger = logging.getLogger(__name__)

_ASYNC_CONTENT_THRESHOLD = 2000
_CONTENT_BACKFILL_CHUNK_SIZE = 512


class BaseOcrTab(QWidget):
    """OCR Tab 基类

    提供所有 OCR Tab 的公共接口和基础功能。

    子类需要实现：
    - _setup_ui(): 设置 UI 布局
    - _connect_signals(): 连接信号槽
    - _on_start(): 开始处理
    - _on_cancel(): 取消处理（可选）
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ocr_service: Any = None
        self._paddlex_service: Any = None
        self._current_ocr_result: Any = None
        self._preview_widget: Any = None
        self._result_widget: Any = None
        self._preprocess_options: Any = None
        self._is_processing = False
        self._content_jobs = GenerationImageJobs(self)
        self._content_jobs.completed.connect(self._on_content_list_prepared)
        self._content_jobs.failed.connect(self._on_content_list_prepare_failed)
        self._pending_content_result: Any = None
        self._pending_content_backfill: Any = None
        self._content_backfill_timer = QTimer(self)
        self._content_backfill_timer.setSingleShot(True)
        self._content_backfill_timer.setInterval(1)
        self._content_backfill_timer.timeout.connect(self._apply_content_backfill_chunk)
        self._content_index_result: Any = None
        self._text_index_by_content: dict[int, int] = {}
        self._result_rebuild_jobs = GenerationImageJobs(self)
        self._result_rebuild_jobs.completed.connect(self._on_result_rebuild_ready)
        self._result_rebuild_jobs.failed.connect(self._on_result_rebuild_failed)
        self._text_rebuild_result: Any = None
        self._text_rebuild_base_markdown = ""
        self._text_rebuild_base_html = ""
        self._text_rebuild_replacements: list[tuple[str, str]] = []

        # 子类在 __init__ 中调用以下方法
        # self._setup_ui()
        # self._connect_signals()

    @property
    def ocr_service(self) -> Any:
        """获取 OCR 服务"""
        return self._ocr_service

    @property
    def is_processing(self) -> bool:
        """检查是否正在处理"""
        return self._is_processing

    def set_ocr_service(self, service: Any) -> None:
        """设置 OCR 服务

        Args:
            service: OCR 服务实例
        """
        self._ocr_service = service
        logger.debug(
            f"[{self.__class__.__name__}] OCR 服务已设置: {service is not None}"
        )

        # 子类可以重写此方法来响应服务变化
        self._on_service_changed(service)

    def _on_service_changed(self, service: Any) -> None:
        """OCR 服务变化回调

        子类可以重写此方法来响应服务变化。

        Args:
            service: 新的 OCR 服务实例
        """

    def set_paddlex_service(self, service) -> None:
        """设置 PaddleX 服务"""
        self._paddlex_service = service

    def _get_service_for_pipeline(self, options):
        """根据管道类型路由到对应的服务"""
        from vibeocr.runtime_contracts.contracts.pipelines import OCRPipeline

        if options.pipeline == OCRPipeline.DOCUMENT_PARSING:
            return self._ocr_service
        return self._paddlex_service

    def _build_content_list(self, result, cancel_event=None) -> list[dict]:
        """从 OCRResult 构建 content_list（含归一化 bbox）"""
        # Never normalize the live dictionaries from a worker.  A detached list
        # is assigned to the model later on the GUI thread.
        content_list = [dict(item) for item in getattr(result, "content_list", [])]
        text_blocks = getattr(result, "text_blocks", [])
        img_w = getattr(result, "image_width", 0)
        img_h = getattr(result, "image_height", 0)

        if content_list:
            for block_index, cl_block in enumerate(content_list):
                if cancel_event is not None and block_index % 256 == 0:
                    if cancel_event.is_set():
                        return []
                bbox = cl_block.get("bbox")
                if bbox and len(bbox) >= 4:
                    cl_block["bbox"] = list(normalize_bbox(bbox[:4], img_w, img_h))
            for block_index, tb in enumerate(text_blocks):
                if cancel_event is not None and block_index % 256 == 0:
                    if cancel_event.is_set():
                        return []
                cl_idx = getattr(tb, "content_index", None)
                if cl_idx is not None and cl_idx < len(content_list):
                    if tb.bbox and "bbox" not in content_list[cl_idx]:
                        content_list[cl_idx]["bbox"] = list(
                            normalize_bbox(tb.bbox, img_w, img_h)
                        )
                    # 表格/图片/印章等结构识别块没有文本置信度（pipeline 里 score
                    # 是占位值），不写入 confidence，避免在 hover title 里显示
                    # 误导性的"置信度: 90%"。文本/标题块保留真实置信度。
                    cl_type = content_list[cl_idx].get("type", "")
                    if cl_type not in ("table", "image", "figure", "chart", "seal"):
                        content_list[cl_idx]["confidence"] = tb.score
            return content_list

        if not text_blocks:
            return []

        built = []
        for block_index, b in enumerate(text_blocks):
            if cancel_event is not None and block_index % 256 == 0:
                if cancel_event.is_set():
                    return []
            entry: dict = {"type": "text", "text": b.text, "confidence": b.score}
            if b.bbox:
                entry["bbox"] = list(normalize_bbox(b.bbox, img_w, img_h))
            if b.page_idx is not None:
                entry["page_idx"] = b.page_idx
            built.append(entry)
        return built

    def _prepare_large_content(self, result, cancel_event):
        """Prepare detached content plus reverse indexes off the GUI thread."""
        content_list = self._build_content_list(result, cancel_event)
        if cancel_event.is_set():
            return result, [], {}, ()

        text_index_by_content: dict[int, int] = {}
        missing_content_indices: list[tuple[int, int]] = []
        text_blocks = result.text_blocks
        for text_index in range(len(text_blocks)):
            if text_index % 256 == 0 and cancel_event.is_set():
                return result, [], {}, ()
            content_index = getattr(text_blocks[text_index], "content_index", None)
            if content_index is None and text_index < len(content_list):
                content_index = text_index
                missing_content_indices.append((text_index, content_index))
            if content_index is not None:
                text_index_by_content[int(content_index)] = text_index
        return (
            result,
            content_list,
            text_index_by_content,
            tuple(missing_content_indices),
        )

    def _prepare_result_display_state(self, result) -> None:
        """重置与新结果展示相关的状态（rebuild、后台 content 准备、索引）。

        抽自 ``_display_result`` 的非渲染步骤，供纯文本路径复用：纯文本路径
        不调用 ``_display_result``（避免 ``display_result`` 渲染 bump 文档 token），
        但仍需重置这些状态以保证前一次结果的残留不污染本次展示。
        """
        self._current_ocr_result = result
        self._reset_text_rebuild_state(result)
        self._content_jobs.cancel_current()
        self._pending_content_result = None
        self._content_backfill_timer.stop()
        self._pending_content_backfill = None
        self._content_index_result = None
        self._text_index_by_content = {}
        if self._preview_widget:
            self._preview_widget.setEnabled(True)

    def _display_result(self, result) -> None:
        """显示 OCR 结果到结果面板和预览面板"""
        self._prepare_result_display_state(result)
        content_count = len(getattr(result, "content_list", ()) or ())
        text_count = len(getattr(result, "text_blocks", ()) or ())
        if max(content_count, text_count) > _ASYNC_CONTENT_THRESHOLD:
            self._pending_content_result = result
            if self._result_widget:
                self._result_widget.clear()
            if self._preview_widget:
                # Editing is frozen while the worker reads the source model,
                # preventing a concurrent GUI mutation from producing a torn DTO.
                self._preview_widget.setEnabled(False)
            self._content_jobs.submit(
                lambda cancel_event: self._prepare_large_content(result, cancel_event)
            )
            return

        self._apply_content_list(result, self._build_content_list(result))

    def _apply_content_index(
        self,
        result,
        content_list: list[dict],
        text_index_by_content: dict[int, int] | None = None,
    ) -> None:
        """只回填 content_list / 设置索引 / 同步左侧预览，不渲染右侧 WebEngine。

        与 ``_apply_content_list`` 的区别：不调用 ``result_widget.display_result``，
        也不在末尾触发 ``_on_content_list_ready``。用于纯文本路径——回填后由
        调用方用 ``display_text_layout`` 渲染一次（避免 display_result 先 bump
        文档 token、display_text_layout 再 bump 一次，引发复制回调 token 失配）。

        回填逻辑与 ``_apply_content_list`` 的回填部分逐行等价（content_list 回填、
        content_index 补建、``_content_index_result`` / ``_text_index_by_content``
        设置、preview.set_content_list / set_text_content_index）。
        """
        # 不冻结预览：纯文本路径随后用 display_text_layout 渲染，其快照直接读
        # result 模型（_capture_stable_result_snapshot），不读 preview 活模型；
        # 冻结/解冻由渲染路径自管。
        # 统一构建 content_list 并回填到 result，保证右侧结果区与左侧预览、
        # 编辑回调用同一套索引。通用 OCR 管道的 content_list 为空（只有 text_blocks），
        # 若不回填，display_result 会走 raw_text 的 <pre> 分支，无法按块编辑；
        # _on_result_block_edited 按 content_index 反查 text_block 也会失败。
        if content_list:
            result.content_list = content_list
            # 为通用 OCR（text_blocks 无 content_index）补建索引，使编辑回调能反查。
            # 结构化管道（table/formula/mineru）的 content_index 在 pipeline 已设好，
            # 这里只在缺失时补，不覆盖。
            if text_index_by_content is None:
                text_index_by_content = {}
                for i, tb in enumerate(result.text_blocks):
                    content_index = getattr(tb, "content_index", None)
                    if content_index is None and i < len(content_list):
                        content_index = i
                        tb.content_index = content_index
                    if content_index is not None:
                        text_index_by_content[int(content_index)] = i
        self._content_index_result = result
        self._text_index_by_content = text_index_by_content or {}
        if self._preview_widget:
            self._preview_widget.set_content_list(content_list)
            set_text_index = getattr(
                self._preview_widget, "set_text_content_index", None
            )
            if callable(set_text_index):
                set_text_index(self._text_index_by_content)

    def _apply_content_list(
        self,
        result,
        content_list: list[dict],
        text_index_by_content: dict[int, int] | None = None,
    ) -> None:
        """Apply a detached content list and start the two presentation paths.

        结构化结果路径：回填 content_list + 同步预览（``_apply_content_index``），
        随后用 ``display_result`` 渲染右侧（渲染前冻结预览编辑，因 snapshot 在
        后台线程读活模型），末尾触发 ``_on_content_list_ready`` 供子类做二次展示。
        """
        self._apply_content_index(result, content_list, text_index_by_content)
        if self._result_widget:
            if self._preview_widget:
                # 渲染前冻结预览编辑：snapshot 在后台线程读取左侧预览的活模型，
                # 并发 GUI 改动会产生残缺 DTO。
                self._preview_widget.setEnabled(False)
            self._result_widget.display_result(result)
        self._on_content_list_ready(result)

    def _on_content_list_ready(self, result) -> None:
        """Hook for subclasses that need a second presentation after preparation."""

    @Slot(int, object)
    def _on_content_list_prepared(self, _generation: int, payload: object) -> None:
        result = self._pending_content_result
        if result is None or result is not self._current_ocr_result:
            return
        self._pending_content_result = None
        if not isinstance(payload, tuple) or len(payload) != 4:
            self._on_content_list_prepare_failed(
                _generation, "后台内容准备返回了无效数据"
            )
            return
        source_result, content_list, text_index_by_content, backfills = payload
        if source_result is not result:
            return
        self._pending_content_backfill = (
            result,
            content_list,
            text_index_by_content,
            backfills,
            0,
        )
        if backfills:
            self._content_backfill_timer.start()
        else:
            self._finish_prepared_content()

    def _apply_content_backfill_chunk(self) -> None:
        pending = self._pending_content_backfill
        if pending is None:
            return
        result, content_list, text_index_by_content, backfills, offset = pending
        if result is not self._current_ocr_result:
            self._pending_content_backfill = None
            return
        end = min(offset + _CONTENT_BACKFILL_CHUNK_SIZE, len(backfills))
        for position in range(offset, end):
            text_index, content_index = backfills[position]
            if text_index < len(result.text_blocks):
                block = result.text_blocks[text_index]
                if getattr(block, "content_index", None) is None:
                    block.content_index = content_index
        if end < len(backfills):
            self._pending_content_backfill = (
                result,
                content_list,
                text_index_by_content,
                backfills,
                end,
            )
            self._content_backfill_timer.start()
            return
        self._finish_prepared_content()

    def _finish_prepared_content(self) -> None:
        pending = self._pending_content_backfill
        if pending is None:
            return
        result, content_list, text_index_by_content, _backfills, _offset = pending
        self._pending_content_backfill = None
        if result is not self._current_ocr_result:
            return
        if self._preview_widget:
            self._preview_widget.setEnabled(True)
        self._apply_content_list(result, content_list, text_index_by_content)

    @Slot(int, str)
    def _on_content_list_prepare_failed(self, _generation: int, error: str) -> None:
        logger.error("后台准备 OCR content_list 失败: %s", error)
        self._pending_content_result = None
        self._pending_content_backfill = None
        self._content_backfill_timer.stop()
        if self._preview_widget:
            self._preview_widget.setEnabled(True)

    def request_base_shutdown(self) -> None:
        """Cancel BaseOcrTab-owned preparation jobs without waiting."""
        self._pending_content_result = None
        self._pending_content_backfill = None
        self._content_backfill_timer.stop()
        self._content_index_result = None
        self._text_index_by_content.clear()
        self._text_rebuild_result = None
        self._text_rebuild_replacements.clear()
        self._content_jobs.close()
        self._result_rebuild_jobs.close()

    def drain_base_jobs(self, timeout_ms: int = 0) -> bool:
        import time

        # A finished native content worker may still have a queued GUI callback,
        # followed by chunked content-index backfill.  Report that state as
        # undrained even though no QThread is currently running.
        if (
            self._pending_content_result is not None
            or self._pending_content_backfill is not None
        ):
            return False
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        if not self._content_jobs.drain(timeout_ms):
            return False
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        return self._result_rebuild_jobs.drain(remaining_ms)

    def _reset_text_rebuild_state(self, result) -> None:
        self._text_rebuild_result = result
        self._text_rebuild_base_markdown = str(result.markdown_text or "")
        self._text_rebuild_base_html = str(result.html_text or "")
        self._text_rebuild_replacements = []

    def _schedule_text_result_rebuild(
        self, result, old_text: str, new_text: str
    ) -> None:
        """Rebuild large aggregate text fields without blocking the edit slot."""
        if self._text_rebuild_result is not result:
            self._reset_text_rebuild_state(result)
        self._text_rebuild_replacements.append((old_text, new_text))

        # Copy only the small edit ledger on the GUI thread.  The 50k scan is
        # pure data work owned by the generation-controlled worker.  An edit
        # arriving during a scan submits a newer generation, so the mixed older
        # result is discarded and the final worker observes every accepted edit.
        replacements = tuple(self._text_rebuild_replacements)
        markdown_base = self._text_rebuild_base_markdown
        html_base = self._text_rebuild_base_html
        text_blocks = result.text_blocks

        def rebuild(cancel_event):
            parts: list[str] = []
            for block_index, block in enumerate(text_blocks):
                if block_index % 256 == 0 and cancel_event.is_set():
                    return result, "", "", ""
                text = block.text
                if text:
                    parts.append(text)
            raw = "\n".join(parts)

            def apply_replacements(aggregate: str) -> str:
                if not aggregate:
                    return raw
                rebuilt = aggregate
                for previous, replacement in replacements:
                    # A plain replace cannot identify which OCR block owns a
                    # repeated string.  In that ambiguous case the stable block
                    # sequence is the only safe source of truth.
                    if not previous or rebuilt.count(previous) != 1:
                        return raw
                    rebuilt = rebuilt.replace(previous, replacement, 1)
                return rebuilt

            rebuilt_markdown = apply_replacements(markdown_base)
            rebuilt_html = apply_replacements(html_base)
            return result, raw, rebuilt_markdown, rebuilt_html

        self._result_rebuild_jobs.submit(rebuild)

    @staticmethod
    def _requires_async_result_rebuild(result) -> bool:
        return (
            max(
                len(getattr(result, "text_blocks", ()) or ()),
                len(getattr(result, "content_list", ()) or ()),
            )
            > _ASYNC_CONTENT_THRESHOLD
        )

    def _schedule_table_result_rebuild(self, result) -> None:
        """Rebuild large table Markdown/HTML aggregates in a worker."""

        def rebuild(cancel_event):
            from vibeocr.classic.table_results import build_result_projections

            projections = build_result_projections(
                result,
                is_cancelled=cancel_event.is_set,
            )
            if projections is None:
                return result, "", "", ""
            return result, *projections

        self._result_rebuild_jobs.submit(rebuild)

    @Slot(int, object)
    def _on_result_rebuild_ready(self, _generation: int, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 4:
            logger.warning("忽略无效的结果重建 payload")
            return
        result, raw, markdown, html = payload
        if result is not self._current_ocr_result:
            return
        result.raw_text = str(raw)
        result.markdown_text = str(markdown)
        result.html_text = str(html)
        if self._result_widget:
            if self._preview_widget:
                self._preview_widget.setEnabled(False)
            self._result_widget.display_result(result)

    @Slot(int, str)
    def _on_result_rebuild_failed(self, _generation: int, error: str) -> None:
        logger.error("后台重建 OCR 编辑结果失败: %s", error)

    def _setup_hover_sync(self) -> None:
        """设置预览 ↔ 结果的双向悬停联动"""
        if not self._result_widget or not self._preview_widget:
            return
        self._result_widget.block_hovered.connect(self._preview_widget.highlight_block)
        self._result_widget.block_unhovered.connect(
            lambda: self._preview_widget.highlight_block(-1)
        )
        self._preview_widget.block_hovered.connect(self._result_widget.highlight_block)
        self._preview_widget.block_unhovered.connect(
            self._result_widget.clear_highlight
        )
        self._result_widget.snapshot_ready.connect(self._on_result_snapshot_ready)
        self._result_widget.snapshot_failed.connect(self._on_result_snapshot_failed)

    @Slot(object, object)
    def _on_result_snapshot_ready(self, result: object, _snapshot: object) -> None:
        if result is self._current_ocr_result and self._preview_widget:
            self._preview_widget.setEnabled(True)

    @Slot(object)
    def _on_result_snapshot_failed(self, result: object) -> None:
        if result is self._current_ocr_result and self._preview_widget:
            self._preview_widget.setEnabled(True)

    def _on_block_text_edited(self, index: int, new_text: str) -> None:
        """文本块被编辑后同步更新结果和展示"""
        if not self._current_ocr_result or index < 0:
            return
        result = self._current_ocr_result
        if index >= len(result.text_blocks):
            return

        old_text = result.text_blocks[index].text
        if old_text == new_text:
            return

        result.text_blocks[index].text = new_text
        result.text_blocks[index].is_manually_edited = True

        if index < len(result.text_with_scores):
            score = result.text_with_scores[index][1]
            result.text_with_scores[index] = (new_text, score)

        cl_idx = None
        if result.content_list:
            cl_idx = getattr(result.text_blocks[index], "content_index", None)
            if cl_idx is not None and cl_idx < len(result.content_list):
                cl_block = result.content_list[cl_idx]
                block_type = cl_block.get("type", "text")
                if block_type == "table":
                    import html as html_lib

                    table_body = cl_block.get("table_body", "")
                    cl_block["table_body"] = table_body.replace(
                        html_lib.escape(old_text), html_lib.escape(new_text), 1
                    )
                else:
                    cl_block["text"] = new_text

        if len(result.text_blocks) > _ASYNC_CONTENT_THRESHOLD:
            if self._preview_widget:
                self._preview_widget.set_text_blocks(result.text_blocks)
            if self._result_widget:
                self._result_widget.invalidate_snapshot()
                self._result_widget.update_block_text(
                    cl_idx if cl_idx is not None else index, new_text
                )
            self._schedule_text_result_rebuild(result, old_text, new_text)
            return

        result.raw_text = "\n".join(b.text for b in result.text_blocks if b.text)

        if result.markdown_text and old_text in result.markdown_text:
            result.markdown_text = result.markdown_text.replace(old_text, new_text, 1)
        else:
            result.markdown_text = result.raw_text

        if result.html_text and old_text in result.html_text:
            result.html_text = result.html_text.replace(old_text, new_text, 1)
        else:
            result.html_text = result.raw_text

        if self._preview_widget:
            self._preview_widget.set_text_blocks(result.text_blocks)
        if self._result_widget:
            if cl_idx is not None:
                self._result_widget.update_block_text(cl_idx, new_text)
            else:
                self._result_widget.update_block_text(index, new_text)
            # Rebuild the immutable export/render snapshot in the background.
            self._result_widget.display_result(result)

    def _on_table_block_edited(self, content_index: int, new_html: str) -> None:
        """表格块被网格编辑后同步更新结果和展示。

        与 ``_on_block_text_edited``（普通文本内联编辑）并列，专处理左侧画布
        双击表格弹出的网格编辑器结果：整体替换 ``content_list`` 中的
        ``table_body`` 及对应 text_block 的文本，重算纯文本/markdown/html，
        并反向同步右侧 HTML 视图。
        """
        if not self._current_ocr_result:
            return
        result = self._current_ocr_result
        if not result.content_list or not (
            0 <= content_index < len(result.content_list)
        ):
            return

        cl_block = result.content_list[content_index]
        old_html = cl_block.get("table_body", "")
        if old_html == new_html:
            return

        is_large_result = (
            max(len(result.text_blocks), len(result.content_list))
            > _ASYNC_CONTENT_THRESHOLD
        )
        mapped_index = None
        if self._content_index_result is result:
            mapped_index = self._text_index_by_content.get(content_index)
        from vibeocr.classic.table_results import replace_result_table_from_html

        edit = replace_result_table_from_html(
            result,
            content_index=content_index,
            new_html=new_html,
            preferred_text_index=mapped_index,
            allow_linear_scan=not is_large_result,
            rebuild_projections=not is_large_result,
        )

        if is_large_result:
            if self._preview_widget:
                self._preview_widget.set_content_list(result.content_list)
            if self._result_widget:
                self._result_widget.invalidate_snapshot()
                self._result_widget.update_block_text(
                    content_index, edit.canonical_html
                )
            self._schedule_table_result_rebuild(result)
            return

        if self._preview_widget:
            self._preview_widget.set_content_list(result.content_list)
        if self._result_widget:
            # update_block_text 现已支持 table 块的 DOM 重建
            self._result_widget.update_block_text(content_index, edit.canonical_html)
            self._result_widget.display_result(result)

    @Slot(str, str, str)
    def _on_table_cell_edited(self, table_id: str, cell_id: str, new_text: str) -> None:
        """Apply one canonical table edit by stable IDs and refresh projections."""

        if not self._current_ocr_result:
            return
        from vibeocr.classic.table_results import update_result_table_cell

        try:
            content_index = update_result_table_cell(
                self._current_ocr_result,
                table_id=table_id,
                cell_id=cell_id,
                new_text=new_text,
            )
        except (KeyError, TypeError, ValueError) as error:
            logger.warning(
                "忽略无法定位的表格单元格编辑 table=%s cell=%s: %s",
                table_id,
                cell_id,
                error,
            )
            return

        result = self._current_ocr_result
        if self._preview_widget:
            self._preview_widget.set_content_list(result.content_list)
        if self._result_widget:
            self._result_widget.invalidate_snapshot()
            self._result_widget.update_block_text(
                content_index,
                result.content_list[content_index]["table_body"],
            )
            self._result_widget.display_result(result)

    @staticmethod
    def _table_html_to_plain_text(html: str) -> str:
        """从表格 HTML 提取纯文本（供 content_list 的 text 字段）。"""
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    def _init_options_from_preferences(self, *, batch: bool = False) -> None:
        """从 OCRPreferences 恢复选项，建立管道切换同步"""
        if not self._preprocess_options:
            return
        try:
            from vibeocr.classic.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
        except RuntimeError:
            return
        if batch:
            self._preprocess_options.set_options(prefs.get_batch_options())
            self._preprocess_options.options_changed.connect(
                lambda opts: OCRPreferences.instance().set_batch_options(opts)
            )
            prefs.batch_options_changed.connect(self._preprocess_options.set_options)
        else:
            source = "main"
            default_pipeline = self._preprocess_options.get_current_pipeline()
            self._preprocess_options.set_options(
                prefs.get_pipeline_options(source, default_pipeline)
            )
            self._preprocess_options.pipeline_switching.connect(
                lambda old_pipeline, opts: (
                    OCRPreferences.instance().set_pipeline_options(
                        source, old_pipeline, opts
                    )
                )
            )
            self._preprocess_options.pipeline_switched.connect(
                lambda new_pipeline: self._preprocess_options.set_options(
                    OCRPreferences.instance().get_pipeline_options(source, new_pipeline)
                )
            )
            self._preprocess_options.options_changed.connect(
                lambda opts: OCRPreferences.instance().set_pipeline_options(
                    source, opts.pipeline, opts
                )
            )

    @abstractmethod
    def _setup_ui(self) -> None:
        """设置 UI 布局

        子类必须实现此方法。
        """

    @abstractmethod
    def _connect_signals(self) -> None:
        """连接信号槽

        子类必须实现此方法。
        """

    @abstractmethod
    def _on_start(self) -> None:
        """开始处理

        子类必须实现此方法。
        """

    def _on_cancel(self) -> None:
        """取消处理

        子类可以重写此方法来实现取消功能。
        """
        logger.warning(f"[{self.__class__.__name__}] 取消功能未实现")

    def _set_processing(self, processing: bool) -> None:
        """设置处理状态

        Args:
            processing: 是否正在处理
        """
        self._is_processing = processing
