from flask import Blueprint, jsonify, send_file, after_this_request
import os

from config import logger
import service_db


db_bp = Blueprint('db', __name__)


@db_bp.route('/db/stats', methods=['GET'])
def database_stats():
    """
    Return database statistics: indexed photos, faces, persons, and metadata/embedding counts.

    Returns: {
        "photos": { "total", "with_embedding", "with_title", "with_caption", "with_keywords", "with_vertexai" },
        "faces": { "total" },
        "persons": { "total" }
    }
    """
    try:
        return jsonify(service_db.get_database_stats())
    except Exception as e:
        logger.error(f"Error computing database stats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@db_bp.route('/db/backup', methods=['GET'])
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
                logger.warning("Could not remove temporary backup zip %s: %s", zip_path, e)
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
