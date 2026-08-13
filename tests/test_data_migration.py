from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from vibeocr.classic.app_paths import (
    LocalAppDataRootResolver,
    resolve_app_paths,
    resolve_legacy_app_paths,
)
from vibeocr.classic.data_migration import (
    DataMigrationResult,
    DataMigrationStatus,
    StableDataRootMigration,
)


def _paths(tmp_path):
    install_root = tmp_path / "legacy-install"
    stable_root = tmp_path / "Local" / "VibeOCRClassicData"
    legacy = resolve_legacy_app_paths(install_root)
    target = resolve_app_paths(
        tmp_path / "Velopack" / "current" / "VibeOCR.exe",
        data_root_resolver=LocalAppDataRootResolver(stable_root.parent),
    )
    return legacy, target


def test_migration_copies_actual_legacy_closure_and_keeps_source(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.config_file.parent.mkdir(parents=True)
    legacy.config_file.write_text('{"network_type": "domestic"}', encoding="utf-8")
    (legacy.data_root / "backend").mkdir(parents=True)
    (legacy.data_root / "backend" / "session.json").write_text(
        '{"pages": [1]}', encoding="utf-8"
    )
    (legacy.runtime_root / "cpu").mkdir(parents=True)
    (legacy.runtime_root / "cpu" / "python.exe").write_bytes(b"runtime")
    legacy.model_cache_root.mkdir(parents=True)
    (legacy.model_cache_root / "model.bin").write_bytes(b"model")
    legacy.output_root.mkdir(parents=True)
    (legacy.output_root / "result.pdf").write_bytes(b"pdf")

    result = StableDataRootMigration(legacy, target).migrate()

    assert result.status is DataMigrationStatus.MIGRATED
    assert result.active_paths == target
    assert target.config_file.read_text(encoding="utf-8") == (
        '{"network_type": "domestic"}'
    )
    assert (target.data_root / "backend" / "session.json").is_file()
    assert (target.runtime_root / "cpu" / "python.exe").read_bytes() == b"runtime"
    assert (target.model_cache_root / "model.bin").read_bytes() == b"model"
    assert (target.output_root / "result.pdf").read_bytes() == b"pdf"
    marker = json.loads(
        (target.state_root / "data-location.json").read_text(encoding="utf-8")
    )
    assert marker["schema_version"] == 1
    assert marker["source_root"] == str(legacy.install_root)

    # 迁移只复制，任何 legacy 用户数据都保留。
    assert legacy.config_file.is_file()
    assert (legacy.runtime_root / "cpu" / "python.exe").is_file()
    assert (legacy.output_root / "result.pdf").is_file()


def test_migration_is_idempotent_and_does_not_overwrite_target(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.config_file.parent.mkdir(parents=True)
    legacy.config_file.write_text('{"theme": "old"}', encoding="utf-8")
    migration = StableDataRootMigration(legacy, target)
    assert migration.migrate().status is DataMigrationStatus.MIGRATED
    target.config_file.write_text('{"theme": "new"}', encoding="utf-8")

    rerun = migration.migrate()

    assert rerun.status is DataMigrationStatus.ALREADY_CURRENT
    assert target.config_file.read_text(encoding="utf-8") == '{"theme": "new"}'
    assert legacy.config_file.read_text(encoding="utf-8") == '{"theme": "old"}'


def test_existing_empty_stable_root_is_safe_to_migrate_and_idempotent(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.config_file.parent.mkdir(parents=True)
    legacy.config_file.write_text('{"theme": "classic"}', encoding="utf-8")
    target.state_root.mkdir(parents=True)

    first = StableDataRootMigration(legacy, target).migrate()
    second = StableDataRootMigration(legacy, target).migrate()

    assert first.status is DataMigrationStatus.MIGRATED
    assert second.status is DataMigrationStatus.ALREADY_CURRENT
    assert target.config_file.read_text(encoding="utf-8") == '{"theme": "classic"}'
    assert legacy.config_file.is_file()


def test_cancelled_migration_is_reentrant_and_never_deletes_source(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.config_file.parent.mkdir(parents=True)
    legacy.config_file.write_text('{"theme": "classic"}', encoding="utf-8")
    cancelled = threading.Event()
    cancelled.set()

    first = StableDataRootMigration(legacy, target, cancel_event=cancelled).migrate()

    assert first.status is DataMigrationStatus.CANCELLED
    assert first.active_paths == legacy
    assert legacy.config_file.is_file()
    assert not target.state_root.exists()
    assert not target.state_root.with_name(
        f".{target.state_root.name}.migrating"
    ).exists()

    retried = StableDataRootMigration(legacy, target).migrate()
    assert retried.status is DataMigrationStatus.MIGRATED
    assert legacy.config_file.is_file()


def test_insufficient_space_keeps_legacy_layout_active(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.output_root.mkdir(parents=True)
    source = legacy.output_root / "important.pdf"
    source.write_bytes(b"important user output")

    result = StableDataRootMigration(
        legacy, target, available_bytes=lambda _path: 0
    ).migrate()

    assert result.status is DataMigrationStatus.INSUFFICIENT_SPACE
    assert result.active_paths == legacy
    assert source.read_bytes() == b"important user output"
    assert not target.state_root.exists()


def test_invalid_settings_can_be_fixed_and_migration_retried(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.config_file.parent.mkdir(parents=True)
    legacy.config_file.write_text("not-json", encoding="utf-8")

    failed = StableDataRootMigration(legacy, target).migrate()

    assert failed.status is DataMigrationStatus.FAILED
    assert failed.active_paths == legacy
    assert legacy.config_file.read_text(encoding="utf-8") == "not-json"
    assert not target.state_root.exists()

    legacy.config_file.write_text('{"fixed": true}', encoding="utf-8")
    retried = StableDataRootMigration(legacy, target).migrate()
    assert retried.status is DataMigrationStatus.MIGRATED
    assert json.loads(target.config_file.read_text(encoding="utf-8")) == {"fixed": True}
    assert legacy.config_file.is_file()


def test_concurrent_migration_serializes_one_promotion_and_preserves_source(tmp_path):
    legacy, target = _paths(tmp_path)
    legacy.data_root.mkdir(parents=True)
    source = legacy.data_root / "user.db"
    source.write_bytes(b"user-data")

    def migrate(_index: int) -> DataMigrationResult:
        return StableDataRootMigration(legacy, target).migrate()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(migrate, range(2)))

    assert {result.status for result in results} == {
        DataMigrationStatus.MIGRATED,
        DataMigrationStatus.ALREADY_CURRENT,
    }
    assert source.read_bytes() == b"user-data"
    assert (target.data_root / "user.db").read_bytes() == b"user-data"
