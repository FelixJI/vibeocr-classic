"""Supervisor 子进程的 Qt 生命周期管理。

本模块只拥有启动任务、Supervisor 进程和就绪令牌。模型加载、TTL、排队与
识别状态均由 Supervisor v2 自己管理，不能在 GUI 进程维护第二份状态。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from vibeocr.classic.runtime_installation import (
    RuntimeInstallerClient,
    RuntimeLaunch,
)
from vibeocr.classic.utils.shutdown_jobs import ExternalShutdownJob

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


class SubprocessStartSignals(QObject):
    """Supervisor 启动任务信号。"""

    started = Signal(bool)
    progress = Signal(str)


class SupervisorStartTask(QRunnable):
    """在线程池启动 Supervisor；Qt adapter 由 GUI 线程安装。"""

    def __init__(
        self,
        python_exe: str | Path | None = None,
        *,
        installer_client: RuntimeInstallerClient | None = None,
    ) -> None:
        super().__init__()
        if python_exe is None and installer_client is None:
            raise ValueError("python_exe or installer_client is required")
        self._python_exe = str(python_exe) if python_exe is not None else None
        self._installer_client = installer_client
        self._cancelled = threading.Event()
        self.signals = SubprocessStartSignals()
        self.supervisor_proc: Any = None

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        # launch 同时完成进程创建与 ready envelope 握手；明确陈述阶段，
        # 避免用户误以为模型也在此时加载。
        self.signals.progress.emit("正在创建子进程并等待就绪握手")
        try:
            from vibeocr.runtime_client.process import SupervisorProcess

            launch: RuntimeLaunch | None = None
            if self._installer_client is not None:
                self.signals.progress.emit("正在确保绑定的 Runtime profile")
                launch = self._installer_client.ensure(
                    progress=self.signals.progress.emit,
                    cancel_event=self._cancelled,
                )
            proc = SupervisorProcess.launch(
                python_exe=(
                    launch.python_executable if launch is not None else self._python_exe
                ),
                module=(
                    launch.supervisor_module
                    if launch is not None
                    else "vibeocr.backend.supervisor.main"
                ),
                extra_env=launch.environment if launch is not None else None,
                working_directory=(
                    launch.working_directory if launch is not None else None
                ),
            )
            self.supervisor_proc = proc
            if self._cancelled.is_set():
                return
            self.signals.started.emit(True)
        except Exception:
            logger.exception("[SubprocessManager] Supervisor 启动失败")
            proc = self.supervisor_proc
            if proc is not None:
                try:
                    proc.shutdown()
                except Exception:  # pragma: no cover - defensive cleanup
                    logger.debug("Supervisor 半启动清理失败", exc_info=True)
                self.supervisor_proc = None
            if not self._cancelled.is_set():
                self.signals.started.emit(False)


class SubprocessManager(QObject):
    """Supervisor 进程 owner 与单一 readiness token。"""

    service_ready = Signal(bool)
    progress_update = Signal(str)
    invalidation_finished = Signal(bool, str)

    def __init__(
        self,
        project_root: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._installer_client = RuntimeInstallerClient(project_root)
        self._thread_pool = QThreadPool()
        self._is_ready = False
        self._start_task: SupervisorStartTask | None = None
        self._start_signals_connected = False
        self._supervisor_process: Any = None
        self._shutdown_requested = False
        self._application_shutdown_requested = False
        self._invalidation_job: ExternalShutdownJob | None = None

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @property
    def is_invalidating(self) -> bool:
        return self._invalidation_job is not None

    def start_supervisor(self) -> None:
        if self._application_shutdown_requested:
            logger.debug("[SubprocessManager] 应用已进入关闭阶段，拒绝重新启动")
            return
        if self._invalidation_job is not None:
            logger.debug("[SubprocessManager] Supervisor 正在失效，拒绝并发启动")
            return
        if self._is_ready:
            logger.debug("[SubprocessManager] Supervisor 已就绪，跳过重复启动")
            return
        if self._start_task is not None:
            logger.debug("[SubprocessManager] Supervisor 正在启动，跳过重复启动")
            return

        self._shutdown_requested = False
        test_python = os.environ.get("VIBEOCR_SELF_TEST_PYTHON")
        if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6" and test_python:
            task = SupervisorStartTask(test_python)
        else:
            task = SupervisorStartTask(installer_client=self._installer_client)
        self._start_task = task
        task.signals.started.connect(self._on_started)
        task.signals.progress.connect(self.progress_update.emit)
        self._start_signals_connected = True
        self._thread_pool.start(task)

    def _on_started(self, success: bool) -> None:
        task = self._start_task
        if task is None or self._shutdown_requested:
            logger.debug("[SubprocessManager] 忽略已取消启动任务的迟到结果")
            return

        if success:
            process = task.supervisor_proc
            try:
                self._install_runtime_adapter(process)
            except Exception:
                logger.exception("[SubprocessManager] Supervisor 适配器初始化失败")
                success = False
                if process is not None:
                    try:
                        process.shutdown()
                    except Exception:
                        logger.debug("Supervisor 适配器失败清理异常", exc_info=True)
                task.supervisor_proc = None
            else:
                self._supervisor_process = process
                task.supervisor_proc = None
        self._is_ready = success
        self._start_task = None
        self._start_signals_connected = False
        self.service_ready.emit(success)
        if success:
            logger.info("[SubprocessManager] Supervisor 已就绪")
        else:
            logger.warning("[SubprocessManager] Supervisor 启动失败")

    @staticmethod
    def _install_runtime_adapter(proc: Any) -> None:
        """在 GUI 线程创建并安装 Qt adapter。

        Supervisor 的阻塞启动仍在 QThreadPool；QObject 必须等 ready signal
        回到 SubprocessManager 所在线程后再构造，才能可靠投递 Qt signals。
        """
        if proc is None:
            raise RuntimeError("Supervisor ready 但进程句柄为空")

        from vibeocr.classic.pdf_client import SyncPdfSupervisorClient
        from vibeocr.classic.pyside.supervisor_adapter import (
            SupervisorClientAdapter,
            set_supervisor_adapter,
        )
        from vibeocr.runtime_client.client import SupervisorClient
        from vibeocr.runtime_client.client import RuntimeHttpClient
        from vibeocr.runtime_client.sync_client import SyncSupervisorClient

        def pdf_factory() -> SyncPdfSupervisorClient:
            return SyncPdfSupervisorClient(
                base_url=proc.base_url,
                session_token=proc.session_token,
                instance_id=proc.ready.instance_id,
            )

        def inference_factory() -> SyncSupervisorClient:
            return SyncSupervisorClient(
                base_url=proc.base_url,
                session_token=proc.session_token,
                instance_id=proc.ready.instance_id,
            )

        def runtime_status_factory() -> RuntimeHttpClient:
            return RuntimeHttpClient(
                base_url=proc.base_url,
                session_token=proc.session_token,
                timeout=10.0,
            )

        client = SupervisorClient(
            base_url=proc.base_url,
            session_token=proc.session_token,
            instance_id=proc.ready.instance_id,
        )
        adapter = SupervisorClientAdapter(
            client_factory=lambda: client,
            pdf_sync_client_factory=pdf_factory,
            inference_sync_client_factory=inference_factory,
            runtime_status_client_factory=runtime_status_factory,
        )
        set_supervisor_adapter(adapter)
        adapter.start()

    def invalidate_supervisor(self) -> bool:
        """非阻塞关闭当前 runtime；完成后通过 ``invalidation_finished`` 通知。

        安装/更新调用方只能在 ``success=True`` 后继续。失败时进程 owner 仍由
        manager 持有，可再次调用本方法重试，不能带着旧 Supervisor 继续维护。
        """

        if self._application_shutdown_requested:
            return False
        if self._invalidation_job is not None:
            return False

        self._request_runtime_stop(application_shutdown=False)
        job = ExternalShutdownJob(
            (("supervisor_runtime", self._shutdown_runtime_for_invalidation),),
            self,
        )
        self._invalidation_job = job
        job.finished.connect(self._on_invalidation_finished)
        job.start()
        return True

    def request_shutdown(self) -> None:
        """请求取消 Qt 启动任务；调用方通过 ``is_drained`` 非阻塞轮询。"""

        self._request_runtime_stop(application_shutdown=True)

    def _request_runtime_stop(self, *, application_shutdown: bool) -> None:
        if application_shutdown:
            self._application_shutdown_requested = True
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._is_ready = False
        task = self._start_task
        if task is not None:
            task.cancel()
            if self._start_signals_connected:
                try:
                    task.signals.started.disconnect(self._on_started)
                    task.signals.progress.disconnect(self.progress_update.emit)
                except (RuntimeError, TypeError):
                    pass
                self._start_signals_connected = False

    def is_drained(self) -> bool:
        return (
            self._thread_pool.activeThreadCount() == 0
            and self._invalidation_job is None
        )

    def _shutdown_runtime_for_invalidation(self) -> None:
        """外部线程执行：等启动任务退出，再关闭仍由 manager 持有的 runtime。"""

        if not self._thread_pool.waitForDone(30_000):
            raise TimeoutError("Supervisor 启动任务未在 30 秒内停止")

        task = self._start_task
        process = self._supervisor_process
        if process is None and task is not None:
            process = task.supervisor_proc
        if not self._is_ready and process is None:
            return

        errors: list[str] = []
        try:
            from vibeocr.classic.pyside.supervisor_adapter import get_supervisor_adapter

            get_supervisor_adapter().shutdown()
        except Exception as exc:  # pragma: no cover - defensive aggregation
            errors.append(f"adapter: {exc}")
        if process is not None:
            try:
                process.shutdown()
            except Exception as exc:
                errors.append(f"process: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _on_invalidation_finished(self) -> None:
        job = self._invalidation_job
        if job is None:
            return
        try:
            job.finished.disconnect(self._on_invalidation_finished)
        except (RuntimeError, TypeError):
            pass

        success = not job.errors
        error = "; ".join(f"{name}: {message}" for name, message in job.errors)
        if success:
            task = self._start_task
            if task is not None:
                task.supervisor_proc = None
            self._start_task = None
            self._start_signals_connected = False
            self._supervisor_process = None
            self._is_ready = False
            if not self._application_shutdown_requested:
                self._shutdown_requested = False

        self._invalidation_job = None
        job.deleteLater()
        self.invalidation_finished.emit(success, error)

    def take_shutdown_callable(self):
        """在线程池排空后转移 runtime owner，供非 GUI 关闭阶段执行。"""

        if not self.is_drained():
            raise RuntimeError("subprocess Qt tasks are still running")

        task = self._start_task
        process = self._supervisor_process
        if process is None and task is not None:
            process = task.supervisor_proc
        if task is not None:
            task.supervisor_proc = None
        had_runtime = self._is_ready or process is not None

        self._start_task = None
        self._start_signals_connected = False
        self._supervisor_process = None
        self._is_ready = False

        if not had_runtime:
            return None

        def _shutdown() -> None:
            try:
                from vibeocr.classic.pyside.supervisor_adapter import (
                    get_supervisor_adapter,
                )

                get_supervisor_adapter().shutdown()
            finally:
                if process is not None:
                    process.shutdown()

        return _shutdown

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """兼容独立调用的同步关闭入口。"""

        self.request_shutdown()
        invalidation_job = self._invalidation_job
        if invalidation_job is not None:
            if not invalidation_job.wait(timeout_ms):
                logger.warning("[SubprocessManager] 失效任务未在超时内排空")
                return False
            self._on_invalidation_finished()

        timed_out = not self._thread_pool.waitForDone(timeout_ms)
        if timed_out:
            logger.warning("[SubprocessManager] 启动任务未在超时内排空")
            return False

        shutdown = self.take_shutdown_callable()
        if shutdown is not None:
            try:
                shutdown()
            except Exception:
                logger.exception("[SubprocessManager] Supervisor 关闭失败")
                return False
        return True


__all__ = [
    "SubprocessManager",
    "SubprocessStartSignals",
    "SupervisorStartTask",
]
