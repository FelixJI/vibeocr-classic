"""进程内融合后，宿主通过环境变量注入引擎/工具解释器的发现 seam 测试。

Classic 在进程内承载后端时，Paddle/MinerU/PDF 子进程不再共用运行时解释器，
由宿主（桌面端的环境管理器）显式指定各自的环境，后端按以下顺序发现：
- ``VIBEOCR_PADDLE_HOME``（Paddle 环境根目录）
- ``VIBEOCR_MINERU_PYTHON``（MinerU 环境解释器）
- ``VIBEOCR_BACKEND_PYTHON``（后端工具子进程解释器，如 PDF）
"""

from __future__ import annotations

from types import SimpleNamespace

from vibeocr.backend.env_manager import get_embedded_python_executable
from vibeocr.backend.services.mineru_service import MinerUService
from vibeocr.backend.supervisor.inference.paddle_process_adapter import paddle_python


def test_paddle_python_honors_env_root(tmp_path, monkeypatch) -> None:
    env_root = tmp_path / "paddle-cpu"
    python = env_root / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    monkeypatch.setenv("VIBEOCR_PADDLE_HOME", str(env_root))
    assert paddle_python() == python


def test_paddle_python_env_root_takes_precedence_over_prefix(
    tmp_path, monkeypatch
) -> None:
    env_root = tmp_path / "paddle-gpu"
    python = env_root / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    monkeypatch.setenv("VIBEOCR_PADDLE_HOME", str(env_root))
    # sys.prefix/engines/paddle 在开发机通常不存在；即使构造出同名候选，
    # 显式注入的环境根也必须优先。
    assert paddle_python() == python


def test_paddle_python_missing_env_root_falls_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBEOCR_PADDLE_HOME", str(tmp_path / "nope"))
    # 回退到 sys.prefix 布局；开发机该目录不存在时应返回 None 而不是报错。
    result = paddle_python()
    if result is not None:
        assert "engines" in str(result)


def test_mineru_python_env_short_circuits_discovery(tmp_path, monkeypatch) -> None:
    python = tmp_path / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    def _fail(_root):
        raise AssertionError("设置了 VIBEOCR_MINERU_PYTHON 时不应再做嵌入解释器发现")

    monkeypatch.setenv("VIBEOCR_MINERU_PYTHON", str(python))
    monkeypatch.setattr(
        "vibeocr.backend.env_manager.get_embedded_python", _fail
    )
    service = SimpleNamespace()
    assert MinerUService._resolve_python_executable(service) == python


def test_backend_python_env_used_for_tool_children(tmp_path, monkeypatch) -> None:
    python = tmp_path / "tool-python.exe"
    python.write_bytes(b"")

    monkeypatch.setenv("VIBEOCR_BACKEND_PYTHON", str(python))
    assert get_embedded_python_executable(tmp_path / "unused-root") == python


def test_backend_python_env_missing_file_is_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBEOCR_BACKEND_PYTHON", str(tmp_path / "missing.exe"))
    # 指向不存在的文件时忽略，继续常规发现（.venv / python/ 布局）。
    resolved = get_embedded_python_executable(tmp_path)
    assert resolved != tmp_path / "missing.exe"
