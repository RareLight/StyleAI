"""
Flask blueprint: POST /style_edit

LLM-free style-matched edit endpoint.  Given a photo and its JPEG preview,
the style engine matches the photo against the user's saved training examples
and returns an interpolated Lightroom edit recipe — no LLM call required.

When the training set is too small or confidence is too low and
``use_llm_fallback=true``, the request is transparently forwarded to
the regular LLM-backed edit pipeline (re-using existing few-shot injection).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import chroma as chroma_service
from services import edit_history
from services import policy_runtime
from services import style_engine as style_engine
from services.policy_features import FEATURE_SCHEMA_VERSION
from services.policy_targets import TARGET_SCHEMA_VERSION
from services.style_engine import CONFIDENCE_LOW
from utils.edit_persistence import _persist_edit_recipe, _success_payload
from utils.request_parsing import _extract_options, _extract_photo_ids

style_edit_bp = Blueprint("style_edit", __name__)


def _persist_inference(
    *,
    photo_id: str,
    recipe: dict[str, Any],
    options: dict[str, Any],
    result: Any,
    engine: str,
) -> str:
    if not config.DB_PATH:
        raise RuntimeError("StyleAI database path is not configured")
    is_policy = engine == "policy_v2"
    return edit_history.create_recipe_inference(
        db_path=config.DB_PATH,
        photo_id=photo_id,
        recipe=recipe,
        current_settings=options.get("current_settings") or {},
        engine=engine,
        algorithm_version=(
            policy_runtime.POLICY_ALGORITHM_VERSION
            if is_policy
            else "local-llm-edit-v1"
        ),
        feature_schema_version=(FEATURE_SCHEMA_VERSION if is_policy else "local-llm"),
        target_schema_version=TARGET_SCHEMA_VERSION,
        generation_id=getattr(result, "generation_id", None),
        policy_id=getattr(result, "policy_id", None),
        hard_partition_key=getattr(result, "hard_partition_key", "default"),
        confidence=getattr(result, "confidence", None),
        entropy=getattr(result, "entropy", None),
        strength=float(options.get("style_strength", 1.0)),
    )


def _get_clip_embedding(photo_id: str):
    """Re-use the CLIP embedding already stored in ChromaDB for this photo."""
    try:
        existing = chroma_service.get_image(photo_id)
        if (
            existing
            and existing.get("ids")
            and existing.get("embeddings") is not None
            and len(existing.get("embeddings")) > 0
        ):
            import numpy as np

            raw_emb = existing["embeddings"][0]
            if raw_emb is not None:
                emb_arr = np.asarray(raw_emb, dtype=np.float32)
                if emb_arr.size > 0 and not np.allclose(emb_arr, 0.0):
                    return emb_arr.tolist()
    except Exception as exc:
        logger.debug("Could not retrieve CLIP embedding for %s: %s", photo_id, exc)
    return None


def _run_single_style_edit(
    photo_id: str,
    image_bytes: bytes,
    filename: str,
    options: dict[str, Any],
    *,
    image_bytes_dark: bytes | None = None,
    image_bytes_bright: bytes | None = None,
    focal_length: float | None = None,
    capture_time_unix: float | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    use_llm_fallback: bool = False,
) -> dict[str, Any]:
    """Run the style engine for a single photo. Returns a result dict."""
    neutral_image_bytes = image_bytes
    source_provenance = "unknown"
    clip_embedding = None
    raw_filepath = options.get("raw_filepath")
    if raw_filepath:
        from utils.image_processing import extract_exiftool_preview

        try:
            raw_preview = extract_exiftool_preview(str(raw_filepath))
        except (PermissionError, TimeoutError) as exc:
            logger.warning("Neutral preview unavailable for %s: %s", photo_id, exc)
            raw_preview = None
        if raw_preview:
            neutral_image_bytes = raw_preview
            source_provenance = "raw_preview"
    if source_provenance != "raw_preview":
        clip_embedding = _get_clip_embedding(photo_id)
    if clip_embedding is None:
        logger.info(
            f"CLIP embedding not found in database for photo_id={photo_id}. Generating dynamically via GPU..."
        )
        from services.metadata import get_analysis_service
        import server_lifecycle
        import io
        from PIL import Image

        clip_model = server_lifecycle.get_model()
        clip_processor = server_lifecycle.get_processor()
        if clip_model and clip_processor:
            try:
                img = Image.open(io.BytesIO(neutral_image_bytes))
                img.thumbnail((512, 512))
                img = img.convert("RGB")
                analysis_service = get_analysis_service()
                batch_embeddings = analysis_service._generate_image_embeddings(
                    [img], clip_model, clip_processor
                )
                if batch_embeddings and batch_embeddings[0] is not None:
                    clip_embedding = batch_embeddings[0]
                    logger.info(
                        f"Successfully generated dynamic CLIP embedding for photo_id={photo_id}."
                    )
                else:
                    logger.warning(
                        f"Failed to generate dynamic CLIP embedding for photo_id={photo_id}."
                    )
            except Exception as e:
                logger.error(
                    f"Error generating dynamic CLIP embedding for photo_id={photo_id}: {e}",
                    exc_info=True,
                )
        else:
            logger.warning(
                "CLIP model or processor not available. Cannot generate dynamic embedding."
            )
    else:
        logger.info(
            f"Successfully loaded existing CLIP embedding from database for photo_id={photo_id}."
        )

    result = style_engine.generate_style_edit(
        photo_id=photo_id,
        image_bytes=neutral_image_bytes,
        focal_length=focal_length,
        capture_time_unix=capture_time_unix,
        clip_embedding=clip_embedding,
        camera_make=camera_make,
        camera_model=camera_model,
        camera_profile=camera_profile,
        user_keywords=user_keywords,
        min_confidence=CONFIDENCE_LOW,
        current_settings=options.get("current_settings"),
        style_strength=options.get("style_strength"),
        style_override=options.get("style_override"),
        do_not_clip=options.get("do_not_clip", True),
        profile_mode=options.get("profile_mode", "suggest"),
        hdr_mode=options.get("hdr_mode", "suggest"),
        source_provenance=source_provenance,
    )

    # An explicitly enabled local-LLM fallback also covers safe ML abstentions.
    if (
        result.engine != "error"
        and result.confidence < CONFIDENCE_LOW
        and use_llm_fallback
    ):
        logger.info(
            "Style engine confidence %.3f below threshold for photo_id=%s, falling back to LLM",
            result.confidence,
            photo_id,
        )
        from services.metadata import get_analysis_service
        from services import training as training_service

        if clip_embedding is not None:
            training_examples = training_service.query_similar_training_examples(
                clip_embedding,
                n_results=3,
                camera_profile=camera_profile,
            )
        else:
            training_examples = []
        options["use_training_style"] = False
        options["_injected_training_examples"] = training_examples

        analysis_service = get_analysis_service()
        if image_bytes_dark and image_bytes_bright:
            image_data = [image_bytes_dark, image_bytes, image_bytes_bright]
        else:
            image_data = image_bytes

        llm_response = analysis_service.generate_edit_recipe_single(
            photo_id, image_data, options
        )

        if not llm_response.success or not llm_response.recipe:
            return {
                "status": "error",
                "engine": "llm",
                "photo_id": photo_id,
                "error": llm_response.error or "LLM edit generation failed",
            }

        _filter_recipe_crop_rotate(llm_response.recipe, options)
        _persist_edit_recipe(photo_id, filename, llm_response.recipe, options)
        inference_id = _persist_inference(
            photo_id=photo_id,
            recipe=llm_response.recipe,
            options=options,
            result=result,
            engine="llm",
        )
        payload = _success_payload(
            photo_id, llm_response.recipe, options, warning=llm_response.warning
        )
        payload["engine"] = "llm"
        payload["confidence"] = round(result.confidence, 3)
        payload["matched_examples"] = result.matched_count
        payload["style_engine_note"] = result.warning
        payload["input_tokens"] = llm_response.input_tokens
        payload["output_tokens"] = llm_response.output_tokens
        payload["edit_inference_id"] = inference_id
        return payload

    # Style engine had an explicit error (e.g. predictive ML model failure)
    if result.engine == "error":
        return {
            "status": "error",
            "engine": "error",
            "photo_id": photo_id,
            "confidence": round(result.confidence, 3),
            "matched_examples": result.matched_count,
            "matched_filenames": result.matched_filenames,
            "error": result.error or "Predictive model failure",
            "message": result.error or "Predictive ML engine failed to run.",
        }

    # Style engine had no result and fallback disabled (or engine was none) — return error
    if result.engine == "none":
        return {
            "status": "error",
            "engine": "none",
            "photo_id": photo_id,
            "confidence": 0.0,
            "matched_examples": 0,
            "error": "profile_mismatch",
            "message": result.warning or "Style engine could not produce a result.",
        }

    # Style engine has low confidence and LLM fallback is disabled
    if result.confidence < CONFIDENCE_LOW and not use_llm_fallback:
        return {
            "status": "error",
            "engine": "none",
            "photo_id": photo_id,
            "confidence": round(result.confidence, 3),
            "matched_examples": result.matched_count,
            "error": "low_confidence",
            "message": "Confidence is too low to apply edit safely.",
        }

    # Successful style engine result
    if not result.recipe:
        return {
            "status": "error",
            "engine": "style",
            "photo_id": photo_id,
            "confidence": round(result.confidence, 3),
            "matched_examples": result.matched_count,
            "error": "Style engine returned an empty recipe.",
        }

    _filter_recipe_crop_rotate(result.recipe, options)
    _persist_edit_recipe(photo_id, filename, result.recipe, options)
    inference_id = _persist_inference(
        photo_id=photo_id,
        recipe=result.recipe,
        options=options,
        result=result,
        engine=result.engine,
    )
    payload = _success_payload(photo_id, result.recipe, options, warning=result.warning)
    payload["engine"] = result.engine
    payload["confidence"] = round(result.confidence, 3)
    payload["matched_examples"] = result.matched_count
    payload["matched_filenames"] = result.matched_filenames
    payload["edit_inference_id"] = inference_id
    return payload


def _filter_recipe_crop_rotate(recipe: dict, options: dict) -> None:
    if not isinstance(recipe, dict) or "global" not in recipe:
        return
    crop_settings = recipe["global"].get("crop")
    if not isinstance(crop_settings, dict):
        return
    if not options.get("allow_auto_crop", True):
        for k in ("left", "right", "top", "bottom", "x", "y", "width", "height"):
            crop_settings.pop(k, None)
    if not options.get("allow_auto_rotate", True):
        for k in ("angle", "rotation"):
            crop_settings.pop(k, None)
    if not crop_settings:
        recipe["global"].pop("crop", None)


@style_edit_bp.route("/style_edit/events/application", methods=["POST"])
def record_style_edit_application():
    """Record Lightroom application outcomes after Develop-setting readback."""
    try:
        data = request.get_json(silent=True) or {}
        raw_events = data.get("events")
        if not isinstance(raw_events, list) or not raw_events or len(raw_events) > 250:
            raise ValueError("events must be a non-empty list of at most 250 items")
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        stored = []
        for item in raw_events:
            if not isinstance(item, dict):
                raise ValueError("every application event must be an object")
            inference_id = str(item.get("edit_inference_id") or "").strip()
            idempotency_key = str(item.get("idempotency_key") or "").strip()
            status = str(item.get("status") or "").strip()
            stored.append(
                edit_history.record_application(
                    db_path=config.DB_PATH,
                    inference_id=inference_id,
                    event_kind=status,
                    idempotency_key=idempotency_key,
                    current_settings=item.get("current_settings"),
                    details={
                        "global_applied": bool(item.get("global_applied", False)),
                        "masks_applied": bool(item.get("masks_applied", False)),
                        "warnings": list(item.get("warnings") or []),
                        "error": str(item.get("error") or ""),
                    },
                )
            )
        return jsonify(
            {
                "results": {"stored": len(stored), "events": stored},
                "error": None,
                "warning": None,
            }
        ), 200
    except (ValueError, LookupError) as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error(
            "Failed to record Lightroom edit application: %s",
            exc,
            exc_info=True,
        )
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@style_edit_bp.route("/style_edit/events/reconcile", methods=["POST"])
def reconcile_style_edit_state():
    """Reconcile a bounded set of Lightroom photos with applied edit history."""
    try:
        data = request.get_json(silent=True) or {}
        items = data.get("items")
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ValueError("items must be a non-empty list of at most 100 photos")
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        results = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("every reconciliation item must be an object")
            results.append(
                edit_history.reconcile_photo_state(
                    db_path=config.DB_PATH,
                    photo_id=str(item.get("photo_id") or "").strip(),
                    current_settings=item.get("current_settings"),
                )
            )
        return jsonify(
            {
                "results": {"checked": len(results), "photos": results},
                "error": None,
                "warning": None,
            }
        ), 200
    except (ValueError, LookupError) as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error("Failed to reconcile Lightroom edit state: %s", exc, exc_info=True)
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@style_edit_bp.route("/style_edit/events/outcomes", methods=["POST"])
def record_style_edit_outcomes():
    """Store bounded, explicit user judgments for applied edits."""
    try:
        data = request.get_json(silent=True) or {}
        items = data.get("items")
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ValueError("items must be a non-empty list of at most 100 photos")
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        results = []
        failures = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("every outcome item must be an object")
            inference_id = str(item.get("edit_inference_id") or "").strip()
            try:
                results.append(
                    edit_history.record_user_outcome(
                        db_path=config.DB_PATH,
                        inference_id=inference_id,
                        outcome=str(item.get("outcome") or "").strip(),
                        current_settings=item.get("current_settings"),
                    )
                )
            except (ValueError, LookupError) as exc:
                failures.append({"edit_inference_id": inference_id, "error": str(exc)})
        return jsonify(
            {
                "results": {
                    "stored": len(results),
                    "failed": len(failures),
                    "photos": results,
                    "failures": failures,
                },
                "error": None,
                "warning": (
                    f"{len(failures)} edit outcome(s) could not be stored"
                    if failures
                    else None
                ),
            }
        ), 200
    except (ValueError, LookupError) as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error("Failed to record Lightroom edit outcomes: %s", exc, exc_info=True)
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@style_edit_bp.route("/style_edit", methods=["POST"])
def style_edit():
    """Generate style-matched edit recipes for one or more photos.

    Multipart/form-data fields (per photo, use array notation [] for batch):
        image[]           (file, JPEG/PNG preview — required)
        photo_id[]        (str — required)
        use_llm_fallback  (bool string "true"/"false" — default: "false")
        focal_length      (number, mm — optional, shared across batch)
        capture_time      (float, unix timestamp — optional)
        camera_make       (string, optional)
        camera_model      (string, optional)
        camera_profile    (string, optional)
        user_keywords     (string, comma-separated — optional)

    Standard options passed through ``_extract_options`` include the selected
    local provider/model, language, and temperature.
    """
    logger.info("Style edit request received")

    images = request.files.getlist("image")
    images_dark = request.files.getlist("image_dark")
    images_bright = request.files.getlist("image_bright")
    photo_ids = _extract_photo_ids(request.form)
    valid_rendering_modes = {"off", "suggest", "auto"}
    for field in ("profile_mode", "hdr_mode"):
        raw_mode = request.form.get(field)
        if (
            raw_mode is not None
            and str(raw_mode).strip().lower() not in valid_rendering_modes
        ):
            return jsonify(
                {
                    "results": None,
                    "error": (
                        f"{field} must be one of: "
                        + ", ".join(sorted(valid_rendering_modes))
                    ),
                    "warning": None,
                }
            ), 400
    options = _extract_options(request.form)
    options["raw_filepath"] = request.form.get("filepath") or options.get(
        "raw_filepath"
    )

    if not images or not photo_ids or len(images) != len(photo_ids):
        return jsonify(
            {
                "error": "Mismatch between number of images and photo IDs, or no images provided"
            }
        ), 400

    use_llm_fallback = request.form.get("use_llm_fallback", "false").lower() in (
        "1",
        "true",
        "yes",
    )

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
    camera_model = _opt_str("camera_model")
    camera_profile = _opt_str("camera_profile")

    user_keywords: list[str] | None = None
    kw_raw = request.form.get("user_keywords", "").strip()
    if kw_raw:
        user_keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]

    # Process each photo
    results: list[dict[str, Any]] = []
    for i, (file, photo_id) in enumerate(zip(images, photo_ids)):
        if not file or not photo_id:
            results.append(
                {
                    "status": "error",
                    "photo_id": photo_id or "unknown",
                    "error": "Missing file or photo_id",
                }
            )
            continue

        image_bytes = file.read()
        image_bytes_dark = images_dark[i].read() if i < len(images_dark) else None
        image_bytes_bright = images_bright[i].read() if i < len(images_bright) else None

        result = _run_single_style_edit(
            photo_id=photo_id,
            image_bytes=image_bytes,
            filename=file.filename or "",
            options=options,
            image_bytes_dark=image_bytes_dark,
            image_bytes_bright=image_bytes_bright,
            focal_length=focal_length,
            capture_time_unix=capture_time_unix,
            camera_make=camera_make,
            camera_model=camera_model,
            camera_profile=camera_profile,
            user_keywords=user_keywords,
            use_llm_fallback=use_llm_fallback,
        )
        results.append(result)

    if len(results) == 1:
        res = results[0]
        status_code = 200
        if res.get("status") == "error":
            status_code = 422 if res.get("engine") == "none" else 500
        return jsonify(res), status_code

    return jsonify(
        {
            "status": "ok",
            "batch_size": len(results),
            "results": results,
        }
    ), 200
