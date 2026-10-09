"""识别引擎环境管理：用 uv 创建并维护 PaddleOCR / MinerU 独立环境。

面向用户的模型很简单：

- 基础识别（截图、图片快速识别）内置于主程序，无需安装任何东西；
- 高级识别按需安装，彼此独立：
  - PaddleOCR 引擎（CPU 版 / GPU 版）
  - MinerU 文档解析（CPU 版 / GPU 版）

安装即 ``uv venv`` + ``uv pip install -r <带哈希清单>``：下载与安装进度由
uv 输出逐行转发给调用方（安装对话框的日志区），不承诺精确的剩余时间；
删除即移除整个环境目录，随时可重装。

环境位置固定在 ``state/envs/<环境名>``；安装成功后由
:func:`apply_runtime_env` 把解释器位置告知进程内后端
（``VIBEOCR_PADDLE_HOME`` / ``VIBEOCR_MINERU_PYTHON``）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_MARKER_NAME = ".vibeocr-engine-env.json"


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Protocol SDK wheel（不在公共 PyPI）。引擎环境里的 Paddle worker 需要
# 导入 contracts；URL 版本与仓库 uv.lock 的来源保持一致。
_CONTRACTS_WHEEL_URL = (
    "https://github.com/FelixJI/vibeocr-protocol/releases/download/v2.9.0/"
    "vibeocr_runtime_contracts-2.9.0-py3-none-any.whl"
)
_CONTRACTS_REQUIREMENT = f"vibeocr-runtime-contracts @ {_CONTRACTS_WHEEL_URL}"


@dataclass(frozen=True, slots=True)
class EngineEnvSpec:
    """一个可安装的引擎环境定义。"""

    id: str
    display_name: str
    description: str
    profile: str  # runtime-profiles 目录名（依赖清单来源）

    @property
    def lock_file_name(self) -> str:
        return f"requirements-{self.profile}.lock"


@dataclass(frozen=True, slots=True)
class EngineEnvState:
    """环境当前状态。"""

    spec_id: str
    installed: bool
    env_root: Path
    python: Path | None

    @property
    def env_python(self) -> str | None:
        return str(self.python) if self.python is not None else None


#: 用户可选择的四个组合（base = 主程序内置，不在此列）。
ENGINE_ENV_SPECS: tuple[EngineEnvSpec, ...] = (
    EngineEnvSpec(
        id="paddle-cpu",
        display_name="PaddleOCR 引擎（CPU 版）",
        description="高精度文字识别。CPU 版适合大多数电脑，下载量约 1 GB。",
        profile="win-x64-paddle-cpu",
    ),
    EngineEnvSpec(
        id="paddle-gpu",
        display_name="PaddleOCR 引擎（GPU 版）",
        description="高精度文字识别。GPU 版需要 NVIDIA 显卡（CUDA 12.6），速度更快，下载量更大。",
        profile="win-x64-paddle-cu126",
    ),
    EngineEnvSpec(
        id="mineru-cpu",
        display_name="MinerU 文档解析（CPU 版）",
        description="把 PDF / 图片文档解析为文字和表格。CPU 版适合大多数电脑，下载量约 2 GB。",
        profile="win-x64-cpu",
    ),
    EngineEnvSpec(
        id="mineru-gpu",
        display_name="MinerU 文档解析（GPU 版）",
        description="把 PDF / 图片文档解析为文字和表格。GPU 版需要 NVIDIA 显卡（CUDA 12.6），速度更快，下载量更大。",
        profile="win-x64-cu126",
    ),
)

_ENGINE_ENV_SPECS_BY_ID = {spec.id: spec for spec in ENGINE_ENV_SPECS}


def get_engine_env_spec(spec_id: str) -> EngineEnvSpec:
    try:
        return _ENGINE_ENV_SPECS_BY_ID[spec_id]
    except KeyError as exc:
        raise KeyError(f"未知的引擎环境: {spec_id}") from exc


def runtime_profiles_root() -> Path:
    """定位引擎依赖清单目录。

    两种布局：
    - wheel 安装：清单随包分发在 ``vibeocr/backend/runtime-profiles``；
    - 仓库源码：清单在包根 ``packages/vibeocr-backend/runtime-profiles``
      （与 ``src/`` 平级）。
    """

    override = os.environ.get("VIBEOCR_RUNTIME_PROFILES")
    if override:
        return Path(override)
    import importlib.resources

    package_root = Path(str(importlib.resources.files("vibeocr.backend")))
    # parents[2]：backend → vibeocr → src → <包根 packages/vibeocr-backend>
    candidates = (
        package_root / "runtime-profiles",
        package_root.parents[2] / "runtime-profiles",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def backend_distribution() -> Path:
    """引擎环境安装 ``vibeocr.backend`` 用的发行位置。

    优先级：``VIBEOCR_BACKEND_DIST``（打包产品指向随包 wheel）；否则回到
    仓库源码包根（含 pyproject.toml，可被 uv 构建安装）。
    """

    override = os.environ.get("VIBEOCR_BACKEND_DIST")
    if override:
        return Path(override)
    import importlib.resources

    package_root = Path(str(importlib.resources.files("vibeocr.backend")))
    # parents[2]：backend → vibeocr → src → <包根 packages/vibeocr-backend>
    for candidate in (package_root.parents[2], package_root.parent):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return package_root.parents[2]


class EngineEnvError(RuntimeError):
    """引擎环境安装/删除失败。"""


class UvRunner:
    """运行 uv 子进程并把输出逐行转发（日志转发的唯一出口）。"""

    def __init__(self, uv_path: str | None = None) -> None:
        self._uv_path = uv_path

    def resolve(self) -> str:
        if self._uv_path:
            return self._uv_path
        override = os.environ.get("VIBEOCR_UV_BIN")
        if override and Path(override).is_file():
            return override
        found = shutil.which("uv")
        if found:
            return found
        raise EngineEnvError(
            "未找到 uv。请先安装 uv（https://docs.astral.sh/uv/）后重试。"
        )

    def run(
        self,
        args: list[str],
        *,
        on_line: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        import queue as queue_module

        uv = self.resolve()
        command = [uv, *args]
        logger.info("[EngineEnv] %s", " ".join(command))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        # 读取放独立线程、主循环轮询队列：uv 长时间无输出（连接/解析）时
        # 取消请求仍能及时终止进程，而不是阻塞在下一次 readline 上。
        lines: "queue_module.Queue[str]" = queue_module.Queue()

        def _read_stdout() -> None:  # pragma: no cover - 线程体
            assert process.stdout is not None
            try:
                for raw_line in process.stdout:
                    stripped = raw_line.rstrip("\r\n")
                    if stripped:
                        lines.put(stripped)
            finally:
                lines.put("")  # EOF 哨兵（读取线程只投递非空行）

        reader = threading.Thread(
            target=_read_stdout, name="vibeocr-uv-output", daemon=True
        )
        reader.start()
        cancelled = False
        while True:
            try:
                line = lines.get(timeout=0.2)
            except queue_module.Empty:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    process.terminate()
                    break
                if not reader.is_alive() and lines.empty():
                    break
                continue
            if line == "":
                # EOF 哨兵：输出已读完。
                break
            logger.info("[EngineEnv] %s", line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # pragma: no cover - UI 回调异常
                    logger.debug("日志转发回调异常", exc_info=True)
        return_code = process.wait()
        reader.join(timeout=2.0)
        if cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise EngineEnvError("安装已取消")
        if return_code != 0:
            raise EngineEnvError(
                f"uv 执行失败（退出码 {return_code}）：{' '.join(args[:3])} …"
            )
        return subprocess.CompletedProcess(command, return_code, stdout="")


def _env_python(env_root: Path) -> Path:
    if sys.platform == "win32":
        return env_root / "Scripts" / "python.exe"
    return env_root / "bin" / "python"


class EngineEnvManager:
    """Paddle/MinerU 独立环境的创建、检查与移除。"""

    def __init__(
        self,
        envs_root: Path,
        uv: UvRunner | None = None,
    ) -> None:
        self._root = Path(envs_root)
        self._uv = uv or UvRunner()

    @property
    def envs_root(self) -> Path:
        return self._root

    @property
    def state_root(self) -> Path:
        """环境根的上级（即 state 根）；下载来源等共享配置存于此。"""

        return self._root.parent

    def env_root(self, spec_id: str) -> Path:
        return self._root / get_engine_env_spec(spec_id).id

    def inspect(self) -> dict[str, EngineEnvState]:
        """返回每个环境的安装状态。

        除目录与标记文件外还核对安装身份（依赖清单哈希与随包后端
        wheel 哈希）：产品升级后清单或后端变化会让旧环境判为未安装，
        引导用户重装，避免旧依赖无限期滞留。
        """

        states: dict[str, EngineEnvState] = {}
        identity = self._current_identity()
        for spec in ENGINE_ENV_SPECS:
            root = self.env_root(spec.id)
            marker = root / _MARKER_NAME
            python = _env_python(root)
            installed = marker.is_file() and python.is_file()
            if installed:
                try:
                    recorded = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    recorded = {}
                installed = (
                    isinstance(recorded, dict)
                    and recorded.get("identity") == identity
                )
            states[spec.id] = EngineEnvState(
                spec_id=spec.id,
                installed=installed,
                env_root=root,
                python=python if installed else None,
            )
        return states

    def _current_identity(self) -> dict[str, str]:
        """当前安装身份：依赖清单与后端发行内容的指纹。"""

        identity: dict[str, str] = {}
        for spec in ENGINE_ENV_SPECS:
            lock_file = (
                runtime_profiles_root() / spec.profile / spec.lock_file_name
            )
            try:
                identity[spec.profile] = _sha256_file(lock_file)
            except OSError:
                identity[spec.profile] = ""
        dist = os.environ.get("VIBEOCR_BACKEND_DIST")
        if dist and Path(dist).is_file():
            identity["backend_dist"] = _sha256_file(Path(dist))
        return identity

    def ensure(
        self,
        spec_id: str,
        *,
        on_phase: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        package_index_id: str | None = None,
    ) -> EngineEnvState:
        """安装（或修复）一个引擎环境；输出逐行转发到 ``on_log``。

        ``package_index_id`` 选择依赖下载源（见 ``download_sources``）；
        缺省时读取用户已保存的选择。清单带哈希锁，换源只影响下载速度
        与出处，不改变安装内容。
        """

        from vibeocr.classic.download_sources import (
            DownloadSourceStore,
            get_package_index,
        )

        if package_index_id is None:
            # envs_root 是 <state_root>/envs；下载来源保存在 state 下。
            package_index_id = DownloadSourceStore(self._root.parent).package_index_id
        index = get_package_index(package_index_id)

        spec = get_engine_env_spec(spec_id)
        root = self.env_root(spec.id)
        lock_file = runtime_profiles_root() / spec.profile / spec.lock_file_name
        if not lock_file.is_file():
            raise EngineEnvError(f"找不到依赖清单：{lock_file}")

        def _phase(message: str) -> None:
            logger.info("[EngineEnv] %s", message)
            if on_phase is not None:
                on_phase(message)

        def _log(line: str) -> None:
            if on_log is not None:
                on_log(line)

        if root.exists():
            _phase(f"正在清理旧的 {spec.display_name} 环境")
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        # 下载缓存与 uv 托管解释器都收口到 state：便携目录移动/复制后
        # 环境仍可用，不在用户级 uv 目录留数据。
        uv_env = os.environ.copy()
        uv_env.setdefault("UV_CACHE_DIR", str(self._root / ".uv-cache"))
        uv_env.setdefault(
            "UV_PYTHON_INSTALL_DIR", str(self._root / ".uv-python")
        )

        _phase(f"正在创建 {spec.display_name} 的独立环境")
        self._uv.run(
            ["venv", "--python", "3.13", str(root)],
            on_line=_log,
            cancel_event=cancel_event,
            env=uv_env,
        )

        python = _env_python(root)
        _phase(f"正在下载并安装依赖（来源：{index.label}；进度见下方日志）")
        self._uv.run(
            [
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--default-index",
                index.url,
                "-r",
                str(lock_file),
            ],
            on_line=_log,
            cancel_event=cancel_event,
            env=uv_env,
        )

        if spec.id.startswith("paddle"):
            # Paddle worker 在引擎环境里导入 vibeocr.backend 与 Protocol 契约。
            _phase("正在接入主程序的识别框架")
            self._uv.run(
                [
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    str(backend_distribution()),
                ],
                on_line=_log,
                cancel_event=cancel_event,
                env=uv_env,
            )
            self._uv.run(
                [
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    _CONTRACTS_REQUIREMENT,
                ],
                on_line=_log,
                cancel_event=cancel_event,
                env=uv_env,
            )

        (root / _MARKER_NAME).write_text(
            json.dumps(
                {
                    "spec_id": spec.id,
                    "profile": spec.profile,
                    "identity": self._current_identity(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _phase(f"{spec.display_name} 安装完成")
        state = self.inspect()[spec.id]
        if not state.installed:  # pragma: no cover - 防御性校验
            raise EngineEnvError("环境安装结束但校验未通过")
        return state

    def remove(self, spec_id: str) -> None:
        """删除一个引擎环境（不影响主程序与其它环境）。"""

        root = self.env_root(spec_id)
        if root.exists():
            shutil.rmtree(root)
        logger.info("[EngineEnv] 已删除引擎环境 %s (%s)", spec_id, root)

    # ------------------------------------------------------------------

    def apply_runtime_env(self) -> dict[str, str]:
        """把已安装环境的解释器位置与设备意图告知进程内后端。

        - 解释器位置：``VIBEOCR_PADDLE_HOME`` / ``VIBEOCR_MINERU_PYTHON``；
        - 设备意图：``VIBEOCR_RUNTIME_ACCELERATOR``——后端用它决定 MinerU
          API 的 GPU 可见性与组件后缀（cpu/cuda）；任一 GPU 环境在位即
          nvidia_cuda，否则 cpu；无任何环境时不覆盖现有值。

        返回本次写入的键值，便于测试与诊断；未安装的环境不设置，
        后端相应能力表现为“未安装”。
        """

        applied: dict[str, str] = {}
        states = self.inspect()
        paddle = (
            states["paddle-gpu"]
            if states["paddle-gpu"].installed
            else states["paddle-cpu"]
        )
        if paddle.installed:
            value = str(paddle.env_root)
            os.environ["VIBEOCR_PADDLE_HOME"] = value
            applied["VIBEOCR_PADDLE_HOME"] = value
        mineru = (
            states["mineru-gpu"]
            if states["mineru-gpu"].installed
            else states["mineru-cpu"]
        )
        if mineru.installed and mineru.python is not None:
            value = str(mineru.python)
            os.environ["VIBEOCR_MINERU_PYTHON"] = value
            applied["VIBEOCR_MINERU_PYTHON"] = value

        gpu_selected = states["paddle-gpu"].installed or states[
            "mineru-gpu"
        ].installed
        any_engine = paddle.installed or mineru.installed
        if any_engine:
            accelerator = "nvidia_cuda" if gpu_selected else "cpu"
            os.environ["VIBEOCR_RUNTIME_ACCELERATOR"] = accelerator
            applied["VIBEOCR_RUNTIME_ACCELERATOR"] = accelerator
        return applied


def describe_selection(
    *,
    paddle: bool,
    mineru: bool,
    gpu: bool,
) -> list[str]:
    """把用户勾选翻译成引擎环境 id 列表。"""

    selected: list[str] = []
    if paddle:
        selected.append("paddle-gpu" if gpu else "paddle-cpu")
    if mineru:
        selected.append("mineru-gpu" if gpu else "mineru-cpu")
    return selected


__all__ = [
    "ENGINE_ENV_SPECS",
    "EngineEnvError",
    "EngineEnvManager",
    "EngineEnvSpec",
    "EngineEnvState",
    "UvRunner",
    "describe_selection",
    "get_engine_env_spec",
    "runtime_profiles_root",
]
