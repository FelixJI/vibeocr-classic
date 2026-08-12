"""Copy-only migration from the portable Classic layout to stable app data."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from vibeocr.classic.app_paths import (
    AppPaths,
    DataRootResolver,
    activate_app_paths,
    resolve_app_paths,
    resolve_legacy_app_paths,
)

_MARKER_NAME = "data-location.json"
_SCHEMA_VERSION = 1
_COPY_BUFFER_BYTES = 1024 * 1024
_MIGRATION_PROCESS_LOCK = threading.Lock()


@contextmanager
def _exclusive_file_lock(path: Path):
    """Hold a crash-released OS lock across the staging-root promotion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class DataMigrationStatus(str, Enum):
    MIGRATED = "migrated"
    ALREADY_CURRENT = "already-current"
    INITIALIZED = "initialized"
    CANCELLED = "cancelled"
    INSUFFICIENT_SPACE = "insufficient-space"
    TARGET_IN_USE = "target-in-use"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DataMigrationResult:
    status: DataMigrationStatus
    active_paths: AppPaths
    detail: str | None = None

    @property
    def uses_stable_root(self) -> bool:
        return self.status in {
            DataMigrationStatus.MIGRATED,
            DataMigrationStatus.ALREADY_CURRENT,
            DataMigrationStatus.INITIALIZED,
        }


class _MigrationCancelled(RuntimeError):
    pass


class _InsufficientSpace(RuntimeError):
    pass


class StableDataRootMigration:
    """Migrate one legacy layout through a verified sibling staging root.

    The legacy source is never deleted. A failure removes only the fixed
    ``.<target>.migrating`` sibling owned by this operation and returns the
    legacy paths as the active layout, allowing a later launch to retry.
    """

    def __init__(
        self,
        legacy_paths: AppPaths,
        target_paths: AppPaths,
        *,
        cancel_event: threading.Event | None = None,
        available_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self._legacy = legacy_paths
        self._target = target_paths
        self._cancel_event = cancel_event
        self._available_bytes = available_bytes or (
            lambda path: shutil.disk_usage(path).free
        )

    def migrate(self) -> DataMigrationResult:
        target_root = self._target.state_root
        lock_path = target_root.with_name(f".{target_root.name}.migration.lock")
        with _MIGRATION_PROCESS_LOCK, _exclusive_file_lock(lock_path):
            return self._migrate_exclusive()

    def _migrate_exclusive(self) -> DataMigrationResult:
        target_root = self._target.state_root
        marker = target_root / _MARKER_NAME
        if target_root.exists():
            if self._valid_marker(marker):
                return DataMigrationResult(
                    DataMigrationStatus.ALREADY_CURRENT, self._target
                )
            if any(target_root.iterdir()):
                return DataMigrationResult(
                    DataMigrationStatus.TARGET_IN_USE,
                    self._legacy,
                    "稳定数据根非空但缺少有效迁移标记",
                )
            target_root.rmdir()

        temporary = target_root.with_name(f".{target_root.name}.migrating")
        target_root.parent.mkdir(parents=True, exist_ok=True)
        self._remove_temporary(temporary)
        has_legacy = any(source.exists() for source, _target in self._roots())
        try:
            self._raise_if_cancelled()
            required = sum(
                path.stat().st_size
                for source, _target in self._roots()
                for path in self._files(source)
            )
            if self._available_bytes(target_root.parent) < required:
                raise _InsufficientSpace(
                    f"迁移需要 {required} 字节，但稳定数据卷空间不足"
                )
            temporary.mkdir()
            for source, relative_target in self._roots():
                if source.exists():
                    self._copy_tree(source, temporary / relative_target)
            self._verify_closure(temporary)
            self._validate_structured_state(temporary)
            self._write_marker(temporary / _MARKER_NAME)
            os.replace(temporary, target_root)
        except _MigrationCancelled as exc:
            self._remove_temporary(temporary)
            return DataMigrationResult(
                DataMigrationStatus.CANCELLED, self._legacy, str(exc)
            )
        except _InsufficientSpace as exc:
            self._remove_temporary(temporary)
            return DataMigrationResult(
                DataMigrationStatus.INSUFFICIENT_SPACE, self._legacy, str(exc)
            )
        except (OSError, ValueError) as exc:
            self._remove_temporary(temporary)
            return DataMigrationResult(
                DataMigrationStatus.FAILED, self._legacy, str(exc)
            )
        return DataMigrationResult(
            DataMigrationStatus.MIGRATED
            if has_legacy
            else DataMigrationStatus.INITIALIZED,
            self._target,
        )

    def _roots(self) -> tuple[tuple[Path, Path], ...]:
        return (
            (self._legacy.data_root, Path("data")),
            (self._legacy.runtime_root, Path("runtimes")),
            (self._legacy.model_cache_root, Path("models")),
            (self._legacy.output_root, Path("output")),
            (self._legacy.config_file.parent, Path("config")),
        )

    def _copy_tree(self, source: Path, target: Path) -> None:
        if source.is_symlink():
            raise ValueError(f"迁移源不允许符号链接: {source}")
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._copy_file(source, target)
            return
        for root, directories, filenames in os.walk(source):
            self._raise_if_cancelled()
            root_path = Path(root)
            relative = root_path.relative_to(source)
            destination = target / relative
            destination.mkdir(parents=True, exist_ok=True)
            for directory in directories:
                child = root_path / directory
                if child.is_symlink():
                    raise ValueError(f"迁移源不允许符号链接: {child}")
            for filename in filenames:
                child = root_path / filename
                if child.is_symlink():
                    raise ValueError(f"迁移源不允许符号链接: {child}")
                self._copy_file(child, destination / filename)

    def _copy_file(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(_COPY_BUFFER_BYTES):
                self._raise_if_cancelled()
                writer.write(chunk)
        shutil.copystat(source, target, follow_symlinks=False)

    def _verify_closure(self, temporary: Path) -> None:
        for source, relative_target in self._roots():
            if not source.exists():
                continue
            target = temporary / relative_target
            source_files = {
                path.relative_to(source): path for path in self._files(source)
            }
            target_files = {
                path.relative_to(target): path for path in self._files(target)
            }
            if source_files.keys() != target_files.keys():
                raise ValueError(f"迁移文件闭包不一致: {relative_target}")
            for relative, source_file in source_files.items():
                self._raise_if_cancelled()
                target_file = target_files[relative]
                if source_file.stat().st_size != target_file.stat().st_size:
                    raise ValueError(
                        f"迁移文件大小不一致: {relative_target / relative}"
                    )
                if self._sha256(source_file) != self._sha256(target_file):
                    raise ValueError(
                        f"迁移文件内容不一致: {relative_target / relative}"
                    )

    def _validate_structured_state(self, temporary: Path) -> None:
        candidates = [temporary / "config" / "app_settings.json"]
        candidates.extend((temporary / "runtimes").rglob("runtime-manifest.json"))
        candidates.extend((temporary / "runtimes").rglob("component-lock.json"))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"迁移状态 JSON 无效: {path.name}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"迁移状态 JSON 必须是对象: {path.name}")

    def _write_marker(self, path: Path) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "source_root": str(self._legacy.install_root),
            "target_root": str(self._target.state_root),
            "source_deleted": False,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _valid_marker(self, path: Path) -> bool:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(value, dict)
            and value.get("schema_version") == _SCHEMA_VERSION
            and value.get("target_root") == str(self._target.state_root)
            and value.get("source_deleted") is False
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise _MigrationCancelled("用户取消了数据迁移")

    @staticmethod
    def _files(root: Path) -> Iterable[Path]:
        if root.is_file():
            return (root,)
        if not root.exists():
            return ()
        return (path for path in root.rglob("*") if path.is_file())

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_COPY_BUFFER_BYTES), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _remove_temporary(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)


def prepare_stable_data_root(
    install_root: Path,
    *,
    data_root_resolver: DataRootResolver | None = None,
    cancel_event: threading.Event | None = None,
) -> DataMigrationResult:
    """Migrate if necessary and activate the verified/fallback layout."""
    legacy = resolve_legacy_app_paths(install_root)
    target = resolve_app_paths(
        install_root,
        data_root_resolver=data_root_resolver,
    )
    result = StableDataRootMigration(
        legacy,
        target,
        cancel_event=cancel_event,
    ).migrate()
    activate_app_paths(result.active_paths)
    return result


def is_stable_data_root_ready() -> bool:
    """Return whether Setup may replace the current portable installation."""
    from vibeocr.classic.app_paths import get_active_app_paths

    paths = get_active_app_paths()
    if paths.state_root == paths.install_root:
        return False
    marker = paths.state_root / _MARKER_NAME
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(value, dict)
        and value.get("schema_version") == _SCHEMA_VERSION
        and value.get("target_root") == str(paths.state_root)
        and value.get("source_deleted") is False
    )


__all__ = [
    "DataMigrationResult",
    "DataMigrationStatus",
    "StableDataRootMigration",
    "is_stable_data_root_ready",
    "prepare_stable_data_root",
]
