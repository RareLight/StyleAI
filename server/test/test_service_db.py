"""Tests for services/db.py — backup directory handling, pruning, and the
stats aggregation that backs /db/stats. The module wraps Chroma + filesystem,
so chroma calls are mocked.
"""

import os
import json
import shutil
import sqlite3
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from services import db as service_db


def _make_sqlite(path, value="original"):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE state (value TEXT NOT NULL)")
    connection.execute("INSERT INTO state VALUES (?)", (value,))
    connection.commit()
    connection.close()


def _read_sqlite(path):
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM state").fetchone()[0]
    finally:
        connection.close()


def test_snapshot_construction_is_serialized(mocker):
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    def build(**_kwargs):
        nonlocal active, maximum_active
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return "/tmp/test.zip", "test.zip"

    mocker.patch.object(service_db, "_build_backup_zip_unlocked", side_effect=build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service_db.build_backup_zip) for _ in range(2)]
        [future.result() for future in futures]

    assert maximum_active == 1


class TestBackupsDir:
    def test_returns_none_when_db_path_missing(self, monkeypatch):
        monkeypatch.setattr("config.DB_PATH", "")
        assert service_db._get_backups_dir() is None

    def test_appends_backups_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        assert service_db._get_backups_dir() == os.path.join(str(tmp_path), "backups")


class TestGetDatabaseStats:
    def test_aggregates_chroma_results(self, mocker):
        mocker.patch.object(
            service_db.chroma_service,
            "get_image_metadata_stats",
            return_value={
                "total": 7,
                "with_embedding": 6,
                "with_title": 5,
                "with_caption": 4,
                "with_keywords": 3,
            },
        )
        stats = service_db.get_database_stats()
        assert stats["photos"]["total"] == 7
        assert stats["photos"]["with_embedding"] == 6


class TestBackupSafety:
    def test_required_persistent_copy_failure_aborts_backup(
        self, monkeypatch, mocker, tmp_path
    ):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        _make_sqlite(tmp_path / "chroma.sqlite3")
        real_copy = shutil.copy2

        def fail_persistent_copy(source, destination, *args, **kwargs):
            if str(destination).endswith(".partial"):
                raise OSError("disk full")
            return real_copy(source, destination, *args, **kwargs)

        mocker.patch.object(
            service_db.shutil, "copy2", side_effect=fail_persistent_copy
        )

        with pytest.raises(RuntimeError, match="persistent backup"):
            service_db.build_backup_zip(require_persistent=True)

    def test_prune_removes_temporary_backup_after_persistent_copy(
        self, mocker, tmp_path
    ):
        temporary_backup = tmp_path / "temporary.zip"
        temporary_backup.write_bytes(b"zip")
        build = mocker.patch.object(
            service_db,
            "build_backup_zip",
            return_value=(str(temporary_backup), "persistent.zip"),
        )
        mocker.patch.object(
            service_db.chroma_service, "get_all_image_ids", return_value=[]
        )

        assert service_db.prune_database(["valid-photo"])["deleted"] == 0
        build.assert_called_once_with(require_persistent=True, reason="pre-prune")
        assert temporary_backup.exists() is False

    def test_validated_backup_contains_manifest_and_consistent_sqlite(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        _make_sqlite(tmp_path / "styles.sqlite", "snapshot")

        archive_path, _ = service_db.build_backup_zip(reason="test")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(
                    archive.read(f"{tmp_path.name}/{service_db.BACKUP_MANIFEST_NAME}")
                )
                assert manifest["backup_format_version"] == 1
                assert manifest["reason"] == "test"
                assert {item["path"] for item in manifest["files"]} >= {
                    "styles.sqlite",
                    service_db.OWNERSHIP_MARKER_NAME,
                }
                assert archive.testzip() is None
        finally:
            os.remove(archive_path)

    def test_restore_round_trip_and_preserves_backup_chain(
        self, monkeypatch, mocker, tmp_path
    ):
        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        _make_sqlite(db_path / "styles.sqlite", "before")
        archive_path, _ = service_db.build_backup_zip(reason="round-trip")
        connection = sqlite3.connect(db_path / "styles.sqlite")
        connection.execute("UPDATE state SET value = 'after'")
        connection.commit()
        connection.close()
        mocker.patch("core.migrations.run_migrations")
        mocker.patch.object(service_db.chroma_service, "unload_collections")
        mocker.patch.object(service_db.training_service, "unload_collections")
        mocker.patch("services.policy_runtime.invalidate_runtime_cache")

        result = service_db.restore_backup_archive(archive_path)

        assert result["success"] is True
        assert _read_sqlite(db_path / "styles.sqlite") == "before"
        assert os.path.isfile(result["pre_restore_backup"])
        os.remove(archive_path)

    def test_restore_reopens_chroma_before_the_next_write(
        self, monkeypatch, mocker, tmp_path
    ):
        """An atomic restore must not reuse handles to the replaced SQLite file."""
        from services import chroma as chroma_service
        from services import training as training_service

        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        mocker.patch("core.migrations.run_migrations")
        mocker.patch("server_lifecycle.recover_catalog_session")
        mocker.patch("services.policy_runtime.invalidate_runtime_cache")

        try:
            chroma_service._ensure_initialized()
            training_service._ensure_initialized()
            training_service._training_collection.upsert(
                ids=["before-restore"],
                embeddings=[[0.1] * training_service.EMBEDDING_DIM],
                metadatas=[{"photo_id": "before-restore"}],
            )
            archive_path, _ = service_db.build_backup_zip(reason="chroma-reopen")

            service_db.restore_backup_archive(archive_path)

            training_service._ensure_initialized()
            training_service._training_collection.upsert(
                ids=["after-restore"],
                embeddings=[[0.2] * training_service.EMBEDDING_DIM],
                metadatas=[{"photo_id": "after-restore"}],
            )
            assert training_service._training_collection.count() == 2
        finally:
            chroma_service.unload_collections()
            training_service.unload_collections()
            if "archive_path" in locals() and os.path.exists(archive_path):
                os.remove(archive_path)

    def test_restore_rejects_a_different_catalog(self, monkeypatch, tmp_path):
        first = tmp_path / "first" / "styleai.db"
        second = tmp_path / "second" / "styleai.db"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        monkeypatch.setattr("config.DB_PATH", str(first))
        _make_sqlite(first / "styles.sqlite")
        archive_path, _ = service_db.build_backup_zip(reason="wrong-catalog")
        monkeypatch.setattr("config.DB_PATH", str(second))
        _make_sqlite(second / "styles.sqlite")
        service_db.ensure_catalog_ownership(str(second))

        with pytest.raises(ValueError, match="different Lightroom catalog"):
            service_db.restore_backup_archive(archive_path)
        os.remove(archive_path)

    def test_restore_rejects_path_traversal(self, monkeypatch, tmp_path):
        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        ownership = service_db.ensure_catalog_ownership(str(db_path))
        archive_path = tmp_path / "unsafe.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape", "bad")

        with pytest.raises(ValueError, match="Unsafe backup archive path"):
            service_db.extract_and_validate_backup(
                str(archive_path),
                expected_catalog_database_id=ownership["catalog_database_id"],
                staging_parent=str(tmp_path),
            )

    def test_restore_rejects_checksum_mismatch(self, monkeypatch, tmp_path):
        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        _make_sqlite(db_path / "styles.sqlite")
        ownership = service_db.ensure_catalog_ownership(str(db_path))
        valid_path, _ = service_db.build_backup_zip(reason="checksum")
        corrupt_path = tmp_path / "corrupt.zip"
        with (
            zipfile.ZipFile(valid_path, "r") as source,
            zipfile.ZipFile(corrupt_path, "w") as destination,
        ):
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename.endswith("styles.sqlite"):
                    data += b"tampered"
                destination.writestr(info, data)

        with pytest.raises(ValueError, match="size mismatch"):
            service_db.extract_and_validate_backup(
                str(corrupt_path),
                expected_catalog_database_id=ownership["catalog_database_id"],
                staging_parent=str(tmp_path),
            )
        os.remove(valid_path)

    def test_restore_checks_staging_disk_space(self, monkeypatch, mocker, tmp_path):
        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        _make_sqlite(db_path / "styles.sqlite")
        ownership = service_db.ensure_catalog_ownership(str(db_path))
        archive_path, _ = service_db.build_backup_zip(reason="disk-space")
        mocker.patch.object(
            service_db.shutil,
            "disk_usage",
            return_value=SimpleNamespace(total=1, used=1, free=0),
        )

        with pytest.raises(OSError, match="Insufficient free disk space"):
            service_db.extract_and_validate_backup(
                archive_path,
                expected_catalog_database_id=ownership["catalog_database_id"],
                staging_parent=str(tmp_path),
            )
        os.remove(archive_path)

    def test_failed_restore_rolls_back_current_database(
        self, monkeypatch, mocker, tmp_path
    ):
        db_path = tmp_path / "styleai.db"
        db_path.mkdir()
        monkeypatch.setattr("config.DB_PATH", str(db_path))
        _make_sqlite(db_path / "styles.sqlite", "backup-version")
        archive_path, _ = service_db.build_backup_zip(reason="rollback")
        connection = sqlite3.connect(db_path / "styles.sqlite")
        connection.execute("UPDATE state SET value = 'current-version'")
        connection.commit()
        connection.close()
        mocker.patch.object(service_db.chroma_service, "unload_collections")
        mocker.patch.object(service_db.training_service, "unload_collections")
        mocker.patch("services.policy_runtime.invalidate_runtime_cache")
        mocker.patch(
            "core.migrations.run_migrations",
            side_effect=RuntimeError("migration validation failed"),
        )

        with pytest.raises(RuntimeError, match="migration validation failed"):
            service_db.restore_backup_archive(archive_path)

        assert _read_sqlite(db_path / "styles.sqlite") == "current-version"
        os.remove(archive_path)


class TestPruneOldBackups:
    def _make_zip(self, dirpath, name, mtime):
        path = os.path.join(dirpath, name)
        with open(path, "w") as f:
            f.write("fake zip")
        os.utime(path, (mtime, mtime))
        return path

    def test_returns_zero_when_dir_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        # No backups subdir created
        assert service_db.prune_old_backups(max_keep=5) == 0

    def test_keeps_only_max_keep_newest(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        backups = tmp_path / "backups"
        backups.mkdir()

        now = time.time()
        # Oldest first; we expect only the 2 newest to survive max_keep=2.
        self._make_zip(str(backups), "old.zip", now - 300)
        self._make_zip(str(backups), "mid.zip", now - 200)
        self._make_zip(str(backups), "newer.zip", now - 100)
        self._make_zip(str(backups), "newest.zip", now)

        deleted = service_db.prune_old_backups(max_keep=2)
        assert deleted == 2
        remaining = sorted(os.listdir(str(backups)))
        assert remaining == ["newer.zip", "newest.zip"]

    def test_zero_max_keep_clamped_to_one(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        backups = tmp_path / "backups"
        backups.mkdir()
        now = time.time()
        self._make_zip(str(backups), "a.zip", now - 100)
        self._make_zip(str(backups), "b.zip", now)

        deleted = service_db.prune_old_backups(max_keep=0)
        # max_keep=0 is clamped to 1, so 1 file should remain
        assert deleted == 1
        assert os.listdir(str(backups)) == ["b.zip"]

    def test_ignores_non_zip_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.DB_PATH", str(tmp_path))
        backups = tmp_path / "backups"
        backups.mkdir()
        # Stray .txt file should not be considered for pruning.
        (backups / "readme.txt").write_text("hi")
        now = time.time()
        self._make_zip(str(backups), "a.zip", now - 100)
        self._make_zip(str(backups), "b.zip", now)

        deleted = service_db.prune_old_backups(max_keep=1)
        assert deleted == 1
        # readme.txt survives even though it's "older"
        assert "readme.txt" in os.listdir(str(backups))


def test_scheduled_backup_is_due_soon_when_none_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("config.DB_PATH", str(tmp_path))
    assert service_db.seconds_until_scheduled_backup(86400, startup_grace=60) == 60


def test_scheduled_backup_uses_persisted_backup_age(monkeypatch, tmp_path):
    monkeypatch.setattr("config.DB_PATH", str(tmp_path))
    backups = tmp_path / "backups"
    backups.mkdir()
    backup = backups / "recent.zip"
    backup.write_bytes(b"zip")
    now = time.time()
    os.utime(backup, (now - 100, now - 100))

    delay = service_db.seconds_until_scheduled_backup(1000, startup_grace=60)

    assert 899 <= delay <= 900
