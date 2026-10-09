"""进程内后端宿主：在本进程的专用线程里运行识别服务。

传统形态里，识别服务（FastAPI Supervisor）是独立的后端进程，由运行时
安装器解析、安装后再拉起；本模块把同一服务直接承载在前端进程内：

- 复用后端的 ``build_supervisor`` 组合根与 ``create_app``，行为与独立进程一致；
- 预绑定 ``127.0.0.1`` 回环 socket 后交给 uvicorn，端口由系统分配；
- 对外只暴露与原子进程句柄同构的 handle（``ready`` / ``base_url`` /
  ``session_token`` / ``shutdown()``），上层 ``SubprocessManager`` 与
  Protocol 客户端适配器不需要感知承载方式的变化。

会话令牌在本进程内生成并直接传给 app 与 handle，不经环境变量/argv。
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# 启动后端（构建组合根 + uvicorn 就绪）的等待上限。
_STARTUP_TIMEOUT_S = 60.0


def inprocess_backend_enabled() -> bool:
    """是否默认以进程内方式承载后端。

    默认开启；``VIBEOCR_SUPERVISOR_SUBPROCESS=1`` 强制回退旧的
    子进程形态（调试/冒烟逃生口）。
    """

    if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
        # T6 冒烟必须验证真实的独立子进程启动路径。
        return False
    return os.environ.get("VIBEOCR_SUPERVISOR_SUBPROCESS", "") != "1"


@dataclass(frozen=True, slots=True)
class _Ready:
    """与子进程 ready envelope 同构的就绪信息。"""

    instance_id: str
    capabilities: tuple[str, ...]


class InProcessSupervisorHandle:
    """后端宿主句柄；表面与 ``runtime_client.SupervisorProcess`` 对齐。"""

    def __init__(self, *, base_url: str, session_token: str, ready: _Ready) -> None:
        self.base_url = base_url
        self.session_token = session_token
        self.ready = ready
        self._stopped = threading.Event()

    @property
    def pid(self) -> int:
        return os.getpid()

    def shutdown(self, timeout_s: float = 20.0) -> None:
        """停止后端线程并释放资源；幂等。"""

        if self._stopped.is_set():
            return
        self._stopped.set()

    def mark_stopped(self) -> None:
        self._stopped.set()


class InProcessBackendHost:
    """承载后端的生命周期 owner。

    ``start`` 在调用线程里完成组合根构建与 uvicorn 启动等待（阻塞），
    返回可交给 ``SubprocessManager`` 的 handle；``shutdown`` 通过
    handle 触发停止。一个 host 实例只承载一次启动。
    """

    def __init__(self, state_root: Path) -> None:
        self._state_root = Path(state_root)
        self._started = False

    def start(
        self,
        progress: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> InProcessSupervisorHandle:
        """构建并启动进程内后端；返回就绪句柄。"""

        if self._started:
            raise RuntimeError("InProcessBackendHost 只能启动一次")
        self._started = True

        def _emit(message: str) -> None:
            logger.info("[BackendHost] %s", message)
            if progress is not None:
                try:
                    progress(message)
                except Exception:  # pragma: no cover - UI 回调异常不阻断启动
                    logger.debug("进度回调异常", exc_info=True)

        _emit("正在启动内置识别服务")

        from vibeocr.backend.supervisor.bootstrap import (
            BootstrapHandle,
            bind_loopback_socket,
            generate_session_token,
            new_instance_id,
        )

        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("后端启动在开始前被取消")

        token = generate_session_token()
        instance_id = new_instance_id()
        sock = bind_loopback_socket()
        port = sock.getsockname()[1]

        stager_root = self._state_root / "temp" / "supervisor-stager"
        stager_root.mkdir(parents=True, exist_ok=True)
        self._apply_environment(stager_root)
        # 已安装的引擎环境（Paddle/MinerU）在此刻告知后端；后续安装由
        # 设置页在停止服务后调用 apply_runtime_env 并重启后端生效。
        from vibeocr.classic.engine_envs import EngineEnvManager

        EngineEnvManager(self._state_root / "envs").apply_runtime_env()

        _emit("正在准备识别服务组件")
        from vibeocr.backend.supervisor.composition import build_supervisor
        from vibeocr.backend.supervisor.settings_store import RuntimeSettingsStore

        settings_store = RuntimeSettingsStore(
            self._state_root / "supervisor-settings.json"
        )
        module, _ = build_supervisor(
            instance_id=instance_id,
            stager_root=stager_root,
            bootstrap_handle=BootstrapHandle(token),
            with_pdf_adapter=True,
            settings_store=settings_store,
        )

        if cancel_event is not None and cancel_event.is_set():
            module.shutdown_now()
            sock.close()
            raise RuntimeError("后端启动在组件就绪后被取消")

        _emit("正在启动本地服务端口")
        from vibeocr.backend.supervisor.app import create_app

        app = create_app(module, token)

        import uvicorn
        from vibeocr.runtime_contracts.generated import ALL_CAPABILITIES

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
            workers=1,
            # 进程内宿主不得重配应用的全局日志（uvicorn 默认会按自己的
            # LOGGING_CONFIG 调整 root/uvicorn logger，影响宿主进程）。
            log_config=None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=self._serve,
            args=(server, sock, module),
            name=f"vibeocr-backend-{instance_id}",
            daemon=True,
        )
        handle = InProcessSupervisorHandle(
            base_url=f"http://127.0.0.1:{port}",
            session_token=token,
            ready=_Ready(
                instance_id=instance_id,
                capabilities=tuple(ALL_CAPABILITIES),
            ),
        )
        self._wire_shutdown(handle, server, sock, module, thread)

        thread.start()
        self._wait_for_server(server, thread, timeout_s=_STARTUP_TIMEOUT_S)
        _emit("内置识别服务已就绪")
        return handle

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _serve(self, server, sock: socket.socket, module) -> None:  # pragma: no cover - 线程体
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(server.serve(sockets=[sock]))
        except Exception:
            logger.exception("[BackendHost] 后端服务线程异常退出")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _wait_for_server(self, server, thread: threading.Thread, *, timeout_s: float) -> None:
        import time

        deadline = time.monotonic() + timeout_s
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("后端服务线程在就绪前退出，详见日志")
            if time.monotonic() > deadline:
                server.should_exit = True
                raise TimeoutError(f"内置识别服务未在 {timeout_s:.0f} 秒内就绪")
            time.sleep(0.05)

    def _wire_shutdown(
        self,
        handle: InProcessSupervisorHandle,
        server,
        sock: socket.socket,
        module,
        thread: threading.Thread,
    ) -> None:
        original_shutdown = handle.shutdown

        def _shutdown(timeout_s: float = 20.0) -> None:
            original_shutdown(timeout_s)
            server.should_exit = True
            thread.join(timeout=timeout_s)
            try:
                sock.close()
            except OSError:
                pass
            try:
                module.shutdown_now()
            except Exception:
                logger.exception("[BackendHost] 后端模块清理失败")
            if thread.is_alive():
                logger.warning("[BackendHost] 后端服务线程未在超时内退出")

        handle.shutdown = _shutdown  # type: ignore[method-assign]

    def _apply_environment(self, stager_root: Path) -> None:
        """为本进程与后端派生的子进程准备运行环境变量。

        只设置缺失的键，不覆盖用户/测试显式提供的值；缓存目录全部收口
        到 state，避免落到用户全局位置。模型下载来源（HuggingFace/
        ModelScope）投影为引擎识别的环境变量，引擎子进程据此决定从哪里
        准备模型。
        """

        cache_root = self._state_root / "cache"
        defaults: dict[str, str] = {
            "VIBEOCR_SUP_ROOT": str(stager_root),
            "VIBEOCR_SUPERVISOR_SETTINGS": str(
                self._state_root / "supervisor-settings.json"
            ),
            "VIBEOCR_PRODUCT_ROOT": str(self._state_root),
            "HF_HOME": str(cache_root / "huggingface"),
            "MODELSCOPE_CACHE": str(cache_root / "modelscope"),
            "PADDLE_PDX_CACHE_HOME": str(cache_root / "paddlex"),
            "MINERU_HOME": str(self._state_root / "mineru4"),
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, value)

        from vibeocr.classic.download_sources import DownloadSourceStore

        DownloadSourceStore(self._state_root).apply_model_source_environment()


def generate_fallback_token() -> str:
    """独立生成会话令牌（测试/诊断用）。"""

    return secrets.token_urlsafe(32)
