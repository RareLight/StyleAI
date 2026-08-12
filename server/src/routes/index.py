import time
from collections import deque
import os
import queue
import config

from flask import Blueprint, request, jsonify

from config import logger, STYLEAI_METADATA_CACHE_BYTES
from services import chroma as chroma_service
from services.index import process_image_task, get_photo_ids_needing_processing
import base64
import json

from utils.request_parsing import _extract_options, _extract_photo_ids
from services import image_cache

index_bp = Blueprint("index", __name__)

# Store timestamps of the last 100 requests to calculate processing speed
request_timestamps = deque(maxlen=100)


@index_bp.route("/index", methods=["POST"])
def index_images_batch():
    """
    Receives a batch of images, processes them synchronously, and indexes them.
    Returns a 200 OK status once all images are processed.
    """
    logger.info("Index request received")

    import server_lifecycle

    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()

    images = request.files.getlist("image")
    photo_ids = _extract_photo_ids(request.form)

    options = _extract_options(request.form)

    if not images or not photo_ids or len(images) != len(photo_ids):
        return jsonify(
            {
                "error": "Mismatch between number of images and photo IDs, or no images provided"
            }
        ), 400

    batch_size = len(images)
    current_time = time.time()
    for _ in range(batch_size):
        request_timestamps.append(current_time)

    if len(request_timestamps) > 10:
        time_span = request_timestamps[-1] - request_timestamps[0]
        if time_span > 1:
            images_per_second = len(request_timestamps) / time_span
            logger.info(f"Indexing at {images_per_second:.2f} images/sec")

    image_triplets = []
    for i in range(batch_size):
        file = images[i]
        photo_id = photo_ids[i]

        if not file or not photo_id:
            logger.warning(
                "Skipping an entry in the batch due to missing file or photo_id."
            )
            continue

        image_triplets.append((file.read(), photo_id, file.filename, None))

    if not image_triplets:
        logger.info("No valid images to process in the batch.")
        return jsonify(
            {"status": "processed", "success_count": 0, "failure_count": batch_size}
        ), 200

    success_count, failure_count, error_messages, warnings = process_image_task(
        image_triplets, options
    )

    logger.info(
        f"Batch processing complete. Success: {success_count}, Failures: {failure_count}."
    )

    if success_count == 0:
        logger.warning("No images were successfully processed in the batch.")
        err_msg = "No images were successfully processed"
        if error_messages:
            unique_errs = list(dict.fromkeys(error_messages))
            err_msg += ": " + " | ".join(unique_errs[:5])
        return jsonify({"error": err_msg}), 500

    return jsonify(
        {
            "status": "processed",
            "success_count": success_count,
            "failure_count": failure_count,
            "error_messages": error_messages,
            "warnings": warnings,
        }
    ), 200


@index_bp.route("/index_base64", methods=["POST"])
def index_images_batch_base64():
    """
    Receives a single image base64 encoded, processes it, and indexes it.
    Returns a 200 OK status once processed.
    """
    logger.info("Index base64 request received")
    import server_lifecycle

    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()

    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400

    # Extract required fields
    image = data.get("image")
    photo_id = data.get("photo_id") or data.get("uuid")
    filename = data.get("filename")

    if not image or not photo_id or not filename:
        logger.warning(
            "Index base64 request missing required fields "
            "(image_present=%s, photo_id_present=%s, filename_present=%s)",
            bool(image),
            bool(photo_id),
            bool(filename),
        )
        return jsonify(
            {"error": "Missing required fields: image, photo_id, filename"}
        ), 400

    options = _extract_options(data)

    image_bytes = base64.b64decode(image.encode("ascii"))

    success_count, failure_count, error_messages, warnings = process_image_task(
        [(image_bytes, photo_id, filename, None)],
        options=options,
    )

    logger.info(
        f"Batch processing complete. Success: {success_count}, Failures: {failure_count}."
    )

    if success_count == 0:
        logger.warning("No images were successfully processed in the batch.")
        err_msg = "No images were successfully processed"
        if error_messages:
            unique_errs = list(dict.fromkeys(error_messages))
            err_msg += ": " + " | ".join(unique_errs[:5])
        return jsonify({"error": err_msg}), 500

    return jsonify(
        {
            "status": "processed",
            "success_count": success_count,
            "failure_count": failure_count,
            "error_messages": error_messages,
            "warnings": warnings or [],
        }
    ), 200


@index_bp.route("/index_base64_batch", methods=["POST"])
def index_images_batch_base64_v2():
    """
    Receives a list of base64 encoded images, decodes them, and processes/indexes them.
    JSON: {
        "images": [
            { "image": "<base64>", "photo_id": "<id>", "filename": "<name>", "options": {...} },
            ...
        ],
        "options": { ... }  # Global options
    }
    """
    logger.info("Index base64 batch request received")
    import server_lifecycle

    server_lifecycle.GLOBAL_CANCEL_EVENT.clear()

    data = request.get_json(silent=True) or {}

    images_data = data.get("images", [])
    global_options = data.get("options", {})
    cache_images = global_options.get("cache_images", False)

    if not images_data:
        return jsonify({"error": "No images provided in batch"}), 400

    image_triplets = []
    per_image_options = []
    cached_photo_ids = []

    for item in images_data:
        image_base64 = item.get("image")
        photo_id = item.get("photo_id") or item.get("uuid")
        lr_uuid = item.get("lr_uuid")
        filename = item.get("filename")

        if not image_base64 or not photo_id or not filename:
            logger.warning("Skipping entry in base64 batch due to missing fields.")
            continue

        try:
            # Merge photo-specific options with global options
            merged_options = dict(global_options)
            merged_options.update(item.get("options", {}))
            photo_options = _extract_options(merged_options)
            photo_options["photo_id"] = photo_id

            image_bytes = base64.b64decode(image_base64.encode("ascii"))

            image_triplets.append((image_bytes, photo_id, filename, lr_uuid))

            if cache_images:
                if not image_cache.store_image(photo_id, image_bytes):
                    for cached_photo_id in cached_photo_ids:
                        image_cache.remove_image(cached_photo_id)
                    return jsonify(
                        {
                            "error": "Metadata image cache is full; retry after current metadata work advances"
                        }
                    ), 429
                cached_photo_ids.append(photo_id)

            per_image_options.append(photo_options)
        except Exception as e:
            logger.error(f"Error decoding image in batch: {e}")

    if not image_triplets:
        return jsonify({"error": "No valid images decoded in batch"}), 400

    success_count, failure_count, error_messages, warnings = process_image_task(
        image_triplets, options=per_image_options
    )

    logger.info(
        f"Batch base64 processing complete. Success: {success_count}, Failures: {failure_count}."
    )

    if success_count == 0:
        logger.warning("No images were successfully processed in the batch.")
        err_msg = "No images were successfully processed"
        if error_messages:
            unique_errs = list(dict.fromkeys(error_messages))
            err_msg += ": " + " | ".join(unique_errs[:5])
        return jsonify({"error": err_msg}), 500

    return jsonify(
        {
            "status": "processed",
            "success_count": success_count,
            "failure_count": failure_count,
            "error_messages": error_messages,
            "warnings": warnings or [],
        }
    ), 200


@index_bp.route("/metadata/generate", methods=["POST"])
def generate_metadata_single():
    """
    Receives a request to generate metadata for a single image.
    If 'image' (base64) is provided, it uses it.
    Otherwise, it checks the in-memory cache using the photo_id.
    """
    logger.info("Metadata generate single request received")
    data = request.get_json(silent=True) or {}

    photo_id = data.get("photo_id") or data.get("uuid")
    filename = data.get("filename", "unknown")

    if not photo_id:
        return jsonify({"error": "Missing photo_id"}), 400

    options = _extract_options(data)
    # Force overrides for this specialized metadata route
    options["compute_embeddings"] = False
    options["compute_metadata"] = True

    image_base64 = data.get("image")
    image_bytes = None
    if image_base64:
        try:
            import base64

            image_bytes = base64.b64decode(image_base64.encode("ascii"))
        except Exception as e:
            return jsonify({"error": f"Failed to decode base64 image: {e}"}), 400
    else:
        from services import image_cache

        image_bytes = image_cache.pop_image(photo_id)

    if not image_bytes:
        logger.warning(
            "No image data provided and image not found in cache for single metadata generation."
        )
        return jsonify(
            {
                "error": "Image data expired or was not admitted; rerun metadata generation for this photo"
            }
        ), 409

    # Let process_image_task handle the robust database commit and metadata merging logic
    success_count, failure_count, error_messages, warnings = process_image_task(
        [(image_bytes, photo_id, filename, None)], options=options
    )

    if success_count == 0:
        err_msg = "Metadata generation failed"
        if error_messages:
            unique_errs = list(dict.fromkeys(error_messages))
            err_msg += ": " + " | ".join(unique_errs[:5])
        return jsonify({"error": err_msg}), 500

    return jsonify(
        {
            "status": "processed",
            "success_count": success_count,
            "failure_count": failure_count,
            "error_messages": error_messages,
            "warnings": warnings or [],
        }
    ), 200


@index_bp.route("/metadata/generate_batch", methods=["POST"])
def generate_metadata_batch():
    """
    Receives a request to generate metadata for a batch of images.
    Expects a JSON body with a list of tasks, where each task has a photo_id and options.
    Retrieves images from the in-memory cache and delegates to process_image_task.
    """
    logger.info("Metadata generate batch request received")
    from services import operations

    operations.refresh_system_pressure()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    job_id = str(data.get("job_id") or "").strip() or None
    job = None
    cancel_signal = None

    if job_id:
        if not config.DB_PATH:
            return jsonify({"error": "StyleAI database path is not configured"}), 500
        job = operations.get_job(config.DB_PATH, job_id, include_items=False)
        if job is None:
            return jsonify({"error": f"operation job not found: {job_id}"}), 404
        if job["kind"] not in {"index", "metadata"}:
            return jsonify({"error": "operation job cannot generate metadata"}), 409
        if job["state"] in operations.TERMINAL_STATES:
            return jsonify({"error": "operation job is already complete"}), 409
        if job["cancel_requested"]:
            return jsonify({"error": "operation job has been canceled"}), 409
        cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)

    tasks = data.get("tasks", [])
    if not tasks:
        return jsonify({"error": "No tasks provided"}), 400
    if not isinstance(tasks, list) or not all(isinstance(task, dict) for task in tasks):
        return jsonify({"error": "tasks must be an array of objects"}), 400
    if len(tasks) > 12:
        return jsonify({"error": "Metadata batches are limited to 12 photos"}), 413

    global_options = data.get("options", {})
    if not isinstance(global_options, dict):
        return jsonify({"error": "options must be an object"}), 400
    valid_tasks = []
    batch_options = []
    inline_image_bytes_total = 0

    from services import image_cache

    for task in tasks:
        photo_id = task.get("photo_id") or task.get("uuid")
        filename = task.get("filename", "unknown")

        if not photo_id:
            continue

        merged_options = dict(global_options)
        task_options = task.get("options", {})
        if not isinstance(task_options, dict):
            return jsonify({"error": "task options must be an object"}), 400
        merged_options.update(task_options)
        photo_options = _extract_options(merged_options)

        # Force overrides for metadata route
        photo_options["compute_embeddings"] = False
        photo_options["compute_metadata"] = True
        photo_options["job_id"] = job_id

        inline_image = task.get("image")
        inline_image_payload = None
        if inline_image:
            inline_image_payload = str(inline_image)
            inline_image_bytes_total += (len(inline_image_payload) * 3) // 4
            byte_limit = min(
                STYLEAI_METADATA_CACHE_BYTES,
                operations.admission.capacities["image_bytes"],
            )
            if inline_image_bytes_total > byte_limit:
                return jsonify(
                    {"error": "Inline metadata image batch exceeds the byte budget"}
                ), 413

        valid_tasks.append((photo_id, filename, inline_image_payload))
        batch_options.append(photo_options)

    if not valid_tasks:
        return jsonify({"error": "No valid metadata tasks found"}), 400

    photo_ids = [photo_id for photo_id, _filename, _image in valid_tasks]
    if len(photo_ids) != len(set(photo_ids)):
        return jsonify({"error": "Duplicate photo_id values are not allowed"}), 400
    if job is not None:
        expected_ids = {
            item["item_id"]
            for item in operations.get_job_items(config.DB_PATH, job_id, photo_ids)
        }
        unexpected_ids = sorted(set(photo_ids) - expected_ids)
        if unexpected_ids:
            return (
                jsonify(
                    {
                        "error": "metadata batch contains photos not admitted to this job",
                        "photo_ids": unexpected_ids,
                    }
                ),
                400,
            )

    item_results: list[dict] = []

    # Wait for upstream embedding batches to complete to avoid racing a slow MPS worker.
    # We must do this *before* claiming admission resources to avoid a hold-and-wait deadlock
    # with the dynamic GPU worker which might need the accelerator to process these embeddings.
    import time
    from server_lifecycle import GLOBAL_CANCEL_EVENT
    from services.index import active_embeddings_uuids

    cpu_claim = min(len(valid_tasks), operations.admission.capacities["cpu_prepare"])
    try:
        for photo_id, _, _ in valid_tasks:
            while photo_id in active_embeddings_uuids:
                if cancel_signal is not None and cancel_signal.is_set():
                    raise InterruptedError("operation job has been canceled")
                if GLOBAL_CANCEL_EVENT.is_set():
                    raise RuntimeError(
                        "Batch canceled while waiting for embeddings during shutdown."
                    )
                time.sleep(0.10)

        with operations.admission.acquire(
            {
                "accelerator": 1,
                "llm": 1,
                "cpu_prepare": max(1, cpu_claim),
            },
            priority=5,
            cancel_event=cancel_signal,
        ):
            decoded_tasks = []
            decoded_inline_bytes = 0
            for photo_id, filename, inline_image_payload in valid_tasks:
                inline_image_bytes = None
                if inline_image_payload is not None:
                    try:
                        inline_image_bytes = base64.b64decode(
                            inline_image_payload.encode("ascii"), validate=True
                        )
                    except Exception as exc:
                        return jsonify(
                            {"error": f"Failed to decode image for {photo_id}: {exc}"}
                        ), 400
                    if not inline_image_bytes:
                        return (
                            jsonify({"error": f"Empty image data for {photo_id}"}),
                            400,
                        )
                    decoded_inline_bytes += len(inline_image_bytes)
                decoded_tasks.append((photo_id, filename, inline_image_bytes))
            byte_limit = min(
                STYLEAI_METADATA_CACHE_BYTES,
                operations.admission.capacities["image_bytes"],
            )
            if decoded_inline_bytes > byte_limit:
                return jsonify(
                    {"error": "Inline metadata image batch exceeds the byte budget"}
                ), 413

            cached_photo_ids = [
                photo_id
                for photo_id, _filename, inline_image_bytes in decoded_tasks
                if inline_image_bytes is None
            ]
            cached_images = {}
            if cached_photo_ids:
                image_bytes_batch, missing_ids = image_cache.pop_images(
                    cached_photo_ids
                )
                if image_bytes_batch is None:
                    logger.warning(
                        "Metadata batch rejected because %d image(s) were absent from the cache: %s",
                        len(missing_ids),
                        ", ".join(missing_ids[:5]),
                    )
                    return jsonify(
                        {
                            "error": "Image data expired or was not admitted; rerun metadata generation for this batch",
                            "missing_photo_ids": missing_ids,
                        }
                    ), 409
                cached_images = dict(zip(cached_photo_ids, image_bytes_batch))

            image_triplets = [
                (
                    inline_image_bytes or cached_images[photo_id],
                    photo_id,
                    filename,
                    None,
                )
                for photo_id, filename, inline_image_bytes in decoded_tasks
            ]
            if job_id:
                if cancel_signal is not None and cancel_signal.is_set():
                    raise InterruptedError("operation job has been canceled")
                try:
                    operations.set_job_state(config.DB_PATH, job_id, "running")
                except ValueError:
                    if cancel_signal is None or not cancel_signal.is_set():
                        raise
                    raise InterruptedError("operation job has been canceled") from None
                if cancel_signal is not None and cancel_signal.is_set():
                    raise InterruptedError("operation job has been canceled")
                try:
                    operations.set_item_states(
                        config.DB_PATH,
                        job_id,
                        [
                            {"item_id": photo_id, "state": "running"}
                            for photo_id in photo_ids
                        ],
                    )
                except ValueError:
                    if cancel_signal is None or not cancel_signal.is_set():
                        raise
                    raise InterruptedError("operation job has been canceled") from None

            success_count, failure_count, error_messages, warnings = process_image_task(
                image_triplets,
                options=batch_options,
                item_results=item_results,
            )
    except InterruptedError:
        for photo_id in photo_ids:
            image_cache.remove_image(photo_id)
        if job_id:
            try:
                operations.set_item_states(
                    config.DB_PATH,
                    job_id,
                    [
                        {
                            "item_id": photo_id,
                            "state": "canceled",
                            "error": "Metadata operation canceled",
                        }
                        for photo_id in photo_ids
                    ],
                )
            except ValueError:
                current_job = operations.get_job(
                    config.DB_PATH, job_id, include_items=False
                )
                if not current_job or current_job["state"] != "canceled":
                    raise
        return jsonify({"error": "operation job has been canceled"}), 409

    if job_id:
        results_by_id = {str(result.get("photo_id")): result for result in item_results}
        item_updates = []
        for photo_id in photo_ids:
            result = results_by_id.get(photo_id)
            result_status = (result or {}).get("status")
            target_state = "committing" if result_status == "succeeded" else "failed"
            if result_status == "canceled":
                target_state = "canceled"
            item_updates.append(
                {
                    "item_id": photo_id,
                    "state": target_state,
                    "error": (
                        None
                        if target_state == "committing"
                        else str(
                            (result or {}).get("error")
                            or "Metadata generation did not return a terminal result"
                        )
                    ),
                    "result": result,
                }
            )
        try:
            operations.set_item_states(config.DB_PATH, job_id, item_updates)
        except ValueError:
            current_job = operations.get_job(
                config.DB_PATH, job_id, include_items=False
            )
            if not (
                item_updates
                and all(update["state"] == "canceled" for update in item_updates)
                and current_job
                and current_job["state"] == "canceled"
            ):
                raise

    all_canceled = bool(item_results) and all(
        result.get("status") == "canceled" for result in item_results
    )
    response_status = "canceled" if all_canceled else "processed"
    status_code = 200 if all_canceled or success_count > 0 else 500

    return jsonify(
        {
            "status": response_status,
            "success_count": success_count,
            "failure_count": failure_count,
            "items": item_results,
            "job_id": job_id,
            "error_messages": error_messages,
            "warnings": warnings or [],
        }
    ), status_code


@index_bp.route("/index_by_reference", methods=["POST"])
def index_images_batch_by_reference():
    """
    Receives a batch of image references in a JSON payload, processes them,
    and indexes them.
    """
    logger.info("Index by reference request received")
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400

    logger.debug(f"Index by reference payload: {data}")

    options = _extract_options(data)

    # Extract image list
    images_data = data.get("images", [])

    # Use a list comprehension to extract paths and photo IDs.
    paths = [item.get("path") for item in images_data]
    photo_ids = [item.get("photo_id") or item.get("uuid") for item in images_data]

    # Check for missing keys or mismatched lengths (robustness).
    if not all(paths) or not all(photo_ids) or len(paths) != len(photo_ids):
        return jsonify(
            {
                "error": "Mismatch in data, or missing 'path' or 'photo_id' keys in some objects"
            }
        ), 400

    batch_size = len(paths)

    image_triplets = []
    failed_paths = []
    for i in range(batch_size):
        path = paths[i]
        photo_id = photo_ids[i]

        if not path or not photo_id:
            logger.warning(
                "Skipping an entry in the batch due to missing file or photo_id."
            )
            continue

        try:
            with open(path, "rb") as file:
                image_data = file.read()

            filename = os.path.basename(path)
            image_triplets.append((image_data, photo_id, filename, None))
        except FileNotFoundError:
            logger.warning(f"File not found at path: {path}. Skipping.")
            failed_paths.append(path)
        except Exception as e:
            logger.error(f"Error processing file at path {path}: {e}")
            failed_paths.append(path)

    read_failures = len(failed_paths)
    if not image_triplets:
        logger.info("No valid image paths to process in the batch.")
        return jsonify(
            {"status": "processed", "success_count": 0, "failure_count": read_failures}
        ), 200

    success_count, processing_failures, error_messages, warnings = process_image_task(
        image_triplets, options=options
    )
    total_failures = read_failures + processing_failures

    logger.info(
        f"Batch processing by reference complete. Success: {success_count}, Failures: {total_failures} ({read_failures} read failures, {processing_failures} processing failures)."
    )

    if success_count == 0:
        logger.warning("No images were successfully processed in the batch.")
        err_msg = "No images were successfully processed"
        if error_messages:
            unique_errs = list(dict.fromkeys(error_messages))
            err_msg += ": " + " | ".join(unique_errs[:5])
        return jsonify({"error": err_msg}), 500

    return jsonify(
        {
            "status": "processed",
            "success_count": success_count,
            "failure_count": total_failures,
            "error_messages": error_messages,
            "warnings": warnings or [],
        }
    ), 200


@index_bp.route("/remove", methods=["POST"])
def remove_image():
    logger.info("Remove request received")
    body = request.json or {}
    photo_id = body.get("photo_id") or body.get("uuid")
    if not photo_id:
        return jsonify({"error": "No photo_id provided"}), 400

    try:
        chroma_service.delete_image(photo_id)
        logger.info("Removed one image record from ChromaDB")
        return jsonify({"status": "removed", "photo_id": photo_id, "uuid": photo_id})
    except Exception as e:
        logger.error(f"Error removing image {photo_id}: {e}")
        return jsonify({"error": "photo_id not found or error during removal"}), 404


@index_bp.route("/remove/metadata", methods=["POST"])
def remove_metadata():
    """
    Clear only AI-generated metadata (title, caption, keywords, alt_text, etc.) for a photo.
    Keeps the document and embeddings so the photo remains in the index and searchable.
    Use when the user discards a suggestion (e.g. in the review dialog) so they can regenerate later.
    """
    logger.info("Remove metadata request received")
    body = request.json or {}
    photo_id = body.get("photo_id") or body.get("uuid")
    if not photo_id:
        return jsonify({"error": "No photo_id provided"}), 400
    try:
        cleared = chroma_service.clear_image_metadata(photo_id)
        if not cleared:
            return jsonify({"error": "photo_id not found"}), 404
        logger.info("Cleared metadata for one photo (embedding kept)")
        return jsonify({"status": "ok", "photo_id": photo_id, "uuid": photo_id})
    except Exception as e:
        logger.error(f"Error clearing metadata for {photo_id}: {e}", exc_info=True)
        return jsonify(
            {"error": "photo_id not found or error during metadata clear"}
        ), 404


@index_bp.route("/get", methods=["POST"])
def get_photo_data():
    """
    Retrieves stored metadata for a photo by photo_id.

    JSON body parameters:
    - photo_id (string): The ID of the photo to retrieve

    Returns:
    - status: "success" or "error"
    - photo_id: The photo ID
    - metadata: Dictionary with all metadata fields (title, caption, keywords, etc.)
    """
    logger.info("Get photo data request received")

    body = request.json or {}
    photo_id = body.get("photo_id") or body.get("uuid")
    if not photo_id:
        return jsonify({"status": "error", "error": "No photo_id provided"}), 400

    try:
        # Get photo data from ChromaDB
        photo_data = chroma_service.get_image(photo_id)
        logger.debug(f"Retrieved photo data for photo_id {photo_id}: {photo_data}")

        if not photo_data or not photo_data["ids"]:
            logger.warning("Requested photo was not found in the database")
            return jsonify({"status": "error", "error": "Photo not found"}), 404

        # Extract metadata
        metadata_dict = photo_data["metadatas"][0] if photo_data["metadatas"] else {}

        # Separate user-facing metadata from internal indexing fields
        metadata_fields = {}
        edit_recipe = None
        edit_warnings = []

        # User metadata field names (from metadata generation)
        metadata_keys = {"title", "caption", "keywords", "alt_text"}

        ai_model = metadata_dict.get("model")
        ai_rundate = metadata_dict.get("run_date")

        for key, value in metadata_dict.items():
            if key in metadata_keys:
                logger.debug("Processing stored metadata field %s", key)
                # Keywords must be returned as JSON string (not parsed) for plugin to handle
                if key == "keywords" and isinstance(value, str) and value:
                    # Keep keywords as JSON string for plugin to parse
                    # The plugin expects either:
                    # - JSON array: ["kw1", "kw2"]
                    # - JSON object: {"Category": ["kw1"], ...}
                    metadata_fields[key] = json.loads(value)
                elif key == "tokens_used" and isinstance(value, str) and value:
                    try:
                        metadata_fields[key] = json.loads(value) if value else []
                    except (json.JSONDecodeError, ValueError):
                        logger.warning(f"Error decoding JSON for {key}: {value}")
                        metadata_fields[key] = []
                else:
                    metadata_fields[key] = value
            elif key == "edit_recipe" and isinstance(value, str) and value:
                try:
                    edit_recipe = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Error decoding stored edit_recipe JSON")
            elif key == "edit_warnings" and isinstance(value, str) and value:
                try:
                    decoded_warnings = json.loads(value)
                    if isinstance(decoded_warnings, list):
                        edit_warnings = decoded_warnings
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Error decoding stored edit_warnings JSON")

        logger.debug("Retrieved %d stored metadata fields", len(metadata_fields))

        return jsonify(
            {
                "status": "success",
                "photo_id": photo_id,
                "uuid": photo_id,
                "metadata": metadata_fields,
                "edit": edit_recipe,
                "edit_summary": metadata_dict.get("edit_summary"),
                "edit_warnings": edit_warnings,
                "edit_model": metadata_dict.get("edit_model"),
                "edit_rundate": metadata_dict.get("edit_run_date"),
                "ai_model": ai_model,
                "ai_rundate": ai_rundate,
            }
        )

    except Exception as e:
        logger.error(f"Error retrieving photo data for {photo_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@index_bp.route("/get/ids", methods=["GET"])
def get_ids():
    """Get all indexed image IDs, optionally filtered by embedding status.

    Query parameters:
        has_embedding (string): 'true' to get only images with real embeddings,
                               'false' to get only images with dummy embeddings,
                               omit to get all images.
    """
    logger.info("Get IDs request received")

    # Parse has_embedding parameter
    has_embedding_param = request.args.get("has_embedding")
    has_embedding = None
    if has_embedding_param is not None:
        has_embedding = has_embedding_param.lower() == "true"
        logger.info(f"Filtering IDs by has_embedding={has_embedding}")

    ids_data = chroma_service.get_all_image_ids(has_embedding=has_embedding)
    logger.info(f"Returning {len(ids_data)} image IDs")
    return jsonify(ids_data)


@index_bp.route("/index/check-unprocessed", methods=["POST"])
def check_unprocessed():
    """
    Returns UUIDs that need processing based on selected tasks and existing backend data.
    Used by the Lightroom plugin for "New or unprocessed photos" scope.
    """
    data = request.get_json() or {}

    # Prioritize native lr_uuids if available for fast metadata searches
    lr_uuids = data.get("lr_uuids")
    if lr_uuids:
        options = _extract_options(data)
        needing = get_photo_ids_needing_processing(
            lr_uuids, options, search_by_lr_uuid=True
        )
        logger.info(
            f"check-unprocessed: {len(needing)} of {len(lr_uuids)} photos need processing (by LR UUID)"
        )
        return jsonify({"lr_uuids": needing}), 200

    # Fallback to global photo IDs
    photo_ids = data.get("photo_ids") or data.get("uuids", [])
    if not photo_ids:
        return jsonify({"photo_ids": [], "uuids": [], "lr_uuids": []}), 200

    options = _extract_options(data)
    needing = get_photo_ids_needing_processing(photo_ids, options)
    logger.info(
        f"check-unprocessed: {len(needing)} of {len(photo_ids)} photos need processing"
    )
    return jsonify({"photo_ids": needing, "uuids": needing}), 200


@index_bp.route("/index_queue", methods=["POST"])
def enqueue_photo():
    """
    Accepts a single base64 encoded image (or small batch) and places it into the asynchronous
    GPU dynamic batching queue. Returns 202 Accepted immediately so Lightroom doesn't block.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    images_data = data.get("images", [])
    if not isinstance(images_data, list):
        return jsonify({"error": "images must be an array"}), 400
    if not all(isinstance(item, dict) for item in images_data):
        return jsonify({"error": "every image must be an object"}), 400
    global_options = data.get("options", {})
    job_id = str(data.get("job_id") or "").strip() or None

    if job_id:
        from services import operations

        if not config.DB_PATH:
            return jsonify({"error": "StyleAI database path is not configured"}), 500
        job = operations.get_job(config.DB_PATH, job_id, include_items=False)
        if job is None:
            return jsonify({"error": f"operation job not found: {job_id}"}), 404
        if job["kind"] != "index":
            return jsonify({"error": "operation job is not an indexing job"}), 409
        if job["state"] in operations.TERMINAL_STATES:
            return jsonify({"error": "indexing operation is already complete"}), 409
        if job["cancel_requested"]:
            return jsonify({"error": "operation job has been canceled"}), 409
        supplied_ids = [
            str(item.get("photo_id") or item.get("uuid") or "").strip()
            for item in images_data
        ]
        if len(supplied_ids) != len(set(supplied_ids)):
            logger.warning(
                "Rejected index batch containing duplicate photo IDs (count=%d)",
                len(supplied_ids),
            )
            return jsonify({"error": "Duplicate photo IDs are not allowed"}), 400
        expected_ids = {
            item["item_id"]
            for item in operations.get_job_items(config.DB_PATH, job_id, supplied_ids)
        }
        unexpected_ids = sorted(
            item_id for item_id in supplied_ids if item_id not in expected_ids
        )
        if unexpected_ids:
            return (
                jsonify(
                    {
                        "error": "index batch contains photos not admitted to this job",
                        "photo_ids": unexpected_ids,
                    }
                ),
                400,
            )

    from services.index import (
        active_embeddings_uuids,
        index_queue,
        is_index_queue_accepting,
    )

    accepted_photo_ids: list[str] = []
    rejected_items: list[dict[str, object]] = []

    def reject(photo_id, reason: str, *, retryable: bool) -> None:
        normalized_id = str(photo_id or "").strip()
        rejected_items.append(
            {
                "photo_id": normalized_id,
                "reason": reason,
                "retryable": retryable,
            }
        )
        if job_id and normalized_id and not retryable:
            operations.set_item_state(
                config.DB_PATH,
                job_id,
                normalized_id,
                "failed",
                error=reason,
            )

    if not is_index_queue_accepting():
        for item in images_data:
            reject(
                item.get("photo_id") or item.get("uuid"),
                "index queue is stopping",
                retryable=True,
            )
        return jsonify(
            {
                "status": "stopping",
                "enqueued": 0,
                "rejected": len(rejected_items),
                "accepted_photo_ids": [],
                "rejected_items": rejected_items,
            }
        ), 503

    for item in images_data:
        image_base64 = item.get("image")
        photo_id = item.get("photo_id") or item.get("uuid")
        lr_uuid = item.get("lr_uuid")
        filename = item.get("filename")

        if not photo_id:
            reject(None, "photo_id is required", retryable=False)
            continue
        if not image_base64:
            reject(photo_id, "image data is required", retryable=False)
            continue
        if not filename:
            reject(photo_id, "filename is required", retryable=False)
            continue

        # Check capacity before decoding base64: decoding an image only to drop
        # it was a major transient-memory spike under large Lightroom batches.
        if index_queue.full():
            reject(photo_id, "index queue is full", retryable=True)
            continue

        image_was_cached = False
        try:
            merged_options = dict(global_options)
            merged_options.update(item.get("options", {}))
            photo_options = _extract_options(merged_options)
            photo_options["photo_id"] = photo_id

            import base64

            image_bytes = base64.b64decode(image_base64.encode("ascii"))

            cache_images = global_options.get("cache_images", False)
            photo_options["defer_terminal"] = bool(cache_images)
            if cache_images:
                from services import image_cache

                if not image_cache.store_image(photo_id, image_bytes):
                    reject(photo_id, "metadata image cache is full", retryable=True)
                    continue
                image_was_cached = True

            queue_item = {
                "image_bytes": image_bytes,
                "uuid": photo_id,
                "filename": filename,
                "lr_uuid": lr_uuid,
                "options": photo_options,
                "job_id": job_id,
            }

            active_embeddings_uuids.add(photo_id)
            if job_id:
                operations.set_item_state(config.DB_PATH, job_id, photo_id, "queued")
            try:
                index_queue.put_nowait(queue_item)
            except queue.Full:
                active_embeddings_uuids.discard(photo_id)
                if image_was_cached:
                    image_cache.remove_image(photo_id)
                queue_item.clear()
                reject(photo_id, "index queue filled during admission", retryable=True)
                continue
            accepted_photo_ids.append(str(photo_id))
        except Exception as e:
            active_embeddings_uuids.discard(photo_id)
            if image_was_cached:
                image_cache.remove_image(photo_id)
            reason = f"invalid index queue item: {e}"
            reject(photo_id, reason, retryable=False)
            logger.error("Error enqueueing image %s: %s", photo_id, e, exc_info=True)

    enqueued = len(accepted_photo_ids)
    rejected = len(rejected_items)
    status = "accepted" if rejected == 0 else "backpressure"
    if job_id and accepted_photo_ids:
        operations.set_job_state(config.DB_PATH, job_id, "running")
    response = {
        "status": status,
        "enqueued": enqueued,
        "rejected": rejected,
        "accepted_photo_ids": accepted_photo_ids,
        "rejected_items": rejected_items,
    }
    if job_id:
        response["job_id"] = job_id
    return jsonify(response), 202


@index_bp.route("/index_queue/status", methods=["GET"])
def index_queue_status():
    """Expose bounded-queue capacity so the plugin can pace extraction."""
    from services.index import get_index_queue_status

    return jsonify(get_index_queue_status())
