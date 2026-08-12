"""PyInstaller entry point for the independently released Classic product."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO


_BOOTSTRAP_LOG_MAX_BYTES = 1024 * 1024


class _TeeTextIO:
    """Mirror bootstrap output to its original stream and a durable log."""

    def __init__(self, primary: TextIO | None, log: TextIO) -> None:
        self._primary = primary
        self._log = log

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        if self._primary is not None:
            try:
                self._primary.write(value)
                self._primary.flush()
            except (OSError, ValueError):
                pass
        self._log.write(value)
        self._log.flush()
        return len(value)

    def flush(self) -> None:
        if self._primary is not None:
            try:
                self._primary.flush()
            except (OSError, ValueError):
                pass
        self._log.flush()


def _bootstrap_log_path() -> Path:
    configured = os.environ.get("VIBEOCR_BOOTSTRAP_LOG")
    if configured:
        return Path(configured)
    if getattr(sys, "frozen", False):
        install_root = Path(sys.executable).resolve().parent
    else:
        install_root = Path(__file__).resolve().parents[1]
    return install_root / "data" / "logs" / "vibeocr-bootstrap.log"


def _open_bootstrap_log() -> TextIO | None:
    path = _bootstrap_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size >= _BOOTSTRAP_LOG_MAX_BYTES:
            rotated = path.with_name(f"{path.stem}.1{path.suffix}")
            rotated.unlink(missing_ok=True)
            path.replace(rotated)
        return path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None


def _run_with_bootstrap(entrypoint: Callable[[], int]) -> int:
    """Run the real entry behind a stdlib-only, pre-import diagnostic boundary."""
    log = _open_bootstrap_log()
    if log is None:
        return entrypoint()
    with log:
        stdout = _TeeTextIO(sys.stdout, log)
        stderr = _TeeTextIO(sys.stderr, log)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            print(
                f"[{datetime.now().astimezone().isoformat()}] "
                f"bootstrap started: pid={os.getpid()}"
            )
            try:
                result = entrypoint()
            except BaseException:
                print("bootstrap failed before application ready:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                raise
            if result != 0:
                print(
                    f"application exited before ready: code={result}", file=sys.stderr
                )
            return result


def _run_application() -> int:
    # Keep this import inside the bootstrap boundary: dependency/import failures are
    # one of the most important failures for a windowed executable to persist.
    from vibeocr.classic.main import main

    return main()


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
    # Velopack startup hooks must run exactly once before Qt, Runtime,
    # Supervisor, logging, or any other application startup work.
    import velopack

    velopack.App().run()

    if getattr(sys, "frozen", False):
        from vibeocr.classic.data_migration import prepare_stable_data_root

        migration = prepare_stable_data_root(Path(sys.executable).resolve().parent)
        os.environ.setdefault(
            "VIBEOCR_BOOTSTRAP_LOG",
            str(migration.active_paths.data_root / "logs" / "vibeocr-bootstrap.log"),
        )
    if os.environ.get("VIBEOCR_SELF_TEST_PDF") == "1":
        os._exit(_run_pdf_smoke())
    if os.environ.get("VIBEOCR_SELF_TEST_WEBENGINE") == "1":
        # QtWebEngine may crash while its Chromium subprocesses are torn down under
        # the offscreen Windows test platform. The load result has already been
        # durably written, so bypass interpreter/Qt finalizers in this test-only path.
        os._exit(_run_webengine_smoke())
    raise SystemExit(_run_with_bootstrap(_run_application))
