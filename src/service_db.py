import os
import tempfile
import zipfile
from datetime import datetime

from config import logger, DB_PATH
import service_chroma as chroma_service
import service_persons as persons_service


def get_database_stats() -> dict:
    """Return database statistics for photos, faces, and persons."""
    image_stats = chroma_service.get_image_metadata_stats()
    face_count = chroma_service.get_face_count()
    persons = persons_service.list_persons()
    person_count = len(persons)

    return {
        "photos": {
            "total": image_stats["total"],
            "with_embedding": image_stats["with_embedding"],
            "with_title": image_stats["with_title"],
            "with_caption": image_stats["with_caption"],
            "with_keywords": image_stats["with_keywords"],
            "with_vertexai": image_stats["with_vertexai"],
        },
        "faces": {"total": face_count},
        "persons": {"total": person_count},
    }


def build_backup_zip() -> tuple[str, str]:
    """Create a temporary ZIP containing all persistent DB files."""
    if not os.path.isdir(DB_PATH):
        raise FileNotFoundError(f"Database path does not exist or is not a directory: {DB_PATH}")

    backup_name = f"lrgeniusai-backend-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    fd, zip_path = tempfile.mkstemp(prefix="lrgeniusai-backup-", suffix=".zip")
    os.close(fd)

    root_parent = os.path.dirname(DB_PATH)
    included_files = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for current_root, _, files in os.walk(DB_PATH):
            files.sort()
            for filename in files:
                full_path = os.path.join(current_root, filename)
                if not os.path.isfile(full_path):
                    continue
                archive_name = os.path.relpath(full_path, root_parent)
                archive.write(full_path, arcname=archive_name)
                included_files += 1

    logger.info("Created DB backup zip at %s with %s files from %s", zip_path, included_files, DB_PATH)
    return zip_path, backup_name
