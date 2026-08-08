"""
Flask blueprint for edit-style training endpoints.

Routes
------
POST /training/add      – Store a new training example (with EXIF + image for exposure/scene analysis)
GET  /training/list     – List all stored training examples (no embeddings)
GET  /training/count    – Return { "count": N }
GET  /training/stats    – Return aggregate style-profile statistics
DELETE /training/<id>   – Remove one training example by photo_id
DELETE /training        – Clear all training examples
"""

from __future__ import annotations

import json
from typing import Any

import config
from flask import Blueprint, jsonify, request
from PIL import Image
import io

from config import logger
from services import operations
from services import training as training_service

training_bp = Blueprint("training", __name__)


def _compute_clip_embedding(image_bytes: bytes):
    """Compute a CLIP embedding for the supplied JPEG/PNG bytes.

    Re-uses the global CLIP model that is already loaded by service_index
    when the server starts.  Returns None when the model is not available.
    """
    try:
        import torch
        import torch.nn.functional as F
        import server_lifecycle
        from config import get_torch_device

        from services import operations

        with operations.admission.acquire(
            {"accelerator": 1, "cpu_prepare": 1}, priority=8
        ):
            clip_model = server_lifecycle.get_model()
            clip_processor = server_lifecycle.get_processor()
            if clip_model is None or clip_processor is None:
                return None

            with Image.open(io.BytesIO(image_bytes)) as source_image:
                source_image.thumbnail((512, 512))
                image = source_image.convert("RGB")
            try:
                image_tensor = clip_processor(image).unsqueeze(0).to(get_torch_device())
                with torch.no_grad():
                    features = clip_model.encode_image(image_tensor)
                    normalized = F.normalize(features, p=2, dim=1)
                    return normalized.cpu().numpy()[0].tolist()
            finally:
                image.close()
    except Exception as exc:
        logger.warning("Could not compute CLIP embedding for training example: %s", exc)
        return None


# ---------------------------------------------------------------------------
# POST /training/add
# ---------------------------------------------------------------------------


@training_bp.route("/training/add", methods=["POST"])
def add_training_example():
    operations.refresh_system_pressure()
    with operations.admission.acquire({"training_upload": 1}, priority=8):
        return _add_training_example_impl()


def _add_training_example_impl():
    """Accept a multipart/form-data upload with:
    - photo_id          (form field, required)
    - develop_settings  (form field, JSON string, required)
    - image             (file, optional – used to compute CLIP embedding + exposure/scene metrics)
    - label             (form field, optional)
    - summary           (form field, optional)
    - focal_length      (form field, float mm, optional)
    - capture_time      (form field, float unix timestamp, optional)
    - camera_make       (form field, string, optional)
    - camera_model      (form field, string, optional)
    - camera_profile    (form field, string, optional)
    - user_keywords     (form field, comma-separated string, optional – e.g. "macro,nature")
    - iso               (form field, float, optional)
    - aperture          (form field, float, optional)
    - shutter_speed     (form field, string, optional)
    """
    photo_id = request.form.get("photo_id", "").strip()
    if not photo_id:
        return jsonify({"error": "photo_id is required"}), 400

    dev_settings_raw = request.form.get("develop_settings", "")
    try:
        develop_settings = json.loads(dev_settings_raw) if dev_settings_raw else {}
    except (ValueError, TypeError):
        return jsonify({"error": "develop_settings must be valid JSON"}), 400

    label = request.form.get("label", "").strip() or "Uncategorized"
    summary = request.form.get("summary", "").strip() or None
    filename = None

    # Optional EXIF fields for richer matching
    focal_length: float | None = None
    try:
        fl_raw = request.form.get("focal_length")
        if fl_raw:
            focal_length = float(fl_raw)
    except (TypeError, ValueError):
        pass

    capture_time_unix: float | None = None
    try:
        ct_raw = request.form.get("capture_time")
        if ct_raw:
            capture_time_unix = float(ct_raw)
    except (TypeError, ValueError):
        pass

    def _opt_str(key):
        val = request.form.get(key, "").strip()
        return val or None

    camera_make = _opt_str("camera_make")
    camera_model_str = _opt_str("camera_model")
    camera_profile = _opt_str("camera_profile")
    shutter_speed = _opt_str("shutter_speed")

    # Parse comma-separated user keywords
    user_keywords: list[str] | None = None
    kw_raw = request.form.get("user_keywords", "").strip()
    if kw_raw:
        user_keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]

    iso: float | None = None
    try:
        iso_raw = request.form.get("iso")
        if iso_raw:
            iso = float(iso_raw)
    except (TypeError, ValueError):
        pass

    aperture: float | None = None
    try:
        ap_raw = request.form.get("aperture")
        if ap_raw:
            aperture = float(ap_raw)
    except (TypeError, ValueError):
        pass

    rating: int = 0
    try:
        r_raw = request.form.get("rating")
        if r_raw:
            rating = int(r_raw)
    except (TypeError, ValueError):
        pass

    pick_status: int = 0
    try:
        ps_raw = request.form.get("pick_status")
        if ps_raw:
            pick_status = int(ps_raw)
    except (TypeError, ValueError):
        pass

    # Compute CLIP embedding from uploaded image (best-effort).
    embedding = None
    image_bytes_data = None
    filepath = request.form.get("filepath", "").strip() or None

    source_provenance = "lightroom_rendered_preview"
    image_file = request.files.get("image")
    if image_file:
        filename = image_file.filename or None
        try:
            image_bytes_data = image_file.read()
            # If filepath provided, attempt to extract unedited camera preview
            if filepath:
                from utils.image_processing import extract_exiftool_preview

                try:
                    raw_preview = extract_exiftool_preview(filepath)
                    if raw_preview:
                        logger.info(
                            f"Successfully extracted unedited raw preview for {filepath}"
                        )
                        image_bytes_data = raw_preview
                        source_provenance = "raw_preview"
                except TimeoutError as exc:
                    return jsonify(
                        {"error": "EXIFTOOL_TIMEOUT", "message": str(exc)}
                    ), 408
                except PermissionError as exc:
                    return jsonify(
                        {"error": "EXIFTOOL_PERMISSION", "message": str(exc)}
                    ), 403
            embedding = _compute_clip_embedding(image_bytes_data)
        except Exception as exc:
            warning_msg = f"Failed to read image for training embedding: {exc}"
            logger.warning(warning_msg)

    try:
        with operations.admission.acquire({"catalog_write": 1}, priority=8):
            training_service.add_training_example(
                photo_id=photo_id,
                develop_settings=develop_settings,
                embedding=embedding,
                label=label,
                filename=filename,
                summary=summary,
                image_bytes=image_bytes_data,
                focal_length=focal_length,
                capture_time_unix=capture_time_unix,
                camera_make=camera_make,
                camera_model=camera_model_str,
                camera_profile=camera_profile,
                user_keywords=user_keywords,
                iso=iso,
                aperture=aperture,
                shutter_speed=shutter_speed,
                rating=rating,
                pick_status=pick_status,
                source_provenance=source_provenance,
            )
        count = training_service.get_training_count()
        response_data = {"status": "ok", "photo_id": photo_id, "total_count": count}
        if image_file and embedding is None:
            response_data["warning"] = (
                "Could not compute CLIP embedding for training example. AI style prediction may be less accurate."
            )
        return jsonify(response_data), 200
    except Exception as exc:
        logger.error(
            "Failed to add training example photo_id=%s: %s",
            photo_id,
            exc,
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /training/add-batch
# ---------------------------------------------------------------------------


@training_bp.route("/training/add-batch", methods=["POST"])
def add_training_batch():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    job_id = str(data.get("job_id") or "").strip() or None
    cancel_signal = None
    if job_id:
        if not config.DB_PATH:
            return jsonify({"error": "StyleAI database path is not configured"}), 500
        job = operations.get_job(config.DB_PATH, job_id, include_items=True)
        if job is None:
            return jsonify({"error": f"operation job not found: {job_id}"}), 404
        if job["kind"] != "training":
            return jsonify(
                {"error": "job_id does not identify a training operation"}
            ), 400
        if job["state"] in operations.TERMINAL_STATES:
            return jsonify({"error": "training operation is already complete"}), 409
        if job["cancel_requested"]:
            return jsonify({"error": "training operation has been canceled"}), 409
        examples = data.get("examples")
        if isinstance(examples, list) and all(
            isinstance(item, dict) for item in examples
        ):
            item_ids = [str(item.get("photo_id") or "").strip() for item in examples]
            if any(not item_id for item_id in item_ids):
                return jsonify({"error": "every job item requires a photo_id"}), 400
            if len(item_ids) != len(set(item_ids)):
                return jsonify({"error": "a job batch cannot repeat a photo_id"}), 400
            expected_ids = {item["item_id"] for item in job.get("items", [])}
            unexpected_ids = sorted(set(item_ids) - expected_ids)
            if unexpected_ids:
                return (
                    jsonify(
                        {
                            "error": "batch contains photos not admitted to this job",
                            "photo_ids": unexpected_ids,
                        }
                    ),
                    400,
                )
        cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)
    operations.refresh_system_pressure()
    try:
        with operations.admission.acquire(
            {"training_upload": 1}, priority=8, cancel_event=cancel_signal
        ):
            if job_id:
                operations.set_job_state(config.DB_PATH, job_id, "running")
            return _add_training_batch_impl(
                data, job_id=job_id, cancel_signal=cancel_signal
            )
    except InterruptedError:
        return jsonify({"error": "training operation has been canceled"}), 409


def _add_training_batch_impl(
    data: dict[str, Any],
    *,
    job_id: str | None = None,
    cancel_signal: operations.JobCancelSignal | None = None,
):
    """Add multiple training examples in one request.

    JSON body:
        examples: list of dicts, each with:
            photo_id (str, required)
            develop_settings (dict, required)
            label (str, optional)
            summary (str, optional)
            focal_length (float, optional)
            capture_time (float, optional)
            camera_make (str, optional)
            camera_model (str, optional)
            camera_profile (str, optional)
            user_keywords (list of str, optional)
            iso (float, optional)
            aperture (float, optional)
            shutter_speed (str, optional)
    """
    examples = data.get("examples", [])
    force_retrain = data.get("force_retrain", False)

    if not examples:
        return jsonify({"error": "Missing 'examples' array in body"}), 400
    if not isinstance(examples, list) or not all(
        isinstance(item, dict) for item in examples
    ):
        return jsonify({"error": "examples must be an array of objects"}), 400

    results: list[dict[str, Any]] = []
    for item in examples:
        if cancel_signal is not None and cancel_signal.is_set():
            break
        photo_id = str(item.get("photo_id") or "").strip()
        if not photo_id:
            results.append(
                {
                    "status": "error",
                    "photo_id": "",
                    "error": "photo_id is required",
                }
            )
            continue
        if job_id:
            current_job = operations.get_job(config.DB_PATH, job_id)
            if current_job is None:
                raise LookupError(f"operation job not found: {job_id}")
            current_item = next(
                (
                    operation_item
                    for operation_item in current_job.get("items", [])
                    if operation_item["item_id"] == photo_id
                ),
                None,
            )
            if current_item is None:
                raise LookupError(f"operation item not found: {job_id}/{photo_id}")
            if current_item["state"] == "succeeded":
                results.append(
                    {
                        "status": "ok",
                        "photo_id": photo_id,
                        "warning": "Already completed in this operation",
                    }
                )
                continue
            if current_item["state"] in operations.TERMINAL_STATES:
                results.append(
                    {
                        "status": "error",
                        "photo_id": photo_id,
                        "error": ("Operation item is already " + current_item["state"]),
                    }
                )
                continue
            operations.set_item_state(config.DB_PATH, job_id, photo_id, "running")

        develop_settings = item.get("develop_settings", {})
        if not isinstance(develop_settings, dict):
            results.append(
                {
                    "status": "error",
                    "photo_id": photo_id,
                    "error": "develop_settings must be a dict",
                }
            )
            continue

        label = item.get("label")
        if not label:
            label = "Uncategorized"

        summary = item.get("summary")
        filename = item.get("filename")

        focal_length = item.get("focal_length")
        lens = item.get("lens")
        capture_time_unix = item.get("capture_time")
        camera_make = item.get("camera_make")
        camera_model = item.get("camera_model")
        camera_profile = item.get("camera_profile")
        user_keywords = item.get("user_keywords")
        iso = item.get("iso")
        aperture = item.get("aperture")
        shutter_speed = item.get("shutter_speed")
        rating = int(item.get("rating") or 0)
        pick_status = int(item.get("pick_status") or 0)

        image_bytes_b64 = item.get("image_bytes")
        image_bytes_data = None
        if image_bytes_b64:
            import base64

            try:
                image_bytes_data = base64.b64decode(image_bytes_b64)
            except Exception as e:
                logger.warning(
                    f"Failed to decode base64 image bytes for {photo_id}: {e}"
                )

        filepath = item.get("filepath", "").strip() or None
        source_provenance = "lightroom_rendered_preview"
        if filepath:
            from utils.image_processing import extract_exiftool_preview

            try:
                raw_preview = extract_exiftool_preview(filepath)
                if raw_preview:
                    logger.info(
                        f"Successfully extracted unedited raw preview for {filepath}"
                    )
                    image_bytes_data = raw_preview
                    source_provenance = "raw_preview"
            except TimeoutError as exc:
                results.append(
                    {
                        "status": "error",
                        "photo_id": photo_id,
                        "error": f"EXIFTOOL_TIMEOUT: {exc}",
                    }
                )
                continue
            except PermissionError as exc:
                results.append(
                    {
                        "status": "error",
                        "photo_id": photo_id,
                        "error": f"EXIFTOOL_PERMISSION: {exc}",
                    }
                )
                continue

        try:
            embedding = None
            if image_bytes_data:
                embedding = _compute_clip_embedding(image_bytes_data)

            if embedding is None:
                try:
                    from services import chroma

                    chroma_data = chroma.get_image(photo_id)
                    if (
                        chroma_data
                        and chroma_data.get("embeddings") is not None
                        and len(chroma_data["embeddings"]) > 0
                    ):
                        embedding = chroma_data["embeddings"][0]
                        if hasattr(embedding, "tolist"):
                            embedding = embedding.tolist()
                except Exception:
                    pass

            with operations.admission.acquire({"catalog_write": 1}, priority=8):
                training_service.add_training_example(
                    photo_id=photo_id,
                    develop_settings=develop_settings,
                    embedding=embedding,
                    label=label,
                    filename=filename,
                    summary=summary,
                    image_bytes=image_bytes_data,
                    focal_length=focal_length,
                    lens=lens,
                    capture_time_unix=capture_time_unix,
                    camera_make=camera_make,
                    camera_model=camera_model,
                    camera_profile=camera_profile,
                    user_keywords=user_keywords,
                    iso=iso,
                    aperture=aperture,
                    shutter_speed=shutter_speed,
                    rating=rating,
                    pick_status=pick_status,
                    skip_discovery=True,
                    force_retrain=force_retrain,
                    source_provenance=source_provenance,
                )
            results.append({"status": "ok", "photo_id": photo_id})
        except ValueError as exc:
            if "Skipped" in str(exc):
                results.append(
                    {"status": "ok", "photo_id": photo_id, "warning": str(exc)}
                )
            else:
                logger.error("Batch add failed for photo_id=%s: %s", photo_id, exc)
                results.append(
                    {
                        "status": "error",
                        "photo_id": photo_id,
                        "error": str(exc),
                    }
                )
        except Exception as exc:
            logger.error("Batch add failed for photo_id=%s: %s", photo_id, exc)
            results.append(
                {
                    "status": "error",
                    "photo_id": photo_id,
                    "error": str(exc),
                }
            )

    success_count = sum(1 for r in results if r["status"] == "ok")
    if job_id:
        for result in results:
            photo_id = str(result.get("photo_id") or "").strip()
            if not photo_id:
                continue
            error = str(result.get("error") or "")
            if error.startswith("EXIFTOOL_"):
                operations.set_item_state(
                    config.DB_PATH,
                    job_id,
                    photo_id,
                    "preparing",
                    error=error,
                )
                continue
            operations.set_item_state(
                config.DB_PATH,
                job_id,
                photo_id,
                "succeeded" if result["status"] == "ok" else "failed",
                error=None if result["status"] == "ok" else error or "Training failed",
                result={"warning": result.get("warning")},
            )
    total_count = training_service.get_training_count()

    policy_generation = None
    policy_rebuild = None
    policy_warning = None
    if data.get("rebuild_policies", True):
        try:
            from services import policy_runtime

            policy_rebuild = policy_runtime.request_rebuild()
        except Exception as exc:
            policy_warning = str(exc)
            logger.error(
                "Failed to rebuild editing policies after batch: %s",
                exc,
                exc_info=True,
            )

    return jsonify(
        {
            "status": "ok",
            "added": success_count,
            "total_count": total_count,
            "results": results,
            "policy_generation": policy_generation,
            "policy_rebuild": policy_rebuild,
            "policy_warning": policy_warning,
        }
    ), 200


# ---------------------------------------------------------------------------
# GET /training/list
# ---------------------------------------------------------------------------


@training_bp.route("/training/list", methods=["GET"])
def list_training_examples():
    try:
        examples = training_service.list_training_examples()
        return jsonify(
            {"status": "ok", "examples": examples, "count": len(examples)}
        ), 200
    except Exception as exc:
        logger.error("Failed to list training examples: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /training/stats
# ---------------------------------------------------------------------------


@training_bp.route("/training/stats", methods=["GET"])
def get_training_stats():
    """Return aggregate style-profile statistics for the plugin UI."""
    try:
        stats = training_service.get_training_stats()
        return jsonify({"status": "ok", **stats}), 200
    except Exception as exc:
        logger.error("Failed to get training stats: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /training/count
# ---------------------------------------------------------------------------


@training_bp.route("/training/count", methods=["GET"])
def get_training_count():
    try:
        count = training_service.get_training_count()
        return jsonify({"count": count}), 200
    except Exception as exc:
        logger.error("Failed to get training count: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# DELETE /training/<photo_id>
# ---------------------------------------------------------------------------


@training_bp.route("/training/<path:photo_id>", methods=["DELETE"])
def delete_training_example(photo_id: str):
    try:
        with operations.admission.acquire(
            {"training_upload": 1, "catalog_write": 1}, priority=9
        ):
            deleted = training_service.delete_training_example(photo_id)
        if deleted:
            count = training_service.get_training_count()
            return jsonify(
                {"status": "ok", "photo_id": photo_id, "total_count": count}
            ), 200
        return jsonify(
            {"error": f"No training example found for photo_id={photo_id}"}
        ), 404
    except Exception as exc:
        logger.error(
            "Failed to delete training example photo_id=%s: %s",
            photo_id,
            exc,
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# DELETE /training  (clear all)
# ---------------------------------------------------------------------------


@training_bp.route("/training", methods=["DELETE"])
def clear_training_examples():
    try:
        from services import policy_runtime

        with operations.admission.acquire(
            {"training_upload": 1, "maintenance": 1, "catalog_write": 1},
            priority=20,
        ):
            from services import db as service_db

            service_db.create_persistent_backup(reason="pre-delete-training")
            styles_removed = policy_runtime.reset_policy_state()
            examples_removed = training_service.clear_all_training_examples()
        return jsonify(
            {
                "status": "ok",
                "removed": examples_removed,
                "styles_removed": styles_removed,
            }
        ), 200
    except Exception as exc:
        logger.error("Failed to clear training examples: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@training_bp.route("/training/all", methods=["DELETE"])
def clear_all_data():
    try:
        from services import policy_runtime

        with operations.admission.acquire(
            {"training_upload": 1, "maintenance": 1, "catalog_write": 1},
            priority=20,
        ):
            from services import db as service_db

            service_db.create_persistent_backup(reason="pre-delete-training")
            styles_removed = policy_runtime.reset_policy_state()
            examples_removed = training_service.clear_all_training_examples()
        return jsonify(
            {
                "status": "ok",
                "removed": examples_removed,
                "styles_removed": styles_removed,
            }
        ), 200
    except Exception as exc:
        logger.error("Failed to clear all training data: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
