"""PySide Classic 冻结入口 smoke 门禁测试。"""

from __future__ import annotations

import importlib.util
import json
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
    assert captured["env"]["VIBEOCR_CLASSIC_DATA_ROOT"] == str(tmp_path / ".smoke-data")
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
