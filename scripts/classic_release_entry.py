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
    from vibeocr.classic.app_paths import get_active_app_paths

    return get_active_app_paths().logs_root / "vibeocr-bootstrap.log"


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
    # 首个 page 创建前收口 cache/persistent storage 到 state 内
    from vibeocr.classic.utils.webengine_paths import configure_webengine_storage

    configure_webengine_storage()
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


def _run_velopack_update_smoke() -> int:
    """Exercise the packaged Portable update path without importing the Qt UI."""
    import asyncio
    import re
    from urllib.parse import urlsplit

    from vibeocr.classic.app_paths import AppPaths, get_active_app_paths
    from vibeocr.classic.runtime_installation import RuntimeInstallerClient
    from vibeocr.classic.runtime_smoke import probe_runtime_launch
    from vibeocr.classic.services.update_coordinator import (
        UpdateApplyStatus,
        UpdateCheckStatus,
    )
    from vibeocr.classic.services.velopack_update import VelopackUpdateCoordinator

    mode = os.environ.get("VIBEOCR_CLASSIC_TEST_MODE")
    nonce = os.environ.get("VIBEOCR_CLASSIC_TEST_NONCE", "")
    if mode != "artifact-smoke" or re.fullmatch(r"[0-9a-f]{32,128}", nonce) is None:
        raise RuntimeError("Velopack artifact smoke requires authenticated test mode")
    feed = os.environ["VIBEOCR_SELF_TEST_UPDATE_FEED"]
    parsed = urlsplit(feed)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Velopack artifact smoke feed must be loopback HTTP")
    target = os.environ["VIBEOCR_SELF_TEST_TARGET_VERSION"]
    paths = get_active_app_paths()
    result_path = Path(os.environ["VIBEOCR_SELF_TEST_RESULT"]).resolve()
    try:
        result_path.relative_to(paths.state_root)
    except ValueError as exc:
        raise RuntimeError("Velopack artifact smoke result escaped state root") from exc

    proxy_names = (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    )
    saved_proxy = {name: os.environ.get(name) for name in proxy_names}
    try:
        for name in proxy_names[:6]:
            os.environ[name] = "http://127.0.0.1:9"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        client = RuntimeInstallerClient(paths.state_root)
        required = client.required_capabilities()
        client.inspect(required_capabilities=required)
        launch = client.ensure(install_component_ids=())
        probe_runtime_launch(launch, paths.state_root)
    finally:
        for name, value in saved_proxy.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def runtime_snapshot(app_paths: AppPaths) -> list[tuple[str, int]]:
        return sorted(
            (path.relative_to(app_paths.runtime_root).as_posix(), path.stat().st_size)
            for path in app_paths.runtime_root.rglob("*")
            if path.is_file()
        )

    snapshot_path = paths.config_file.parent / f"runtime-e2e-{nonce}.json"
    current_tree = runtime_snapshot(paths)
    coordinator = VelopackUpdateCoordinator(source_candidates=(feed,))
    current = asyncio.run(coordinator.installed_version())
    if current == target:
        if not snapshot_path.is_file():
            raise RuntimeError("packaged update lost the pre-update Runtime snapshot")
        previous_tree = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if previous_tree != [list(item) for item in current_tree]:
            raise RuntimeError("packaged update/restart rewrote state/runtime")
        temporary_result = result_path.with_suffix(".tmp")
        temporary_result.write_text(
            json.dumps(
                {
                    "installed_version": current,
                    "install_root": str(paths.install_root),
                    "state_root": str(paths.state_root),
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary_result, result_path)
        return 0

    snapshot_path.write_text(json.dumps(current_tree), encoding="utf-8")

    check = asyncio.run(coordinator.check())
    if check.status is not UpdateCheckStatus.AVAILABLE or check.version != target:
        raise RuntimeError(
            f"expected update {target}, got {check.status.value}: {check.version}"
        )
    applied = asyncio.run(coordinator.download_and_apply())
    if applied.status is not UpdateApplyStatus.APPLY_STARTED:
        raise RuntimeError(
            f"Velopack apply did not start: {applied.status.value}: {applied.detail}"
        )
    return 0


def _activate_portable_state() -> None:
    """在 Qt/Runtime/日志之前激活并探针验证便携状态根。

    不可写或越界时 fail closed：冻结态用原生 MessageBox 提示用户移动到
    可写位置，不请求管理员权限、不回退 LocalAppData/系统 Temp。
    """

    from vibeocr.classic.app_paths import (
        EnvironmentTestDataRootResolver,
        PortableStateError,
        activate_portable_state,
    )

    executable = (
        Path(sys.executable).resolve()
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    try:
        data_root_resolver = (
            EnvironmentTestDataRootResolver.from_environment()
            if os.environ.get("VIBEOCR_CLASSIC_DATA_ROOT")
            else None
        )
        paths = activate_portable_state(
            executable,
            data_root_resolver=data_root_resolver,
        )
    except PortableStateError as error:
        message = (
            f"{error}\n\n程序目录：{executable}\n"
            "VibeOCR 需要在程序目录下的 state 文件夹保存配置与运行数据。"
        )
        # windowed 冻结进程没有可用 stderr；artifact smoke 通过结果文件
        # 获得可靠的 fail-closed 证据。
        result_path = os.environ.get("VIBEOCR_SELF_TEST_RESULT")
        if result_path:
            try:
                Path(result_path).write_text(
                    json.dumps(
                        {"portable_state_error": str(error)}, ensure_ascii=False
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
        if sys.stderr is not None:
            print(f"[VibeOCR] {message}", file=sys.stderr)
        # 无头/自动化环境（artifact smoke）通过该探针缝跳过原生弹窗，
        # 仍以非零退出码 fail closed。
        if os.name == "nt" and not os.environ.get("VIBEOCR_SILENT_PORTABLE_ERROR"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "VibeOCR Classic", 0x10)
        raise SystemExit(2) from error
    os.environ.setdefault(
        "VIBEOCR_BOOTSTRAP_LOG",
        str(paths.logs_root / "vibeocr-bootstrap.log"),
    )
    # Chromium 磁盘缓存必须在首个 QtWebEngine 子进程启动前指定到 state 内。
    os.environ.setdefault(
        "QTWEBENGINE_DISK_CACHE_PATH",
        str(paths.webengine_cache_dir),
    )


if __name__ == "__main__":
    # Velopack startup hooks must run exactly once before Qt, Runtime,
    # Supervisor, logging, or any other application startup work.
    import velopack

    velopack.App().run()

    _activate_portable_state()
    if os.environ.get("VIBEOCR_SELF_TEST_PDF") == "1":
        os._exit(_run_with_bootstrap(_run_pdf_smoke))
    if os.environ.get("VIBEOCR_SELF_TEST_WEBENGINE") == "1":
        # QtWebEngine may crash while its Chromium subprocesses are torn down under
        # the offscreen Windows test platform. The load result has already been
        # durably written, so bypass interpreter/Qt finalizers in this test-only path.
        os._exit(_run_with_bootstrap(_run_webengine_smoke))
    if os.environ.get("VIBEOCR_SELF_TEST_VELOPACK_UPDATE") == "1":
        raise SystemExit(_run_with_bootstrap(_run_velopack_update_smoke))
    raise SystemExit(_run_with_bootstrap(_run_application))
