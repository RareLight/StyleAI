"""
Flask blueprint for database management and utility endpoints.

Provides routes for fetching database statistics, creating backup zips,
migrating old photo IDs, and pruning orphaned records.
"""

from flask import Blueprint, jsonify, request
import os
import shutil

from config import logger
from services import db as service_db
from services import operations


db_bp = Blueprint("db", __name__)


@db_bp.route("/db/stats", methods=["GET"])
def database_stats():
    """
    Return database statistics: indexed photos, faces, persons, and metadata/embedding counts.

    Returns: {
        "photos": { "total", "with_embedding", "with_title", "with_caption", "with_keywords" },
        "faces": { "total" },
        "persons": { "total" }
    }
    """
    try:
        return jsonify(service_db.get_database_stats())
    except Exception as e:
        logger.error(f"Error computing database stats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@db_bp.route("/db/backup", methods=["POST"])
def backup_database():
    try:
        data = request.get_json(silent=True) or {}
        output_path = data.get("output_path")
        if not output_path:
            return jsonify(
                {"results": None, "error": "output_path is required", "warning": None}
            ), 400

        with operations.admission.acquire(
            {"maintenance": 1, "training_upload": 1, "catalog_write": 1},
            priority=15,
        ):
            zip_path, _backup_name = service_db.build_backup_zip(
                persist=False,
                reason="manual-export",
            )

        # Publish the export atomically so a canceled/failed copy is never
        # mistaken for a valid backup.
        partial_output_path = output_path + ".partial"
        try:
            shutil.copy2(zip_path, partial_output_path)
            os.replace(partial_output_path, output_path)
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass
            try:
                os.remove(partial_output_path)
            except OSError:
                pass

        return jsonify(
            {
                "results": {"success": True, "path": output_path},
                "error": None,
                "warning": None,
            }
        )
    except Exception as e:
        logger.error("Database backup failed: %s", e, exc_info=True)
        return jsonify({"results": None, "error": str(e), "warning": None}), 500


@db_bp.route("/db/restore", methods=["POST"])
def restore_database():
    """Restore a validated backup belonging to the active Lightroom catalog."""
    try:
        data = request.get_json(silent=True) or {}
        archive_path = data.get("archive_path")
        if not archive_path:
            return jsonify(
                {"results": None, "error": "archive_path is required", "warning": None}
            ), 400
        with operations.admission.acquire(
            {"maintenance": 1, "training_upload": 1, "catalog_write": 1},
            priority=30,
        ):
            result = service_db.restore_backup_archive(str(archive_path))
        return jsonify({"results": result, "error": None, "warning": None})
    except Exception as exc:
        logger.error("Database restore failed: %s", exc, exc_info=True)
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@db_bp.route("/db/prune", methods=["POST"])
def prune_database():
    """
    Remove orphaned metadata and embeddings for photos that no longer exist in the catalog.
    Expects JSON: { "valid_photo_ids": ["id1", "id2", ...] }
    """
    try:
        data = request.get_json(silent=True) or {}
        valid_photo_ids = data.get("valid_photo_ids")

        if not isinstance(valid_photo_ids, list):
            return jsonify({"error": "valid_photo_ids must be a list of strings"}), 400

        with operations.admission.acquire(
            {
                "maintenance": 1,
                "training_upload": 1,
                "catalog_write": 1,
            },
            priority=20,
        ):
            result = service_db.prune_database(valid_photo_ids)
        return jsonify({"results": result, "error": None, "warning": None})
    except Exception as e:
        logger.error("Database prune failed: %s", e, exc_info=True)
        return jsonify({"results": None, "error": str(e), "warning": None}), 500
