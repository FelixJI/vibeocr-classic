"""从仓内事实生成发布身份锁（component-lock / frontend-protocol-lock）。

融合形态不再从外部仓库解析后端 Release；两把锁改为描述本仓库自身：

- ``component-lock.json``：后端=本仓库内置（版本取 ``apps/vibeocr-backend/version.txt``），
  Protocol 版本取前端依赖的 SDK wheel（``tool.uv.sources`` 固定的 URL），
  required_capabilities 沿用 ``component-policy.json``；
- ``frontend-protocol-lock.json``：两只 SDK wheel 的来源 URL 与 SHA-256
  （与 vibeocr-protocol v2.9.0 Release 的 SHA256SUMS 一致）。

这两把锁继续作为发布资产（identity_asset）供 CD 校验协议兼容性。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Protocol SDK wheels：URL 与 SHA-256 来自 vibeocr-protocol v2.9.0 Release
# （SHA256SUMS）。与 apps/vibeocr-pyside/pyproject.toml 的 tool.uv.sources 一致。
SDK_ARTIFACTS = {
    "vibeocr_runtime_contracts": (
        "https://github.com/FelixJI/vibeocr-protocol/releases/download/v2.9.0/"
        "vibeocr_runtime_contracts-2.9.0-py3-none-any.whl",
        "b567867fd4ac503a0d575ccc689d4d4953968d65bbe008cbc95cb6f26c325013",
    ),
    "vibeocr_runtime_client": (
        "https://github.com/FelixJI/vibeocr-protocol/releases/download/v2.9.0/"
        "vibeocr_runtime_client-2.9.0-py3-none-any.whl",
        "4fdc519ee46c0dc5bd11828656cae1128597e4176d0ba50bef469b2c6426d7f8",
    ),
}
SDK_VERSION = "2.9.0"
PROTOCOL_REPOSITORY = "FelixJI/vibeocr-protocol"


def _protocol_version_from_frontend() -> str:
    pyproject = REPO_ROOT / "apps/vibeocr-pyside/pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    urls = [
        source.get("url", "")
        for source in data.get("tool", {}).get("uv", {}).get("sources", {}).values()
        if isinstance(source, dict)
    ]
    for url in urls:
        match = re.search(r"v2\.(\d+)\.(\d+)", url)
        if match:
            return f"2.{match.group(1)}.{match.group(2)}"
    return SDK_VERSION


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(
    *,
    output_dir: Path,
    backend_wheel: Path | None,
    backend_version: str | None,
    policy_path: Path,
) -> tuple[Path, Path]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    required_capabilities = list(policy["required_capabilities"])
    accelerator = str(policy.get("backend", {}).get("accelerator", "cpu"))

    protocol_version = _protocol_version_from_frontend()
    if protocol_version != SDK_VERSION:
        # 前端依赖升级了 SDK 而 SDK_ARTIFACTS 常量未同步：继续生成会发布
        # 自相矛盾的身份锁（宣称新版本、列出旧 wheel），必须 fail closed。
        raise SystemExit(
            f"Protocol SDK 版本不一致：前端解析到 {protocol_version}，"
            f"锁资产常量为 {SDK_VERSION}。请同步 SDK_ARTIFACTS 的 wheel "
            "URL 与 SHA-256（来源：对应 Release 的 SHA256SUMS）。"
        )

    backend_version = backend_version or (
        REPO_ROOT / "apps/vibeocr-backend/version.txt"
    ).read_text(encoding="utf-8").strip()
    artifact_sha256 = _sha256(backend_wheel) if backend_wheel else ""

    component_lock = {
        "schema_version": 1,
        "protocol": {
            "repository": PROTOCOL_REPOSITORY,
            "version": protocol_version,
        },
        "backend": {
            "repository": "FelixJI/vibeocr-classic",
            "version": backend_version,
            "artifact_sha256": artifact_sha256,
            "accelerator": accelerator,
        },
        "required_capabilities": required_capabilities,
    }
    frontend_lock = {
        "schema_version": 1,
        "repository": PROTOCOL_REPOSITORY,
        "version": protocol_version,
        "artifacts": {
            Path(url).name: {"url": url, "sha256": sha256}
            for url, sha256 in SDK_ARTIFACTS.values()
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    component_path = output_dir / "component-lock.json"
    frontend_path = output_dir / "frontend-protocol-lock.json"
    component_path.write_text(
        json.dumps(component_lock, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    frontend_path.write_text(
        json.dumps(frontend_lock, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return component_path, frontend_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "build/automation/workspace-locks",
    )
    parser.add_argument(
        "--backend-wheel",
        type=Path,
        default=None,
        help="构建出的 vibeocr-backend wheel（把其 SHA-256 写入组件锁）",
    )
    parser.add_argument("--backend-version", default=None)
    parser.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "component-policy.json",
    )
    args = parser.parse_args(argv)
    component_path, frontend_path = generate(
        output_dir=args.output_dir,
        backend_wheel=args.backend_wheel,
        backend_version=args.backend_version,
        policy_path=args.policy,
    )
    print(f"component-lock: {component_path}")
    print(f"frontend-protocol-lock: {frontend_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
