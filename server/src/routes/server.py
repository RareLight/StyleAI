import os
from flask import Blueprint, jsonify, request, send_file

import server_lifecycle
import config
from config import logger
from services.metadata import get_analysis_service
from services import version as service_version
from services import update as service_update

server_bp = Blueprint("server", __name__)


@server_bp.route("/ping", methods=["GET"])
def ping():
    # logger.info("Ping request received")
    return "pong"


@server_bp.route("/shutdown", methods=["POST"])
def shutdown():
    server_lifecycle.request_shutdown()
    return jsonify({"status": "Server is shutting down..."})


@server_bp.route("/cancel_all_tasks", methods=["POST"])
def cancel_all_tasks():
    """Immediately flags any running backend tasks to abort their processing loops."""
    logger.info("Cancel request received: Aborting all ongoing backend tasks.")
    server_lifecycle.GLOBAL_CANCEL_EVENT.set()
    from services.index import discard_pending_index_queue
    from services import image_cache

    discarded = discard_pending_index_queue()
    released_cached_images = image_cache.clear()
    return jsonify(
        {
            "status": "Canceled active tasks.",
            "discarded": discarded,
            "released_cached_images": released_cached_images,
        }
    )


@server_bp.route("/clear_cancel_tasks", methods=["POST"])
def clear_cancel_tasks():
    """Clears the cancellation flag to allow new tasks to run."""
    logger.info("Clear cancel request received: Backend ready for new tasks.")
    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()
    return jsonify({"status": "Ready for new tasks."})


@server_bp.route("/unload", methods=["POST"])
def unload():
    """Unload models and collections from memory without stopping the server."""
    logger.info("Unload request received via API")
    server_lifecycle.unload_all_resources()
    return jsonify({"status": "Resources unloaded successfully."})


@server_bp.route("/backup", methods=["POST"])
def backup_db():
    """Backward-compatible automatic backup trigger."""
    data = request.get_json() or {}
    try:
        from services import db as service_db
        from services import operations

        raw_max_keep = data.get("max_keep", data.get("rotation_days", 14))
        max_keep = max(1, int(raw_max_keep))
        with operations.admission.acquire(
            {"maintenance": 1, "training_upload": 1, "catalog_write": 1},
            priority=15,
        ):
            path = service_db.create_persistent_backup(
                reason="automatic",
                max_keep=max_keep,
            )

        return jsonify(
            {"results": {"status": "Backup created successfully.", "path": path}}
        )
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@server_bp.route("/restart", methods=["POST"])
def restart():
    """
    Gracefully shut down the server.
    If running as an OS service (launchd/Windows Service), it will be automatically restarted.
    """
    logger.info("Restart request received via API")
    server_lifecycle.request_shutdown()
    return jsonify({"status": "Restarting..."})


@server_bp.route("/update/apply", methods=["POST"])
def update_apply():
    """
    Apply a code-only update from a manifest.
    JSON: { "manifest": {...}, "plugin_path": "/path/to/plugin" }
    Only accepted from loopback to prevent arbitrary file-write via network.
    """
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(f"Rejected /update/apply from non-loopback address: {remote}")
        return jsonify(
            {"error": "update endpoint only available on local backend"}
        ), 403

    data = request.get_json(silent=True) or {}
    manifest = data.get("manifest")
    plugin_path = data.get("plugin_path")

    if not manifest or not plugin_path:
        return jsonify({"error": "manifest and plugin_path are required"}), 400

    success, message = service_update.perform_code_update(manifest, plugin_path)
    if success:
        # The updater thread handles backend shutdown after spawning the GUI process.
        # Do NOT call request_shutdown() here — it would race with the response.
        logger.info("Update process started successfully")
        return jsonify({"status": "success", "message": message})
    else:
        return jsonify({"error": message}), 500


@server_bp.route("/initialize", methods=["POST"])
def initialize():
    """
    Called by the Lightroom plugin to 'attach' the running service to a specific catalog.
    JSON: { "db_path": "/path/to/catalog/folder/styleai.db" }
    """
    data = request.get_json(silent=True) or {}
    db_path = data.get("db_path")
    if not db_path:
        return jsonify({"error": "db_path is required"}), 400

    from services import chroma as service_chroma
    from services import db as service_db
    from services.chroma import CatalogOwnershipError
    from core.migrations import run_migrations

    try:
        switched = service_chroma.ensure_db_path(db_path)
        service_db.ensure_catalog_ownership(db_path)
        # Run migrations on the catalog's database path immediately after binding
        if switched:
            run_migrations(db_path)
            server_lifecycle.recover_catalog_session()
    except CatalogOwnershipError as e:
        logger.warning("Rejected catalog switch: %s", e)
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        logger.error(f"Failed to initialize database at {db_path}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    if not switched:
        return jsonify({"status": "already_initialized", "db_path": db_path})

    server_lifecycle.write_ok_file()
    server_lifecycle.write_pid_file()
    return jsonify({"status": "success", "db_path": db_path})


@server_bp.route("/styles/rename", methods=["POST"])
def rename_style():
    """
    Rename an AI-discovered style by setting a custom user_style_name.
    JSON: { "style_id": "...", "new_name": "..." }
    """
    data = request.get_json(silent=True) or {}
    style_id = data.get("style_id")
    new_name = data.get("new_name")

    if not style_id or not new_name:
        return jsonify({"error": "style_id and new_name are required"}), 400

    from services import policy_runtime

    try:
        success = policy_runtime.rename_active_policy(style_id, new_name)
        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Style not found"}), 404
    except Exception as e:
        logger.error(f"Failed to rename style {style_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@server_bp.route("/models", methods=["GET", "POST"])
def list_models():
    """
    Returns all available multimodal models from all providers.

    Dynamically checks availability of Ollama and LM Studio on each request.
    Always filters for multimodal (vision-capable) models only.

    Returns: {
        "models": {
            "ollama": [{"key": "model1", "label": "model1"}],
            "lmstudio": [{
                "key": "publisher/model@q4_k_m",
                "label": "Model - Q4_K_M / GGUF / publisher"
            }]
        }
    }
    """
    data = request.get_json(silent=True) if request.method == "POST" else None
    requested_provider = (data or {}).get("provider")
    ax_model_root = (data or {}).get("ax_model_root")
    logger.info("Models request received")

    try:
        service = get_analysis_service()
        if requested_provider is not None:
            service.set_active_provider(requested_provider, ax_model_root=ax_model_root)
        models = service.get_available_models()
        draft_models = service.get_available_draft_models()
        unavailable_speculation = service.get_unavailable_speculation_models()
        return jsonify(
            {
                "models": models,
                "draft_models": draft_models,
                "unavailable_speculation": unavailable_speculation,
                "active_provider": service.get_active_provider(),
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error listing models: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@server_bp.route("/metadata/provider", methods=["GET", "POST"])
def metadata_provider():
    """Read or set the single active local metadata API provider."""
    service = get_analysis_service()
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or "provider" not in data:
            return jsonify({"error": "provider is required"}), 400
        try:
            service.set_active_provider(
                data["provider"], ax_model_root=data.get("ax_model_root")
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify(
        {
            "active_provider": service.get_active_provider(),
            "supported_providers": sorted(
                configured
                for configured in ("disabled", "ollama", "lmstudio", "axengine")
            ),
        }
    )


@server_bp.route("/version", methods=["GET"])
def version():
    return jsonify(service_version.get_backend_version_info())


@server_bp.route("/version/check", methods=["POST"])
def version_check():
    data = request.get_json(silent=True) or {}
    plugin_version = data.get("plugin_version")
    plugin_release_tag = data.get("plugin_release_tag")
    plugin_build = data.get("plugin_build")

    result = service_version.check_plugin_backend_version(
        plugin_version=plugin_version,
        plugin_build=plugin_build,
        plugin_release_tag=plugin_release_tag,
    )
    return jsonify(result), 200


@server_bp.route("/health", methods=["GET"])
def health():
    """Return health status of various backend components."""
    health_data = {}

    # Model health (CLIP)
    health_data.update(server_lifecycle.get_health_status())

    # LLM Provider health
    health_data.update(get_analysis_service().get_health_status())

    return jsonify(health_data)


@server_bp.route("/debug/captures", methods=["GET", "POST"])
def diagnostic_captures():
    """Inspect or clear local diagnostic image captures."""
    from services import audit

    data = request.get_json(silent=True) or {}
    output_dir = data.get("path") or request.args.get("path")
    try:
        if request.method == "POST":
            result = audit.clear_diagnostic_captures(output_dir)
        else:
            result = audit.get_capture_info(output_dir)
        return jsonify({"results": result, "error": None, "warning": None})
    except ValueError as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error("Diagnostic capture maintenance failed", exc_info=True)
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@server_bp.route("/logs", methods=["GET"])
def get_logs():
    """
    Returns backend logs and optionally local Ollama logs if accessible.
    """
    logger.debug("GET /logs request received")
    logs = {}

    # 1. Backend logs
    log_path = config.LOG_PATH
    if os.path.isfile(log_path):
        try:
            logger.debug(f"Reading backend logs from: {log_path}")
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                # Return last 1MB of logs to avoid huge response
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 1024 * 1024))
                logs["backend"] = f.read()
            logger.debug("Successfully read backend logs")
        except Exception as e:
            logger.error(f"Failed to read backend logs: {e}")
            logs["backend_error"] = str(e)

    # 2. Try to find Ollama logs on the server's machine
    ollama_log_paths = [
        os.path.expanduser("~/.ollama/logs/server.log"),
        "/root/.ollama/logs/server.log",
        r"C:\Users\%USERNAME%\AppData\Local\ollama\server.log",
    ]
    logger.debug("Searching for Ollama logs...")
    for p in ollama_log_paths:
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            try:
                logger.debug(f"Found Ollama logs at: {p}")
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 512 * 1024))  # Last 512KB
                    logs["ollama"] = f.read()
                break
            except Exception as e:
                logger.warning(f"Failed to read Ollama log at {p}: {e}")

    # 3. Try to find LM Studio logs
    lmstudio_log_paths = [
        os.path.expanduser("~/Library/Logs/LM Studio/main.log"),
        r"%APPDATA%\LM Studio\logs\main.log",
    ]
    logger.debug("Searching for LM Studio logs...")
    for p in lmstudio_log_paths:
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            try:
                logger.debug(f"Found LM Studio logs at: {p}")
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 512 * 1024))
                    logs["lmstudio"] = f.read()
                break
            except Exception as e:
                logger.warning(f"Failed to read LM Studio log at {p}: {e}")

    logger.debug(f"Log fetch complete. Found: {list(logs.keys())}")
    return jsonify(logs)


@server_bp.route("/logs/raw/<log_type>", methods=["GET"])
def get_raw_log(log_type):
    """
    Returns the raw log file for the specified type (backend, ollama, lmstudio).
    """
    logger.debug(f"GET /logs/raw/{log_type} request received")

    path = None
    if log_type == "backend":
        path = config.LOG_PATH
    elif log_type == "ollama":
        ollama_log_paths = [
            os.path.expanduser("~/.ollama/logs/server.log"),
            "/root/.ollama/logs/server.log",
            r"C:\Users\%USERNAME%\AppData\Local\ollama\server.log",
        ]
        for p in ollama_log_paths:
            p = os.path.expandvars(p)
            if os.path.isfile(p):
                path = p
                break
    elif log_type == "lmstudio":
        lmstudio_log_paths = [
            os.path.expanduser("~/Library/Logs/LM Studio/main.log"),
            r"%APPDATA%\LM Studio\logs\main.log",
        ]
        for p in lmstudio_log_paths:
            p = os.path.expandvars(p)
            if os.path.isfile(p):
                path = p
                break

    if path and os.path.isfile(path):
        return send_file(path, mimetype="text/plain")

    return jsonify({"error": f"Log file for {log_type} not found"}), 404
