import os
import sys
import threading
import time
from flask import Flask, jsonify, request
from waitress import serve
from werkzeug.exceptions import HTTPException

# Import modularized components
import config
from config import logger, args
from services.version import get_backend_version_info

import json

import server_lifecycle
from services import backup

# Import blueprints only (services are imported by routes when needed)
from routes.index import index_bp
from routes.edit import edit_bp
from routes.server import server_bp
from routes.db import db_bp
from routes.training import training_bp
from routes.style_edit import style_edit_bp
from routes.style_catalog import style_catalog_bp
from routes.clip import clip_bp
from routes.image_processing import bp as image_processing_bp
from services import chroma as service_chroma
from services import db as service_db

app = Flask(__name__)
logger.info("Flask app created")


@app.before_request
def _touch_activity():
    """Record that the server received an HTTP request so idle-shutdown knows we're alive."""
    server_lifecycle.note_request()


# Register blueprints — core style-learning endpoints only
app.register_blueprint(index_bp)
app.register_blueprint(edit_bp)
app.register_blueprint(server_bp)
app.register_blueprint(db_bp)
app.register_blueprint(training_bp)
app.register_blueprint(style_edit_bp)
app.register_blueprint(style_catalog_bp)
app.register_blueprint(clip_bp)
app.register_blueprint(image_processing_bp)


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _start_housekeeping_scheduler() -> None:
    """
    Periodically run housekeeping tasks such as database backups.

    Controlled via environment variables:
      STYLEAI_BACKUP_ENABLED       (bool; default: false)
      STYLEAI_BACKUP_INTERVAL      (seconds; default: 86400, min 600)
      STYLEAI_BACKUP_MAX_KEEP      (int; number of backup files to keep; default: 14)
    """
    if not _bool_env("STYLEAI_BACKUP_ENABLED", default=False):
        logger.info("DB backup scheduler disabled (STYLEAI_BACKUP_ENABLED not set).")
        return

    try:
        interval = int(os.environ.get("STYLEAI_BACKUP_INTERVAL", "86400"))
    except ValueError:
        interval = 86400
    interval = max(600, interval)

    try:
        max_keep = int(os.environ.get("STYLEAI_BACKUP_MAX_KEEP", "14"))
    except ValueError:
        max_keep = 14
    if max_keep <= 0:
        max_keep = 1

    def _loop() -> None:
        logger.info(
            "Starting DB backup scheduler: interval=%ss, max_keep=%s",
            interval,
            max_keep,
        )
        while True:
            try:
                zip_path, backup_name = service_db.build_backup_zip()
                logger.info(
                    "Periodic DB backup created: %s (%s)", backup_name, zip_path
                )
                service_db.prune_old_backups(max_keep=max_keep)
                try:
                    os.remove(zip_path)
                except OSError as e:
                    logger.warning(
                        "Could not remove temporary backup zip %s: %s", zip_path, e
                    )
            except Exception as e:
                logger.error("Periodic DB backup failed: %s", e, exc_info=True)
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="db-backup-scheduler", daemon=True)
    t.start()


# Endpoints that don't need (and shouldn't pay the cost of) a chroma init:
# liveness checks and the explicit init route, which handles db_path itself.
_DB_PATH_BYPASS_ENDPOINTS = frozenset(
    {
        "server.ping",
        "server.initialize",
    }
)


@app.before_request
def _auto_bind_db_path():
    """Fallback recovery: if a request carries `db_path` and the backend
    isn't bound to it yet (e.g. after an unexpected process restart), bind
    it transparently before the route runs. No-op when already matching.
    """
    if request.endpoint in _DB_PATH_BYPASS_ENDPOINTS:
        return

    db_path = None
    if request.method in ("POST", "PUT", "PATCH"):
        data = request.get_json(silent=True, cache=True)
        if isinstance(data, dict):
            db_path = data.get("db_path")
    if not db_path:
        db_path = request.args.get("db_path")
    if not db_path:
        return

    from core.migrations import run_migrations

    try:
        if service_chroma.ensure_db_path(db_path):
            run_migrations(db_path)
    except Exception as e:
        # Don't 500 here — let the route's own error handling report the
        # underlying failure with more context. We only log so we can see
        # an init crash separately from the route-level failure.
        logger.error("Auto-bind to db_path %s failed: %s", db_path, e, exc_info=True)


@app.after_request
def enforce_consistent_api_envelope(response):
    """
    Ensure all JSON responses conform to the uniform API envelope:
    { "results": {...}, "error": null, "warning": null }
    """
    if response.is_json:
        try:
            data = response.get_json()
            if isinstance(data, dict):
                # Check if it already conforms strictly to the envelope
                # We check if these three keys are the ONLY keys in the dictionary
                if set(data.keys()) == {"results", "error", "warning"}:
                    return response

                # It does not conform. We need to wrap it.
                error_val = data.pop("error", None)
                if not error_val and data.get("status") == "error":
                    error_val = data.pop("message", "Unknown error")

                warning_val = data.pop("warning", None)

                # If it's an error, results should be None. Otherwise, results is the data dict itself.
                results_val = None if error_val else data

                new_data = {
                    "results": results_val,
                    "error": str(error_val) if error_val else None,
                    "warning": str(warning_val) if warning_val else None,
                }
                response.set_data(json.dumps(new_data))
        except Exception as e:
            logger.error(f"Error enforcing API envelope: {e}", exc_info=True)
    return response


@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if isinstance(e, HTTPException):
        return e

    logger.error(f"Unhandled Exception: {e}", exc_info=True)
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    version_info = get_backend_version_info()
    logger.info("=" * 60)
    logger.info(
        "StyleAI Server version %s (build %s)",
        version_info.get("backend_version", "?"),
        version_info.get("backend_build", "?"),
    )
    logger.info("StyleAI Server starting...")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(
        f"Database Path: {config.DB_PATH or 'Idle (waiting for plugin initialize)'}"
    )
    logger.info("=" * 60)

    # Mark server as ready for startup scripts
    server_lifecycle.write_ok_file()

    # Write PID for lifecycle management
    server_lifecycle.write_pid_file()

    # Start optional background schedulers (housekeeping only)
    _start_housekeeping_scheduler()

    # Start idle monitor (model unload + server auto-shutdown)
    server_lifecycle._ensure_unloader_thread()

    # Start automated backups if enabled (default true)
    if os.environ.get("STYLEAI_BACKUP_ENABLED", "true").lower() == "true":
        import threading

        threading.Thread(
            target=backup.run_backup_loop,
            args=(server_lifecycle.GLOBAL_CANCEL_EVENT,),
            daemon=True,
        ).start()

    # Priority 8 Security: Strictly bind to localhost to prevent local network exposure.
    # We only allow override via STYLEAI_HOST when running in debug mode.
    if args.debug:
        host = os.environ.get("STYLEAI_HOST", "127.0.0.1")
    else:
        host = "127.0.0.1"

    port = int(os.environ.get("STYLEAI_PORT", "19819"))
    try:
        if args.debug:
            logger.info(
                f"Starting Flask development server in debug mode on http://{host}:{port}"
            )
            app.run(debug=True, host=host, port=port)
        else:
            import multiprocessing

            cpu_count = multiprocessing.cpu_count()
            # Scale threads between 8 and 16 based on hardware
            num_threads = max(8, min(16, cpu_count))
            logger.info(
                f"Starting production server on http://{host}:{port} with {num_threads} threads"
            )
            serve(app, host=host, port=port, threads=num_threads)
    finally:
        logger.info("Shutting down server...")
        server_lifecycle.remove_pid_file()
        server_lifecycle.remove_ok_file()
        logger.info("Bye.")
