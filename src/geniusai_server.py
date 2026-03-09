import os
import sys
import threading
import time
from flask import Flask, jsonify
from waitress import serve
import datetime
import json

# Import modularized components
from config import logger, args, DB_PATH
logger.info("Imported config")

# Lazy import server_lifecycle to speed up startup
import server_lifecycle
logger.info("Imported server_lifecycle")

# Import blueprints only (services are imported by routes when needed)
from routes_index import index_bp
from routes_search import search_bp
from routes_server import server_bp
from routes_db import db_bp
from routes_import import import_bp
from routes_clip import clip_bp
from routes_faces import faces_bp
import service_chroma
import service_persons

app = Flask(__name__)
logger.info("Flask app created")

# Register blueprints
app.register_blueprint(index_bp)
app.register_blueprint(search_bp)
app.register_blueprint(server_bp)
app.register_blueprint(db_bp)
app.register_blueprint(clip_bp)
app.register_blueprint(import_bp)
app.register_blueprint(faces_bp)


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _start_faces_cluster_scheduler() -> None:
    """
    Periodically run face clustering in a background thread while the backend is running.

    Controlled via environment variables:
      GENIUSAI_FACES_CLUSTER_ENABLED    (bool; default: false)
      GENIUSAI_FACES_CLUSTER_INTERVAL   (seconds; default: 3600)
      GENIUSAI_FACES_CLUSTER_DISTANCE   (float cosine distance; default: 0.5)
      GENIUSAI_FACES_CLUSTER_MIN_FACES  (int; optional; if unset -> None)
      GENIUSAI_FACES_CLUSTER_LINKAGE    ("complete" | "average"; default: "complete")
    """
    if not _bool_env("GENIUSAI_FACES_CLUSTER_ENABLED", default=False):
        logger.info("Faces cluster scheduler disabled (GENIUSAI_FACES_CLUSTER_ENABLED not set).")
        return

    try:
        interval = int(os.environ.get("GENIUSAI_FACES_CLUSTER_INTERVAL", "3600"))
    except ValueError:
        interval = 3600

    try:
        distance = float(os.environ.get("GENIUSAI_FACES_CLUSTER_DISTANCE", "0.5"))
    except ValueError:
        distance = 0.5

    min_faces_raw = os.environ.get("GENIUSAI_FACES_CLUSTER_MIN_FACES", "").strip()
    min_faces = None
    if min_faces_raw:
        try:
            min_faces = int(min_faces_raw)
        except ValueError:
            min_faces = None

    linkage = (os.environ.get("GENIUSAI_FACES_CLUSTER_LINKAGE", "complete") or "complete").strip().lower()
    if linkage not in ("complete", "average"):
        linkage = "complete"

    def _loop() -> None:
        logger.info(
            "Starting faces cluster scheduler: interval=%ss, distance=%.3f, min_faces=%s, linkage=%s",
            interval,
            distance,
            str(min_faces) if min_faces is not None else "None",
            linkage,
        )
        while True:
            try:
                summary = service_persons.run_clustering(
                    distance_threshold=distance,
                    min_faces_per_person=min_faces,
                    linkage=linkage,
                )
                logger.info("Periodic faces clustering summary: %s", summary)
            except Exception as e:
                logger.error("Periodic faces clustering failed: %s", e, exc_info=True)
            time.sleep(max(60, interval))

    t = threading.Thread(target=_loop, name="faces-cluster-scheduler", daemon=True)
    t.start()

@app.errorhandler(500)
def handle_internal_server_error(e):
    logger.error(f"Internal Server Error: {e}")
    return jsonify({"error": "Internal Server Error"}), 500

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("LrGenius Server starting...")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"Database: {args.db_path}")
    logger.info("=" * 60)
    
    # Optional one-shot ID migration for deployed databases.
    # Set GENIUSAI_MIGRATION_FILE to a JSON list/object with mappings.
    migration_file = os.environ.get("GENIUSAI_MIGRATION_FILE", "").strip()
    if migration_file:
        migration_path = migration_file if os.path.isabs(migration_file) else os.path.join(DB_PATH, migration_file)
        try:
            with open(migration_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            mappings = payload.get("mappings", payload)
            summary = service_chroma.migrate_photo_ids(mappings or [])
            logger.info("Startup photo_id migration summary: %s", summary)
        except Exception as e:
            logger.error("Startup photo_id migration failed: %s", e, exc_info=True)

    # Mark server as ready for startup scripts
    server_lifecycle.write_ok_file()
    
    # Write PID for lifecycle management
    server_lifecycle.write_pid_file()

    # Start optional background faces clustering scheduler
    _start_faces_cluster_scheduler()
    
    host = os.environ.get("GENIUSAI_HOST", "127.0.0.1")
    port = int(os.environ.get("GENIUSAI_PORT", "19819"))
    try:
        if args.debug:
            logger.info(f"Starting Flask development server in debug mode on http://{host}:{port}")
            app.run(debug=True, host=host, port=port)
        else:
            logger.info(f"Starting production server on http://{host}:{port}")
            serve(app, host=host, port=port, threads=4)
    finally:
        logger.info("Shutting down server...")
        server_lifecycle.remove_pid_file()
        server_lifecycle.remove_ok_file()
        logger.info("Bye.")
