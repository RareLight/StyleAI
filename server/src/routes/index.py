import time
from collections import deque
import os

from flask import Blueprint, request, jsonify

from config import logger
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
        logger.info(f"{image}, {photo_id}, {filename}")
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
                image_cache.store_image(photo_id, image_bytes)

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
    options["compute_faces"] = False

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
        return jsonify(
            {"error": "No image data provided and image not found in cache"}
        ), 400

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
        chroma_service.delete_faces_by_photo_uuid(photo_id)
        logger.info(
            f"Image ID {photo_id} removed from ChromaDB (including face embeddings)."
        )
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
        logger.info(f"Metadata cleared for photo_id {photo_id} (embeddings kept).")
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
            logger.warning(f"Photo with photo_id {photo_id} not found in database")
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
                logger.info(f"Processing metadata field {key}: {value}")
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
                    logger.warning(f"Error decoding edit_recipe JSON for {photo_id}")
            elif key == "edit_warnings" and isinstance(value, str) and value:
                try:
                    decoded_warnings = json.loads(value)
                    if isinstance(decoded_warnings, list):
                        edit_warnings = decoded_warnings
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"Error decoding edit_warnings JSON for {photo_id}")

        logger.info(
            f"Retrieved data for photo {photo_id}: {len(metadata_fields)} metadata fields"
        )

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
    data = request.get_json(silent=True) or {}
    images_data = data.get("images", [])
    global_options = data.get("options", {})

    from services.index import index_queue

    enqueued = 0
    for item in images_data:
        image_base64 = item.get("image")
        photo_id = item.get("photo_id") or item.get("uuid")
        lr_uuid = item.get("lr_uuid")
        filename = item.get("filename")

        if not image_base64 or not photo_id or not filename:
            continue

        try:
            merged_options = dict(global_options)
            merged_options.update(item.get("options", {}))
            photo_options = _extract_options(merged_options)
            photo_options["photo_id"] = photo_id

            import base64

            image_bytes = base64.b64decode(image_base64.encode("ascii"))

            cache_images = global_options.get("cache_images", False)
            if cache_images:
                from services import image_cache

                image_cache.store_image(photo_id, image_bytes)

            queue_item = {
                "image_bytes": image_bytes,
                "uuid": photo_id,
                "filename": filename,
                "lr_uuid": lr_uuid,
                "options": photo_options,
            }
            index_queue.put(queue_item)
            enqueued += 1
        except Exception as e:
            logger.error(f"Error enqueueing image: {e}")

    return jsonify({"status": "accepted", "enqueued": enqueued}), 202
