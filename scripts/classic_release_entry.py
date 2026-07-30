"""PyInstaller entry point for the independently released Classic product."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vibeocr.classic.main import main


def _run_pdf_smoke() -> int:
    """Prove the frozen frontend can load QtPdf without the removed PyMuPDF."""
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtWidgets import QApplication

    result_path = Path(os.environ["VIBEOCR_SELF_TEST_RESULT"])
    app = QApplication.instance() or QApplication([])
    document = QPdfDocument()
    result = {"qt_pdf_created": document.pageCount() == 0}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    document.close()
    app.processEvents()
    return 0 if result["qt_pdf_created"] else 1


def _run_webengine_smoke() -> int:
    """Prove the frozen WebEngine and WebChannel can complete a JS round trip."""
    from PySide6.QtCore import QObject, QTimer, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView

    result_path = Path(os.environ["VIBEOCR_SELF_TEST_RESULT"])
    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    result = {"load_finished": False, "webchannel_round_trip": False}

    def finish_if_ready() -> None:
        if not all(result.values()):
            return
        result_path.write_text(json.dumps(result), encoding="utf-8")
        app.quit()

    def loaded(ok: bool) -> None:
        result["load_finished"] = bool(ok)
        finish_if_ready()

    class SmokeBridge(QObject):
        @Slot(str)
        def report(self, value: str) -> None:
            result["webchannel_round_trip"] = value == "ok"
            finish_if_ready()

    def timeout() -> None:
        result_path.write_text(json.dumps(result), encoding="utf-8")
        app.quit()

    bridge = SmokeBridge()
    channel = QWebChannel(view.page())
    channel.registerObject("smoke", bridge)
    view.page().setWebChannel(channel)
    view.loadFinished.connect(loaded)
    view.setHtml(
        """
        <!doctype html>
        <title>VibeOCR WebEngine smoke</title>
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script>
          new QWebChannel(qt.webChannelTransport, function(channel) {
            channel.objects.smoke.report("ok");
          });
        </script>
        """
    )
    QTimer.singleShot(20_000, timeout)
    app.exec()
    view.deleteLater()
    app.processEvents()
    if not result_path.is_file():
        result_path.write_text(json.dumps(result), encoding="utf-8")
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    if os.environ.get("VIBEOCR_SELF_TEST_PDF") == "1":
        os._exit(_run_pdf_smoke())
    if os.environ.get("VIBEOCR_SELF_TEST_WEBENGINE") == "1":
        # QtWebEngine may crash while its Chromium subprocesses are torn down under
        # the offscreen Windows test platform. The load result has already been
        # durably written, so bypass interpreter/Qt finalizers in this test-only path.
        os._exit(_run_webengine_smoke())
    raise SystemExit(main())
