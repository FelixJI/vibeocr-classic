"""build_manifest 模块测试（发布产物清单生成 + ZIP 校验 + CLI）。

覆盖成功路径、失败路径与边界条件。重点验证：
- 禁止路径（output/.venv/data 等）即使在白名单中也被拒绝；
- manifest 缺失 / size 不匹配 / sha256 篡改 / 含禁止路径的失败路径；
- CLI main() 各退出码；
- 带顶层前缀（VibeOCR/）的归一化。

辅助：用 tmp_path 构造 staging 目录 + zipfile 造测试归档。
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from vibeocr.classic.build_manifest import (
    FORBIDDEN_TOP_NAMES,
    MANIFEST_FILENAME,
    ManifestEntry,
    _is_forbidden,
    _sha256_file,
    create_manifest,
    main,
    verify_archive,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: bytes = b"hello") -> Path:
    """写入文件（含父目录），返回该路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_manifest(entries):
    """构造 manifest dict。"""
    return {
        "version": 1,
        "created_by": "vibeocr.classic.build_manifest",
        "entry_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "entries": entries,
    }


def _entry(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _build_zip(zip_path: Path, members: dict[str, bytes]) -> Path:
    """构造一个含指定成员的 ZIP。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zip_path


# ---------------------------------------------------------------------------
# _is_forbidden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FORBIDDEN_TOP_NAMES))
def test_is_forbidden_all_top_names(name):
    """每个禁止顶层名在顶层时被拒。"""
    assert _is_forbidden(Path(name) / "file.txt") is True


def test_is_forbidden_deeply_nested():
    """深层嵌套路径中的禁止名也被拒。"""
    assert _is_forbidden(Path("app") / "output" / "result.txt") is True
    assert _is_forbidden(Path("a") / "b" / ".venv" / "lib") is True


def test_is_forbidden_legal_path():
    """合法路径不被拒。"""
    assert _is_forbidden(Path("app") / "main.py") is False
    assert _is_forbidden(Path("runtimes") / "python" / "python.exe") is False


def test_is_forbidden_root_level_file():
    """根级合法文件不被拒。"""
    assert _is_forbidden(Path("README.md")) is False


# ---------------------------------------------------------------------------
# _sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path):
    """_sha256_file 与 hashlib.sha256 一致。"""
    data = b"abc" * 1000
    fp = _write(tmp_path / "f.bin", data)
    assert _sha256_file(fp) == hashlib.sha256(data).hexdigest()


def test_sha256_file_empty(tmp_path):
    """空文件的 sha256。"""
    fp = _write(tmp_path / "empty", b"")
    assert _sha256_file(fp) == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# create_manifest
# ---------------------------------------------------------------------------


def test_create_manifest_basic(tmp_path):
    """基本 manifest 生成：包含路径/size/sha256 与汇总字段。"""
    _write(tmp_path / "app" / "main.py", b"print(1)")
    _write(tmp_path / "app" / "lib.py", b"x=1")
    manifest = create_manifest(tmp_path, ("app",))
    assert manifest["version"] == 1
    assert manifest["created_by"] == "vibeocr.classic.build_manifest"
    assert manifest["entry_count"] == 2
    paths = {e["path"] for e in manifest["entries"]}
    assert paths == {"app/main.py", "app/lib.py"}
    # total_bytes 正确
    assert manifest["total_bytes"] == len(b"print(1)") + len(b"x=1")


def test_create_manifest_entry_fields(tmp_path):
    """每个 entry 含 path/size/sha256 三字段，sha256 正确。"""
    data = b"content"
    _write(tmp_path / "f.txt", data)
    manifest = create_manifest(tmp_path, ("f.txt",))
    e = manifest["entries"][0]
    assert e["path"] == "f.txt"
    assert e["size"] == len(data)
    assert e["sha256"] == hashlib.sha256(data).hexdigest()


def test_create_manifest_empty_allowed_roots(tmp_path):
    """空 allowed_roots 生成空 manifest。"""
    manifest = create_manifest(tmp_path, ())
    assert manifest["entry_count"] == 0
    assert manifest["entries"] == []
    assert manifest["total_bytes"] == 0


def test_create_manifest_nonexistent_allowed_root_skipped(tmp_path):
    """不存在的 allowed_root 被静默跳过。"""
    _write(tmp_path / "real.txt", b"x")
    manifest = create_manifest(tmp_path, ("real.txt", "does_not_exist"))
    assert manifest["entry_count"] == 1
    assert manifest["entries"][0]["path"] == "real.txt"


def test_create_manifest_forbidden_allowed_root_skipped(tmp_path):
    """白名单条目本身是禁止路径时静默跳过（不 fail-fast）。"""
    _write(tmp_path / "app" / "main.py", b"1")
    _write(tmp_path / "output" / "leak.txt", b"leak")
    manifest = create_manifest(tmp_path, ("app", "output"))
    paths = {e["path"] for e in manifest["entries"]}
    assert paths == {"app/main.py"}
    assert "output/leak.txt" not in paths


def test_create_manifest_forbidden_subdir_rejected(tmp_path):
    """遍历中遇到禁止子目录（app/output/）二次防御被拒。"""
    _write(tmp_path / "app" / "main.py", b"1")
    _write(tmp_path / "app" / "output" / "secret.txt", b"secret")
    manifest = create_manifest(tmp_path, ("app",))
    paths = {e["path"] for e in manifest["entries"]}
    assert "app/main.py" in paths
    assert "app/output/secret.txt" not in paths


def test_create_manifest_mixed_file_and_dir(tmp_path):
    """白名单混合文件与目录。"""
    _write(tmp_path / "top.txt", b"top")
    _write(tmp_path / "dir" / "a.py", b"a")
    manifest = create_manifest(tmp_path, ("top.txt", "dir"))
    paths = {e["path"] for e in manifest["entries"]}
    assert paths == {"top.txt", "dir/a.py"}


def test_create_manifest_paths_are_posix(tmp_path):
    """生成的 path 用 posix 分隔符（正斜杠）。"""
    _write(tmp_path / "dir" / "nested" / "f.py", b"x")
    manifest = create_manifest(tmp_path, ("dir",))
    assert manifest["entries"][0]["path"] == "dir/nested/f.py"


def test_create_manifest_total_bytes(tmp_path):
    """total_bytes 累加所有文件大小。"""
    _write(tmp_path / "a", b"12345")
    _write(tmp_path / "b", b"12")
    manifest = create_manifest(tmp_path, ("a", "b"))
    assert manifest["total_bytes"] == 7


# ---------------------------------------------------------------------------
# verify_archive
# ---------------------------------------------------------------------------


def test_verify_archive_not_found(tmp_path):
    """归档不存在抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        verify_archive(tmp_path / "missing.zip")


def _build_valid_archive(tmp_path, prefix=""):
    """构造一个合法归档（manifest + 一个文件），返回 (zip_path, manifest_dict)。

    manifest entry 的 path 始终是相对路径（不含 ZIP 内前缀）；前缀只用于
    定位 ZIP member（仿真实发布包的 VibeOCR/ 顶层目录）。
    """
    content = b"app content"
    rel = "app/main.py"
    entry = _entry(rel, content)  # entry path 无前缀
    manifest = _make_manifest([entry])
    members = {
        f"{prefix}{MANIFEST_FILENAME}": json.dumps(manifest).encode("utf-8"),
        f"{prefix}{rel}": content,
    }
    zip_path = _build_zip(tmp_path / "ok.zip", members)
    return zip_path, manifest


def test_verify_archive_valid(tmp_path):
    """合法归档校验通过（不抛）。"""
    zip_path, _ = _build_valid_archive(tmp_path)
    verify_archive(zip_path)  # 不抛即通过


def test_verify_archive_valid_with_prefix(tmp_path):
    """带顶层前缀 VibeOCR/ 的归档归一化后校验通过。"""
    zip_path, _ = _build_valid_archive(tmp_path, prefix="VibeOCR/")
    verify_archive(zip_path)  # 不抛即通过


def test_verify_archive_manifest_missing(tmp_path):
    """缺 manifest 抛 ValueError。"""
    zip_path = _build_zip(tmp_path / "no_manifest.zip", {"app/main.py": b"x"})
    with pytest.raises(ValueError, match="manifest"):
        verify_archive(zip_path)


def test_verify_archive_size_mismatch(tmp_path):
    """size 不匹配抛 ValueError。"""
    content = b"app content"
    rel = "app/main.py"
    # manifest 记录的 size 故意错误
    entry = {"path": rel, "size": len(content) + 100, "sha256": "0" * 64}
    manifest = _make_manifest([entry])
    zip_path = _build_zip(
        tmp_path / "bad_size.zip",
        {MANIFEST_FILENAME: json.dumps(manifest).encode(), rel: content},
    )
    with pytest.raises(ValueError, match="size mismatch"):
        verify_archive(zip_path)


def test_verify_archive_sha256_mismatch(tmp_path):
    """sha256 篡改抛 ValueError。"""
    content = b"app content"
    rel = "app/main.py"
    entry = {"path": rel, "size": len(content), "sha256": "0" * 64}
    manifest = _make_manifest([entry])
    zip_path = _build_zip(
        tmp_path / "bad_sha.zip",
        {MANIFEST_FILENAME: json.dumps(manifest).encode(), rel: content},
    )
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_archive(zip_path)


def test_verify_archive_entry_missing_in_zip(tmp_path):
    """manifest 记录的文件在 ZIP 中不存在抛 ValueError。"""
    rel = "app/ghost.py"
    entry = _entry(rel, b"ghost")
    manifest = _make_manifest([entry])
    # ZIP 里只有 manifest，没有 ghost.py
    zip_path = _build_zip(
        tmp_path / "ghost.zip",
        {MANIFEST_FILENAME: json.dumps(manifest).encode()},
    )
    with pytest.raises(ValueError, match="missing in archive"):
        verify_archive(zip_path)


def test_verify_archive_forbidden_path_in_zip(tmp_path):
    """ZIP 含禁止路径文件抛 ValueError。"""
    content = b"ok"
    rel = "app/main.py"
    entry = _entry(rel, content)
    manifest = _make_manifest([entry])
    # 额外塞一个 output/leak.txt（manifest 未记录且禁止）
    zip_path = _build_zip(
        tmp_path / "forbidden.zip",
        {
            MANIFEST_FILENAME: json.dumps(manifest).encode(),
            rel: content,
            "output/leak.txt": b"leak",
        },
    )
    with pytest.raises(ValueError, match="forbidden path"):
        verify_archive(zip_path)


def test_verify_archive_multiple_entries(tmp_path):
    """多文件合法归档校验通过。"""
    files = {
        "app/main.py": b"main",
        "app/lib/util.py": b"util",
        "config/settings.json": b"{}",
    }
    entries = [_entry(p, d) for p, d in files.items()]
    manifest = _make_manifest(entries)
    members = {MANIFEST_FILENAME: json.dumps(manifest).encode()}
    members.update(files)
    zip_path = _build_zip(tmp_path / "multi.zip", members)
    verify_archive(zip_path)  # 不抛即通过


def test_verify_archive_forbidden_with_prefix(tmp_path):
    """带前缀的禁止路径也被拒。"""
    content = b"ok"
    rel = "app/main.py"
    entry = _entry(rel, content)
    manifest = _make_manifest([entry])
    zip_path = _build_zip(
        tmp_path / "prefixed_forbidden.zip",
        {
            f"VibeOCR/{MANIFEST_FILENAME}": json.dumps(manifest).encode(),
            f"VibeOCR/{rel}": content,
            "VibeOCR/output/leak.txt": b"leak",
        },
    )
    with pytest.raises(ValueError, match="forbidden path"):
        verify_archive(zip_path)


# ---------------------------------------------------------------------------
# ManifestEntry dataclass
# ---------------------------------------------------------------------------


def test_manifest_entry_is_frozen():
    """ManifestEntry 是 frozen dataclass（不可变）。"""
    e = ManifestEntry(path="a", size=1, sha256="x")
    with pytest.raises(Exception):
        e.path = "b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


def test_main_no_args_returns_2(capsys):
    """无参返回 2 并打印 usage。"""
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower()


def test_main_verify_no_path_returns_2(capsys):
    """verify 无路径返回 2。"""
    assert main(["verify"]) == 2
    captured = capsys.readouterr()
    assert "requires an archive path" in captured.err


def test_main_unknown_command_returns_2(capsys):
    """未知命令返回 2。"""
    assert main(["frobnicate", "x"]) == 2
    captured = capsys.readouterr()
    assert "unknown command" in captured.err.lower()


def test_main_verify_success(tmp_path, capsys):
    """verify 成功返回 0 并打印 VERIFY OK。"""
    zip_path, _ = _build_valid_archive(tmp_path)
    assert main(["verify", str(zip_path)]) == 0
    captured = capsys.readouterr()
    assert "VERIFY OK" in captured.out


def test_main_verify_failure_returns_1(tmp_path, capsys):
    """verify 校验失败返回 1。"""
    zip_path = _build_zip(
        tmp_path / "bad.zip",
        {"app/main.py": b"x"},  # 无 manifest
    )
    assert main(["verify", str(zip_path)]) == 1
    captured = capsys.readouterr()
    assert "VERIFY FAIL" in captured.err


def test_main_verify_missing_file_returns_1(tmp_path, capsys):
    """verify 不存在的归档返回 1（FileNotFoundError 被 catch）。"""
    rc = main(["verify", str(tmp_path / "ghost.zip")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "VERIFY FAIL" in captured.err
