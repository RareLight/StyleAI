"""
Database operations service.

Handles non-search operations across ChromaDB and SQLite, including
generating statistics, migrating old ID formats, producing full backup zips,
and pruning orphaned records.
"""

import config
from . import chroma as chroma_service
from . import training as training_service
from config import logger

import hashlib
import json
import os
from pathlib import PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import threading
import zipfile
from datetime import UTC, datetime
from uuid import uuid4


BACKUP_FORMAT_VERSION = 1
DEFAULT_BACKUP_MAX_KEEP = 14
BACKUP_MANIFEST_NAME = "styleai-backup-manifest.json"
OWNERSHIP_MARKER_NAME = ".styleai-catalog-ownership.json"
SQLITE_FILENAMES = frozenset({"chroma.sqlite3", "styles.sqlite"})
MAX_BACKUP_FILE_COUNT = 100_000
MIN_RESTORE_EXPANSION_LIMIT = 1024 * 1024 * 1024
_snapshot_lock = threading.RLock()


# Ordner für serverseitig aufgehobene Backups: Docker /data/db/backups, Standalone <db-path>/backups
def _get_backups_dir():
    if not config.DB_PATH:
        return None
    return os.path.join(config.DB_PATH, "backups")


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _directory_size(path: str) -> int:
    total = 0
    for current_root, dirs, files in os.walk(path):
        if current_root == path and "backups" in dirs:
            dirs.remove("backups")
        for filename in files:
            full_path = os.path.join(current_root, filename)
            if os.path.isfile(full_path):
                total += os.path.getsize(full_path)
    return total


def ensure_catalog_ownership(db_path: str) -> dict:
    """Return the stable catalog-local ownership marker, creating it if absent."""
    marker_path = os.path.join(db_path, OWNERSHIP_MARKER_NAME)
    try:
        with open(marker_path, encoding="utf-8") as marker_file:
            marker = json.load(marker_file)
        if marker.get("catalog_database_id"):
            return marker
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        logger.warning("Replacing invalid catalog ownership marker", exc_info=True)

    marker = {
        "format_version": 1,
        "catalog_database_id": uuid4().hex,
        "created_at": _utc_timestamp(),
    }
    os.makedirs(db_path, exist_ok=True)
    temporary_path = marker_path + f".{uuid4().hex}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as marker_file:
        json.dump(marker, marker_file, indent=2, sort_keys=True)
        marker_file.flush()
        os.fsync(marker_file.fileno())
    os.replace(temporary_path, marker_path)
    return marker


def _copy_sqlite_database(source_path: str, destination_path: str) -> None:
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    source = sqlite3.connect(source_path)
    source.execute("PRAGMA query_only=ON")
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def _validate_sqlite(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA query_only=ON")
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise RuntimeError(
            f"SQLite integrity check failed for {os.path.basename(path)}"
        )


def _schema_versions(staged_db_path: str) -> list[str]:
    styles_path = os.path.join(staged_db_path, "styles.sqlite")
    if not os.path.isfile(styles_path):
        return []
    connection = sqlite3.connect(styles_path)
    connection.execute("PRAGMA query_only=ON")
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_versions'"
        ).fetchone()
        if not exists:
            return []
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT version_id FROM schema_versions ORDER BY version_id"
            ).fetchall()
        ]
    finally:
        connection.close()


def _stage_database_snapshot(db_path: str, staging_parent: str) -> tuple[str, dict]:
    database_name = os.path.basename(os.path.normpath(db_path)) or "styleai.db"
    staged_db_path = os.path.join(staging_parent, database_name)
    os.makedirs(staged_db_path, exist_ok=True)
    ownership = ensure_catalog_ownership(db_path)

    for current_root, dirs, files in os.walk(db_path):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if not (current_root == db_path and directory == "backups")
        )
        relative_root = os.path.relpath(current_root, db_path)
        destination_root = (
            staged_db_path
            if relative_root == "."
            else os.path.join(staged_db_path, relative_root)
        )
        os.makedirs(destination_root, exist_ok=True)
        for filename in sorted(files):
            if filename.endswith(("-wal", "-shm")):
                continue
            source_path = os.path.join(current_root, filename)
            if not os.path.isfile(source_path):
                continue
            destination_path = os.path.join(destination_root, filename)
            if filename in SQLITE_FILENAMES:
                _copy_sqlite_database(source_path, destination_path)
                _validate_sqlite(destination_path)
            else:
                shutil.copy2(source_path, destination_path)

    files: list[dict] = []
    for current_root, _, filenames in os.walk(staged_db_path):
        for filename in sorted(filenames):
            if filename == BACKUP_MANIFEST_NAME:
                continue
            full_path = os.path.join(current_root, filename)
            relative_path = os.path.relpath(full_path, staged_db_path).replace(
                os.sep, "/"
            )
            files.append(
                {
                    "path": relative_path,
                    "size": os.path.getsize(full_path),
                    "sha256": _sha256(full_path),
                }
            )

    from services.version import get_backend_version_info

    version_info = get_backend_version_info()
    manifest = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "created_at": _utc_timestamp(),
        "catalog_database_id": ownership["catalog_database_id"],
        "database_directory": database_name,
        "backend_version": version_info.get("backend_version"),
        "backend_build": version_info.get("backend_build"),
        "schema_versions": _schema_versions(staged_db_path),
        "files": files,
    }
    return staged_db_path, manifest


def _write_manifest(staged_db_path: str, manifest: dict, *, reason: str) -> None:
    complete_manifest = dict(manifest)
    complete_manifest["reason"] = str(reason or "manual")
    manifest_path = os.path.join(staged_db_path, BACKUP_MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(complete_manifest, manifest_file, indent=2, sort_keys=True)
        manifest_file.flush()
        os.fsync(manifest_file.fileno())


def _archive_staged_snapshot(staging_parent: str, archive_path: str) -> None:
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for current_root, _, files in os.walk(staging_parent):
            for filename in sorted(files):
                full_path = os.path.join(current_root, filename)
                archive_name = os.path.relpath(full_path, staging_parent)
                archive.write(full_path, arcname=archive_name)
    with zipfile.ZipFile(archive_path, "r") as archive:
        corrupt_member = archive.testzip()
        if corrupt_member:
            raise RuntimeError(f"Backup ZIP verification failed for {corrupt_member}")


def get_database_stats() -> dict:
    """Return database statistics for photos, faces, and persons."""
    image_stats = chroma_service.get_image_metadata_stats()
    return {
        "photos": {
            "total": image_stats["total"],
            "with_embedding": image_stats["with_embedding"],
            "with_title": image_stats["with_title"],
            "with_caption": image_stats["with_caption"],
            "with_keywords": image_stats["with_keywords"],
        },
    }


def _build_backup_zip_unlocked(
    *,
    require_persistent: bool = False,
    persist: bool = True,
    reason: str = "manual",
) -> tuple[str, str]:
    """Create a validated temporary snapshot and optionally persist a copy."""
    db_path = config.DB_PATH
    if not db_path or not os.path.isdir(db_path):
        raise FileNotFoundError(
            f"Database path does not exist or is not a directory: {db_path}"
        )

    safe_reason = (
        "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in str(reason or "manual").lower()
        ).strip("-")
        or "manual"
    )
    backup_name = "styleai-backup-{}-{}-{}.zip".format(
        datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f"),
        safe_reason,
        uuid4().hex[:8],
    )
    fd, zip_path = tempfile.mkstemp(prefix="styleai-backup-", suffix=".zip")
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory(prefix="styleai-backup-stage-") as staging:
            staged_db_path, manifest = _stage_database_snapshot(db_path, staging)
            _write_manifest(staged_db_path, manifest, reason=reason)
            _archive_staged_snapshot(staging, zip_path)
        logger.info(
            "Created validated DB backup %s with %s files from %s",
            zip_path,
            len(manifest["files"]),
            db_path,
        )
    except Exception:
        try:
            os.remove(zip_path)
        except OSError:
            pass
        raise

    # Kopie serverseitig aufbewahren (Docker: /data/db/backups, Standalone: <db-path>/backups)
    backups_dir = _get_backups_dir()
    if persist and backups_dir:
        try:
            os.makedirs(backups_dir, exist_ok=True)
            persistent_path = os.path.join(backups_dir, backup_name)
            partial_path = persistent_path + ".partial"
            shutil.copy2(zip_path, partial_path)
            os.replace(partial_path, persistent_path)
            logger.info("DB backup saved server-side to %s", persistent_path)
        except Exception as e:
            logger.warning("Could not save backup to %s: %s", backups_dir, e)
            if require_persistent:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
                raise RuntimeError(
                    f"Could not save persistent backup to {backups_dir}: {e}"
                ) from e
    elif require_persistent:
        try:
            os.remove(zip_path)
        except OSError:
            pass
        raise RuntimeError("Persistent backup directory is unavailable")

    return zip_path, backup_name


def build_backup_zip(
    *,
    require_persistent: bool = False,
    persist: bool = True,
    reason: str = "manual",
) -> tuple[str, str]:
    """Serialize snapshot construction even for startup and legacy callers."""
    with _snapshot_lock:
        return _build_backup_zip_unlocked(
            require_persistent=require_persistent,
            persist=persist,
            reason=reason,
        )


def create_persistent_backup(
    *, reason: str, max_keep: int | None = DEFAULT_BACKUP_MAX_KEEP
) -> str:
    """Create one required persistent backup and return its final path."""
    temporary_path, backup_name = build_backup_zip(
        require_persistent=True,
        reason=reason,
    )
    try:
        if max_keep is not None:
            prune_old_backups(max_keep=max_keep)
    finally:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
    backups_dir = _get_backups_dir()
    if not backups_dir:
        raise RuntimeError("Persistent backup directory is unavailable")
    return os.path.join(backups_dir, backup_name)


def _safe_archive_member(info: zipfile.ZipInfo) -> PurePosixPath:
    path = PurePosixPath(info.filename)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe backup archive path: {info.filename}")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise ValueError(f"Backup archive may not contain symlinks: {info.filename}")
    return path


def extract_and_validate_backup(
    archive_path: str,
    *,
    expected_catalog_database_id: str,
    staging_parent: str,
) -> tuple[str, str, dict]:
    """Safely extract and verify a StyleAI backup into a same-volume staging dir."""
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Backup file does not exist: {archive_path}")
    extraction_root = tempfile.mkdtemp(
        prefix=".styleai-restore-stage-", dir=staging_parent
    )
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_BACKUP_FILE_COUNT:
                raise ValueError("Backup archive contains too many files")
            expanded_size = sum(max(0, info.file_size) for info in infos)
            current_size = _directory_size(config.DB_PATH or staging_parent)
            expansion_limit = max(
                MIN_RESTORE_EXPANSION_LIMIT,
                current_size * 4 + 512 * 1024 * 1024,
            )
            if expanded_size > expansion_limit:
                raise ValueError("Backup archive expands beyond the safe restore limit")
            required_free = expanded_size + max(64 * 1024 * 1024, expanded_size // 10)
            if shutil.disk_usage(staging_parent).free < required_free:
                raise OSError(
                    "Insufficient free disk space to stage this backup safely"
                )
            for info in infos:
                if (
                    info.file_size > 100 * 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size > info.compress_size * 1000
                ):
                    raise ValueError(
                        "Backup archive contains an unsafe compression ratio"
                    )
            paths = [_safe_archive_member(info) for info in infos]
            if len({str(path) for path in paths}) != len(paths):
                raise ValueError("Backup archive contains duplicate paths")
            corrupt_member = archive.testzip()
            if corrupt_member:
                raise ValueError(f"Backup archive is corrupt at {corrupt_member}")
            manifest_paths = [
                path for path in paths if path.name == BACKUP_MANIFEST_NAME
            ]
            if len(manifest_paths) != 1 or len(manifest_paths[0].parts) != 2:
                raise ValueError(
                    "Backup manifest is missing or not at the database root"
                )
            database_directory = manifest_paths[0].parts[0]
            if any(path.parts[0] != database_directory for path in paths):
                raise ValueError(
                    "Backup archive must contain exactly one database directory"
                )
            archive.extractall(extraction_root)

        staged_db_path = os.path.join(extraction_root, database_directory)
        manifest_path = os.path.join(staged_db_path, BACKUP_MANIFEST_NAME)
        with open(manifest_path, encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError("Unsupported StyleAI backup format version")
        if manifest.get("database_directory") != database_directory:
            raise ValueError("Backup database directory does not match its manifest")
        if manifest.get("catalog_database_id") != expected_catalog_database_id:
            raise ValueError("This backup belongs to a different Lightroom catalog")

        declared_files = manifest.get("files")
        if not isinstance(declared_files, list) or not declared_files:
            raise ValueError("Backup manifest does not contain a file inventory")
        expected_paths: set[str] = set()
        for item in declared_files:
            if not isinstance(item, dict):
                raise ValueError("Backup manifest contains an invalid file entry")
            relative = PurePosixPath(str(item.get("path") or ""))
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise ValueError("Backup manifest contains an unsafe file path")
            relative_string = str(relative)
            if relative_string in expected_paths:
                raise ValueError("Backup manifest contains duplicate file entries")
            expected_paths.add(relative_string)
            full_path = os.path.join(staged_db_path, *relative.parts)
            if not os.path.isfile(full_path):
                raise ValueError(f"Backup file is missing: {relative_string}")
            if os.path.getsize(full_path) != int(item.get("size", -1)):
                raise ValueError(f"Backup file size mismatch: {relative_string}")
            if _sha256(full_path) != item.get("sha256"):
                raise ValueError(f"Backup checksum mismatch: {relative_string}")

        actual_paths: set[str] = set()
        for current_root, _, files in os.walk(staged_db_path):
            for filename in files:
                if filename == BACKUP_MANIFEST_NAME:
                    continue
                full_path = os.path.join(current_root, filename)
                actual_paths.add(
                    os.path.relpath(full_path, staged_db_path).replace(os.sep, "/")
                )
        if actual_paths != expected_paths:
            raise ValueError(
                "Backup archive contains files not declared by its manifest"
            )

        ownership_path = os.path.join(staged_db_path, OWNERSHIP_MARKER_NAME)
        with open(ownership_path, encoding="utf-8") as ownership_file:
            restored_ownership = json.load(ownership_file)
        if (
            restored_ownership.get("catalog_database_id")
            != expected_catalog_database_id
        ):
            raise ValueError("Backup ownership marker does not match its manifest")
        for sqlite_filename in SQLITE_FILENAMES:
            sqlite_path = os.path.join(staged_db_path, sqlite_filename)
            if os.path.isfile(sqlite_path):
                _validate_sqlite(sqlite_path)
        return extraction_root, staged_db_path, manifest
    except Exception:
        shutil.rmtree(extraction_root, ignore_errors=True)
        raise


def restore_backup_archive(archive_path: str) -> dict:
    """Restore one validated same-catalog backup with atomic swap and rollback."""
    db_path = config.DB_PATH
    if not db_path or not os.path.isdir(db_path):
        raise FileNotFoundError("The active StyleAI database directory is unavailable")
    ownership = ensure_catalog_ownership(db_path)
    parent = os.path.dirname(db_path)
    extraction_root, staged_db_path, manifest = extract_and_validate_backup(
        archive_path,
        expected_catalog_database_id=ownership["catalog_database_id"],
        staging_parent=parent,
    )
    pre_restore_backup = create_persistent_backup(reason="pre-restore")
    rollback_path = os.path.join(
        parent,
        f".{os.path.basename(db_path)}.restore-rollback-{uuid4().hex}",
    )
    swapped = False
    try:
        from services import policy_runtime

        chroma_service.unload_collections()
        training_service.unload_collections()
        policy_runtime.invalidate_runtime_cache()

        os.replace(db_path, rollback_path)
        os.replace(staged_db_path, db_path)
        swapped = True

        # Backups are excluded from archives. Preserve the current catalog's
        # recovery chain, including the required pre-restore snapshot.
        old_backups = os.path.join(rollback_path, "backups")
        new_backups = os.path.join(db_path, "backups")
        if os.path.isdir(old_backups):
            os.makedirs(new_backups, exist_ok=True)
            for filename in os.listdir(old_backups):
                source = os.path.join(old_backups, filename)
                destination = os.path.join(new_backups, filename)
                if filename.lower().endswith(".zip") and os.path.isfile(source):
                    shutil.copy2(source, destination)

        restored_ownership = ensure_catalog_ownership(db_path)
        if (
            restored_ownership["catalog_database_id"]
            != ownership["catalog_database_id"]
        ):
            raise RuntimeError("Restored catalog ownership validation failed")
        for sqlite_filename in SQLITE_FILENAMES:
            sqlite_path = os.path.join(db_path, sqlite_filename)
            if os.path.isfile(sqlite_path):
                _validate_sqlite(sqlite_path)

        from core.migrations import run_migrations

        run_migrations(db_path, force=True)
        from server_lifecycle import recover_catalog_session

        recover_catalog_session()

        shutil.rmtree(rollback_path)
        rollback_path = ""
        return {
            "success": True,
            "created_at": manifest.get("created_at"),
            "reason": manifest.get("reason"),
            "pre_restore_backup": pre_restore_backup,
        }
    except Exception:
        logger.error("Database restore failed; rolling back", exc_info=True)
        chroma_service.unload_collections()
        training_service.unload_collections()
        if swapped and os.path.isdir(rollback_path):
            failed_path = os.path.join(
                parent,
                f".{os.path.basename(db_path)}.failed-restore-{uuid4().hex}",
            )
            if os.path.isdir(db_path):
                os.replace(db_path, failed_path)
            os.replace(rollback_path, db_path)
            shutil.rmtree(failed_path, ignore_errors=True)
        elif not os.path.isdir(db_path) and os.path.isdir(rollback_path):
            os.replace(rollback_path, db_path)
        raise
    finally:
        shutil.rmtree(extraction_root, ignore_errors=True)


def prune_old_backups(max_keep: int = 10) -> int:
    """
    Remove old backup ZIPs from BACKUPS_DIR, keeping only the newest max_keep files.

    Args:
        max_keep: Number of most recent backup files to retain.

    Returns:
        Number of backup files that were deleted.
    """
    if max_keep <= 0:
        max_keep = 1

    backups_dir = _get_backups_dir()
    if not backups_dir or not os.path.isdir(backups_dir):
        return 0

    try:
        entries = [
            os.path.join(backups_dir, name)
            for name in os.listdir(backups_dir)
            if name.lower().endswith(".zip")
            and os.path.isfile(os.path.join(backups_dir, name))
        ]
    except Exception as e:
        logger.warning("Could not list backups in %s: %s", backups_dir, e)
        return 0

    if len(entries) <= max_keep:
        return 0

    entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    to_delete = entries[max_keep:]
    deleted = 0
    for path in to_delete:
        try:
            os.remove(path)
            deleted += 1
            logger.info("Pruned old DB backup: %s", path)
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("Could not remove old backup %s: %s", path, e)
    if deleted > 0:
        logger.info(
            "Pruned %s old backups in %s, kept %s newest.",
            deleted,
            backups_dir,
            max_keep,
        )
    return deleted


def seconds_until_scheduled_backup(interval: int, *, startup_grace: int = 60) -> int:
    """Return a restart-stable delay based on the newest successful backup."""
    backups_dir = _get_backups_dir()
    newest_mtime: float | None = None
    if backups_dir and os.path.isdir(backups_dir):
        for filename in os.listdir(backups_dir):
            path = os.path.join(backups_dir, filename)
            if filename.lower().endswith(".zip") and os.path.isfile(path):
                mtime = os.path.getmtime(path)
                newest_mtime = (
                    mtime if newest_mtime is None else max(newest_mtime, mtime)
                )
    if newest_mtime is None:
        return max(1, min(int(interval), int(startup_grace)))
    elapsed = max(0.0, datetime.now(UTC).timestamp() - newest_mtime)
    return (
        max(1, int(interval - elapsed)) if elapsed < interval else max(1, startup_grace)
    )


def prune_database(valid_photo_ids: list) -> dict:
    """
    Removes photo metadata, embeddings, and face embeddings for any photo NOT in valid_photo_ids.
    """
    if not valid_photo_ids:
        raise ValueError(
            "Cannot prune database with 0 valid photo IDs. Aborting to prevent accidental data loss."
        )

    try:
        backup_path = create_persistent_backup(reason="pre-prune")
        logger.info("Created automatic pre-prune backup at %s", backup_path)
    except Exception as e:
        logger.error(f"Failed to create pre-prune backup: {e}")
        raise RuntimeError("Database backup failed. Aborting prune operation.")

    valid_set = set(valid_photo_ids)
    all_ids = chroma_service.get_all_image_ids()

    deleted = 0

    for pid in all_ids:
        if pid not in valid_set:
            chroma_service.delete_image(pid)
            training_service.delete_training_example(pid)
            deleted += 1

    logger.info(f"Pruned database: {deleted} deleted from {len(all_ids)} checked.")
    return {"deleted": deleted, "checked": len(all_ids)}
