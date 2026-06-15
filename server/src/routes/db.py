"""
Flask blueprint for database management and utility endpoints.

Provides routes for fetching database statistics, creating backup zips,
migrating old photo IDs, and pruning orphaned records.
"""

from flask import Blueprint, jsonify, send_file, after_this_request, request
import os

from config import logger
from services import db as service_db


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


@db_bp.route("/db/backup", methods=["GET"])
def backup_database():
    try:
        zip_path, backup_name = service_db.build_backup_zip()

        @after_this_request
        def cleanup_backup(response):
            try:
                os.remove(zip_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(
                    "Could not remove temporary backup zip %s: %s", zip_path, e
                )
            return response

        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=backup_name,
            max_age=0,
        )
    except Exception as e:
        logger.error("Database backup failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


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

        result = service_db.prune_database(valid_photo_ids)
        return jsonify({"results": result, "error": None, "warning": None})
    except Exception as e:
        logger.error("Database prune failed: %s", e, exc_info=True)
        return jsonify({"results": None, "error": str(e), "warning": None}), 500
