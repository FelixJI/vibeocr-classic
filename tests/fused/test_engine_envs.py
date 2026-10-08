"""引擎环境管理器（uv）单元测试：命令序列、日志转发、状态与选择翻译。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from vibeocr.classic.engine_envs import (
    ENGINE_ENV_SPECS,
    EngineEnvError,
    EngineEnvManager,
    UvRunner,
    describe_selection,
    runtime_profiles_root,
)


class FakeUvRunner:
    """记录命令、模拟 venv 创建解释器、按需吐日志行、可注入取消。"""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.lines = lines or []
        self.fail_on: set[int] = set()

    def run(self, args, *, on_line=None, cancel_event=None, env=None):
        import subprocess

        self.commands.append(list(args))
        if len(self.commands) in self.fail_on:
            raise EngineEnvError("boom")
        if args[0] == "venv":
            root = Path(args[-1])
            python = root / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        for line in self.lines:
            if on_line is not None:
                on_line(line)
        if cancel_event is not None and cancel_event.is_set():
            raise EngineEnvError("安装已取消")
        return subprocess.CompletedProcess(["uv", *args], 0, stdout="")


@pytest.fixture()
def envs(tmp_path):
    manager = EngineEnvManager(tmp_path / "envs")
    return manager, tmp_path / "envs"


def test_all_specs_have_lock_files_in_repo() -> None:
    root = runtime_profiles_root()
    for spec in ENGINE_ENV_SPECS:
        assert (root / spec.profile / spec.lock_file_name).is_file(), spec.id


def test_inspect_empty(envs) -> None:
    manager, _ = envs
    states = manager.inspect()
    assert set(states) == {"paddle-cpu", "paddle-gpu", "mineru-cpu", "mineru-gpu"}
    assert all(not state.installed for state in states.values())


def test_ensure_paddle_runs_full_command_sequence(envs) -> None:
    manager, _ = envs
    fake = FakeUvRunner(lines=["Resolved 90 packages", "Installed 90 packages"])
    logs: list[str] = []
    phases: list[str] = []
    manager._uv = fake

    state = manager.ensure(
        "paddle-cpu", on_phase=phases.append, on_log=logs.append
    )

    assert state.installed
    assert state.python is not None and state.python.is_file()
    assert len(fake.commands) == 4
    first = fake.commands[0]
    assert first[:3] == ["venv", "--python", "3.13"]
    second = fake.commands[1]
    assert second[0] == "pip"
    assert "--require-hashes" in second
    assert any(str(p).endswith("requirements-win-x64-paddle-cpu.lock") for p in second)
    third = fake.commands[2]
    assert "--no-deps" in third
    fourth = fake.commands[3]
    assert fourth[-1].startswith("vibeocr-runtime-contracts @ ")
    assert "Resolved 90 packages" in logs
    assert any("安装完成" in phase for phase in phases)


def test_ensure_mineru_skips_backend_install(envs) -> None:
    manager, _ = envs
    fake = FakeUvRunner()
    manager._uv = fake

    manager.ensure("mineru-gpu")

    assert len(fake.commands) == 2
    assert any("cu126" in str(p) for p in fake.commands[1])


def test_ensure_cancel_raises(envs) -> None:
    manager, _ = envs
    manager._uv = FakeUvRunner()
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(EngineEnvError):
        manager.ensure("paddle-cpu", cancel_event=cancel)


def test_ensure_missing_lock_fails(envs, monkeypatch) -> None:
    manager, _ = envs
    monkeypatch.setattr(
        "vibeocr.classic.engine_envs.runtime_profiles_root",
        lambda: Path("Z:/nonexistent"),
    )
    with pytest.raises(EngineEnvError):
        manager.ensure("paddle-cpu")


def test_remove_deletes_directory(envs) -> None:
    manager, root = envs
    target = root / "paddle-cpu"
    target.mkdir(parents=True)
    (target / "marker.txt").write_text("x", encoding="utf-8")

    manager.remove("paddle-cpu")
    assert not target.exists()


def test_apply_runtime_env_sets_discovery_vars(envs, monkeypatch) -> None:
    manager, root = envs
    # 手工构造“已安装”状态：目录 + 标记 + 假 python 文件。
    paddle_root = root / "paddle-gpu"
    (paddle_root / "Scripts").mkdir(parents=True)
    (paddle_root / "Scripts" / "python.exe").write_bytes(b"")
    (paddle_root / ".vibeocr-engine-env.json").write_text("{}", encoding="utf-8")
    mineru_root = root / "mineru-cpu"
    (mineru_root / "Scripts").mkdir(parents=True)
    (mineru_root / "Scripts" / "python.exe").write_bytes(b"")
    (mineru_root / ".vibeocr-engine-env.json").write_text("{}", encoding="utf-8")

    monkeypatch.delenv("VIBEOCR_PADDLE_HOME", raising=False)
    monkeypatch.delenv("VIBEOCR_MINERU_PYTHON", raising=False)

    import os

    applied = manager.apply_runtime_env()

    assert applied["VIBEOCR_PADDLE_HOME"] == str(paddle_root)
    assert applied["VIBEOCR_MINERU_PYTHON"] == str(mineru_root / "Scripts" / "python.exe")
    assert os.environ["VIBEOCR_PADDLE_HOME"] == str(paddle_root)


def test_describe_selection_combinations() -> None:
    assert describe_selection(paddle=False, mineru=False, gpu=False) == []
    assert describe_selection(paddle=True, mineru=False, gpu=False) == ["paddle-cpu"]
    assert describe_selection(paddle=True, mineru=False, gpu=True) == ["paddle-gpu"]
    assert describe_selection(paddle=False, mineru=True, gpu=True) == ["mineru-gpu"]
    assert describe_selection(paddle=True, mineru=True, gpu=True) == [
        "paddle-gpu",
        "mineru-gpu",
    ]


def test_uv_runner_resolve_prefers_env(monkeypatch, tmp_path) -> None:
    fake_uv = tmp_path / "uv.exe"
    fake_uv.write_bytes(b"")
    monkeypatch.setenv("VIBEOCR_UV_BIN", str(fake_uv))
    runner = UvRunner()
    assert runner.resolve() == str(fake_uv)
