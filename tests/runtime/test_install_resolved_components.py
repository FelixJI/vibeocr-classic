from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.install_resolved_components import _locked_sdk_wheels, install


def _release_input(tmp_path: Path) -> Path:
    root = tmp_path / "release-input"
    sdk = root / "protocol-sdk"
    runtime_protocol = root / "protocol"
    backend = root / "backend"
    for directory in (sdk, runtime_protocol, backend):
        directory.mkdir(parents=True)
    names = (
        "vibeocr_runtime_contracts-2.4.0-py3-none-any.whl",
        "vibeocr_runtime_client-2.4.0-py3-none-any.whl",
    )
    for name in names:
        (sdk / name).write_bytes(name.encode())
    (runtime_protocol / "vibeocr_runtime_contracts-2.3.0-py3-none-any.whl").write_bytes(
        b"runtime-protocol"
    )
    backend_wheel = "vibeocr_backend-0.10.0-py3-none-any.whl"
    (backend / backend_wheel).write_bytes(b"backend")
    (backend / "runtime-manifest.json").write_text(
        json.dumps({"backend_wheel": backend_wheel}), encoding="utf-8"
    )
    (root / "frontend-protocol-lock.json").write_text(
        json.dumps(
            {
                "version": "2.4.0",
                "artifacts": {name: {} for name in names},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_install_selects_frontend_sdk_wheels_not_runtime_protocol(
    tmp_path: Path, monkeypatch
) -> None:
    root = _release_input(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.install_resolved_components.subprocess.run",
        lambda command, **_kwargs: commands.append(command),
    )

    install(root)

    wheel_command = commands[0]
    assert wheel_command[:4] == [sys.executable, "-m", "pip", "install"]
    assert any("contracts-2.4.0" in item for item in wheel_command)
    assert any("client-2.4.0" in item for item in wheel_command)
    assert all("contracts-2.3.0" not in item for item in wheel_command)
    assert all("vibeocr_backend" not in item for item in wheel_command)

    backend_fixture_command = commands[1]
    assert backend_fixture_command[:5] == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
    ]
    assert "vibeocr_backend-0.10.0" in backend_fixture_command[-1]


def test_sdk_wheel_selection_requires_exact_locked_pair(tmp_path: Path) -> None:
    root = _release_input(tmp_path)
    duplicate = (
        root / "protocol-sdk" / "vibeocr_runtime_client-2.4.0-2-py3-none-any.whl"
    )
    duplicate.write_bytes(b"duplicate")
    lock_path = root / "frontend-protocol-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["artifacts"][duplicate.name] = {}
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one vibeocr_runtime_client"):
        _locked_sdk_wheels(root)
