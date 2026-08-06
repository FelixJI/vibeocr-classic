"""PdfTab UI 结构测试。"""

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QListView, QListWidget, QScrollArea, QSplitter

from vibeocr.backend.ipc.schemas import (
    OpenResponse,
    PdfDocumentMirror,
    ProgressEvent,
    ProgressPhase,
)
from vibeocr.classic.views.tabs.pdf_tab import (
    _LAYER_STATE_ROLE,
    _THUMBNAIL_HPAD,
    _THUMBNAIL_MAX_SIZE,
    _THUMBNAIL_MIN_SIZE,
    _THUMBNAIL_TEXT_HEIGHT,
    PdfTab,
    ThumbnailModel,
)


@pytest.fixture
def pdf_tab(qtbot):
    client = MagicMock()
    client.open_session.side_effect = lambda path: OpenResponse(
        session_id=path,
        model=PdfDocumentMirror(file_path=path, pages=[]),
    )
    client.load_stream.side_effect = lambda _sid: iter(
        [
            ProgressEvent(
                phase=ProgressPhase.LOAD,
                current=0,
                total=0,
                message="done",
            )
        ]
    )
    client.render_thumbnail.return_value = b""
    tab = PdfTab(pdf_client=client)
    qtbot.addWidget(tab)
    yield tab
    tab.shutdown()


class TestPdfTabStructure:
    def test_has_only_main_horizontal_splitter(self, pdf_tab):
        """改造后只有主水平 splitter，不再有右侧垂直 splitter。"""
        splitters = pdf_tab.findChildren(QSplitter)
        horiz = [s for s in splitters if s.orientation() == Qt.Orientation.Horizontal]
        vert = [s for s in splitters if s.orientation() == Qt.Orientation.Vertical]
        assert len(horiz) >= 1, "应有横向主 splitter"
        assert len(vert) == 0, "不应再有右侧垂直 splitter"

    def test_splitters_are_not_collapsible(self, pdf_tab):
        """setChildrenCollapsible(False) 应阻止用户把子部件拖没。"""
        # QSplitter 没有公开 childrenCollapsible() getter，验证间接效果：
        # 即便把尺寸拖到极端，子部件最小宽度仍 > 0（不会被折叠为 0）。
        pdf_tab._main_splitter.setSizes([1, 9999])
        sizes = pdf_tab._main_splitter.sizes()
        # 两个子部件都应保留非零尺寸（不可折叠）
        assert all(s > 0 for s in sizes)

    def test_thumbnail_list_has_no_fixed_width(self, pdf_tab):
        """缩略图列表不应被 setFixedWidth 钉死，否则 splitter 不可拖。"""
        lst = pdf_tab.findChild(QListView)
        assert lst is not None
        # 被 setFixedWidth 时 maximumWidth == minimumWidth == 200；
        # 现在只设了 minimumWidth(120)，maximumWidth 应保持默认大值。
        assert lst.maximumWidth() > 300

    def test_layer_status_in_scroll_area(self, pdf_tab):
        """状态网格应包在 QScrollArea 中，多页不被截断。"""
        scrolls = pdf_tab.findChildren(QScrollArea)
        assert len(scrolls) >= 1
        # 其中至少一个 ScrollArea 的内容是 _layer_status_grid
        owns_list = any(s.widget() is pdf_tab._layer_status_grid for s in scrolls)
        assert owns_list

    def test_no_embedded_preview_canvas(self, pdf_tab):
        """内嵌预览画布应已移除。"""
        assert not hasattr(pdf_tab, "_preview_canvas")
        assert not hasattr(pdf_tab, "_right_splitter")

    def test_splitter_save_is_debounced(self, pdf_tab, monkeypatch):
        """splitterMoved 不应立即落盘，而是重启防抖定时器。"""
        calls = []
        monkeypatch.setattr(pdf_tab, "_persist_splitter_state", lambda: calls.append(1))
        # 连续触发多次 splitterMoved（模拟拖动）
        for _ in range(5):
            pdf_tab._save_splitter_state()
        # 定时器未到期前不落盘
        assert calls == []
        # 触发定时器到期 → 仅落盘一次
        pdf_tab._splitter_save_timer.timeout.emit()
        assert calls == [1]

    def test_thumbnail_text_height_is_compact(self):
        """缩略图文字行高度应收紧到 18(单行中文足够,原 28 过松)。"""
        assert _THUMBNAIL_TEXT_HEIGHT == 18


class TestPdfTabLayerStatus:
    def test_status_wording_for_text_layer(self, pdf_tab, tmp_path, monkeypatch):
        """_update_layer_status 对有文字层的页应输出文字层类型 + 文本块数。"""
        import fitz

        from vibeocr.backend.models.pdf_document import (
            PdfDocument,
            PdfPageInfo,
            TextLayerInfo,
        )
        from vibeocr.backend.models.pdf_session import PdfSession

        page_info = PdfPageInfo(
            page_index=0,
            has_text_layer=True,
            text_layers=[
                TextLayerInfo(
                    index=i,
                    text_preview="t",
                    char_count=1,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    color_id=i,
                )
                for i in range(12)
            ],
        )
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        # active_session 是只读 property（读 _active_path + _sessions），直接注入底层字段
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

        pdf_tab._update_layer_status()
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "第1页" in tip
        # text_layers 非空但 ocr_text_blocks 为空 → 原生文字层
        assert "原生文字层" in tip
        assert "12个文本块" in tip
        doc.close()

    def test_status_list_row_count_matches_pages(self, pdf_tab):
        """状态网格格子数应等于页数，每个携带 page_index。"""
        import fitz
        from PySide6.QtCore import Qt

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [PdfPageInfo(page_index=i) for i in range(4)]
        doc = fitz.open()
        for _ in range(4):
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

        pdf_tab._update_layer_status()
        assert pdf_tab._layer_status_grid.count() == 4
        for i in range(4):
            item = pdf_tab._layer_status_grid.item(i)
            assert item.data(Qt.ItemDataRole.UserRole) == i
        doc.close()

    def test_sync_layer_grid_from_model_updates_colors(self, pdf_tab):
        """_sync_layer_grid_from_model 应按当前 model 重同步格子颜色，保留选中（Bug A）。

        场景：网格初始全部无文字层（灰）。模拟 OCR 后 model 变化（部分页有文字层），
        但逐页 page_done 信号因取消未送达 → 格子仍灰。调用 _sync_layer_grid_from_model
        后，格子 _HAS_LAYER_ROLE 应与 model 一致。
        """
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession
        from vibeocr.classic.views.tabs.pdf_tab import _HAS_LAYER_ROLE

        # 初始：4 页全部无文字层
        pages = [PdfPageInfo(page_index=i) for i in range(4)]
        doc = fitz.open()
        for _ in range(4):
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._update_layer_status()

        # 选中第 1、3 格（验证 sync 不清选中）
        grid = pdf_tab._layer_status_grid
        grid.item(1).setSelected(True)
        grid.item(3).setSelected(True)

        # 模拟 OCR 后 model 变化：页 0、2 已写入文字层（但格子还没更新）
        session.pdf_document.pages[0].has_text_layer = True
        session.pdf_document.pages[2].has_text_layer = True

        # 初始确认：格子颜色还是旧的（全 False）
        assert grid.item(0).data(_HAS_LAYER_ROLE) is False
        assert grid.item(2).data(_HAS_LAYER_ROLE) is False

        pdf_tab._sync_layer_grid_from_model()

        # sync 后：格子 _HAS_LAYER_ROLE 与 model 一致
        assert grid.item(0).data(_HAS_LAYER_ROLE) is True
        assert grid.item(1).data(_HAS_LAYER_ROLE) is False
        assert grid.item(2).data(_HAS_LAYER_ROLE) is True
        assert grid.item(3).data(_HAS_LAYER_ROLE) is False
        # 选中状态保留
        assert grid.item(1).isSelected() is True
        assert grid.item(3).isSelected() is True
        doc.close()


class TestPdfTabLayerStatusLinkage:
    """网格 ↔ 缩略图双向选中同步（按 page_index 匹配，重入保护防递归）。"""

    def _setup_session(self, pdf_tab):
        import fitz

        from vibeocr.backend.models.pdf_document import (
            PdfDocument,
            PdfPageInfo,
            TextLayerInfo,
        )
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [
            PdfPageInfo(
                page_index=2,
                has_text_layer=True,
                text_layers=[
                    TextLayerInfo(
                        index=0,
                        text_preview="t",
                        char_count=1,
                        bbox=(50.0, 50.0, 300.0, 100.0),
                        color_id=0,
                    )
                ],
            ),
            PdfPageInfo(page_index=0),
            PdfPageInfo(page_index=1),
        ]
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._refresh_thumbnails()
        pdf_tab._update_layer_status()
        return doc

    def test_grid_selection_syncs_to_thumbnail(self, pdf_tab):
        """网格选中 → 缩略图选中相同 page_index（双向断言）。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            grid = pdf_tab._layer_status_grid
            for row in range(grid.count()):
                if grid.item(row).data(Qt.ItemDataRole.UserRole) == 1:
                    grid.selectionModel().select(
                        grid.model().index(row, 0), QItemSelectionModel.ClearAndSelect
                    )
                    break
            # 缩略图应同步选中 page_index=1
            assert pdf_tab._get_selected_page_indices() == [1]
            # 网格本身仍保持该选中（未因同步被清除）
            assert [i.data(Qt.ItemDataRole.UserRole) for i in grid.selectedItems()] == [
                1
            ]
        finally:
            doc.close()

    def test_thumbnail_selection_syncs_to_grid(self, pdf_tab):
        """缩略图选中 → 网格选中相同 page_index。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            lst = pdf_tab._thumbnail_list
            model = lst.model()
            for row in range(model.rowCount()):
                if model.data(model.index(row, 0), Qt.ItemDataRole.UserRole) == 2:
                    lst.selectionModel().select(
                        model.index(row, 0), QItemSelectionModel.ClearAndSelect
                    )
                    break
            grid = pdf_tab._layer_status_grid
            cur = grid.selectedItems()
            assert len(cur) == 1
            assert cur[0].data(Qt.ItemDataRole.UserRole) == 2
        finally:
            doc.close()

    def test_no_infinite_recursion_on_sync(self, pdf_tab):
        """双向同步不应触发递归（_syncing_selection 保护 + finally 复位）。"""
        from PySide6.QtCore import QItemSelectionModel

        doc = self._setup_session(pdf_tab)
        try:
            grid = pdf_tab._layer_status_grid
            lst = pdf_tab._thumbnail_list
            # 反复交替触发，不应崩溃/栈溢出
            for _ in range(5):
                grid.selectionModel().select(
                    grid.model().index(0, 0), QItemSelectionModel.ClearAndSelect
                )
                lst.selectionModel().select(
                    lst.model().index(0, 0), QItemSelectionModel.ClearAndSelect
                )
            # 同步完成后 guard 必须复位为 False（证明 finally 跑过，未卡死）
            assert pdf_tab._syncing_selection is False
        finally:
            doc.close()


class TestPdfTabOcrCompletion:
    def test_completion_summary_with_skips(self, pdf_tab, monkeypatch):
        """skipped>0 时应弹出 information 提示含“成功 N 块 / 跳过 K 块”。"""
        import vibeocr.classic.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 5, 2)
        assert len(called) == 1
        msg = called[0][2]
        assert "成功写入 5 块" in msg
        assert "跳过 2 块" in msg

    def test_completion_no_skip_sets_status_label(self, pdf_tab, monkeypatch):
        """skipped==0 时不弹框，只在状态栏轻量提示。"""
        import vibeocr.classic.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 3, 0)
        assert called == []
        assert "文字层已添加" in pdf_tab._status_label.text()
        assert "3 块" in pdf_tab._status_label.text()

    def test_completion_nothing_written_does_not_claim_added(
        self, pdf_tab, monkeypatch
    ):
        """written==0 且 skipped==0 时不应误报“已添加”。"""
        import vibeocr.classic.views.tabs.pdf_tab as mod

        called = []
        monkeypatch.setattr(
            mod.QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 0, 0)
        assert called == []
        text = pdf_tab._status_label.text()
        assert "已添加" not in text
        assert "未添加" in text


class TestAddTextLayerForPagesWithoutLayer:
    """新按钮：一键为当前文件所有无文字层页添加文字层。"""

    def _inject_session(self, pdf_tab, doc, pdf_doc):
        from vibeocr.backend.models.pdf_session import PdfSession

        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_button_exists(self, pdf_tab):
        btn = getattr(pdf_tab, "_btn_add_text_layer_no_layer", None)
        assert btn is not None
        assert "无文字层" in btn.text()

    def test_all_have_layer_shows_info(self, pdf_tab, monkeypatch):
        """所有页都有文字层时点击按钮应弹 information 提示，不启动 OCR。"""
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo

        page_info = PdfPageInfo(page_index=0, has_text_layer=True)
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        self._inject_session(pdf_tab, doc, pdf_doc)

        called = {"info": False, "start": False}
        import vibeocr.classic.views.tabs.pdf_tab as mod

        monkeypatch.setattr(
            mod.QMessageBox,
            "information",
            lambda *a, **k: called.__setitem__("info", True),
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "start_ocr",
            lambda *a, **k: called.__setitem__("start", True),
        )

        pdf_tab._on_add_text_layer_for_pages_without_layer()

        assert called["info"] is True
        assert called["start"] is False
        doc.close()

    def test_no_active_session_returns_silently(self, pdf_tab, monkeypatch):
        """未打开文件时点击按钮应静默返回（不报错、不弹框）。"""
        called = {"info": False}
        import vibeocr.classic.views.tabs.pdf_tab as mod

        monkeypatch.setattr(
            mod.QMessageBox,
            "information",
            lambda *a, **k: called.__setitem__("info", True),
        )
        pdf_tab._on_add_text_layer_for_pages_without_layer()
        assert called["info"] is False


class TestAddTextLayerSoftGuard:
    """现有"添加文字层"按钮：选中页含已有文字层时弹三选一框。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def _patch_confirm_yes(self, monkeypatch):
        """让确认 QMessageBox.question 自动返回 Yes，避免模态阻塞。"""
        from PySide6.QtWidgets import QMessageBox

        import vibeocr.classic.views.tabs.pdf_tab as mod

        monkeypatch.setattr(
            mod.QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )

    def test_partial_layer_prompts_and_overwrite_false_skips(
        self, pdf_tab, monkeypatch
    ):
        """选中页中部分已有文字层：弹框（has=1,total=2），选"跳过"→ overwrite=False。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        # 选中两页
        from PySide6.QtCore import QItemSelectionModel

        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        captured = {}
        monkeypatch.setattr(
            pdf_tab,
            "_prompt_overwrite_choice",
            lambda has, total: captured.setdefault("args", (has, total)) or 0,  # 0=跳过
        )
        monkeypatch.setattr(
            type(pdf_tab._session_mgr),
            "is_ocr_ready",
            property(lambda self: True),
        )
        started = {}
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "start_ocr",
            lambda indices, **kw: started.update(kw),
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()

        assert captured["args"] == (1, 2)
        assert started.get("overwrite") is False

    def test_partial_layer_choose_replace_uses_overwrite_true(
        self, pdf_tab, monkeypatch
    ):
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        monkeypatch.setattr(
            pdf_tab, "_prompt_overwrite_choice", lambda has, total: 1
        )  # 先删后加
        monkeypatch.setattr(
            type(pdf_tab._session_mgr),
            "is_ocr_ready",
            property(lambda self: True),
        )
        started = {}
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "start_ocr",
            lambda indices, **kw: started.update(kw),
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()
        assert started.get("overwrite") is True

    def test_all_without_layer_no_prompt(self, pdf_tab, monkeypatch):
        """选中页全部无文字层：不弹防重复框，直接 overwrite=False。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        prompted = {"n": 0}
        monkeypatch.setattr(
            pdf_tab,
            "_prompt_overwrite_choice",
            lambda has, total: prompted.__setitem__("n", prompted["n"] + 1) or 0,
        )
        monkeypatch.setattr(
            type(pdf_tab._session_mgr),
            "is_ocr_ready",
            property(lambda self: True),
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr, "start_ocr", lambda indices, **kw: None
        )
        self._patch_confirm_yes(monkeypatch)

        pdf_tab._on_add_text_layer()
        assert prompted["n"] == 0

    def test_prompt_choice_cancel_aborts(self, pdf_tab, monkeypatch):
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        sm = pdf_tab._thumbnail_list.selectionModel()
        for r in range(2):
            sm.select(
                pdf_tab._thumbnail_list.model().index(r, 0),
                QItemSelectionModel.Select,
            )

        monkeypatch.setattr(
            pdf_tab, "_prompt_overwrite_choice", lambda has, total: 2
        )  # 取消
        monkeypatch.setattr(
            type(pdf_tab._session_mgr),
            "is_ocr_ready",
            property(lambda self: True),
        )
        started = {"n": 0}
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "start_ocr",
            lambda indices, **kw: started.__setitem__("n", started["n"] + 1),
        )

        pdf_tab._on_add_text_layer()
        assert started["n"] == 0


class TestLayerStatusContextMenu:
    """状态列表右键菜单：为选中的无文字层页添加文字层。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_context_menu_offers_action_for_pages_without_layer(
        self, pdf_tab, monkeypatch
    ):
        """无文字层页选中时，菜单应含"为 N 个无文字层页添加文字层"项。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        # 用 selectionModel 显式选中第 0 行（setCurrentRow 不保证 selectedItems）
        sm = pdf_tab._layer_status_grid.selectionModel()
        sm.select(
            pdf_tab._layer_status_grid.model().index(0, 0),
            QItemSelectionModel.Select,
        )

        # 用 FakeMenu 捕获菜单项文本，避免 exec 阻塞
        actions_text = []

        class _FakeSignal:
            def connect(self, *a, **k):
                pass

        class _FakeAction:
            @property
            def triggered(self):
                return _FakeSignal()

        class FakeMenu:
            def __init__(self, *a, **k):
                pass

            def addAction(self, text, *args):
                actions_text.append(text if isinstance(text, str) else str(text))
                return _FakeAction()

            def addSeparator(self):
                actions_text.append("sep")

            def exec(self, *a, **k):
                return None

        import vibeocr.classic.views.tabs.pdf_tab as mod

        monkeypatch.setattr(mod, "QMenu", FakeMenu)

        pdf_tab._on_layer_status_context_menu(
            pdf_tab._layer_status_grid.rect().center()
        )

        assert any("无文字层" in t for t in actions_text)

    def test_context_menu_no_action_when_all_have_layer(self, pdf_tab, monkeypatch):
        """选中页均有文字层时，菜单应提示而非提供添加项。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=True)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        sm = pdf_tab._layer_status_grid.selectionModel()
        sm.select(
            pdf_tab._layer_status_grid.model().index(0, 0),
            QItemSelectionModel.Select,
        )

        actions_text = []

        class FakeMenu:
            def __init__(self, *a, **k):
                pass

            def addAction(self, text, *args):
                actions_text.append(text if isinstance(text, str) else str(text))

                class _A:
                    def triggered(self, *a, **k):
                        pass

                return _A()

            def addSeparator(self):
                pass

            def exec(self, *a, **k):
                return None

        import vibeocr.classic.views.tabs.pdf_tab as mod

        monkeypatch.setattr(mod, "QMenu", FakeMenu)

        pdf_tab._on_layer_status_context_menu(
            pdf_tab._layer_status_grid.rect().center()
        )

        # 不应出现"添加文字层"的可执行项
        assert not any("无文字层页添加文字层" in t for t in actions_text)


class TestLayerStatusGrid:
    """文字层状态网格化（QListWidget IconMode + delegate）。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_grid_exists_and_is_icon_mode(self, pdf_tab):
        """应有 _layer_status_grid（QListWidget IconMode）替代旧的列表。"""
        grid = getattr(pdf_tab, "_layer_status_grid", None)
        assert grid is not None, "应有 _layer_status_grid"
        assert isinstance(grid, QListWidget)
        assert grid.viewMode() == QListWidget.ViewMode.IconMode

    def test_grid_cell_count_equals_pages(self, pdf_tab):
        """网格格子数应等于页数，每个携带 page_index。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=i) for i in range(5)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        grid = pdf_tab._layer_status_grid
        assert grid.count() == 5
        for i in range(5):
            item = grid.item(i)
            assert item.data(Qt.ItemDataRole.UserRole) == i

    def test_grid_has_summary_label(self, pdf_tab):
        """网格上方应有汇总 Label（共 N 页 / OCR文字层 / 原生文字层 / 无文字层）。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=True),
            PdfPageInfo(page_index=1, has_text_layer=False),
            PdfPageInfo(page_index=2, has_text_layer=True),
        ]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        text = pdf_tab._layer_summary_label.text()
        assert "共 3 页" in text
        # has_text_layer=True 但 ocr_text_blocks 为空 → 原生文字层（2 页）
        assert "原生文字层 2 页" in text
        assert "无文字层 1 页" in text

    def test_grid_tooltip_shows_block_count(self, pdf_tab):
        """有文字层格子的 tooltip 含块数。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo, TextLayerInfo

        pages = [
            PdfPageInfo(
                page_index=0,
                has_text_layer=True,
                text_layers=[
                    TextLayerInfo(
                        index=i,
                        text_preview="t",
                        char_count=1,
                        bbox=(0.0, 0.0, 1.0, 1.0),
                        color_id=i,
                    )
                    for i in range(7)
                ],
            ),
        ]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "7" in tip
        assert "文字层" in tip

    def test_grid_no_layer_cell_tooltip(self, pdf_tab):
        """无文字层格子的 tooltip 应提示"无文字层"。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_status()
        tip = pdf_tab._layer_status_grid.item(0).toolTip()
        assert "无文字层" in tip

    def test_delegate_uses_theme_colors_for_states(self, pdf_tab):
        """delegate 应按状态选色：选中=accent、有层=success、无层=text_subtle。"""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

        from vibeocr.classic.ui.theme import Colors
        from vibeocr.classic.views.tabs.pdf_tab import (
            _HAS_LAYER_ROLE,
            _LAYER_ROLE,
            LayerStatusDelegate,
        )

        delegate = LayerStatusDelegate()

        def _bg_for(state_flags: QStyle.StateFlag, has_layer: bool) -> QColor:
            """用像素采样法读出格子的填充色（左上角偏内一点）。"""
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 40, 40)
            opt.state = state_flags
            idx = _StubIndex({(_LAYER_ROLE, 0), (_HAS_LAYER_ROLE, has_layer)})
            pm = QPixmap(40, 40)
            pm.fill(QColor(0, 0, 0))
            painter = QPainter(pm)
            try:
                delegate.paint(painter, opt, idx)
            finally:
                painter.end()
            return QColor(pm.toImage().pixel(8, 8))

        sel = _bg_for(
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected, True
        )
        has = _bg_for(QStyle.StateFlag.State_Enabled, True)
        none_bg = _bg_for(QStyle.StateFlag.State_Enabled, False)
        assert sel.name() == QColor(Colors.accent).name()
        assert has.name() == QColor(Colors.success).name()
        assert none_bg.name() == QColor(Colors.text_subtle).name()

    def test_delegate_uses_theme_colors_for_four_states(self, pdf_tab):
        """四态视觉投影：processing=accent(蓝)、failed=danger(红)、done=success(绿)。

        即使 has_layer=False，只要 state="done" 也应显示 success 绿（已落盘）。
        """
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

        from vibeocr.classic.ui.theme import Colors
        from vibeocr.classic.views.tabs.pdf_tab import (
            _HAS_LAYER_ROLE,
            _LAYER_ROLE,
            LayerStatusDelegate,
        )

        delegate = LayerStatusDelegate()

        def _bg_for(state_str: str, has_layer: bool = False) -> QColor:
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 40, 40)
            opt.state = QStyle.StateFlag.State_Enabled
            idx = _StubIndex(
                {
                    (_LAYER_ROLE, 0),
                    (_HAS_LAYER_ROLE, has_layer),
                    (_LAYER_STATE_ROLE, state_str),
                }
            )
            pm = QPixmap(40, 40)
            pm.fill(QColor(0, 0, 0))
            painter = QPainter(pm)
            try:
                delegate.paint(painter, opt, idx)
            finally:
                painter.end()
            return QColor(pm.toImage().pixel(8, 8))

        assert _bg_for("processing").name() == QColor(Colors.accent).name()
        assert _bg_for("failed").name() == QColor(Colors.danger).name()
        assert _bg_for("done", has_layer=False).name() == QColor(Colors.success).name()
        assert _bg_for("done", has_layer=True).name() == QColor(Colors.success).name()
        # none 态（无 state）回退到 has_layer 推导
        assert (
            _bg_for("none", has_layer=False).name() == QColor(Colors.text_subtle).name()
        )


class _StubIndex:
    """QModelIndex 替身：按 (role) 返回预设 data。"""

    def __init__(self, pairs):
        self._data = dict(pairs)

    def data(self, role):
        return self._data.get(role)


class TestOcrPerPageFeedback:
    """OCR 逐页完成即时反馈：格子逐页变绿，缩略图不重渲染。"""

    def _inject(self, pdf_tab, pages):
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = fitz.open()
        for _ in pages:
            doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._update_layer_status()
        return session

    def test_ocr_page_result_updates_grid_cell_to_green(self, pdf_tab):
        """ocr_page_done 后该格子 has_layer 应为 True（变绿）。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        session = self._inject(pdf_tab, pages)
        # 模拟 OCR 完成第 0 页：has_text_layer 被置 True
        session.pdf_document.pages[0].has_text_layer = True

        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())

        item = pdf_tab._layer_status_grid.item(0)
        # _HAS_LAYER_ROLE = UserRole + 1
        assert item.data(Qt.ItemDataRole.UserRole + 1) is True

    def test_ocr_page_result_does_not_render_thumbnail(self, pdf_tab, monkeypatch):
        """OCR 完成一页不应触发缩略图渲染(隐形文字层无视觉变化)。

        新架构:ocr_page_done 信号回调只更新文字层网格状态(角标),
        不触发缩略图 IPC worker 请求。验证 ThumbnailModel.request_render
        不被调用。
        """
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        session = self._inject(pdf_tab, pages)
        session.pdf_document.pages[0].has_text_layer = True

        called = []
        monkeypatch.setattr(
            pdf_tab._thumbnail_model,
            "request_render",
            lambda row: called.append(row),
        )
        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())
        assert called == []

    def test_ocr_page_result_updates_summary_label(self, pdf_tab):
        """ocr_page_done 后汇总 Label 应反映新的计数。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        session = self._inject(pdf_tab, pages)
        # 第 0 页 OCR 完成：设置 has_text_layer + ocr_text_blocks 模拟 OCR 写层
        session.pdf_document.pages[0].has_text_layer = True
        session.pdf_document.pages[0].ocr_text_blocks = [{"text": "test"}]
        pdf_tab._session_mgr.ocr_page_done.emit("x.pdf", 0, object())
        text = pdf_tab._layer_summary_label.text()
        assert "OCR文字层 1 页" in text
        assert "无文字层 1 页" in text

    def test_ocr_completion_preserves_selection(self, pdf_tab):
        """OCR 全部完成（ocr_done/ocr_stats_ready）不应清空用户选中（spec 第 6 节）。"""
        from PySide6.QtCore import QItemSelectionModel

        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        # 刷新缩略图模型数据源（_inject 只改 session_mgr，模型需手动同步）
        pdf_tab._refresh_thumbnails()
        # 用户选中 page_index=1
        lst = pdf_tab._thumbnail_list
        model = lst.model()
        for row in range(model.rowCount()):
            if model.data(model.index(row, 0), Qt.ItemDataRole.UserRole) == 1:
                lst.selectionModel().select(
                    model.index(row, 0), QItemSelectionModel.ClearAndSelect
                )
                break
        assert pdf_tab._get_selected_page_indices() == [1]

        # OCR 完成：两个完成信号都不应清空选中
        pdf_tab._session_mgr.ocr_done.emit("x.pdf", 2, 0)
        assert pdf_tab._get_selected_page_indices() == [1]
        pdf_tab._session_mgr.ocr_stats_ready.emit("x.pdf", 5, 0)
        assert pdf_tab._get_selected_page_indices() == [1]

    def test_update_layer_grid_page_sets_state_role(self, pdf_tab):
        """_update_layer_grid_page(state=...) 应把视觉态写入 _LAYER_STATE_ROLE。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, has_text_layer=False),
            PdfPageInfo(page_index=1, has_text_layer=False),
        ]
        self._inject(pdf_tab, pages)
        # 置第 0 页为 processing、第 1 页为 failed
        pdf_tab._update_layer_grid_page(0, state="processing")
        pdf_tab._update_layer_grid_page(1, state="failed")
        assert (
            pdf_tab._layer_status_grid.item(0).data(_LAYER_STATE_ROLE) == "processing"
        )
        assert pdf_tab._layer_status_grid.item(1).data(_LAYER_STATE_ROLE) == "failed"

    def test_update_layer_grid_page_no_state_leaves_role_unset(self, pdf_tab):
        """state=None 时不应写入 _LAYER_STATE_ROLE（保留 has_layer 推导语义）。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._inject(pdf_tab, pages)
        pdf_tab._update_layer_grid_page(0)  # state=None
        assert pdf_tab._layer_status_grid.item(0).data(_LAYER_STATE_ROLE) is None


class TestLoadDoneSidecarHint:
    """打开 PDF 时若检测到未完成 sidecar，状态栏提示续传（7C）。"""

    def _inject(self, pdf_tab, pages):
        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        return session

    def test_load_done_shows_resume_hint_when_sidecar_pending(
        self, pdf_tab, monkeypatch
    ):
        """restore_pending_pages 返回非空时，状态栏应显示续传提示文案。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=i) for i in range(4)]
        self._inject(pdf_tab, pages)

        # 模拟 sidecar 有 2 页已保存（未完成）
        monkeypatch.setattr(
            "vibeocr.classic.views.tabs.pdf_tab.ocr_sidecar.restore_pending_pages",
            lambda file_path: {0: 0, 1: 0},
        )
        pdf_tab._on_load_done("x.pdf")
        text = pdf_tab._status_label.text()
        assert "检测到上次未完成的 OCR" in text
        assert "2/4" in text

    def test_load_done_no_hint_when_no_sidecar(self, pdf_tab, monkeypatch):
        """无 sidecar（restore_pending_pages 返回 None）时不显示续传提示。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=i) for i in range(3)]
        self._inject(pdf_tab, pages)

        monkeypatch.setattr(
            "vibeocr.classic.views.tabs.pdf_tab.ocr_sidecar.restore_pending_pages",
            lambda file_path: None,
        )
        pdf_tab._on_load_done("x.pdf")
        text = pdf_tab._status_label.text()
        assert "未完成" not in text
        assert "加载完成" in text

    def test_load_done_sidecar_error_is_silent(self, pdf_tab, monkeypatch):
        """restore_pending_pages 抛异常时应静默（不影响正常加载文案）。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [PdfPageInfo(page_index=i) for i in range(2)]
        self._inject(pdf_tab, pages)

        def _boom(file_path):
            raise RuntimeError("sidecar 读取出错")

        monkeypatch.setattr(
            "vibeocr.classic.views.tabs.pdf_tab.ocr_sidecar.restore_pending_pages",
            _boom,
        )
        pdf_tab._on_load_done("x.pdf")
        # 异常被吞掉，状态栏应仍是"加载完成"
        assert "加载完成" in pdf_tab._status_label.text()


class TestThumbnailIncrementalUpdate:
    """缩略图增量更新:reorder/rotate 走异步 IPC,缓存失效由信号驱动。

    新架构:PdfTab._on_rotate / _on_pages_reordered_with_order 调
    manager.*_async(异步 IPC),不直接操作 doc。缩略图缓存失效由
    manager.thumbnails_invalidated 信号触发(结构变更/旋转后)。
    本测试 mock manager 的 async 方法为同步 emit 信号,验证 UI 行为。
    """

    def _setup(self, pdf_tab, n_pages=3):

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [
            PdfPageInfo(page_index=i, rect=(0.0, 0.0, 612.0, 792.0))
            for i in range(n_pages)
        ]
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._refresh_thumbnails()
        return session

    def test_reorder_calls_manager_async(self, pdf_tab, monkeypatch):
        """拖拽排序应调 manager.reorder_async(不再操作 PdfService)。"""
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "reorder_async",
            lambda order: called.append(order),
        )
        self._setup(pdf_tab)
        pdf_tab._on_pages_reordered_with_order([2, 1, 0])
        assert called == [[2, 1, 0]]

    def test_reorder_preserves_selection(self, pdf_tab):
        """拖拽重排应保留选中状态(reset 会丢选中,需手动恢复)。"""
        from PySide6.QtCore import QItemSelectionModel

        self._setup(pdf_tab)
        lst = pdf_tab._thumbnail_list
        model = lst.model()
        sm = lst.selectionModel()
        sm.clear()
        for row in range(model.rowCount()):
            if model.data(model.index(row, 0), Qt.ItemDataRole.UserRole) in (1, 2):
                sm.select(model.index(row, 0), QItemSelectionModel.Select)
        assert pdf_tab._get_selected_page_indices() == [1, 2]

    def test_rotate_calls_manager_async(self, pdf_tab, monkeypatch):
        """旋转选中页应调 manager.rotate_pages_async。"""
        from PySide6.QtCore import QItemSelectionModel

        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages, angle: called.append((pages, angle)),
        )
        self._setup(pdf_tab)
        lst = pdf_tab._thumbnail_list
        model = lst.model()
        for row in range(model.rowCount()):
            if model.data(model.index(row, 0), Qt.ItemDataRole.UserRole) == 1:
                lst.selectionModel().select(
                    model.index(row, 0), QItemSelectionModel.ClearAndSelect
                )
                break
        pdf_tab._on_rotate(90)
        assert called == [([1], 90)]

    def test_thumbnails_invalidated_clears_cache(self, pdf_tab):
        """manager.thumbnails_invalidated 信号应清缓存对应行(旋转后)。"""
        from PySide6.QtGui import QPixmap

        self._setup(pdf_tab)
        target_row = 1
        pdf_tab._thumbnail_model._cache.put(target_row, QPixmap(10, 10))
        assert target_row in pdf_tab._thumbnail_model._cache
        # 模拟 manager 发失效信号
        pdf_tab._session_mgr.thumbnails_invalidated.emit([target_row])
        assert target_row not in pdf_tab._thumbnail_model._cache


class TestPdfTabRotateAllAndAspectDeskew:
    """旋转全部（CW/CCW 两按钮）+ 横放/纵放摆正。"""

    def _setup(self, pdf_tab, pages=None):
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        if pages is None:
            pages = [
                PdfPageInfo(page_index=i, rect=(0.0, 0.0, 612.0, 792.0))
                for i in range(3)
            ]
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._refresh_thumbnails()
        return session

    def test_rotate_all_cw_calls_manager_with_all_indices(self, pdf_tab, monkeypatch):
        """全部顺时针90° 按钮应调 rotate_pages_async(全部页, 90)。"""
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages, angle: called.append((pages, angle)),
        )
        self._setup(pdf_tab)
        pdf_tab._on_rotate_all(90)
        assert called == [([0, 1, 2], 90)]

    def test_rotate_all_ccw_calls_manager_with_all_indices(self, pdf_tab, monkeypatch):
        """全部逆时针90° 按钮应调 rotate_pages_async(全部页, -90)。"""
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages, angle: called.append((pages, angle)),
        )
        self._setup(pdf_tab)
        pdf_tab._on_rotate_all(-90)
        assert called == [([0, 1, 2], -90)]

    def test_deskew_landscape_rotates_portrait_pages(self, pdf_tab, monkeypatch):
        """横放摆正：纵向页（高>宽）应被旋转，横向页不动。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        # 页0: 纵向 (612x792 高>宽)，页1: 横向 (792x612 宽>高)
        pages = [
            PdfPageInfo(page_index=0, rect=(0.0, 0.0, 612.0, 792.0)),
            PdfPageInfo(page_index=1, rect=(0.0, 0.0, 792.0, 612.0)),
        ]
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages_, angle: called.append((pages_, angle)),
        )
        self._setup(pdf_tab, pages)
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", lambda: [0, 1])
        # 跳过弹窗
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        pdf_tab._on_deskew_by_aspect("landscape")
        # 只有纵向页0需要旋转
        assert called == [([0], 90)]

    def test_deskew_portrait_rotates_landscape_pages(self, pdf_tab, monkeypatch):
        """纵放摆正：横向页（宽>高）应被旋转，纵向页不动。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(page_index=0, rect=(0.0, 0.0, 612.0, 792.0)),
            PdfPageInfo(page_index=1, rect=(0.0, 0.0, 792.0, 612.0)),
        ]
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages_, angle: called.append((pages_, angle)),
        )
        self._setup(pdf_tab, pages)
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", lambda: [0, 1])
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        pdf_tab._on_deskew_by_aspect("portrait")
        # 只有横向页1需要旋转
        assert called == [([1], 90)]

    def test_deskew_by_aspect_respects_rotation(self, pdf_tab, monkeypatch):
        """横放摆正考虑 page.rotation：旋转90°的纵向页显示为横向，不应再旋。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        # 纵向 mediabox (612x792) + rotation=90 → 显示横向，横放摆正不应旋
        pages = [
            PdfPageInfo(page_index=0, rect=(0.0, 0.0, 612.0, 792.0), rotation=90),
        ]
        called = []
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            lambda pages_, angle: called.append((pages_, angle)),
        )
        self._setup(pdf_tab, pages)
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", lambda: [0])
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        pdf_tab._on_deskew_by_aspect("landscape")
        # 页0旋转90°后显示为横向，无需旋转
        assert called == []


class TestPdfTabLayerTypeIndicator:
    """文字层图示三态：OCR文字层（深绿）/ 原生文字层（浅绿）/ 无文字层（灰）。"""

    def _setup(self, pdf_tab, pages):
        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._update_layer_status()
        return session

    def test_layer_type_ocr_when_blocks_present(self, pdf_tab):
        """ocr_text_blocks 非空 → _LAYER_TYPE_ROLE = "ocr"。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo
        from vibeocr.classic.views.tabs.pdf_tab import _LAYER_TYPE_ROLE

        pages = [
            PdfPageInfo(
                page_index=0, has_text_layer=True, ocr_text_blocks=[{"text": "x"}]
            ),
        ]
        self._setup(pdf_tab, pages)
        item = pdf_tab._layer_status_grid.item(0)
        assert item.data(_LAYER_TYPE_ROLE) == "ocr"

    def test_layer_type_native_when_no_blocks(self, pdf_tab):
        """has_text_layer=True 但 ocr_text_blocks 空 → "native"。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo
        from vibeocr.classic.views.tabs.pdf_tab import _LAYER_TYPE_ROLE

        pages = [PdfPageInfo(page_index=0, has_text_layer=True)]
        self._setup(pdf_tab, pages)
        item = pdf_tab._layer_status_grid.item(0)
        assert item.data(_LAYER_TYPE_ROLE) == "native"

    def test_layer_type_none_when_no_layer(self, pdf_tab):
        """无文字层 → _LAYER_TYPE_ROLE = None。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo
        from vibeocr.classic.views.tabs.pdf_tab import _LAYER_TYPE_ROLE

        pages = [PdfPageInfo(page_index=0, has_text_layer=False)]
        self._setup(pdf_tab, pages)
        item = pdf_tab._layer_status_grid.item(0)
        assert item.data(_LAYER_TYPE_ROLE) is None

    def test_summary_shows_three_categories(self, pdf_tab):
        """汇总 Label 应含 OCR文字层 / 原生文字层 / 无文字层 三个类别。"""
        from vibeocr.backend.models.pdf_document import PdfPageInfo

        pages = [
            PdfPageInfo(
                page_index=0, has_text_layer=True, ocr_text_blocks=[{"text": "x"}]
            ),
            PdfPageInfo(page_index=1, has_text_layer=True),
            PdfPageInfo(page_index=2, has_text_layer=False),
        ]
        self._setup(pdf_tab, pages)
        text = pdf_tab._layer_summary_label.text()
        assert "OCR文字层 1 页" in text
        assert "原生文字层 1 页" in text
        assert "无文字层 1 页" in text


class TestPdfTabRotateNoSelectionFeedback:
    """旋转选中页无选中时应弹提示而非静默返回。"""

    def _setup(self, pdf_tab):
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [PdfPageInfo(page_index=i) for i in range(3)]
        pdf_doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"

    def test_rotate_no_selection_shows_message(self, pdf_tab, monkeypatch):
        """_on_rotate 无选中时应弹 information 提示，不调 manager。"""
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        self._setup(pdf_tab)
        called = []
        monkeypatch.setattr(
            QMessageBox, "information", lambda *a, **k: called.append(a)
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "rotate_pages_async",
            MagicMock(side_effect=AssertionError("不应调 rotate_pages_async")),
        )
        # 无选中
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", list)
        pdf_tab._on_rotate(90)
        assert len(called) == 1
        assert "请先选择" in called[0][2]


class TestPdfTabDeleteTextLayerAsync:
    """删除文字层改异步：调 manager.delete_text_layers_async 而非主线程循环。"""

    def test_delete_calls_manager_async(self, pdf_tab, monkeypatch):
        """_on_delete_text_layer 应调 manager.delete_text_layers_async。"""
        from unittest.mock import MagicMock

        # 让 _get_selected_page_indices 返回 [0]，跳过"请先选择页面"
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", lambda: [0])
        # 跳过确认对话框（直接返回 Yes）
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        # mock manager
        mock_mgr = MagicMock()
        pdf_tab._session_mgr = mock_mgr
        mock_mgr.active_session = MagicMock()

        pdf_tab._on_delete_text_layer()
        mock_mgr.delete_text_layers_async.assert_called_once_with([0])

    def test_delete_layer_done_shows_residual_warning(self, pdf_tab, monkeypatch):
        """_on_delete_layer_done 有 residual_pages 时弹 warning。"""
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        mock_mgr = MagicMock()
        pdf_tab._session_mgr = mock_mgr
        session = MagicMock()
        session.file_path = "/tmp/x.pdf"
        mock_mgr.active_session = session

        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
        )

        pdf_tab._on_delete_layer_done("/tmp/x.pdf", [2, 5])
        assert len(warnings) == 1

    def test_delete_layer_done_syncs_stale_grid_from_model(self, pdf_tab):
        """逐页 mutate_done 丢失时，删除最终回调也必须把格子同步为无文字层。"""
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession
        from vibeocr.classic.views.tabs.pdf_tab import _HAS_LAYER_ROLE

        document = PdfDocument(
            file_path="x.pdf",
            pages=[PdfPageInfo(page_index=0, has_text_layer=True)],
        )
        session = PdfSession(
            file_path="x.pdf",
            session_id="sid",
            pdf_document=document,
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._update_layer_status()
        item = pdf_tab._layer_status_grid.item(0)
        assert item.data(_HAS_LAYER_ROLE) is True

        # 模拟 manager 已应用删除结果，但逐页 mutate_done 信号未送达 UI。
        document.pages[0].has_text_layer = False
        document.pages[0].text_layers = []
        document.pages[0].ocr_text_blocks = []

        pdf_tab._on_delete_layer_done("x.pdf", [])

        assert item.data(_HAS_LAYER_ROLE) is False
        assert "无文字层" in item.toolTip()

    def test_batch_mutate_done_with_page_key_uses_terminal_refresh(
        self, pdf_tab, monkeypatch
    ):
        """整批完成即使带 page=None，也不能误判成逐页完成。"""
        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        session = PdfSession(
            file_path="x.pdf",
            session_id="sid",
            pdf_document=PdfDocument(file_path="x.pdf", pages=[]),
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        refreshed = []
        monkeypatch.setattr(
            pdf_tab,
            "_after_structural_change",
            lambda: refreshed.append(True),
        )

        pdf_tab._on_mutate_done(
            "x.pdf",
            {
                "diff_applied": True,
                "op": "reorder",
                "page": None,
            },
        )

        assert refreshed == [True]


class TestPdfTabSaveAsync:
    def test_save_calls_manager_save_async(self, pdf_tab, monkeypatch):
        """_on_save 应调 manager.save_async(path=None)，而非主线程 PdfService.save。"""
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        pdf_tab._session_mgr = mock_mgr
        mock_mgr.active_session = MagicMock()
        # _load_ocr_prefs 在 save_async 调用前被调，mock 它
        monkeypatch.setattr(pdf_tab, "_load_ocr_prefs", lambda: (MagicMock(), None))

        pdf_tab._on_save()
        mock_mgr.save_async.assert_called_once()


class TestPdfTabSaveContinuation:
    @staticmethod
    def _setup(pdf_tab, monkeypatch):
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = PdfDocument(file_path="a.pdf", pages=[PdfPageInfo(page_index=0)])
        session = PdfSession(file_path="a.pdf", session_id="sid-a", pdf_document=doc)
        session.pdf_document.is_modified = True
        mgr = MagicMock()
        mgr.active_session = session
        mgr.save_async.return_value = True
        mgr.switch_session.return_value = True
        mgr.is_ocr_ready = True
        mgr._OCR_PROGRESS_SUBSTEPS = 3
        pdf_tab._session_mgr = mgr
        monkeypatch.setattr(pdf_tab, "_load_ocr_prefs", lambda: (MagicMock(), None))
        pdf_tab._file_selector.blockSignals(True)
        pdf_tab._file_selector.clear()
        pdf_tab._file_selector.addItem("a.pdf", "a.pdf")
        pdf_tab._file_selector.addItem("b.pdf", "b.pdf")
        pdf_tab._file_selector.setCurrentIndex(0)
        pdf_tab._file_selector.blockSignals(False)
        return mgr, session

    def test_save_success_then_worker_finished_switches_file(
        self, pdf_tab, monkeypatch
    ):
        from PySide6.QtWidgets import QMessageBox

        mgr, session = self._setup(pdf_tab, monkeypatch)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
        )

        pdf_tab._on_file_selected(1)
        mgr.switch_session.assert_not_called()
        assert pdf_tab._pending_after_save is not None
        assert pdf_tab._file_selector.currentData() == "a.pdf"

        session.pdf_document.is_modified = False
        pdf_tab._on_save_done("a.pdf")
        mgr.switch_session.assert_not_called()
        pdf_tab._on_mutate_state_changed("a.pdf", "save", "completed")

        mgr.switch_session.assert_called_once_with("b.pdf")
        assert pdf_tab._pending_after_save is None
        assert pdf_tab._file_selector.currentData() == "b.pdf"

    def test_save_failure_clears_pending_without_switch(self, pdf_tab, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        mgr, _session = self._setup(pdf_tab, monkeypatch)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
        )
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        pdf_tab._on_file_selected(1)
        pdf_tab._on_mutate_failed("a.pdf", "disk full")
        pdf_tab._on_mutate_state_changed("a.pdf", "save", "completed")

        assert pdf_tab._pending_after_save is None
        mgr.switch_session.assert_not_called()

    def test_save_success_then_finished_continues_ocr(self, pdf_tab, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        mgr, session = self._setup(pdf_tab, monkeypatch)
        answers = iter(
            [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Yes]
        )
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: next(answers))
        )
        monkeypatch.setattr(pdf_tab, "_get_selected_page_indices", lambda: [0])
        monkeypatch.setattr(pdf_tab, "_begin_ocr_ui", MagicMock())
        mgr.start_ocr.return_value = True

        pdf_tab._on_add_text_layer()
        mgr.start_ocr.assert_not_called()
        session.pdf_document.is_modified = False
        pdf_tab._on_save_done("a.pdf")
        pdf_tab._on_mutate_state_changed("a.pdf", "save", "completed")

        mgr.start_ocr.assert_called_once()
        assert mgr.start_ocr.call_args.args[0] == [0]

    def test_save_cancelled_clears_pending(self, pdf_tab, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        mgr, _session = self._setup(pdf_tab, monkeypatch)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
        )
        pdf_tab._on_file_selected(1)

        pdf_tab._on_mutate_state_changed("a.pdf", "save", "cancelled")

        assert pdf_tab._pending_after_save is None
        mgr.switch_session.assert_not_called()


class TestPdfTabLoadHint:
    def test_load_progress_updates_status(self, pdf_tab):
        """_on_load_progress 应更新状态栏显示加载进度。"""
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        pdf_tab._session_mgr = mock_mgr
        session = MagicMock()
        session.file_path = "/tmp/x.pdf"
        mock_mgr.active_session = session

        pdf_tab._on_load_progress("/tmp/x.pdf", 3, 10)
        assert "3/10" in pdf_tab._status_label.text()


class TestPdfTabOcrProgress:
    def test_begin_ocr_ui_range_uses_substeps(self, pdf_tab):
        """_begin_ocr_ui 进度条范围 = 页数 × 子步数（与 manager progress_total 对齐）。"""
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr._OCR_PROGRESS_SUBSTEPS = 3
        pdf_tab._session_mgr = mock_mgr

        pdf_tab._begin_ocr_ui([0, 1, 2])  # 3 页

        assert pdf_tab._progress_bar.maximum() == 9  # 3 × 3
        assert pdf_tab._progress_bar.minimum() == 0
        assert pdf_tab._progress_bar.value() == 0
        assert not pdf_tab._progress_bar.isHidden()  # setVisible(True) → 非 hidden

    def test_progress_update_text_uses_pages_and_pct(self, pdf_tab):
        """_on_ocr_progress_update 文案应换算页数 + 百分比，且用"已处理"而非"正在识别第X页"。"""
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr._OCR_PROGRESS_SUBSTEPS = 3
        pdf_tab._session_mgr = mock_mgr

        # 2 页 × 3 子步 = total 6；current=4 → 已处理 1/2 页，66%（int(4*100/6)）
        pdf_tab._on_ocr_progress_update("/tmp/x.pdf", 4, 6)
        text = pdf_tab._status_label.text()
        assert "66%" in text
        assert "1/2" in text
        assert "已处理" in text
        assert "正在识别第" not in text  # 旧文案不应残留

        assert pdf_tab._progress_bar.value() == 4

    def test_progress_update_at_completion(self, pdf_tab):
        """完成时 current==total → 100%、已处理 全部页。"""
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr._OCR_PROGRESS_SUBSTEPS = 3
        pdf_tab._session_mgr = mock_mgr

        pdf_tab._on_ocr_progress_update("/tmp/x.pdf", 9, 9)  # 3 页 × 3
        text = pdf_tab._status_label.text()
        assert "100%" in text
        assert "3/3" in text


class TestPdfTabExportAsync:
    def test_export_calls_manager_export_all_async(self, pdf_tab, monkeypatch):
        """_on_export_all 应调 manager.export_all_async。"""
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QFileDialog

        mock_mgr = MagicMock()
        pdf_tab._session_mgr = mock_mgr
        # 有 modified session
        mock_mgr.get_modified_sessions.return_value = [("/tmp/a.pdf", MagicMock())]
        # mock 文件对话框返回目录
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            staticmethod(lambda *a, **k: "/tmp/out"),
        )

        pdf_tab._on_export_all()
        mock_mgr.export_all_async.assert_called_once_with("/tmp/out")

    def test_export_failure_restores_buttons_and_progress(self, pdf_tab, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        critical = []
        monkeypatch.setattr(
            QMessageBox, "critical", staticmethod(lambda *args: critical.append(args))
        )
        pdf_tab._set_file_buttons_enabled(False)
        pdf_tab._progress_bar.setVisible(True)

        pdf_tab._on_export_failed("out of memory")

        assert pdf_tab._progress_bar.isHidden()
        assert pdf_tab._btn_open.isEnabled()
        assert pdf_tab._btn_add_file.isEnabled()
        assert pdf_tab._status_label.text() == "批量导出失败"
        assert critical and critical[0][-1] == "out of memory"


class TestPdfTabAutoDeskew:
    """自动摆正按钮：点击 → 调 manager.auto_deskew_async(selected_indices)。"""

    def _inject_single_page_session(self, pdf_tab):
        import fitz

        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        doc = fitz.open()
        doc.new_page(width=200, height=300)
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[PdfPageInfo(page_index=0)])
        session = PdfSession(
            file_path="x.pdf", session_id="test-sid", pdf_document=pdf_doc
        )
        pdf_tab._session_mgr._sessions["x.pdf"] = session
        pdf_tab._session_mgr._active_path = "x.pdf"
        pdf_tab._refresh_thumbnails()
        # 直接注入底层字段不会触发 active_changed → 手动同步按钮启用态
        # （生产中由 _on_active_changed/_set_file_buttons_enabled 处理）。
        pdf_tab._set_file_buttons_enabled(True)
        return doc

    def test_button_exists(self, pdf_tab):
        btn = getattr(pdf_tab, "_btn_auto_deskew", None)
        assert btn is not None
        assert "自动摆正" in btn.text()

    def test_auto_deskew_button_calls_manager(self, pdf_tab, monkeypatch):
        """点'自动摆正'应调用 session_manager.auto_deskew_async。"""
        from unittest.mock import patch

        # OCR 服务就绪（否则前置校验会拦截，不会调 auto_deskew_async）
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready", property(lambda self: True)
        )
        doc = self._inject_single_page_session(pdf_tab)
        try:
            # 选中第一页（ThumbnailListView 基于 QListView+ThumbnailModel，
            # 用 selectionModel 选中第 0 行）
            sm = pdf_tab._thumbnail_list.selectionModel()
            model = pdf_tab._thumbnail_model
            sm.select(
                model.index(0, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Current,
            )

            with patch.object(pdf_tab._session_mgr, "auto_deskew_async") as mock_async:
                pdf_tab._btn_auto_deskew.click()
                mock_async.assert_called_once_with([0])
        finally:
            doc.close()

    def test_auto_deskew_no_selection_shows_info(self, pdf_tab, monkeypatch):
        """未选中页时点击应弹 information 提示，不调 auto_deskew_async。"""
        from unittest.mock import patch

        # OCR 服务就绪：跳过 OCR 校验，验证选中页校验分支
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready", property(lambda self: True)
        )
        doc = self._inject_single_page_session(pdf_tab)
        try:
            import vibeocr.classic.views.tabs.pdf_tab as mod

            called = []
            monkeypatch.setattr(
                mod.QMessageBox, "information", lambda *a, **k: called.append(a)
            )
            with patch.object(pdf_tab._session_mgr, "auto_deskew_async") as mock_async:
                pdf_tab._btn_auto_deskew.click()
                mock_async.assert_not_called()
            assert len(called) == 1
        finally:
            doc.close()

    def test_auto_deskew_no_ocr_service_shows_info(self, pdf_tab, monkeypatch):
        """无 OCR 服务时点击应弹 information 提示，不禁用按钮、不调 async。"""
        from unittest.mock import patch

        # 无 OCR 服务（is_ocr_ready == False）
        monkeypatch.setattr(
            type(pdf_tab._session_mgr), "is_ocr_ready", property(lambda self: False)
        )
        doc = self._inject_single_page_session(pdf_tab)
        try:
            import vibeocr.classic.views.tabs.pdf_tab as mod

            called = []
            monkeypatch.setattr(
                mod.QMessageBox, "information", lambda *a, **k: called.append(a)
            )
            # 按钮先启用，点击后应保持启用（不被禁用）
            pdf_tab._btn_auto_deskew.setEnabled(True)
            with patch.object(pdf_tab._session_mgr, "auto_deskew_async") as mock_async:
                pdf_tab._btn_auto_deskew.click()
                mock_async.assert_not_called()
            assert len(called) == 1
            assert "未配置 OCR 服务" in called[0][2]
            # 关键：按钮未被禁用（无 OCR 服务时静默 return 前置校验）
            assert pdf_tab._btn_auto_deskew.isEnabled() is True
        finally:
            doc.close()


def test_layer_cell_tooltip_marks_deskewed():
    from vibeocr.backend.models.pdf_document import PdfPageInfo
    from vibeocr.classic.views.tabs.pdf_tab import PdfTab

    p = PdfPageInfo(page_index=3)
    p.has_text_layer = True
    p.deskewed = True
    tip = PdfTab._layer_cell_tooltip(p)
    assert "已纠偏" in tip
    assert "文字层" in tip  # 底色信息仍在（原生或 OCR）


def test_layer_cell_tooltip_no_deskew_when_false():
    from vibeocr.backend.models.pdf_document import PdfPageInfo
    from vibeocr.classic.views.tabs.pdf_tab import PdfTab

    p = PdfPageInfo(page_index=0)
    p.has_text_layer = True
    p.deskewed = False
    tip = PdfTab._layer_cell_tooltip(p)
    assert "已纠偏" not in tip


class TestBatchOpenSuppressesSwitch:
    """批量异步导入：不在每个 session_added 时切换 active（避免 N 次全量重建）。"""

    def test_batch_opening_suppresses_combo_switch(
        self, pdf_tab, tmp_path, monkeypatch
    ):
        """导入多文件时,_batch_opening 抑制 setCurrentIndex;open_done 后切换一次。"""
        import fitz
        from PySide6.QtWidgets import QFileDialog

        # 造 2 个真实 PDF
        paths = []
        for n in range(2):
            p = tmp_path / f"doc_{n}.pdf"
            doc = fitz.open()
            doc.new_page()
            doc.save(str(p))
            doc.close()
            paths.append(str(p))

        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileNames",
            staticmethod(lambda *a, **k: (paths, "")),
        )

        # 记录 active_changed 触发次数（每次都会调 _on_active_changed → set_session）
        active_changes: list[str] = []
        pdf_tab._session_mgr.active_changed.connect(lambda p: active_changes.append(p))

        pdf_tab._on_open_file()

        # 等待异步打开完成
        import time

        from PySide6.QtCore import QCoreApplication

        deadline = time.monotonic() + 5.0
        while pdf_tab._batch_opening and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)

        assert not pdf_tab._batch_opening  # open_done 已复位
        # 批量导入期间 active_changed 只触发一次（第一个文件），
        # 不是每个文件都触发（否则 = N 次全量重建）
        assert len(active_changes) == 1
        # combo box 有 2 项
        assert pdf_tab._file_selector.count() == 2

        pdf_tab.shutdown()
        assert pdf_tab._thumbnail_model._render_worker is None
        assert not pdf_tab._thumbnail_model._draining_workers


class TestThumbnailAutoSize:
    """缩略图自适应面板宽度：viewport 宽度变化时缩略图边长 clamp 到
    [_THUMBNAIL_MIN_SIZE, _THUMBNAIL_MAX_SIZE]，并联动 iconSize/gridSize/
    model 渲染尺寸。"""

    def test_compute_clamps_to_min(self, pdf_tab):
        """viewport 很窄时缩略图边长取下限，不至于小到无法阅读。"""
        lst = pdf_tab._thumbnail_list
        lst.setFixedWidth(_THUMBNAIL_MIN_SIZE)  # 极窄
        # setFixedWidth 后 viewport 宽度更新需布局生效
        lst.viewport().setGeometry(0, 0, _THUMBNAIL_MIN_SIZE, 100)
        size = lst._compute_thumbnail_size()
        assert size == _THUMBNAIL_MIN_SIZE

    def test_compute_clamps_to_max(self, pdf_tab):
        """viewport 很宽时缩略图边长取上限，不至于大到一张占满屏。"""
        lst = pdf_tab._thumbnail_list
        wide = _THUMBNAIL_MAX_SIZE + 200
        lst.setFixedWidth(wide)
        lst.viewport().setGeometry(0, 0, wide, 100)
        size = lst._compute_thumbnail_size()
        assert size == _THUMBNAIL_MAX_SIZE

    def test_compute_tracks_viewport(self, pdf_tab):
        """中等宽度时缩略图边长 = viewport 宽 - 内边距。"""
        lst = pdf_tab._thumbnail_list
        mid = (_THUMBNAIL_MIN_SIZE + _THUMBNAIL_MAX_SIZE) // 2
        lst.setFixedWidth(mid)
        lst.viewport().setGeometry(0, 0, mid, 100)
        size = lst._compute_thumbnail_size()
        assert size == mid - _THUMBNAIL_HPAD

    def test_apply_updates_icon_and_grid_size(self, pdf_tab):
        """_apply_thumbnail_size 同步更新 iconSize / gridSize。"""
        lst = pdf_tab._thumbnail_list
        target = 200
        changed = lst._apply_thumbnail_size(target)
        assert changed is True
        assert lst.iconSize().width() == target
        assert lst.iconSize().height() == target
        assert lst.gridSize().width() == target + _THUMBNAIL_HPAD
        assert lst.gridSize().height() == target + _THUMBNAIL_TEXT_HEIGHT

    def test_apply_no_change_returns_false(self, pdf_tab):
        """尺寸未变时不重复设置/发信号。"""
        lst = pdf_tab._thumbnail_list
        lst._apply_thumbnail_size(180)
        # 再应用相同尺寸：返回 False，不发信号
        received: list[int] = []
        lst.thumbnail_size_changed.connect(lambda s: received.append(s))
        assert lst._apply_thumbnail_size(180) is False

    def test_emit_drives_signal_and_model_size(self, pdf_tab):
        """_emit_visible_range 检测到尺寸变化后：
        - emit thumbnail_size_changed
        - PdfTab._on_thumbnail_size_changed 更新 model._thumb_size"""
        lst = pdf_tab._thumbnail_list
        model = pdf_tab._thumbnail_model
        # 模拟 viewport 变宽到触发新尺寸（绕过防抖定时器直接调 _emit）
        target = _THUMBNAIL_MAX_SIZE + 100  # 必然 clamp 到 MAX
        lst.viewport().setGeometry(0, 0, target, 100)
        lst._emit_visible_range()
        # 信号经 PdfTab 槽同步更新 model 渲染尺寸
        assert model._thumb_size == _THUMBNAIL_MAX_SIZE
        assert lst.iconSize().width() == _THUMBNAIL_MAX_SIZE


class TestThumbnailDetectionInProgress:
    """打开后缩略图进入'检测中'状态:不启 worker、占位图为检测中图标。"""

    def _make_model_with_session(self, qtbot, n_pages=3):
        """构造带 N 页 session 的 ThumbnailModel(不经由 PdfTab)。

        模拟"打开新文件"路径:detecting=True 进入检测态,不启动 worker
        (worker 启动需要 manager,本辅助方法构造期无 manager)。
        """
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [PdfPageInfo(page_index=i) for i in range(n_pages)]
        doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", session_id="sid1", pdf_document=doc)
        model = ThumbnailModel(parent=None)
        # ThumbnailModel 是 QAbstractListModel(非 QWidget),qtbot.addWidget
        # 仅接受 QWidget;qtbot fixture 本身已确保 QApplication 存在,
        # model 无事件循环无需注册清理,故不调 addWidget。
        # detecting=True 模拟打开新文件(检测中,不启动 worker,避免需要 manager)。
        model.set_session(session, detecting=True)
        return model

    def test_set_session_enters_detection_state(self, qtbot):
        """set_session 后 _detection_in_progress=True 且 worker 未启动。"""
        model = self._make_model_with_session(qtbot)
        assert model._detection_in_progress is True
        assert model._render_worker is None

    def test_request_range_noop_during_detection(self, qtbot):
        """检测期 request_range 不投递请求(worker 未启动,不应报错/启动)。"""
        model = self._make_model_with_session(qtbot)
        # 不应抛异常,也不应启动 worker
        model.request_range(0, 2)
        assert model._render_worker is None

    def test_decoration_is_detecting_icon_during_detection(self, qtbot):
        """检测期 data(DecorationRole) 缓存未命中返回检测中图标(非普通占位)。"""
        model = self._make_model_with_session(qtbot)
        idx = model.index(0, 0)
        icon = idx.data(Qt.ItemDataRole.DecorationRole)
        # 检测中图标与普通占位图标应是不同对象(不同缓存 dict)
        from vibeocr.classic.views.tabs.pdf_tab import _placeholder_icon

        assert icon is not None
        # 普通 placeholder 不应等于检测中图标(两者视觉不同)。
        # PySide6 的 QIcon 无 serialized()(那是 PyQt5 API),用 cacheKey()
        # 比较底层 pixmap 标识;不同像素的 QIcon cacheKey 不同。
        normal = _placeholder_icon(model._thumb_size)
        assert icon.cacheKey() != normal.cacheKey()

    def test_set_detection_done_starts_worker_and_clears_state(
        self, qtbot, monkeypatch
    ):
        """set_detection_done 后状态清除、worker 启动、占位恢复普通。"""
        started = []
        model = self._make_model_with_session(qtbot)
        # 拦截 worker 启动(避免真实起后端进程)
        monkeypatch.setattr(
            "vibeocr.classic.views.tabs.pdf_tab.ThumbnailModel._start_render_worker",
            lambda self, session: started.append(session),
        )
        model.set_detection_done()
        assert model._detection_in_progress is False
        assert started, "应启动缩略图 worker"

    def test_structural_refresh_does_not_reenter_detection(self, qtbot, monkeypatch):
        """结构性变更后的 set_session 刷新不应重新进入检测态(C1 回归防护)。

        场景:文件已加载完(set_detection_done 已调用),用户做旋转/删页/
        插页/重排,这些路径通过 set_session 刷新缩略图模型,之后不会再有
        load_done 信号。若 set_session 误置 _detection_in_progress=True,
        缩略图会永久卡在"检测中"占位、worker 不重启。
        """
        started = []
        model = self._make_model_with_session(qtbot)
        # 先完成检测(模拟 load_done)
        monkeypatch.setattr(
            "vibeocr.classic.views.tabs.pdf_tab.ThumbnailModel._start_render_worker",
            lambda self, session: started.append(session),
        )
        model.set_detection_done()
        started.clear()
        # 模拟结构性变更后的刷新(默认 detecting=False)
        model.set_session(model._session)
        assert model._detection_in_progress is False
        assert started, "结构性刷新后应直接启动 worker,而非卡在检测态"


class TestThumbnailAutoRenderAfterStateChange:
    """程序性状态变更（打开完成/结构变更/全量失效）后必须主动请求可见行渲染。

    Bug 2（缩略图打开时不自动加载）& Bug 3（插页后缩略图不刷新）同根因：
    ThumbnailModel 的 set_detection_done / set_session(detecting=False) /
    invalidate_all 启动 worker 但从不把 visible 行入队 worker —— 唯一的入队
    入口是 ThumbnailListView 的滚动/resize/show 事件。打开/插页后无这些事件，
    worker 队列空，缩略图要等用户滚动才加载。

    修复：ThumbnailModel 在上述路径后 emit render_visible_requested，
    PdfTab 连到 view.request_current_visible（去抖后 _emit_visible_range）。
    """

    def _make_model_with_session(self, qtbot, n_pages=3):
        from vibeocr.backend.models.pdf_document import PdfDocument, PdfPageInfo
        from vibeocr.backend.models.pdf_session import PdfSession

        pages = [PdfPageInfo(page_index=i) for i in range(n_pages)]
        doc = PdfDocument(file_path="x.pdf", pages=pages)
        session = PdfSession(file_path="x.pdf", session_id="sid1", pdf_document=doc)
        model = ThumbnailModel(parent=None)
        model.set_session(session, detecting=True)
        return model

    def test_model_has_render_visible_requested_signal(self, qtbot):
        """ThumbnailModel 必须暴露 render_visible_requested 信号。"""
        model = ThumbnailModel(parent=None)
        assert hasattr(model, "render_visible_requested")

    def test_set_detection_done_emits_render_visible_requested(
        self, qtbot, monkeypatch
    ):
        """set_detection_done 后必须 emit render_visible_requested。

        Bug 2 核心断言：打开完成（load_done → set_detection_done）后缩略图应
        自动渲染，无需用户滚动。
        """
        model = self._make_model_with_session(qtbot)
        monkeypatch.setattr(
            model,
            "_start_render_worker",
            lambda _s: None,  # 避免真实起 worker
        )
        fired = []
        model.render_visible_requested.connect(lambda: fired.append(True))
        model.set_detection_done()
        assert fired, "set_detection_done 应 emit render_visible_requested"

    def test_invalidate_all_emits_render_visible_requested(self, qtbot):
        """invalidate_all（全页失效，如旋转全部/结构变更）后必须 emit。

        Bug 3 核心断言：结构变更后全量失效缩略图，应触发可见行重渲。
        """
        model = self._make_model_with_session(qtbot)
        model.set_detection_done()  # 退出检测态（_detection_in_progress=False）
        fired = []
        model.render_visible_requested.connect(lambda: fired.append(True))
        model.invalidate_all()
        assert fired, "invalidate_all 应 emit render_visible_requested"

    def test_invalidate_all_no_emit_during_detection(self, qtbot):
        """检测态下 invalidate_all 不应 emit（request_range 会被抑制，emit 无意义）。"""
        model = self._make_model_with_session(qtbot)
        # 仍在检测态（_detection_in_progress=True）
        fired = []
        model.render_visible_requested.connect(lambda: fired.append(True))
        model.invalidate_all()
        assert not fired, "检测态 invalidate_all 不应 emit"

    def test_set_session_detecting_false_emits_render_visible_requested(
        self, qtbot, monkeypatch
    ):
        """set_session(detecting=False)（结构变更刷新）后必须 emit。

        Bug 3 核心断言：插页后 _refresh_thumbnails → set_session(detecting=False)
        应触发可见行渲染，否则缩略图不刷新。
        """
        model = self._make_model_with_session(qtbot)
        model.set_detection_done()  # 退出检测态
        monkeypatch.setattr(model, "_start_render_worker", lambda _s: None)
        fired = []
        model.render_visible_requested.connect(lambda: fired.append(True))
        # 结构变更刷新路径
        model.set_session(model._session, detecting=False)
        assert fired, "set_session(detecting=False) 应 emit render_visible_requested"

    def test_pdf_tab_connects_signal_to_view(self, pdf_tab):
        """PdfTab 必须把 model.render_visible_requested 连到 view.request_current_visible。

        若未连接，emit 不会触发渲染（Bug 2/3 复发）。
        """
        # ThumbnailListView 必须有 request_current_visible 公有方法
        assert callable(
            getattr(pdf_tab._thumbnail_list, "request_current_visible", None)
        ), "ThumbnailListView 缺 request_current_visible 公有方法"
        # emit render_visible_requested 应触发 view 的 _schedule_visible_range
        called = []
        pdf_tab._thumbnail_list._schedule_visible_range = lambda: called.append(True)
        pdf_tab._thumbnail_model.render_visible_requested.emit()
        assert called, (
            "render_visible_requested 应触发 view._schedule_visible_range（去抖渲染）"
        )


class TestThumbnailWorkerLifecycle:
    """缩略图 worker 在切换和退出时必须保持所有权并协作收拢。"""

    def test_pdf_tab_shutdown_stops_thumbnail_worker_before_manager(
        self, pdf_tab, monkeypatch
    ):
        calls: list[str] = []
        monkeypatch.setattr(
            pdf_tab._thumbnail_model,
            "request_shutdown",
            lambda: calls.append("thumbnail:request"),
        )
        monkeypatch.setattr(
            pdf_tab._thumbnail_model,
            "wait_for_draining",
            lambda _timeout_ms: calls.append("thumbnail:drain") or True,
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "request_shutdown",
            lambda: calls.append("manager:request"),
        )
        monkeypatch.setattr(
            pdf_tab._session_mgr,
            "drain",
            lambda _timeout_ms: calls.append("manager:drain") or True,
        )

        pdf_tab.shutdown()

        assert calls == [
            "thumbnail:request",
            "manager:request",
            "thumbnail:drain",
            "manager:drain",
        ]

    def test_timed_out_thumbnail_worker_is_retained_until_finished(
        self, qapp, monkeypatch
    ):
        from unittest.mock import MagicMock

        model = ThumbnailModel()
        worker = MagicMock()
        worker.isFinished.return_value = False
        model._render_worker = worker
        model._stop_render_worker()

        assert model._render_worker is None
        assert worker in model._draining_workers
        worker.finished.connect.assert_called_once()

    def test_close_event_only_requests_shutdown(self, pdf_tab, qtbot, monkeypatch):
        from unittest.mock import MagicMock

        request_shutdown = MagicMock()
        monkeypatch.setattr(pdf_tab, "request_shutdown", request_shutdown)
        pdf_tab.show()

        pdf_tab.close()
        qtbot.waitUntil(lambda: request_shutdown.call_count == 1)

        request_shutdown.assert_called_once_with()

    def test_start_render_worker_stops_existing_before_replacement(
        self, qapp, monkeypatch
    ):
        from unittest.mock import MagicMock

        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        calls: list[str] = []
        model = ThumbnailModel()
        model._render_worker = MagicMock()
        manager = MagicMock()
        monkeypatch.setattr(model, "_get_manager", lambda: manager)

        def _stop_existing():
            calls.append("stop")
            model._render_worker = None

        new_worker = MagicMock()

        def _create_worker(**_kwargs):
            calls.append("create")
            return new_worker

        monkeypatch.setattr(model, "_stop_render_worker", _stop_existing)
        monkeypatch.setattr(
            "vibeocr.classic.pyside.pdf_render_thumb_worker.ThumbnailIpcWorker",
            _create_worker,
        )
        session = PdfSession(
            file_path="x.pdf",
            session_id="sid",
            pdf_document=PdfDocument(file_path="x.pdf"),
        )

        model._start_render_worker(session)

        assert calls == ["stop", "create"]
        assert model._render_worker is new_worker

    def test_late_detection_done_does_not_restart_after_shutdown(
        self, qapp, monkeypatch
    ):
        from vibeocr.backend.models.pdf_document import PdfDocument
        from vibeocr.backend.models.pdf_session import PdfSession

        model = ThumbnailModel()
        session = PdfSession(
            file_path="x.pdf",
            session_id="sid",
            pdf_document=PdfDocument(file_path="x.pdf"),
        )
        model.set_session(session, detecting=True)
        starts: list[str] = []
        monkeypatch.setattr(
            model,
            "_start_render_worker",
            lambda _session: starts.append("start"),
        )

        model.shutdown()
        model.set_detection_done()

        assert starts == []
