"""批量识别标签页：文件选择结果展示测试

回归 bug：update_file_status(path, "failed", {"error": ...}) 把错误 dict 存进
file_info["result"]。用户点失败文件 → _on_file_selected 的 `result = f.get("result")`
拿到非空 dict（truthy）→ _display_result(dict) → _reset_text_rebuild_state 访问
result.markdown_text → AttributeError: 'dict' object has no attribute 'markdown_text'。

修复：_on_file_selected 只把真正的 OCRResult（带 markdown_text）交给 _display_result；
失败项（错误 dict）/ pending 走 _result_widget.clear()。
"""

from unittest.mock import MagicMock

from vibeocr.classic.recognition_result import OCRResult
from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionTab


def _make_tab(qtbot, monkeypatch, file_path: str, result) -> BatchRecognitionTab:
    """构造一个最小 BatchRecognitionTab，注入单个文件及其 result。

    _preview_widget / _result_widget / _export_widget 全部 mock，避免依赖真实
    文件 I/O 和子组件；_display_result 用 spy 记录调用。
    """
    tab = BatchRecognitionTab(backend=MagicMock())
    qtbot.addWidget(tab)

    # 注入文件到 file_list_widget
    tab._file_list_widget.add_files([file_path])
    # 直接设置 result（模拟 update_file_status 的存储）
    tab._file_list_widget._files[0]["result"] = result

    # mock 掉子组件，避免 load_file / 真实渲染
    tab._preview_widget = MagicMock()
    tab._result_widget = MagicMock()
    tab._export_widget = MagicMock()
    # _display_result 是 BaseOcrTab 方法；用 spy 而非全 mock，便于断言是否被调
    tab._display_result = MagicMock()
    return tab


def test_select_failed_file_clears_without_crash(qtbot, monkeypatch, tmp_path):
    """点失败文件（result 是错误 dict）不应崩溃，应清空结果区。"""
    path = str(tmp_path / "fail.png")
    tab = _make_tab(qtbot, monkeypatch, path, result={"error": "识别失败"})

    # 修复前：这里会抛 AttributeError
    tab._on_file_selected(path)

    tab._display_result.assert_not_called()
    tab._result_widget.clear.assert_called_once()
    # 导出 widget 不应收到错误 dict
    tab._export_widget.set_current_result.assert_not_called()


def test_select_completed_file_displays_result(qtbot, monkeypatch, tmp_path):
    """点成功文件（result 是 OCRResult）应正常显示。"""
    path = str(tmp_path / "ok.png")
    ocr_result = OCRResult(markdown_text="识别文本", raw_text="识别文本")
    tab = _make_tab(qtbot, monkeypatch, path, result=ocr_result)

    tab._on_file_selected(path)

    tab._display_result.assert_called_once_with(ocr_result)
    tab._export_widget.set_current_result.assert_called_once_with(ocr_result)
    tab._result_widget.clear.assert_not_called()


def test_select_pending_file_clears(qtbot, monkeypatch, tmp_path):
    """点未处理文件（无 result）应清空结果区。"""
    path = str(tmp_path / "pending.png")
    tab = _make_tab(qtbot, monkeypatch, path, result=None)
    # pending 项没有 result 键
    tab._file_list_widget._files[0].pop("result", None)

    tab._on_file_selected(path)

    tab._display_result.assert_not_called()
    tab._result_widget.clear.assert_called_once()
