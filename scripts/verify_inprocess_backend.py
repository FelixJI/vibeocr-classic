"""进程内后端 e2e 验证：ASGI 直连构建、健康检查与能力核对。

取代旧的 ``verify_component_release_input``（外部 Release 资产校验）：
融合形态的后端在本仓库内，验证对象就是“当前环境的后端能否以与应用
进程内一致的方式组装并通过 Protocol v2 健康检查”。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps/vibeocr-pyside/src"))

POLICY_PATH = REPO_ROOT / "component-policy.json"


async def _verify() -> list[str]:
    import httpx

    from vibeocr.backend.supervisor.bootstrap import (
        BootstrapHandle,
        generate_session_token,
        new_instance_id,
    )
    from vibeocr.backend.supervisor.composition import build_supervisor
    from vibeocr.backend.supervisor.settings_store import RuntimeSettingsStore
    from vibeocr.backend.supervisor.app import create_app
    from vibeocr.runtime_contracts.generated import ALL_CAPABILITIES

    required = set(
        json.loads(POLICY_PATH.read_text(encoding="utf-8"))["required_capabilities"]
    )

    problems: list[str] = []
    token = generate_session_token()
    with tempfile.TemporaryDirectory(prefix="vibeocr-e2e-") as temp:
        module, _ = build_supervisor(
            instance_id=new_instance_id(),
            stager_root=Path(temp) / "staging",
            bootstrap_handle=BootstrapHandle(token),
            with_pdf_adapter=False,
            settings_store=RuntimeSettingsStore(Path(temp) / "settings.json"),
        )
        try:
            app = create_app(module, token)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://supervisor.local"
            ) as client:
                response = await client.get(
                    "/v2/health",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status_code != 200:
                problems.append(f"/v2/health returned {response.status_code}")
            elif response.json().get("ready") is not True:
                problems.append(f"/v2/health not ready: {response.text[:200]}")

            missing = sorted(required - set(ALL_CAPABILITIES))
            if missing:
                problems.append(
                    "缺少产品必需 capability: " + ", ".join(missing)
                )
        finally:
            module.shutdown_now()
    return problems


def main() -> int:
    try:
        problems = asyncio.run(_verify())
    except Exception as exc:
        print(f"[verify-inprocess-backend] 失败：{exc}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(f"[verify-inprocess-backend] {problem}", file=sys.stderr)
        return 1
    print("[verify-inprocess-backend] OK：进程内后端组装、健康检查与能力核对通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
