"""PDF 批量导出测试(进程化版本)。

新架构:导出走 manager.export_all_modified → IPC save(后端子进程),
不再用 PdfExportWorker(它依赖 session.doc,已废弃)。本测试改为
mock PdfBackendClient.save 验证 manager 导出逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vibeocr.classic.managers.pdf_session_manager import PdfSessionManager
from vibeocr.classic.pdf_workspace import PdfDocument, PdfPageInfo, PdfSession


def _make_session(path, modified=True):
    pdf_doc = PdfDocument(file_path=path)
    pdf_doc.pages = [PdfPageInfo(page_index=0)]
    pdf_doc.is_modified = modified
    return PdfSession(file_path=path, session_id="test-sid", pdf_document=pdf_doc)


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager(parent=qapp, client=MagicMock())
    yield mgr
    mgr.shutdown()


class TestPdfExportAllModified:
    def test_exports_modified_sessions(self, manager, tmp_path):
        """export_all_modified 应对所有 modified session 调 IPC save。"""
        s1 = _make_session(str(tmp_path / "a.pdf"))
        s2 = _make_session(str(tmp_path / "b.pdf"))
        manager._sessions[s1.file_path] = s1
        manager._sessions[s2.file_path] = s2
        out = tmp_path / "out"

        saved_paths: list[str] = []
        with patch.object(
            manager._client,
            "save",
            lambda sid, path=None, pdf_settings=None: saved_paths.append(path or ""),
        ):
            exported = manager.export_all_modified(str(out))
        assert len(exported) == 2
        assert len(saved_paths) == 2

    def test_skips_unmodified(self, manager, tmp_path):
        """export_all_modified 应跳过未修改的 session。"""
        s1 = _make_session(str(tmp_path / "a.pdf"), modified=True)
        s2 = _make_session(str(tmp_path / "b.pdf"), modified=False)
        manager._sessions[s1.file_path] = s1
        manager._sessions[s2.file_path] = s2
        out = tmp_path / "out"

        with patch.object(
            manager._client,
            "save",
            lambda sid, path=None, pdf_settings=None: None,
        ):
            exported = manager.export_all_modified(str(out))
        assert len(exported) == 1
