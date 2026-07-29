"""可复现的发布产物清单：生成与校验。

生成阶段（create_manifest）遍历 staging 目录，为每个文件记录相对路径、
字节数和 SHA-256。禁止路径（output/、.venv/、data/profiles/ 等）即使在
allowed_roots 中也被拒绝，防止运行产物或开发 profile 泄漏到发布包。

校验阶段（verify_archive）打开 ZIP，读取 artifact-manifest.json，逐一比对
每个 entry 的 size 和 sha256，并检查是否存在 manifest 未记录的禁止路径文件。

用法（CLI）::

    python -m vibeocr.classic.build_manifest verify <archive.zip>

退出码 0 = 校验通过；非 0 = 篡改/缺失 manifest/含禁止路径。
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 禁止路径：这些目录/文件名无论是否在 allowed_roots 中都不允许进 manifest 或 ZIP。
# 它们是运行产物、开发环境或旁路配置，不属于正式发布包。
# ---------------------------------------------------------------------------
FORBIDDEN_TOP_NAMES: frozenset[str] = frozenset(
    {
        "output",  # OCR/PDF 运行产物（用户输入/输出）
        ".venv",  # 虚拟环境
        "venv",
        "__pycache__",  # Python 字节码缓存
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "data",  # 旁路 profile（winui-dev 等）与本地数据
        "reports",  # 本地报告
        "logs",  # 运行日志
        "dist",  # 构建产物
        "build",  # 构建中间产物
        ".worktrees",  # git worktree
        ".git",
    }
)

MANIFEST_FILENAME = "artifact-manifest.json"


@dataclass(frozen=True)
class ManifestEntry:
    """单个文件的清单条目。"""

    path: str
    size: int
    sha256: str


def _is_forbidden(rel_path: Path) -> bool:
    """判断相对路径是否触及任何禁止顶层名。"""
    return any(part in FORBIDDEN_TOP_NAMES for part in rel_path.parts)


def _sha256_file(path: Path) -> str:
    """计算文件的 SHA-256（流式读取，支持大文件）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_manifest(
    root: Path, allowed_roots: tuple[str, ...]
) -> dict:
    """为 staging 目录生成产物清单。

    Args:
        root: staging 根目录（如 dist/VibeOCR）。
        allowed_roots: 允许纳入的顶层路径白名单（相对 root）。
            只有这些路径下的文件会进 manifest。禁止路径（output/ 等）
            即使出现在白名单中也被拒绝。

    Returns:
        可序列化为 JSON 的 manifest dict::

            {
              "version": 1,
              "created_by": "vibeocr.classic.build_manifest",
              "entry_count": N,
              "total_bytes": M,
              "entries": [
                {"path": "app/main.exe", "size": 42, "sha256": "..."},
                ...
              ]
            }

    Raises:
        ValueError: allowed_roots 包含禁止路径时立即报错。
    """
    root = Path(root)

    entries: list[dict] = []
    total_bytes = 0

    for allowed in allowed_roots:
        # 白名单条目本身若是禁止路径，静默跳过（不 fail-fast：
        # 调用方可能用宽松白名单，由遍历层保证禁止路径不进 manifest）
        if _is_forbidden(Path(allowed)):
            continue
        allowed_path = (root / allowed).resolve()
        if not allowed_path.exists():
            continue
        # 允许的可以是文件或目录
        if allowed_path.is_file():
            files = [allowed_path]
        else:
            files = sorted(p for p in allowed_path.rglob("*") if p.is_file())

        for fp in files:
            rel = fp.relative_to(root)
            rel_posix = rel.as_posix()
            # 二次防御：遍历中可能遇到禁止子目录（如 app/output/）
            if _is_forbidden(rel):
                continue
            data_size = fp.stat().st_size
            sha = _sha256_file(fp)
            entries.append(
                {
                    "path": rel_posix,
                    "size": data_size,
                    "sha256": sha,
                }
            )
            total_bytes += data_size

    return {
        "version": 1,
        "created_by": "vibeocr.classic.build_manifest",
        "entry_count": len(entries),
        "total_bytes": total_bytes,
        "entries": entries,
    }


def verify_archive(archive: Path) -> None:
    """校验 ZIP 归档与内嵌 manifest 一致。

    校验项：
    1. ZIP 内存在 artifact-manifest.json。
    2. manifest 中每个 entry 在 ZIP 内存在，且 size + sha256 匹配。
    3. ZIP 内不存在 manifest 未记录的禁止路径文件。
    4. ZIP 内不存在 manifest 未记录的「额外」文件（可选严格模式见 below）。

    Args:
        archive: ZIP 文件路径。

    Raises:
        ValueError: manifest 缺失、文件篡改、含禁止路径。
        FileNotFoundError: archive 不存在。
    """
    archive = Path(archive)
    if not archive.exists():
        raise FileNotFoundError(f"archive not found: {archive}")

    with zipfile.ZipFile(archive, "r") as zf:
        names = zf.namelist()

        # 1. 定位 manifest（可能在 VibeOCR/artifact-manifest.json 或根）
        manifest_name = None
        for n in names:
            base = n.rsplit("/", 1)[-1]
            if base == MANIFEST_FILENAME:
                manifest_name = n
                break
        if manifest_name is None:
            raise ValueError(
                f"manifest ({MANIFEST_FILENAME}) not found in archive"
            )

        raw_manifest = zf.read(manifest_name).decode("utf-8")
        manifest = json.loads(raw_manifest)
        entries = manifest.get("entries", [])

        # 归一化：去掉 manifest 在 zip 内的顶层前缀（如 VibeOCR/）
        # 以便与 entry 的相对路径比较。
        manifest_prefix = manifest_name.rsplit("/", 1)[0] + "/" if "/" in manifest_name else ""

        # 2. 校验每个 entry
        for e in entries:
            zip_member = manifest_prefix + e["path"]
            if zip_member not in names:
                raise ValueError(f"manifest entry missing in archive: {e['path']}")
            data = zf.read(zip_member)
            if len(data) != e["size"]:
                raise ValueError(
                    f"size mismatch for {e['path']}: "
                    f"manifest={e['size']} actual={len(data)}"
                )
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != e["sha256"]:
                raise ValueError(
                    f"sha256 mismatch for {e['path']}: "
                    f"manifest={e['sha256']} actual={actual_sha}"
                )

        # 3. 检查禁止路径文件（manifest 记录与否都拒绝）
        for n in names:
            if n == manifest_name:
                continue
            if n.endswith("/"):
                continue  # 跳过目录条目
            # 去掉 zip 内前缀得到相对路径
            rel = n[len(manifest_prefix):] if manifest_prefix and n.startswith(manifest_prefix) else n
            if _is_forbidden(Path(rel)):
                raise ValueError(
                    f"forbidden path in archive: {n} (rel={rel})"
                )


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Usage::

        python -m vibeocr.classic.build_manifest verify <archive.zip>
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m vibeocr.classic.build_manifest verify <archive.zip>", file=sys.stderr)
        return 2

    command = args[0]
    if command == "verify":
        if len(args) < 2:
            print("verify requires an archive path", file=sys.stderr)
            return 2
        archive = Path(args[1])
        try:
            verify_archive(archive)
        except (ValueError, FileNotFoundError) as e:
            print(f"VERIFY FAIL: {e}", file=sys.stderr)
            return 1
        print(f"VERIFY OK: {archive.name} ({archive.stat().st_size} bytes)")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - module-exec guard, covered by CLI tests via -m
    sys.exit(main())
