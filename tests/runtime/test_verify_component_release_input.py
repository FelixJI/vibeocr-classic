from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.bind_component_releases import bind_protocol_release
from scripts.verify_component_release_input import verify


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _release_input(tmp_path: Path) -> Path:
    root = tmp_path / "release-input"
    protocol = root / "protocol"
    backend = root / "backend"
    sdk = root / "protocol-sdk"
    for directory in (protocol, backend, sdk):
        directory.mkdir(parents=True)
    (protocol / "runtime.whl").write_bytes(b"runtime")
    (backend / "backend.whl").write_bytes(b"backend")
    sdk_wheel = sdk / "vibeocr_runtime_contracts-2.4.0-py3-none-any.whl"
    sdk_wheel.write_bytes(b"sdk")
    (sdk / "release-manifest.json").write_text(
        json.dumps(
            {
                "protocol_version": "2.4.0",
                "artifacts": {
                    sdk_wheel.name: {
                        "sha256": _sha(sdk_wheel.read_bytes()),
                        "size": sdk_wheel.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "component-lock.json").write_text(
        json.dumps(
            {
                "protocol": {
                    "repository": "FelixJI/vibeocr-protocol",
                    "version": "2.3.0",
                },
                "backend": {
                    "repository": "FelixJI/vibeocr-backend",
                    "version": "0.10.0",
                },
            }
        ),
        encoding="utf-8",
    )
    bind_protocol_release(
        release_dir=sdk,
        repository="FelixJI/vibeocr-protocol",
        version="2.4.0",
        output=root / "frontend-protocol-lock.json",
    )
    return root


def test_verify_proves_frontend_sdk_independently(monkeypatch, tmp_path: Path) -> None:
    root = _release_input(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.verify_component_release_input.subprocess.run",
        lambda command, **_kwargs: calls.append(command),
    )

    lock = verify(root)

    assert lock["protocol"]["version"] == "2.3.0"
    assert lock["frontend_protocol"]["version"] == "2.4.0"
    assert any("protocol-sdk" in command[3] for command in calls)


def test_verify_rejects_component_lock_in_frontend_lock_slot(
    monkeypatch, tmp_path: Path
) -> None:
    root = _release_input(tmp_path)
    monkeypatch.setattr(
        "scripts.verify_component_release_input.subprocess.run",
        lambda *_args, **_kwargs: None,
    )
    (root / "frontend-protocol-lock.json").write_bytes(
        (root / "component-lock.json").read_bytes()
    )

    with pytest.raises(ValueError, match="invalid frontend Protocol identity"):
        verify(root)


def test_verify_rejects_frontend_sdk_from_another_major(
    monkeypatch, tmp_path: Path
) -> None:
    root = _release_input(tmp_path)
    monkeypatch.setattr(
        "scripts.verify_component_release_input.subprocess.run",
        lambda *_args, **_kwargs: None,
    )
    lock_path = root / "frontend-protocol-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = "3.0.0"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ValueError, match="majors differ"):
        verify(root)
