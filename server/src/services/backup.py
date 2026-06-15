import os
import shutil
import time
import zipfile
from pathlib import Path
import threading
from config import logger, DB_PATH

BACKUP_INTERVAL_SECONDS = 86400  # 24 hours

def _do_backup(rotation_days: int = 0):
    """Perform the actual backup of styles.sqlite and chroma/.
    If rotation_days > 0, old backups beyond that many days are removed."""
    try:
        backup_dir = Path(DB_PATH) / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_path = backup_dir / f"styleai_backup_{timestamp}.zip"

        styles_db = Path(DB_PATH) / "styles.sqlite"
        chroma_dir = Path(DB_PATH) / "chroma"

        if not styles_db.exists() and not chroma_dir.exists():
            logger.info("Backup skipped: No databases found to backup.")
            return

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if styles_db.exists():
                zipf.write(styles_db, arcname="styles.sqlite")
            
            if chroma_dir.exists():
                for root, _, files in os.walk(chroma_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(DB_PATH)
                        zipf.write(file_path, arcname=str(arcname))
        
        logger.info(f"Successfully created database backup: {zip_path.name}")

        # Rotate old backups
        if rotation_days > 0:
            cutoff_time = time.time() - (rotation_days * 86400)
            backups = sorted(backup_dir.glob("styleai_backup_*.zip"))
            for old_backup in backups:
                try:
                    if old_backup.stat().st_mtime < cutoff_time:
                        old_backup.unlink()
                        logger.debug(f"Removed old backup: {old_backup.name} (older than {rotation_days} days)")
                except Exception as e:
                    logger.warning(f"Failed to remove old backup {old_backup.name}: {e}")

    except Exception as e:
        logger.error(f"Database backup failed: {e}", exc_info=True)


def run_backup_loop(cancel_event: threading.Event):
    """Run the backup loop in a background thread."""
    logger.info("Starting automated database backup service.")
    
    # Do an initial backup on startup if the last backup is older than 24h
    try:
        if DB_PATH is None:
            logger.warning("DB_PATH is not set, skipping backup.")
            return

        backup_dir = Path(DB_PATH) / "backups"
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("styleai_backup_*.zip"))
            if backups:
                last_backup = backups[-1]
                age = time.time() - last_backup.stat().st_mtime
                if age < BACKUP_INTERVAL_SECONDS:
                    logger.info(f"Skipping initial backup. Last backup was {age/3600:.1f} hours ago.")
                else:
                    _do_backup()
            else:
                _do_backup()
        else:
            _do_backup()
    except Exception as e:
        logger.error(f"Error checking initial backup: {e}")

    # Enter polling loop
    while not cancel_event.is_set():
        if cancel_event.wait(BACKUP_INTERVAL_SECONDS):
            break
        _do_backup()
    logger.info("Automated backup service stopped.")
