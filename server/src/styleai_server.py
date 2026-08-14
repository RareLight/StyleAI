import sys
import threading
from flask import Flask, g, jsonify, request
from waitress import serve
from werkzeug.exceptions import HTTPException

# Import modularized components
import config
from config import logger, args
from services.version import get_backend_version_info

import json

import server_lifecycle

# Import blueprints only (services are imported by routes when needed)
from routes.index import index_bp
from routes.server import server_bp
from routes.db import db_bp
from routes.training import training_bp
from routes.style_edit import style_edit_bp
from routes.style_catalog import style_catalog_bp
from routes.clip import clip_bp
from routes.operations import operations_bp
from routes.metadata_benchmark import metadata_benchmark_bp
from services import chroma as service_chroma
from services import db as service_db
from services import operations

app = Flask(__name__)
logger.info("Flask app created")


def _tracks_client_activity(path: str) -> bool:
    return path not in ("/ping", "/health") and not path.startswith("/status")


@app.before_request
def _touch_activity():
    """Protect substantive requests from idle shutdown while they are running."""
    path = request.path or ""
    if _tracks_client_activity(path):
        server_lifecycle.begin_client_request()
        g.styleai_client_activity_active = True


@app.after_request
def _touch_activity_after(response):
    """Give completed long requests a fresh idle window before the next request."""
    if getattr(g, "styleai_client_activity_active", False):
        server_lifecycle.end_client_request()
        g.styleai_client_activity_active = False
    return response


@app.teardown_request
def _release_activity_after_error(_error):
    """Release activity if request processing ended before after_request ran."""
    if getattr(g, "styleai_client_activity_active", False):
        server_lifecycle.end_client_request()
        g.styleai_client_activity_active = False


# Register blueprints — core style-learning endpoints only
app.register_blueprint(index_bp)
app.register_blueprint(server_bp)
app.register_blueprint(db_bp)
app.register_blueprint(training_bp)
app.register_blueprint(style_edit_bp)
app.register_blueprint(style_catalog_bp)
app.register_blueprint(clip_bp)
app.register_blueprint(operations_bp)
app.register_blueprint(metadata_benchmark_bp)


def _start_housekeeping_scheduler() -> None:
    """
    Periodically run housekeeping tasks such as database backups.

    Controlled via --disable-backup, --backup-interval, and --backup-max-keep.
    Due time is based on the newest persisted backup so backend restarts do not
    postpone snapshots indefinitely.
    """
    if config.args.disable_backup:
        logger.info("DB backup scheduler disabled (--disable-backup is set).")
        return

    interval = max(600, config.args.backup_interval)

    max_keep = max(1, config.args.backup_max_keep)

    def _loop() -> None:
        logger.info(
            "Starting DB backup scheduler: interval=%ss, max_keep=%s",
            interval,
            max_keep,
        )
        delay = service_db.seconds_until_scheduled_backup(interval)
        while not server_lifecycle.GLOBAL_SHUTDOWN_EVENT.wait(delay):
            if config.DB_PATH:
                try:
                    with operations.admission.acquire(
                        {"maintenance": 1, "training_upload": 1, "catalog_write": 1},
                        priority=-10,
                        cancel_event=server_lifecycle.GLOBAL_SHUTDOWN_EVENT,
                    ):
                        backup_path = service_db.create_persistent_backup(
                            reason="scheduled",
                            max_keep=max_keep,
                        )
                        pruned_jobs = operations.prune_terminal_jobs(config.DB_PATH)
                    logger.info("Periodic DB backup created: %s", backup_path)
                    if pruned_jobs:
                        logger.info("Pruned %s old operation job(s)", pruned_jobs)
                except InterruptedError:
                    logger.info("Periodic DB backup canceled during shutdown")
                except Exception as e:
                    logger.error("Periodic DB backup failed: %s", e, exc_info=True)
            else:
                logger.debug("Skipping scheduled backup: no catalog is attached")
            delay = interval

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
            service_db.ensure_catalog_ownership(db_path)
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

    if config.DB_PATH:
        service_db.ensure_catalog_ownership(config.DB_PATH)
        server_lifecycle.recover_catalog_session()

    # Mark server as ready for startup scripts
    server_lifecycle.write_ok_file()

    # Write PID for lifecycle management
    server_lifecycle.write_pid_file()

    # Preserve clean session state and marker ownership if launchd or another
    # process manager asks the backend to terminate gracefully.
    server_lifecycle.install_signal_handlers()

    # Start optional background schedulers (housekeeping only)
    _start_housekeeping_scheduler()

    # Start idle monitor (model unload + server auto-shutdown)
    server_lifecycle._ensure_unloader_thread()

    # Preload models asynchronously to prevent blocking the first request
    server_lifecycle.preload_models_async()

    # The Lightroom bridge is process-local and is never network-addressable.
    host = "127.0.0.1"
    port = config.args.port
    try:
        if args.debug:
            logger.info(
                f"Starting Flask development server in debug mode on http://{host}:{port}"
            )
            app.run(debug=True, host=host, port=port)
        else:
            num_threads = config.STYLEAI_HTTP_THREADS
            logger.info(
                f"Starting production server on http://{host}:{port} with {num_threads} threads"
            )
            serve(app, host=host, port=port, threads=num_threads)
    finally:
        logger.info("Shutting down server...")
        server_lifecycle.remove_pid_file()
        server_lifecycle.remove_ok_file()
        logger.info("Bye.")
