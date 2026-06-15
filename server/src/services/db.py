"""
Database operations service.

Handles non-search operations across ChromaDB and SQLite, including
generating statistics, migrating old ID formats, producing full backup zips,
and pruning orphaned records.
"""

import config
from . import chroma as chroma_service
from config import logger

import os
import shutil
import tempfile
import zipfile
from datetime import datetime


# Ordner für serverseitig aufgehobene Backups: Docker /data/db/backups, Standalone <db-path>/backups
def _get_backups_dir():
    if not config.DB_PATH:
        return None
    return os.path.join(config.DB_PATH, "backups")


def get_database_stats() -> dict:
    """Return database statistics for photos, faces, and persons.
    """
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


def build_backup_zip() -> tuple[str, str]:
    """Create a temporary ZIP containing all persistent DB files."""
    db_path = config.DB_PATH
    if not db_path or not os.path.isdir(db_path):
        raise FileNotFoundError(
            f"Database path does not exist or is not a directory: {db_path}"
        )

    backup_name = (
        f"styleai-backend-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    )
    fd, zip_path = tempfile.mkstemp(prefix="styleai-backup-", suffix=".zip")
    os.close(fd)

    root_parent = os.path.dirname(db_path)
    included_files = 0
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for current_root, dirs, files in os.walk(db_path):
            # Do not include the backups directory in the backup (avoid self-embedding and runaway size)
            dirs[:] = [
                d for d in dirs if not (current_root == db_path and d == "backups")
            ]
            files.sort()
            for filename in files:
                full_path = os.path.join(current_root, filename)
                if not os.path.isfile(full_path):
                    continue
                archive_name = os.path.relpath(full_path, root_parent)
                archive.write(full_path, arcname=archive_name)
                included_files += 1

    logger.info(
        "Created DB backup zip at %s with %s files from %s",
        zip_path,
        included_files,
        db_path,
    )

    # Kopie serverseitig aufbewahren (Docker: /data/db/backups, Standalone: <db-path>/backups)
    backups_dir = _get_backups_dir()
    if backups_dir:
        try:
            os.makedirs(backups_dir, exist_ok=True)
            persistent_path = os.path.join(backups_dir, backup_name)
            shutil.copy2(zip_path, persistent_path)
            logger.info("DB backup saved server-side to %s", persistent_path)
        except Exception as e:
            logger.warning("Could not save backup to %s: %s", backups_dir, e)

    return zip_path, backup_name


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


def prune_database(valid_photo_ids: list) -> dict:
    """
    Removes photo metadata, embeddings, and face embeddings for any photo NOT in valid_photo_ids.
    """
    valid_set = set(valid_photo_ids)
    all_ids = chroma_service.get_all_image_ids()

    deleted = 0

    for pid in all_ids:
        if pid not in valid_set:
            chroma_service.delete_image(pid)
            chroma_service.delete_faces_by_photo_uuid(pid)
            deleted += 1

    logger.info(
        f"Pruned database: {deleted} deleted from {len(all_ids)} checked."
    )
    return {"deleted": deleted, "checked": len(all_ids)}
