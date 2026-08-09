"""
Flask blueprint: POST /style_edit

Given a photo and its JPEG preview, the policy runtime predicts absolute
Lightroom targets from the user's saved training examples. Ambiguous or
low-confidence matches abstain rather than invoking a generative fallback.
"""

from __future__ import annotations

from functools import wraps
from time import perf_counter
from typing import Any

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import chroma as chroma_service
from services import edit_history
from services import operations
from services import policy_runtime
from services import source_embeddings
from services import style_engine as style_engine
from services.policy_features import FEATURE_SCHEMA_VERSION
from services.policy_targets import TARGET_SCHEMA_VERSION
from services.style_engine import CONFIDENCE_LOW
from utils.edit_persistence import _persist_edit_recipe, _success_payload
from utils.request_parsing import _extract_options, _extract_photo_ids

style_edit_bp = Blueprint("style_edit", __name__)


def _maintenance_safe_workflow(function):
    """Keep one edit inference coherent across maintenance replacement/reset."""

    @wraps(function)
    def guarded(*args, **kwargs):
        job_id = str(kwargs.get("job_id") or "").strip() or None
        cancel_signal = (
            operations.JobCancelSignal(config.DB_PATH, job_id)
            if config.DB_PATH and job_id
            else None
        )
        with operations.workflow_maintenance_gate.workflow(cancel_event=cancel_signal):
            return function(*args, **kwargs)

    return guarded


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
    return edit_history.create_recipe_inference(
        db_path=config.DB_PATH,
        photo_id=photo_id,
        recipe=recipe,
        current_settings=options.get("current_settings") or {},
        engine=engine,
        algorithm_version=policy_runtime.POLICY_ALGORITHM_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        target_schema_version=TARGET_SCHEMA_VERSION,
        generation_id=getattr(result, "generation_id", None),
        policy_id=getattr(result, "policy_id", None),
        hard_partition_key=getattr(result, "hard_partition_key", "default"),
        confidence=getattr(result, "confidence", None),
        entropy=getattr(result, "entropy", None),
        strength=float(options.get("style_strength", 1.0)),
    )


def _get_canonical_source_embedding(
    photo_id: str,
    *,
    raw_filepath: str | None,
    rendered_image_bytes: bytes,
) -> tuple[list[float] | None, dict[str, Any], dict[str, float] | None]:
    """Load an embedding only when its complete neutral-source stamp matches."""
    try:
        existing = chroma_service.get_image(photo_id)
        metadatas = existing.get("metadatas") or []
        metadata = dict(metadatas[0]) if metadatas else {}
        embedding = source_embeddings.compatible_embedding(
            existing,
            raw_filepath=raw_filepath,
            rendered_image_bytes=rendered_image_bytes,
        )
        metrics = source_embeddings.cached_source_metrics(metadata)
        if embedding is not None and metrics is not None:
            return embedding, metadata, metrics
    except Exception as exc:
        logger.debug(
            "Could not retrieve canonical CLIP embedding for %s: %s", photo_id, exc
        )
    return None, {}, None


def _cache_canonical_source_embedding(
    photo_id: str,
    filename: str,
    embedding: list[float],
    source: source_embeddings.NeutralSource,
    source_metrics: dict[str, float],
) -> None:
    """Atomically replace one derived vector without disturbing catalog metadata."""
    existing = chroma_service.get_image(photo_id)
    metadatas = existing.get("metadatas") or []
    metadata = dict(metadatas[0]) if metadatas else {}
    metadata.update(
        {
            "filename": filename,
            "photo_id": photo_id,
            "has_embedding": True,
        }
    )
    metadata = source_embeddings.stamp_metadata(
        metadata,
        source,
        source_metrics=source_metrics,
    )
    if existing.get("ids"):
        chroma_service.update_image(photo_id, metadata, embedding=embedding)
    else:
        chroma_service.add_image(photo_id, embedding, metadata)


@_maintenance_safe_workflow
def _run_single_style_edit(
    photo_id: str,
    image_bytes: bytes,
    filename: str,
    options: dict[str, Any],
    *,
    focal_length: float | None = None,
    capture_time_unix: float | None = None,
    camera_make: str | None = None,
    camera_model: str | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run the style engine for a single photo. Returns a result dict."""
    started_at = perf_counter()
    timings_ms: dict[str, float] = {}

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        timings_ms["total"] = round((perf_counter() - started_at) * 1000.0, 1)
        payload["timings_ms"] = dict(timings_ms)
        logger.info(
            "Style edit timing photo_id=%s cache_hit=%s timings_ms=%s",
            photo_id,
            bool(payload.get("source_embedding_cache_hit")),
            timings_ms,
        )
        return payload

    neutral_image_bytes = image_bytes
    source_provenance = "unknown"
    source_metrics = None
    cancel_signal = (
        operations.JobCancelSignal(config.DB_PATH, job_id)
        if config.DB_PATH and job_id
        else None
    )
    raw_filepath = options.get("raw_filepath")
    stage_started = perf_counter()
    clip_embedding, cached_metadata, source_metrics = _get_canonical_source_embedding(
        photo_id,
        raw_filepath=raw_filepath,
        rendered_image_bytes=image_bytes,
    )
    timings_ms["embedding_lookup"] = round((perf_counter() - stage_started) * 1000.0, 1)
    cache_hit = clip_embedding is not None
    canonical_source = None
    if cache_hit:
        source_provenance = str(
            cached_metadata.get("source_embedding_provenance") or "unknown"
        )

    if clip_embedding is None:
        logger.info(
            "Compatible source embedding not found for photo_id=%s. Generating dynamically via GPU...",
            photo_id,
        )
        from services.metadata import get_analysis_service
        from services import training as training_service
        import server_lifecycle

        stage_started = perf_counter()
        canonical_source = source_embeddings.resolve_neutral_source(
            image_bytes,
            raw_filepath,
        )
        neutral_image_bytes = canonical_source.image_bytes
        source_provenance = canonical_source.provenance
        source_metrics = training_service.compute_exposure_metrics(neutral_image_bytes)
        image = source_embeddings.decode_for_embedding(neutral_image_bytes)
        timings_ms["source_prepare"] = round(
            (perf_counter() - stage_started) * 1000.0, 1
        )

        stage_started = perf_counter()
        with operations.admission.acquire(
            {"accelerator": 1, "cpu_prepare": 1},
            priority=9,
            cancel_event=cancel_signal,
        ):
            clip_model = server_lifecycle.get_model()
            clip_processor = server_lifecycle.get_processor()
            if clip_model and clip_processor and image is not None:
                try:
                    try:
                        analysis_service = get_analysis_service()
                        batch_embeddings = analysis_service._generate_image_embeddings(
                            [image], clip_model, clip_processor
                        )
                    finally:
                        image.close()
                    if batch_embeddings and batch_embeddings[0] is not None:
                        clip_embedding = batch_embeddings[0]
                        logger.info(
                            "Successfully generated dynamic CLIP embedding for photo_id=%s.",
                            photo_id,
                        )
                    else:
                        logger.warning(
                            "Failed to generate dynamic CLIP embedding for photo_id=%s.",
                            photo_id,
                        )
                except Exception as exc:
                    logger.error(
                        "Error generating dynamic CLIP embedding for photo_id=%s: %s",
                        photo_id,
                        exc,
                        exc_info=True,
                    )
            else:
                logger.warning(
                    "CLIP model or processor not available. Cannot generate dynamic embedding."
                )
                if image is not None:
                    image.close()
        timings_ms["embedding_inference"] = round(
            (perf_counter() - stage_started) * 1000.0, 1
        )

        if clip_embedding is not None and canonical_source is not None:
            stage_started = perf_counter()
            try:
                if cancel_signal is None or not cancel_signal.is_set():
                    with operations.admission.acquire(
                        {"catalog_write": 1},
                        priority=9,
                        cancel_event=cancel_signal,
                    ):
                        _cache_canonical_source_embedding(
                            photo_id,
                            filename,
                            clip_embedding,
                            canonical_source,
                            source_metrics or {},
                        )
            except InterruptedError:
                raise
            except Exception as exc:
                logger.warning(
                    "Could not cache canonical source embedding for %s: %s",
                    photo_id,
                    exc,
                    exc_info=True,
                )
            timings_ms["embedding_cache_write"] = round(
                (perf_counter() - stage_started) * 1000.0, 1
            )
    else:
        logger.info(
            "Loaded compatible canonical source embedding for photo_id=%s.", photo_id
        )

    stage_started = perf_counter()
    with operations.admission.acquire(
        {"cpu_prepare": 1}, priority=9, cancel_event=cancel_signal
    ):
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
            profile_mode=options.get("profile_mode", "suggest"),
            hdr_mode=options.get("hdr_mode", "suggest"),
            source_provenance=source_provenance,
            source_metrics=source_metrics,
        )
    timings_ms["policy_inference"] = round((perf_counter() - stage_started) * 1000.0, 1)

    # Style engine had an explicit error (e.g. predictive ML model failure)
    if result.engine == "error":
        return finish(
            {
                "status": "error",
                "engine": "error",
                "photo_id": photo_id,
                "confidence": round(result.confidence, 3),
                "matched_examples": result.matched_count,
                "matched_filenames": result.matched_filenames,
                "error": result.error or "Predictive model failure",
                "message": result.error or "Predictive ML engine failed to run.",
                "source_embedding_cache_hit": cache_hit,
            }
        )

    # The policy runtime could not produce a compatible result.
    if result.engine == "none":
        return finish(
            {
                "status": "error",
                "engine": "none",
                "photo_id": photo_id,
                "confidence": 0.0,
                "matched_examples": 0,
                "error": "profile_mismatch",
                "message": result.warning or "Style engine could not produce a result.",
                "source_embedding_cache_hit": cache_hit,
            }
        )

    # Ambiguous matches abstain rather than blending or generating a fallback.
    if result.confidence < CONFIDENCE_LOW:
        return finish(
            {
                "status": "error",
                "engine": "none",
                "photo_id": photo_id,
                "confidence": round(result.confidence, 3),
                "matched_examples": result.matched_count,
                "error": "low_confidence",
                "message": "Confidence is too low to apply edit safely.",
                "source_embedding_cache_hit": cache_hit,
            }
        )

    # Successful style engine result
    if not result.recipe:
        return finish(
            {
                "status": "error",
                "engine": "style",
                "photo_id": photo_id,
                "confidence": round(result.confidence, 3),
                "matched_examples": result.matched_count,
                "error": "Style engine returned an empty recipe.",
                "source_embedding_cache_hit": cache_hit,
            }
        )

    _filter_recipe_crop_rotate(result.recipe, options)
    stage_started = perf_counter()
    with operations.admission.acquire({"catalog_write": 1}, priority=9):
        _persist_edit_recipe(photo_id, filename, result.recipe, options)
        inference_id = _persist_inference(
            photo_id=photo_id,
            recipe=result.recipe,
            options=options,
            result=result,
            engine=result.engine,
        )
    timings_ms["recipe_persist"] = round((perf_counter() - stage_started) * 1000.0, 1)
    payload = _success_payload(photo_id, result.recipe, options, warning=result.warning)
    payload["engine"] = result.engine
    payload["confidence"] = round(result.confidence, 3)
    payload["matched_examples"] = result.matched_count
    payload["matched_filenames"] = result.matched_filenames
    payload["edit_inference_id"] = inference_id
    payload["source_embedding_cache_hit"] = cache_hit
    return finish(payload)


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
        with operations.admission.acquire({"catalog_write": 1}, priority=9):
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
        with operations.admission.acquire({"catalog_write": 1}, priority=9):
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
        with operations.admission.acquire({"catalog_write": 1}, priority=9):
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
                    failures.append(
                        {"edit_inference_id": inference_id, "error": str(exc)}
                    )
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
        focal_length      (number, mm — optional, shared across batch)
        capture_time      (float, unix timestamp — optional)
        camera_make       (string, optional)
        camera_model      (string, optional)
        camera_profile    (string, optional)
        user_keywords     (string, comma-separated — optional)

    Standard options passed through ``_extract_options`` control target families,
    rendering-state selection, and application safety.
    """
    logger.info("Style edit request received")
    operations.refresh_system_pressure()

    images = request.files.getlist("image")
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
    job_id = str(request.form.get("job_id") or "").strip() or None
    job = None
    if job_id:
        if not config.DB_PATH:
            return jsonify({"error": "StyleAI database path is not configured"}), 500
        job = operations.get_job(config.DB_PATH, job_id, include_items=True)
        if job is None:
            return jsonify({"error": f"operation job not found: {job_id}"}), 404
        if job["kind"] != "edit":
            return jsonify({"error": "job_id does not identify an edit operation"}), 400
        if job["state"] in operations.TERMINAL_STATES:
            return jsonify({"error": "edit operation is already complete"}), 409

    if not images or not photo_ids or len(images) != len(photo_ids):
        return jsonify(
            {
                "error": "Mismatch between number of images and photo IDs, or no images provided"
            }
        ), 400
    if len(photo_ids) != len(set(photo_ids)):
        return jsonify({"error": "Duplicate photo IDs are not allowed"}), 400
    if job is not None:
        expected_ids = {item["item_id"] for item in job.get("items", [])}
        unexpected_ids = sorted(set(photo_ids) - expected_ids)
        if unexpected_ids:
            return (
                jsonify(
                    {
                        "error": "edit request contains photos not admitted to this job",
                        "photo_ids": unexpected_ids,
                    }
                ),
                400,
            )
        operations.set_job_state(config.DB_PATH, job_id, "running")

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

        if job_id and operations.is_cancel_requested(config.DB_PATH, job_id):
            operations.set_item_state(
                config.DB_PATH,
                job_id,
                photo_id,
                "canceled",
                error="Edit operation canceled before execution",
            )
            results.append(
                {
                    "status": "error",
                    "engine": "none",
                    "photo_id": photo_id,
                    "error": "canceled",
                    "message": "Edit operation canceled before execution.",
                }
            )
            continue

        if job_id:
            operations.set_item_state(config.DB_PATH, job_id, photo_id, "running")

        image_bytes = file.read()
        try:
            result = _run_single_style_edit(
                photo_id=photo_id,
                image_bytes=image_bytes,
                filename=file.filename or "",
                options=options,
                focal_length=focal_length,
                capture_time_unix=capture_time_unix,
                camera_make=camera_make,
                camera_model=camera_model,
                camera_profile=camera_profile,
                user_keywords=user_keywords,
                job_id=job_id,
            )
        except InterruptedError:
            result = {
                "status": "error",
                "engine": "none",
                "photo_id": photo_id,
                "error": "canceled",
                "message": "Edit operation canceled while waiting for resources.",
            }
        if job_id:
            canceled = result.get("error") == "canceled"
            succeeded = result.get("status") == "success"
            operations.set_item_state(
                config.DB_PATH,
                job_id,
                photo_id,
                "canceled" if canceled else ("committing" if succeeded else "failed"),
                error=None
                if succeeded or canceled
                else str(result.get("error") or "Edit failed"),
                result={
                    "engine": result.get("engine"),
                    "confidence": result.get("confidence"),
                },
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
