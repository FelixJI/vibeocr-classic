"""SingleRecognitionTab 测试"""

import time

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from vibeocr.classic.views.tabs.base_tab import BaseOcrTab
from vibeocr.classic.views.tabs.single_recognition_tab import SingleRecognitionTab


class _FakeBackend:
    def start(self, **kwargs) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def recognize_sync(self, *args, **kwargs):
        from vibeocr.backend.models.ocr_result import OCRResult

        return OCRResult(raw_text="fake")


@pytest.fixture(autouse=True)
def _never_start_real_worker():
    """Tests inject their sync seam explicitly; no legacy worker exists."""
    return _FakeBackend()


class TestSingleRecognitionTab:
    def test_creation(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, QWidget)

    def test_is_base_ocr_tab(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, BaseOcrTab)

    def test_has_preview_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preview_widget is not None

    def test_has_result_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._result_widget is not None

    def test_has_preprocess_options(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preprocess_options is not None

    def test_has_action_buttons(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._screenshot_btn is not None
        assert tab._file_btn is not None
        assert tab._paste_btn is not None

    def test_has_copy_image_button(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn is not None
        assert tab._copy_image_btn.text() == "复制图片"

    def test_copy_image_btn_disabled_by_default(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn.isEnabled() is False

    def test_copy_image_btn_enabled_after_set_pixmap(self, qapp, sample_pixmap):
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_set_image_for_recognition(
        self, qapp, sample_pixmap
    ):
        # 真实流程（main_window）：set_image_for_recognition 与 set_pixmap 配合调用；
        # 仅前者不加载预览，故复制按钮以预览 original_pixmap 为准。
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab.set_image_for_recognition(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_paste(self, qapp, sample_pixmap, monkeypatch):
        """模拟粘贴：让剪贴板返回 sample_pixmap（_on_paste 用 QGuiApplication）。"""

        class FakeClipboard:
            def pixmap(self):
                return sample_pixmap

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab._on_paste()
        assert tab._copy_image_btn.isEnabled() is True

    def test_screenshot_btn_emits_signal(self, qapp):
        tab = SingleRecognitionTab()
        emitted = []
        tab.screenshot_requested.connect(lambda: emitted.append(True))
        tab._screenshot_btn.click()
        assert emitted


class TestRecognitionStatus:
    """状态栏摘要必须使用同一组文本框统计，且不把空结果说成成功。"""

    def test_status_counts_boxes_low_confidence_and_elapsed(self):
        from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

        result = OCRResult(
            raw_text="可信\n待复核",
            text_blocks=[
                TextBlock("可信", 0.96, (0, 0, 10, 10)),
                TextBlock("待复核", 0.79, (10, 0, 20, 10)),
                TextBlock("   ", 0.20, (20, 0, 30, 10)),
            ],
        )

        status = SingleRecognitionTab._build_recognition_status(result, 1.234)

        assert status == (
            "识别到 2 个文本框 · 低置信（<80%）1 个 · 耗时 1.23 秒"
        )

    def test_status_reports_empty_result_honestly(self):
        from vibeocr.backend.models.ocr_result import OCRResult

        status = SingleRecognitionTab._build_recognition_status(
            OCRResult(), 0.248
        )

        assert status == "未识别到文本 · 耗时 248 毫秒"

    def test_status_does_not_claim_zero_boxes_when_statistics_are_missing(self):
        from vibeocr.backend.models.ocr_result import OCRResult

        status = SingleRecognitionTab._build_recognition_status(
            OCRResult(raw_text="后端只返回了文本"), 12.34
        )

        assert status == "识别完成（未返回文本框统计） · 耗时 12.3 秒"

    def test_failed_run_replaces_previous_success_status(self, qapp, monkeypatch):
        tab = SingleRecognitionTab()
        emitted: list[str] = []
        tab.status_changed.connect(emitted.append)
        tab._recognition_started_at = 10.0
        monkeypatch.setattr(time, "monotonic", lambda: 10.5)

        tab._on_ocr_error("后端异常")

        assert emitted[-1] == "识别失败 · 耗时 500 毫秒"


class TestSingleRecognitionTabCopyImage:
    """「复制图片」复制逻辑测试"""

    def test_on_copy_image_copies_original(self, qapp, sample_pixmap, monkeypatch):
        """点复制图片后，剪贴板收到的是原始图片（original_pixmap）。"""
        captured = {}

        class FakeClipboard:
            def setPixmap(self, pm):
                captured["pixmap"] = pm

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        assert "pixmap" in captured
        # 复制的是原图（cacheKey 与 original_pixmap 一致）
        assert (
            captured["pixmap"].cacheKey()
            == tab._preview_widget.original_pixmap().cacheKey()
        )

    def test_on_copy_image_shows_toast(self, qapp, sample_pixmap, monkeypatch):
        class FakeClipboard:
            def setPixmap(self, pm):
                pass

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        # 注意：未 show() 顶层窗口时 isVisible() 恒为 False，改用 isHidden() 判定
        assert tab._copy_toast.isHidden() is False
        assert "原图已复制" in tab._copy_toast.text()

    def test_on_copy_image_noop_without_pixmap(self, qapp, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()  # 无图
        tab._on_copy_image()
        assert called["yes"] is False
        # 无图时应给出明确反馈，而非静默无反应
        assert tab._copy_toast.isHidden() is False
        assert "暂无图片可复制" in tab._copy_toast.text()

    def test_copy_image_btn_triggers_on_copy(self, qapp, sample_pixmap, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._copy_image_btn.click()
        assert called["yes"] is True


class TestPasteAndStartFeedback:
    """「粘贴」「开始识别」失败路径应给出反馈，而非静默无反应。"""

    def test_paste_empty_clipboard_shows_toast(self, qapp, monkeypatch):
        """剪贴板无图片时点粘贴，应提示「剪贴板中没有图片」。"""

        class EmptyClipboard:
            def pixmap(self):
                from PySide6.QtGui import QPixmap

                return QPixmap()  # null pixmap

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: EmptyClipboard())
        tab = SingleRecognitionTab()
        tab._on_paste()
        assert tab._copy_toast.isHidden() is False
        assert "剪贴板中没有图片" in tab._copy_toast.text()

    def test_process_file_missing_shows_warning(self, qapp, monkeypatch, tmp_path):
        """待识别文件已被删除时，process_file 应弹警告而非静默。"""
        warnings: list[tuple] = []

        class FakeQMessageBox:
            @staticmethod
            def warning(parent, title, text):
                warnings.append((title, text))

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox", FakeQMessageBox
        )
        tab = SingleRecognitionTab()
        tab._pending_file_path = str(tmp_path / "deleted.png")  # 文件不存在
        tab.process_file(str(tmp_path / "deleted.png"))

        assert warnings, "文件不存在应弹警告"
        assert "无法识别" in warnings[0][0]
        assert "deleted.png" in warnings[0][1]


def _make_table_result():
    """构造一个表格管道风格的 OCRResult（content_list + text_blocks + table_body）。"""
    from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

    table_html = "<table><tr><td>A</td><td>B</td></tr></table>"
    return OCRResult(
        content_list=[{"type": "table", "table_body": table_html, "text": "A B"}],
        text_blocks=[
            TextBlock(
                text=table_html,
                score=0.9,
                bbox=(10, 10, 200, 80),
                label="table",
                content_index=0,
            )
        ],
        text_with_scores=[(table_html, 0.9)],
        raw_text=table_html,
        markdown_text="| A | B |\n|---|---|",
    )


def _make_formula_result():
    """构造一个公式管道风格的 OCRResult（content_list type=formula）。"""
    from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

    latex = "E=mc^2"
    return OCRResult(
        content_list=[{"type": "formula", "text": latex}],
        text_blocks=[
            TextBlock(
                text=latex, score=1.0, bbox=(10, 10, 200, 80),
                label="formula", content_index=0,
            )
        ],
        text_with_scores=[(latex, 1.0)],
        raw_text=latex,
        markdown_text=f"$${latex}$$",
    )


class TestResultBlockEditedTableDelegation:
    """右侧双击编辑表格块 → _on_result_block_edited 应委托 _on_table_block_edited，
    复用左侧网格编辑的正确同步逻辑（更新 table_body、保持块类型模式）。
    """

    def test_table_edit_delegates_to_table_handler(self, qapp):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()

        delegated: list = []
        tab._on_table_block_edited = lambda ci, html: delegated.append((ci, html))

        new_html = "<table><tr><td>X</td><td>Y</td></tr></table>"
        tab._on_result_block_edited(0, new_html)

        assert delegated == [(0, new_html)], "table 块应委托 _on_table_block_edited"
        # 委托后不应继续走文本分支：content_list 的 text 不应被改成 HTML
        assert tab._current_ocr_result.content_list[0]["text"] == "A B"

    def test_table_edit_updates_table_body_and_keeps_block_type_mode(self, qapp, monkeypatch):
        """端到端：右侧表格编辑后，content_list.table_body 被更新、
        左侧用 set_content_list（块类型模式）刷新而非 set_text_blocks。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()

        refreshed: list = []
        monkeypatch.setattr(
            tab._preview_widget, "set_content_list",
            lambda cl: refreshed.append(("content_list", cl)),
        )
        monkeypatch.setattr(
            tab._preview_widget, "set_text_blocks",
            lambda tb: refreshed.append(("text_blocks", tb)),
        )
        # update_block_text 触发 WebEngine JS，测试环境 stub 掉
        monkeypatch.setattr(tab._result_widget, "update_block_text", lambda *a, **k: None)

        new_html = "<table><tr><td>X</td></tr></table>"
        tab._on_result_block_edited(0, new_html)

        # canonical table 是权威数据源，table_body 是带稳定 ID 的兼容投影。
        block = tab._current_ocr_result.content_list[0]
        assert block["table"]["cells"][0]["text"] == "X"
        assert "data-table-id" in block["table_body"]
        # TextBlock/raw 用纯文本，不能混入整段 HTML。
        assert tab._current_ocr_result.text_blocks[0].text == "X"
        assert tab._current_ocr_result.text_blocks[0].is_manually_edited is True
        # 左侧应走块类型模式刷新（set_content_list），而非切到置信度模式
        assert refreshed and refreshed[0][0] == "content_list"


def _make_plain_text_result():
    """构造一个通用 OCR（纯文本）风格的 OCRResult。

    content_list 为空（仅 text_blocks）—— 通用 OCR 管道在 _display_result 之前
    的真实形态。段落处理选项只对这类结果有意义（结构化结果走块类型渲染）。
    """
    from vibeocr.backend.models.ocr_result import OCRResult, TextBlock

    return OCRResult(
        text_blocks=[
            TextBlock(text="第一行", score=0.95, bbox=(10, 10, 200, 40), label="text"),
            TextBlock(text="第二行", score=0.93, bbox=(10, 50, 200, 80), label="text"),
        ],
        text_with_scores=[("第一行", 0.95), ("第二行", 0.93)],
        raw_text="第一行\n第二行",
        markdown_text="第一行\n第二行",
        image_height=100,
    )


class TestTextOptionsLiveUpdate:
    """识别完成后切换「文本块处理」选项，应实时重算 raw_text 并刷新结果区。

    根因：TextBlockOptionsWidget.options_changed 信号此前从未被
    SingleRecognitionTab 连接，切换选项只在重新识别后才生效。
    """

    def test_line_mode_change_recomputes_raw_text(self, qapp, monkeypatch):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_plain_text_result()
        # 记录识别完成时的原始标志（_display_result 会回填 content_list 使
        # has_content_list 变 True，但段落处理应只作用于纯文本结果）。
        tab._plain_text_at_recognition = True

        # 切换为「合并成一段」：raw_text 应变为两块直接拼接（无换行）
        tab._text_options_widget._mode_combo.setCurrentIndex(1)  # 合并成一段

        assert tab._current_ocr_result.raw_text == "第一行第二行"

    def test_line_mode_change_refreshes_result_view(self, qapp, monkeypatch):
        """切换选项后结果区应重新渲染。

        纯文本结果走 display_text_layout（按选项整体排版），不再走逐块的
        display_result——后者无法体现换行模式/空格/缩进的变化。
        """
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_plain_text_result()
        tab._plain_text_at_recognition = True

        refreshed = {"count": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_text_layout",
            lambda *a, **k: refreshed.__setitem__("count", refreshed["count"] + 1),
        )

        tab._text_options_widget._mode_combo.setCurrentIndex(1)

        assert refreshed["count"] >= 1, "切换段落选项应刷新结果区"

    def test_structured_result_not_affected(self, qapp, monkeypatch):
        """结构化结果（表格/公式）走块类型渲染，不读 raw_text，
        切换段落选项不应改其 raw_text（避免误伤复制/导出链路）。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_table_result()
        tab._plain_text_at_recognition = False  # 结构化结果

        original_raw = tab._current_ocr_result.raw_text
        tab._text_options_widget._mode_combo.setCurrentIndex(1)

        assert tab._current_ocr_result.raw_text == original_raw

    def test_no_result_does_not_crash(self, qapp):
        """尚未识别时切换选项不应抛异常。"""
        tab = SingleRecognitionTab()
        tab._current_ocr_result = None
        # 不应抛异常
        tab._text_options_widget._mode_combo.setCurrentIndex(1)


class TestOcrFinishedEmitsBringToFront:
    """OCR 完成后应发出信号通知外层（MainWindow）把主窗口提到前台。

    根因：截图确认 → run_ocr（异步，数秒后完成）→ _on_ocr_finished。
    此前主窗口的激活/置顶只发生在「截图确认的瞬间」（OCR 开始前），OCR 期间
    用户/系统切走窗口后，识别完成时窗口就静悄悄留在后台。修复：识别完成时
    发出信号，由 MainWindow 决定是否重新前置。

    仅截图来源的识别需要抢焦点（用户离开过应用）；文件打开来源不需要
    （用户本就在应用内）。故信号在 run_ocr 时由调用方标记是否为截图来源，
    _on_ocr_finished 据此决定是否发信号。
    """

    def test_tab_has_bring_to_front_requested_signal(self, qapp):
        """SingleRecognitionTab 应定义 bring_to_front_requested 信号（可 connect）。"""
        tab = SingleRecognitionTab()
        assert hasattr(tab, "bring_to_front_requested"), (
            "SingleRecognitionTab 应定义 bring_to_front_requested 信号"
        )
        # 信号真正可 connect + emit（验证它是 Qt Signal 而非普通属性）
        received: list = []
        tab.bring_to_front_requested.connect(lambda: received.append(True))
        tab.bring_to_front_requested.emit()
        assert received == [True]

    def test_on_ocr_finished_emits_signal_when_from_screenshot(self, qapp, monkeypatch):
        """截图来源的识别完成时应发出 bring_to_front_requested。"""
        tab = SingleRecognitionTab()
        emitted: list = []
        tab.bring_to_front_requested.connect(lambda: emitted.append(True))

        # 标记本次识别来自截图
        tab._ocr_from_screenshot = True
        # _display_result 会触发 WebEngine，stub 掉
        monkeypatch.setattr(tab, "_display_result", lambda r: None)

        tab._on_ocr_finished(_make_plain_text_result())

        assert emitted == [True], "截图来源识别完成应发出 bring_to_front_requested"

    def test_on_ocr_finished_no_signal_when_from_file(self, qapp, monkeypatch):
        """文件打开来源的识别完成时不应发信号（用户本就在应用内）。"""
        tab = SingleRecognitionTab()
        emitted: list = []
        tab.bring_to_front_requested.connect(lambda: emitted.append(True))

        tab._ocr_from_screenshot = False
        monkeypatch.setattr(tab, "_display_result", lambda r: None)

        tab._on_ocr_finished(_make_plain_text_result())

        assert emitted == [], "文件来源识别完成不应发 bring_to_front_requested"

    def test_on_ocr_finished_no_signal_default(self, qapp, monkeypatch):
        """未显式标记来源时（默认 False），识别完成不发信号（保守，不抢焦点）。"""
        tab = SingleRecognitionTab()
        emitted: list = []
        tab.bring_to_front_requested.connect(lambda: emitted.append(True))

        monkeypatch.setattr(tab, "_display_result", lambda r: None)

        tab._on_ocr_finished(_make_plain_text_result())

        assert emitted == []

    def test_on_ocr_error_does_not_emit_signal(self, qapp, monkeypatch):
        """识别失败不应发 bring_to_front（失败路径保留状态由调用方处理）。

        但失败仍需复位 _ocr_from_screenshot 标记，避免下次文件来源识别误判。
        """
        tab = SingleRecognitionTab()
        emitted: list = []
        tab.bring_to_front_requested.connect(lambda: emitted.append(True))

        tab._ocr_from_screenshot = True
        monkeypatch.setattr(tab._result_widget, "_ensure_web_view", lambda: _FakeWebView())

        tab._on_ocr_error("boom")

        assert emitted == []
        # 失败后标记应复位
        assert tab._ocr_from_screenshot is False

    def test_run_ocr_sets_screenshot_flag(self, qapp, qtbot, qasync_loop, monkeypatch):
        """run_ocr 应根据参数设置 _ocr_from_screenshot 标记。

        from_screenshot=True（截图确认路径）→ True；
        不传或 False（文件/粘贴路径）→ False。

        异步化后 run_ocr 立即返回，后端调用在 qasync loop 上跑。这里在
        fake_recognize 内捕获标记值，再用 wait_until_done 推进协程完成。两次
        调用必须串行（忙时守卫会吞掉并发请求），故第一次完成后再启动第二次。
        """
        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        observed: list[bool] = []

        def fake_recognize(*args, **kwargs):
            observed.append(tab._ocr_from_screenshot)
            return _make_plain_text_result()

        monkeypatch.setattr(tab, "_call_backend_recognize", fake_recognize)
        monkeypatch.setattr(tab, "_display_result", lambda result: None)
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *args: True
        )
        options = OCROptions()

        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, options, from_screenshot=True)
        # 等第一次识别完成（_on_ocr_async_finished 会清 _recognize_task）
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)
        assert observed == [True]

        tab.run_ocr(pixmap, options)
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)
        assert observed == [True, False]
        assert tab._ocr_from_screenshot is False


class TestRunOcrAsync:
    """异步化 run_ocr 的行为测试。

    异步化根因：截图确认 → run_ocr 此前在 GUI 线程同步阻塞于
    SyncBackendClient.recognize_sync → fut.result(timeout=300)，模型未加载完成
    时窗口完全无响应。改造后 run_ocr 把后端调用派发到 qasync loop，GUI 保持响应。
    """

    def test_run_ocr_completes_and_renders(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        """run_ocr 异步完成后应调 _on_ocr_finished 并清忙时状态。"""
        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        finished_calls: list = []

        monkeypatch.setattr(
            tab, "_call_backend_recognize", lambda *a, **k: _make_plain_text_result()
        )
        monkeypatch.setattr(tab, "_display_result", lambda r: None)
        monkeypatch.setattr(
            tab, "_on_ocr_finished", lambda r: finished_calls.append(r)
        )
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *a: True
        )

        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())

        # 异步：立即返回时结果尚未就绪
        assert finished_calls == []
        assert tab.is_processing is True
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)

        assert len(finished_calls) == 1
        assert tab.is_processing is False

    def test_run_ocr_does_not_block_qt_event_loop(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        """核心回归测试：OCR in-flight 期间 Qt 事件循环必须保持响应。

        这是本次异步化的全部意义。用阻塞的 fake_recognize 模拟长耗时后端调用，
        在 OCR 期间触发一个 QTimer.singleShot(0, ...)；若事件循环被阻塞
        （改造前的同步路径），该 timer 在 barrier 释放前不可能触发。
        异步化后，to_thread 把阻塞调用挪到线程池，主线程的 Qt 事件循环照常
        推进，timer 在识别仍 in-flight 时即触发。
        """
        import threading

        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        barrier = threading.Event()

        def blocking_recognize(*args, **kwargs):
            # 模拟长耗时后端调用（在 to_thread 的线程池里跑，不阻塞 GUI）
            barrier.wait(timeout=2.0)
            return _make_plain_text_result()

        monkeypatch.setattr(tab, "_call_backend_recognize", blocking_recognize)
        monkeypatch.setattr(tab, "_display_result", lambda r: None)
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *a: True
        )

        timer_fired: list[bool] = []
        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())
        assert tab.is_processing is True, "识别应仍 in-flight"

        # OCR in-flight 期间排一个 timer。wait_until_done 推进 qasync loop +
        # Qt 事件：timer 应在被释放的 barrier 之前触发（证明主线程未被阻塞）。
        QTimer.singleShot(0, lambda: timer_fired.append(True))
        wait_until_done(qtbot, qasync_loop, lambda: timer_fired == [True])

        # 此时识别仍 in-flight（barrier 未释放），但 timer 已触发 → 非阻塞证据
        assert tab.is_processing is True
        assert timer_fired == [True]

        # 释放阻塞的后端调用，让识别完成、task 清理
        barrier.set()
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)

    def test_run_ocr_busy_guard_ignores_second_call(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        """OCR 进行中再次调用 run_ocr 应被忽略（不产生第二个 task）。"""
        import threading

        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        barrier = threading.Event()
        recognize_calls: list = []

        def blocking_recognize(*args, **kwargs):
            recognize_calls.append(True)
            barrier.wait(timeout=2.0)
            return _make_plain_text_result()

        monkeypatch.setattr(tab, "_call_backend_recognize", blocking_recognize)
        monkeypatch.setattr(tab, "_display_result", lambda r: None)
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *a: True
        )

        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())
        first_task = tab._recognize_task
        assert first_task is not None

        # 忙时第二次调用应被吞
        tab.run_ocr(pixmap, OCROptions())
        assert tab._recognize_task is first_task

        # 释放并等待完成
        barrier.set()
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)
        assert len(recognize_calls) == 1, "第二次 run_ocr 不应触发后端调用"

    def test_run_ocr_error_path_calls_on_ocr_error(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        """后端调用抛异常时应走 _on_ocr_error，并复位忙时状态。"""
        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        error_calls: list[str] = []

        def raising_recognize(*args, **kwargs):
            raise RuntimeError("backend boom")

        monkeypatch.setattr(tab, "_call_backend_recognize", raising_recognize)
        monkeypatch.setattr(
            tab, "_on_ocr_error", lambda msg: error_calls.append(msg)
        )
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *a: True
        )

        tab._ocr_from_screenshot = True  # 验证错误路径不复位由 _on_ocr_error 负责
        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())

        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)
        assert len(error_calls) == 1
        assert "backend boom" in error_calls[0]
        assert tab.is_processing is False

    def test_set_closing_cancels_inflight_task(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        """set_closing(True) 应取消进行中的识别 task，且 _on_ocr_finished 不被调。"""
        import threading

        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        barrier = threading.Event()
        finished_calls: list = []

        def blocking_recognize(*args, **kwargs):
            barrier.wait(timeout=2.0)
            return _make_plain_text_result()

        monkeypatch.setattr(tab, "_call_backend_recognize", blocking_recognize)
        monkeypatch.setattr(tab, "_display_result", lambda r: None)
        monkeypatch.setattr(
            tab, "_on_ocr_finished", lambda r: finished_calls.append(r)
        )
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *a: True
        )

        pixmap = QPixmap(4, 4)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())
        task = tab._recognize_task
        assert task is not None
        # Ensure asyncio.to_thread has entered the native call before testing the
        # split task/native-completion shutdown contract.  Cancelling earlier is
        # a different (and already drained) state.
        wait_until_done(qtbot, qasync_loop, tab._has_native_calls)

        # 关闭态应立即取消 task（不等 barrier）
        tab.set_closing(True)
        wait_until_done(qtbot, qasync_loop, lambda: task.cancelled() or task.done())

        # asyncio Task 已取消不代表 to_thread 已退出；关闭轮询必须继续持有 owner。
        assert tab.is_processing is True
        assert tab.drain(0) is False
        assert tab.is_drained() is False

        # 释放 barrier 让 to_thread 线程不卡死（task 已 cancel，结果被忽略）
        barrier.set()
        wait_until_done(qtbot, qasync_loop, tab.is_drained)

        # _on_ocr_finished 被 closing 守卫短路
        assert finished_calls == []
        assert tab._closing is True
        assert tab.is_processing is False


class _FakeWebView:
    """最小 web view 替身，仅实现 setHtml。"""

    def setHtml(self, html):
        self.last_html = html


class TestRecognitionPreparationAsync:
    def test_png_encoding_runs_off_gui_thread(
        self, qapp, qtbot, qasync_loop, monkeypatch
    ):
        import threading

        from PySide6.QtGui import QPixmap

        from tests.conftest import wait_until_done
        from vibeocr.classic.recognition_settings import OCROptions

        tab = SingleRecognitionTab()
        gui_thread = threading.get_ident()
        encode_threads: list[int] = []
        original = tab._qimage_to_png_bytes

        def record_thread(image):
            encode_threads.append(threading.get_ident())
            return original(image)

        monkeypatch.setattr(tab, "_qimage_to_png_bytes", record_thread)
        monkeypatch.setattr(tab, "_display_result", lambda result: None)
        monkeypatch.setattr(
            "vibeocr.backend.pipeline_status.is_pipeline_ever_succeeded", lambda *args: True
        )
        pixmap = QPixmap(8, 8)
        pixmap.fill()
        tab.run_ocr(pixmap, OCROptions())
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)

        assert encode_threads and encode_threads[0] != gui_thread

    def test_document_read_runs_off_gui_thread(
        self, qapp, qtbot, qasync_loop, monkeypatch, tmp_path
    ):
        import threading
        from pathlib import Path

        from tests.conftest import wait_until_done

        tab = SingleRecognitionTab()
        path = tmp_path / "large.pdf"
        path.write_bytes(b"payload")
        gui_thread = threading.get_ident()
        read_threads: list[int] = []
        original = Path.read_bytes

        def record_read(file_path):
            read_threads.append(threading.get_ident())
            return original(file_path)

        monkeypatch.setattr(Path, "read_bytes", record_read)
        monkeypatch.setattr(tab, "_display_result", lambda result: None)
        tab._run_ocr_with_file(path)
        wait_until_done(qtbot, qasync_loop, lambda: tab._recognize_task is None)

        assert read_threads and read_threads[0] != gui_thread

    def test_business_error_does_not_restart_backend(self, qapp):
        class BusinessErrorBackend:
            def __init__(self):
                self.shutdown_calls = 0

            def recognize_sync(self, *args, **kwargs):
                raise ValueError("invalid request")

            def shutdown(self):
                self.shutdown_calls += 1

        backend = BusinessErrorBackend()
        tab = SingleRecognitionTab(backend=backend)

        with pytest.raises(ValueError, match="invalid request"):
            tab._call_backend_recognize(b"payload", "OCR")
        assert backend.shutdown_calls == 0


def _raise_async(msg):
    async def _coro(*a, **k):
        raise AssertionError(msg)

    return _coro


class TestResultBlockEditedFormulaSync:
    """右侧编辑公式块 → _on_result_block_edited 走文本分支，但 has_content_list
    时左侧应走 set_content_list（保持块类型模式），且 tb.is_manually_edited=True。
    """

    def test_formula_edit_uses_set_content_list(self, qapp, monkeypatch):
        tab = SingleRecognitionTab()
        tab._current_ocr_result = _make_formula_result()

        refreshed: list = []
        monkeypatch.setattr(
            tab._preview_widget, "set_content_list",
            lambda cl: refreshed.append(("content_list", cl)),
        )
        monkeypatch.setattr(
            tab._preview_widget, "set_text_blocks",
            lambda tb: refreshed.append(("text_blocks", tb)),
        )

        tab._on_result_block_edited(0, "F=ma")

        assert tab._current_ocr_result.content_list[0]["text"] == "F=ma"
        assert tab._current_ocr_result.text_blocks[0].text == "F=ma"
        assert tab._current_ocr_result.text_blocks[0].is_manually_edited is True
        # has_content_list → 块类型模式刷新
        assert refreshed and refreshed[0][0] == "content_list"


class TestPlainTextRendersOnce:
    """纯文本 OCR 结果在 _on_ocr_finished 后只渲染一次（display_text_layout），
    不再先 display_result 再 display_text_layout 双重渲染。

    根因：双重渲染时 display_result 先 bump 文档 token 为 A，display_text_layout
    又 bump 为 B 并清空 _rendered_document_token；两次渲染之间用户点「复制文本」，
    异步 JS 回调返回旧 token A，命中 _on_web_copy_payload 的「结果已刷新」toast。
    修法：纯文本分支只回填 content_list（_apply_content_index），随后用
    display_text_layout 渲染一次。
    """

    def test_plain_text_does_not_call_display_result(self, qapp, monkeypatch):
        """纯文本识别完成时，result_view_widget.display_result 不应被调用。"""
        tab = SingleRecognitionTab()
        counts = {"display_result": 0, "display_text_layout": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_result",
            lambda *a, **k: counts.__setitem__("display_result", counts["display_result"] + 1),
        )
        monkeypatch.setattr(
            tab._result_widget, "display_text_layout",
            lambda *a, **k: counts.__setitem__("display_text_layout", counts["display_text_layout"] + 1),
        )

        tab._on_ocr_finished(_make_plain_text_result())

        assert counts["display_result"] == 0, (
            "纯文本结果不应触发 display_result（会 bump 文档 token 引发复制竞态）"
        )

    def test_plain_text_calls_display_text_layout_exactly_once(self, qapp, monkeypatch):
        """纯文本识别完成时，display_text_layout 恰好被调用一次。"""
        tab = SingleRecognitionTab()
        counts = {"display_result": 0, "display_text_layout": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_result",
            lambda *a, **k: counts.__setitem__("display_result", counts["display_result"] + 1),
        )
        monkeypatch.setattr(
            tab._result_widget, "display_text_layout",
            lambda *a, **k: counts.__setitem__("display_text_layout", counts["display_text_layout"] + 1),
        )

        tab._on_ocr_finished(_make_plain_text_result())

        assert counts["display_text_layout"] == 1, (
            "纯文本结果应只渲染一次（display_text_layout）"
        )

    def test_plain_text_backfills_content_list_for_preview(self, qapp, monkeypatch):
        """纯文本路径仍需为左侧预览回填 content_list（块级编辑入口）。

        回填由 _apply_content_index 完成（不渲染 WebEngine）。
        """
        tab = SingleRecognitionTab()
        monkeypatch.setattr(tab._result_widget, "display_text_layout", lambda *a, **k: None)
        monkeypatch.setattr(tab._result_widget, "display_result", lambda *a, **k: None)

        result = _make_plain_text_result()
        tab._on_ocr_finished(result)

        assert result.content_list, "纯文本路径应回填 content_list 使预览可块级编辑"
        assert len(result.content_list) == len(result.text_blocks)
        # text_blocks 补建 content_index（编辑回调按此反查）
        assert result.text_blocks[0].content_index == 0
        assert result.text_blocks[1].content_index == 1

    def test_plain_text_no_pending_text_layout_leftover(self, qapp, monkeypatch):
        """纯文本分支同步渲染后，_pending_text_layout 不应残留（避免
        _on_content_list_ready 二次触发 display_text_layout）。"""
        tab = SingleRecognitionTab()
        monkeypatch.setattr(tab._result_widget, "display_text_layout", lambda *a, **k: None)
        monkeypatch.setattr(tab._result_widget, "display_result", lambda *a, **k: None)

        tab._on_ocr_finished(_make_plain_text_result())

        assert tab._pending_text_layout is None

    def test_structured_result_still_uses_display_result(self, qapp, monkeypatch):
        """结构化结果（表格）仍走 display_result（恰好一次），不调 display_text_layout。"""
        tab = SingleRecognitionTab()
        counts = {"display_result": 0, "display_text_layout": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_result",
            lambda *a, **k: counts.__setitem__("display_result", counts["display_result"] + 1),
        )
        monkeypatch.setattr(
            tab._result_widget, "display_text_layout",
            lambda *a, **k: counts.__setitem__("display_text_layout", counts["display_text_layout"] + 1),
        )

        tab._on_ocr_finished(_make_table_result())

        assert counts["display_result"] == 1, "结构化结果应走 display_result 一次"
        assert counts["display_text_layout"] == 0, "结构化结果不应触发 display_text_layout"

    def test_structured_formula_result_still_uses_display_result(self, qapp, monkeypatch):
        """结构化结果（公式）仍走 display_result（恰好一次）。"""
        tab = SingleRecognitionTab()
        counts = {"display_result": 0, "display_text_layout": 0}
        monkeypatch.setattr(
            tab._result_widget, "display_result",
            lambda *a, **k: counts.__setitem__("display_result", counts["display_result"] + 1),
        )
        monkeypatch.setattr(
            tab._result_widget, "display_text_layout",
            lambda *a, **k: counts.__setitem__("display_text_layout", counts["display_text_layout"] + 1),
        )

        tab._on_ocr_finished(_make_formula_result())

        assert counts["display_result"] == 1
        assert counts["display_text_layout"] == 0
