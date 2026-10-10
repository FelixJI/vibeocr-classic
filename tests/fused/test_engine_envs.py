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
    assert "--default-index" in second
    # 缺省使用保存的/默认依赖下载源（清华镜像）。
    index_url = second[second.index("--default-index") + 1]
    assert index_url == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
    assert any(str(p).endswith("requirements-win-x64-paddle-cpu.lock") for p in second)
    third = fake.commands[2]
    assert "--no-deps" in third
    fourth = fake.commands[3]
    assert fourth[-1].startswith("vibeocr-runtime-contracts @ ")
    assert "Resolved 90 packages" in logs
    assert any("安装完成" in phase for phase in phases)


def test_ensure_respects_explicit_package_index(envs) -> None:
    manager, _ = envs
    fake = FakeUvRunner()
    manager._uv = fake

    manager.ensure("mineru-cpu", package_index_id="pypi")

    second = fake.commands[1]
    index_url = second[second.index("--default-index") + 1]
    assert index_url == "https://pypi.org/simple"


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


def _mark_installed(manager, root: Path, spec_id: str) -> None:
    """按当前安装身份写入标记文件与解释器，模拟已安装环境。"""
    import json

    env_root = root / spec_id
    (env_root / "Scripts").mkdir(parents=True, exist_ok=True)
    (env_root / "Scripts" / "python.exe").write_bytes(b"")
    (env_root / ".vibeocr-engine-env.json").write_text(
        json.dumps(
            {
                "spec_id": spec_id,
                "identity": manager._current_identity(),
            }
        ),
        encoding="utf-8",
    )


def test_apply_runtime_env_sets_discovery_vars(envs, monkeypatch) -> None:
    manager, root = envs
    # 手工构造“已安装”状态：目录 + 标记（含当前安装身份）+ 假 python 文件。
    paddle_root = root / "paddle-gpu"
    _mark_installed(manager, root, "paddle-gpu")
    mineru_root = root / "mineru-cpu"
    _mark_installed(manager, root, "mineru-cpu")

    monkeypatch.delenv("VIBEOCR_PADDLE_HOME", raising=False)
    monkeypatch.delenv("VIBEOCR_MINERU_PYTHON", raising=False)
    monkeypatch.delenv("VIBEOCR_RUNTIME_ACCELERATOR", raising=False)

    import os

    applied = manager.apply_runtime_env()

    assert applied["VIBEOCR_PADDLE_HOME"] == str(paddle_root)
    assert applied["VIBEOCR_MINERU_PYTHON"] == str(mineru_root / "Scripts" / "python.exe")
    assert os.environ["VIBEOCR_PADDLE_HOME"] == str(paddle_root)


def test_apply_runtime_env_projects_accelerator(envs, monkeypatch) -> None:
    manager, root = envs
    _mark_installed(manager, root, "paddle-cpu")
    _mark_installed(manager, root, "mineru-gpu")
    monkeypatch.delenv("VIBEOCR_RUNTIME_ACCELERATOR", raising=False)

    import os

    applied = manager.apply_runtime_env()
    # 任一 GPU 环境在位 → nvidia_cuda。
    assert applied["VIBEOCR_RUNTIME_ACCELERATOR"] == "nvidia_cuda"
    assert os.environ["VIBEOCR_RUNTIME_ACCELERATOR"] == "nvidia_cuda"


def test_apply_runtime_env_cpu_when_only_cpu_envs(envs, monkeypatch) -> None:
    manager, root = envs
    _mark_installed(manager, root, "paddle-cpu")
    monkeypatch.delenv("VIBEOCR_RUNTIME_ACCELERATOR", raising=False)

    applied = manager.apply_runtime_env()
    assert applied["VIBEOCR_RUNTIME_ACCELERATOR"] == "cpu"


def test_inspect_detects_identity_drift(envs, monkeypatch) -> None:
    """产品升级（清单/后端变化）后旧环境应判为未安装，引导重装。"""
    import json

    manager, root = envs
    env_root = root / "paddle-cpu"
    (env_root / "Scripts").mkdir(parents=True)
    (env_root / "Scripts" / "python.exe").write_bytes(b"")
    (env_root / ".vibeocr-engine-env.json").write_text(
        json.dumps(
            {
                "spec_id": "paddle-cpu",
                "identity": {"win-x64-paddle-cpu": "0" * 64},
            }
        ),
        encoding="utf-8",
    )

    assert manager.inspect()["paddle-cpu"].installed is False


def test_backend_distribution_points_at_workspace_package_root() -> None:
    from vibeocr.classic.engine_envs import backend_distribution

    dist = backend_distribution()
    assert (dist / "pyproject.toml").is_file()
    assert dist.name == "vibeocr-backend"


def test_describe_selection_combinations() -> None:
    assert describe_selection(paddle=False, mineru=False, gpu=False) == []
    assert describe_selection(paddle=True, mineru=False, gpu=False) == ["paddle-cpu"]
    assert describe_selection(paddle=True, mineru=False, gpu=True) == ["paddle-gpu"]
    assert describe_selection(paddle=False, mineru=True, gpu=True) == ["mineru-gpu"]
    assert describe_selection(paddle=True, mineru=True, gpu=True) == [
        "paddle-gpu",
        "mineru-gpu",
    ]


def _states(envs, installed_ids: set[str]) -> dict:
    from vibeocr.classic.engine_envs import EngineEnvState

    manager, _ = envs
    return {
        spec.id: EngineEnvState(
            spec_id=spec.id,
            installed=spec.id in installed_ids,
            env_root=manager.env_root(spec.id),
            python=manager.env_root(spec.id) / "Scripts" / "python.exe"
            if spec.id in installed_ids
            else None,
        )
        for spec in ENGINE_ENV_SPECS
    }


def test_device_framework_matches_engine_environments(envs) -> None:
    from vibeocr.classic.engine_envs import device_framework

    # 无任何引擎：基础内置，不冒充任何计算设备。
    assert device_framework(_states(envs, set())) is None
    assert device_framework(_states(envs, {"paddle-cpu"})) == "cpu"
    # 任一 GPU 环境在位即 GPU（与 apply_runtime_env 的设备意图一致）。
    assert device_framework(_states(envs, {"paddle-cpu", "mineru-gpu"})) == "gpu"
    assert device_framework(_states(envs, {"mineru-gpu"})) == "gpu"


def test_framework_profile_maps_display_vocabulary() -> None:
    from vibeocr.classic.engine_envs import framework_profile

    assert framework_profile(None) == "win-x64-base"
    assert framework_profile("cpu") == "win-x64-cpu"
    assert framework_profile("gpu") == "win-x64-cu126"


def test_uv_runner_resolve_prefers_env(monkeypatch, tmp_path) -> None:
    fake_uv = tmp_path / "uv.exe"
    fake_uv.write_bytes(b"")
    monkeypatch.setenv("VIBEOCR_UV_BIN", str(fake_uv))
    runner = UvRunner()
    assert runner.resolve() == str(fake_uv)
