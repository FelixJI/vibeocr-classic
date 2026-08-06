"""Install verified Classic CI wheels without editable or path development modes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _locked_sdk_wheels(root: Path) -> tuple[Path, Path]:
    lock = json.loads(
        (root / "frontend-protocol-lock.json").read_text(encoding="utf-8")
    )
    artifacts = lock.get("artifacts")
    version = lock.get("version")
    if not isinstance(artifacts, dict) or not isinstance(version, str):
        raise ValueError("frontend Protocol lock is incomplete")
    sdk_root = root / "protocol-sdk"
    wheels: list[Path] = []
    for distribution in ("vibeocr_runtime_contracts", "vibeocr_runtime_client"):
        matches = [
            sdk_root / name
            for name in artifacts
            if isinstance(name, str)
            and name.startswith(f"{distribution}-{version}-")
            and name.endswith(".whl")
            and (sdk_root / name).is_file()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"frontend Protocol lock must select exactly one {distribution} wheel"
            )
        wheels.append(matches[0])
    return wheels[0], wheels[1]


def _locked_backend_wheel(root: Path) -> Path:
    manifest = json.loads(
        (root / "backend" / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    wheel_name = manifest.get("backend_wheel")
    if not isinstance(wheel_name, str):
        raise ValueError("Backend runtime manifest has no wheel")
    wheel = root / "backend" / wheel_name
    if not wheel.is_file():
        raise ValueError("Backend runtime manifest wheel is missing")
    return wheel


def install(release_input: Path) -> None:
    root = release_input.resolve(strict=True)
    sdk_wheels = _locked_sdk_wheels(root)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", *(str(path) for path in sdk_wheels)],
        check=True,
    )
    # Classic 的生产包只依赖 Protocol SDK。完整测试仍覆盖已解析的正式
    # Backend 作为跨进程/legacy fixture，但它的 Python metadata 不能参与前端
    # SDK 求解，否则旧 Runtime 的 exact contracts lock 会重新制造 Backend-first
    # 发布顺序。Backend Release 自身已在上游完成依赖闭包验证。
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            str(_locked_backend_wheel(root)),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "pytest",
            "pytest-asyncio",
            "pytest-qt",
            "./apps/vibeocr-pyside",
        ],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-input", type=Path, required=True)
    args = parser.parse_args(argv)
    install(args.release_input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
