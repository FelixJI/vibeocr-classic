"""PySide Classic 冻结入口 smoke 门禁测试。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_pyside_artifact.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_pyside_artifact_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_startup_smoke_requires_t6_in_isolated_environment(
    monkeypatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    exe = tmp_path / "VibeOCR.exe"
    exe.write_bytes(b"MZ")
    captured = {}
    monkeypatch.setenv("PYTHONPATH", r"C:\workspace\apps\vibeocr-pyside\src")
    monkeypatch.setenv("PYTHONHOME", r"C:\workspace\.python")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\workspace\.venv")
    monkeypatch.setenv("VIBEOCR_REPOSITORY_ROOT", r"C:\workspace")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["stdout"] = kwargs["stdout"]
        captured["stderr"] = kwargs["stderr"]
        trace = Path(kwargs["env"]["VIBEOCR_STARTUP_TRACE"])
        trace.write_text(
            json.dumps(
                {
                    "T0": 0.0,
                    "T1": 0.1,
                    "T2": 0.2,
                    "T3": 0.3,
                    "T4": 0.4,
                    "T5": 0.5,
                    "T6": 0.6,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        module_file = tmp_path / "_internal" / "vibeocr" / "supervisor" / "main.py"
        module_file.parent.mkdir(parents=True)
        module_file.write_text("# bundled supervisor\n", encoding="utf-8")
        result = Path(kwargs["env"]["VIBEOCR_SELF_TEST_RESULT"])
        result.write_text(
            json.dumps(
                {
                    "supervisor_ready": True,
                    "module_file": str(module_file),
                    "python_executable": kwargs["env"]["VIBEOCR_SELF_TEST_PYTHON"],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier,
        "_prepare_smoke_python",
        lambda root: (
            Path(sys.executable),
            root / ".smoke-runtime/site-packages",
        ),
    )

    verifier._verify_frozen_startup(tmp_path)

    assert captured["command"] == [str(exe)]
    assert captured["env"]["VIBEOCR_SELF_TEST_SMOKE"] == "t6"
    assert captured["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["VIBEOCR_SELF_TEST_PYTHON"] == sys.executable
    smoke_data = Path(captured["env"]["VIBEOCR_CLASSIC_DATA_ROOT"])
    assert smoke_data.parent == tmp_path
    assert smoke_data.name.startswith(".smoke-data-")
    assert captured["env"]["VIBEOCR_CLASSIC_TEST_MODE"] == "artifact-smoke"
    assert (
        captured["env"]["VIBEOCR_CLASSIC_TEST_NONCE"]
        == smoke_data.name.removeprefix(".smoke-data-")
    )
    smoke_pythonpath = Path(captured["env"]["PYTHONPATH"])
    assert ".smoke-runtime" in smoke_pythonpath.parts
    assert smoke_pythonpath.parts[-1] == "site-packages"
    assert "PYTHONHOME" not in captured["env"]
    assert "VIRTUAL_ENV" not in captured["env"]
    assert "VIBEOCR_REPOSITORY_ROOT" not in captured["env"]
    assert captured["stdout"] is not verifier.subprocess.PIPE
    assert captured["stderr"] is not verifier.subprocess.PIPE
    assert not (tmp_path / ".startup-smoke.jsonl").exists()
    assert not (tmp_path / ".startup-smoke.stdout.log").exists()
    assert not (tmp_path / ".startup-smoke.stderr.log").exists()
    assert not (tmp_path / ".startup-smoke-result.json").exists()


def test_frozen_startup_smoke_rejects_trace_that_stops_at_t3(
    monkeypatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")

    def fake_run(*args, **kwargs):
        trace = Path(kwargs["env"]["VIBEOCR_STARTUP_TRACE"])
        trace.write_text(
            json.dumps({"T0": 0.0, "T1": 0.1, "T2": 0.2, "T3": 0.3}) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier,
        "_prepare_smoke_python",
        lambda root: (Path(sys.executable), root / ".smoke-runtime/site-packages"),
    )

    with pytest.raises(RuntimeError, match="did not reach T6"):
        verifier._verify_frozen_startup(tmp_path)


def test_frozen_startup_smoke_rejects_supervisor_loaded_outside_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")

    def fake_run(*args, **kwargs):
        trace = Path(kwargs["env"]["VIBEOCR_STARTUP_TRACE"])
        trace.write_text(
            json.dumps({f"T{index}": index / 10 for index in range(7)}) + "\n",
            encoding="utf-8",
        )
        result = Path(kwargs["env"]["VIBEOCR_SELF_TEST_RESULT"])
        result.write_text(
            json.dumps(
                {
                    "supervisor_ready": True,
                    "module_file": r"C:\workspace\packages\vibeocr-backend"
                    r"\src\vibeocr\backend\supervisor\main.py",
                    "python_executable": sys.executable,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier,
        "_prepare_smoke_python",
        lambda root: (Path(sys.executable), root / ".smoke-runtime/site-packages"),
    )

    with pytest.raises(RuntimeError, match="outside extracted artifact"):
        verifier._verify_frozen_startup(tmp_path)


def test_frozen_startup_smoke_rejects_missing_trace(
    monkeypatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        verifier,
        "_prepare_smoke_python",
        lambda root: (Path(sys.executable), root / ".smoke-runtime/site-packages"),
    )

    with pytest.raises(RuntimeError, match="produced no trace"):
        verifier._verify_frozen_startup(tmp_path)


def test_portable_state_smoke_uses_default_portable_resolution(
    monkeypatch, tmp_path: Path
) -> None:
    """便携 smoke 在含空格/中文的便携根使用默认解析，不注入状态根。"""
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")
    captured: list[dict] = []

    def fake_run(command, **kwargs):
        env = kwargs["env"]
        portable_root = Path(kwargs["cwd"])
        captured.append({"command": command, "env": env, "cwd": portable_root})
        if "VIBEOCR_SILENT_PORTABLE_ERROR" in env:
            Path(env["VIBEOCR_SELF_TEST_RESULT"]).write_text(
                json.dumps({"portable_state_error": "VibeOCR 状态目录不可用：blocked"}),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=2, stderr=b"")
        state = portable_root / "state"
        for relative in (
            "config",
            "logs",
            "temp/clipboard",
            "web/qtwebengine/cache",
            "web/qtwebengine/persistent",
        ):
            (state / relative).mkdir(parents=True, exist_ok=True)
        (state / "logs" / "vibeocr-bootstrap.log").write_text(
            "bootstrap", encoding="utf-8"
        )
        Path(env["VIBEOCR_SELF_TEST_RESULT"]).write_text(
            json.dumps({"qt_pdf_created": True}), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    verifier._verify_portable_state_smoke(tmp_path)

    assert len(captured) == 2
    blocked, happy = captured
    # 必须运行副本内的 exe（便携根解析跟随 sys.executable，不是 cwd）
    assert happy["command"] == [str(happy["cwd"] / "VibeOCR.exe")]
    assert "VIBEOCR_SILENT_PORTABLE_ERROR" in blocked["env"]
    assert " " in happy["cwd"].name and any(
        "\u4e00" <= ch <= "\u9fff" for ch in happy["cwd"].name
    )
    assert "VIBEOCR_CLASSIC_DATA_ROOT" not in happy["env"]
    assert happy["env"]["LOCALAPPDATA"].endswith(".smoke-localappdata")
    assert happy["env"]["QT_QPA_PLATFORM"] == "offscreen"
    # 临时 smoke 父目录（含复制便携根与监控目录）整体清理
    assert not happy["cwd"].exists()
    assert not happy["cwd"].parent.exists()


def test_portable_state_smoke_requires_fail_closed_exit(
    monkeypatch, tmp_path: Path
) -> None:
    """state 被占据但入口未 fail closed（exit != 2 或无明确原因）必须拒绝。"""
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="fail-closed"):
        verifier._verify_portable_state_smoke(tmp_path)


class _FakeOfflineClient:
    def __init__(self, *, capabilities: tuple[str, ...], root: Path) -> None:
        self.negotiated_capabilities = capabilities
        self._root = root
        self.ensure_calls: list[dict] = []
        self.inspect_required: tuple[str, ...] | None = None

    def required_capabilities(self) -> tuple[str, ...]:
        return ("ocr.engine-selection.v1", "runtime.component-selection.v1")

    def inspect(self, **kwargs) -> None:
        # 复刻 Runtime Host 语义：negotiated 回显请求的 required 集
        required = tuple(kwargs.get("required_capabilities") or ())
        self.inspect_required = required
        if required:
            self.negotiated_capabilities = tuple(
                name for name in required if name in self.negotiated_capabilities
            )
        backend = self._root / "backend"
        backend.mkdir(parents=True, exist_ok=True)
        (backend / "runtime-manifest.json").write_text(
            json.dumps({"backend_version": "0.12.1"}), encoding="utf-8"
        )

    def ensure(self, **kwargs):
        self.ensure_calls.append(kwargs)
        runtime = self._root / "state" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "python.exe").write_bytes(b"py")
        return SimpleNamespace(python_executable=str(runtime / "python.exe"))


def test_offline_base_smoke_skips_without_component_selection_capability(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """v0.12.0 未协商能力：输出原因跳过，不执行任何安装。"""
    verifier = _load_verifier()
    client = _FakeOfflineClient(capabilities=("runtime.maintenance.v2",), root=tmp_path)

    result = verifier._verify_offline_base_smoke(
        tmp_path, tmp_path / "installer.exe", client_factory=lambda: client
    )

    assert result == "skipped"
    assert client.ensure_calls == []
    assert "does not negotiate" in capsys.readouterr().out


def test_offline_base_smoke_enforces_offline_intent_and_reuse(
    monkeypatch, tmp_path: Path
) -> None:
    """能力协商通过：三次 base-only ensure 均带显式空安装范围且断网。"""
    verifier = _load_verifier()
    client = _FakeOfflineClient(
        capabilities=(
            "runtime.maintenance.v2",
            "runtime.component-selection.v1",
        ),
        root=tmp_path,
    )
    seen_proxies: list[dict] = []

    real_ensure = client.ensure

    def spy_ensure(**kwargs):
        seen_proxies.append(
            {name: os.environ.get(name) for name in ("http_proxy", "https_proxy")}
        )
        return real_ensure(**kwargs)

    client.ensure = spy_ensure
    for name in ("component-lock.json", "frontend-protocol-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    result = verifier._verify_offline_base_smoke(
        tmp_path, tmp_path / "installer.exe", client_factory=lambda: client
    )

    assert result == "enforced"
    assert len(client.ensure_calls) == 3
    assert all(call.get("install_component_ids") == () for call in client.ensure_calls)
    assert all(
        proxy == "http://127.0.0.1:9"
        for proxies in seen_proxies
        for proxy in proxies.values()
    )


def test_offline_base_smoke_runs_supervisor_rapidocr_pdf_probe_twice(
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    client = _FakeOfflineClient(
        capabilities=(
            "runtime.maintenance.v2",
            "runtime.component-selection.v1",
        ),
        root=tmp_path,
    )
    probes: list[Path] = []
    for name in ("component-lock.json", "frontend-protocol-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    result = verifier._verify_offline_base_smoke(
        tmp_path,
        tmp_path / "installer.exe",
        client_factory=lambda: client,
        runtime_probe=lambda _launch, root: probes.append(root),
    )

    assert result == "enforced"
    assert probes == [tmp_path, tmp_path]


def test_offline_base_smoke_uses_post_probe_tree_as_reensure_baseline(
    tmp_path: Path,
) -> None:
    """Runtime 首次启动可写入树；未改树的后续 ensure 不应被误报。"""
    verifier = _load_verifier()
    client = _FakeOfflineClient(
        capabilities=("runtime.component-selection.v1",), root=tmp_path
    )
    for name in ("component-lock.json", "frontend-protocol-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    def write_runtime_cache(_launch, _root: Path) -> None:
        runtime_cache = tmp_path / "state" / "runtime" / "runtime-cache.bin"
        runtime_cache.write_bytes(b"runtime-owned")

    result = verifier._verify_offline_base_smoke(
        tmp_path,
        tmp_path / "installer.exe",
        client_factory=lambda: client,
        runtime_probe=write_runtime_cache,
    )

    assert result == "enforced"
    assert len(client.ensure_calls) == 3


def test_offline_base_smoke_detects_rewrite_on_reensure(
    monkeypatch, tmp_path: Path
) -> None:
    """幂等 ensure 重写 runtime 树（重复下载）时必须失败。"""
    verifier = _load_verifier()
    client = _FakeOfflineClient(
        capabilities=("runtime.component-selection.v1",), root=tmp_path
    )
    calls = {"n": 0}
    real_ensure = client.ensure

    def grow_ensure(**kwargs):
        calls["n"] += 1
        result = real_ensure(**kwargs)
        if calls["n"] == 2:
            (tmp_path / "state" / "runtime" / f"extra-{calls['n']}.whl").write_bytes(
                b"x"
            )
        return result

    client.ensure = grow_ensure
    for name in ("component-lock.json", "frontend-protocol-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="re-download|rewrote"):
        verifier._verify_offline_base_smoke(
            tmp_path, tmp_path / "installer.exe", client_factory=lambda: client
        )


def test_offline_base_smoke_detects_same_size_content_rewrite(
    tmp_path: Path,
) -> None:
    """发布边界快照必须发现路径和大小不变的 runtime 内容覆盖。"""
    verifier = _load_verifier()
    client = _FakeOfflineClient(
        capabilities=("runtime.component-selection.v1",), root=tmp_path
    )
    calls = {"n": 0}
    real_ensure = client.ensure

    def rewrite_ensure(**kwargs):
        calls["n"] += 1
        result = real_ensure(**kwargs)
        if calls["n"] == 2:
            (tmp_path / "state" / "runtime" / "python.exe").write_bytes(b"zz")
        return result

    client.ensure = rewrite_ensure
    for name in ("component-lock.json", "frontend-protocol-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="re-download|rewrote"):
        verifier._verify_offline_base_smoke(
            tmp_path, tmp_path / "installer.exe", client_factory=lambda: client
        )
