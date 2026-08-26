"""
Flask blueprint: POST /style_edit

Given a photo and its JPEG preview, the policy runtime predicts absolute
Lightroom targets from the user's saved training examples. Ambiguous or
low-confidence matches abstain rather than invoking a generative fallback.
"""

from __future__ import annotations

from functools import wraps
import hashlib
import json
import math
from time import perf_counter
from typing import Any

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import chroma as chroma_service
from services import edit_history
from services import edit_burst_coherence
from services import operations
from services import policy_runtime
from services import source_embeddings
from services import training as training_service
from services import style_engine as style_engine
from services.policy_features import FEATURE_SCHEMA_VERSION
from services.policy_targets import TARGET_SCHEMA_VERSION
from services.style_engine import CONFIDENCE_LOW
from services.policy_targets import interpolate_absolute_target
from services.photo_constraints import is_stitched_panorama
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
    job_id: str | None = None,
    burst_provenance: dict[str, Any] | None = None,
) -> str:
    if not config.DB_PATH:
        raise RuntimeError("StyleAI database path is not configured")
    inference_id = None
    if job_id:
        inference_id = (
            "edit:"
            + hashlib.sha256(
                f"edit-inference-v2\0{job_id}\0{photo_id}".encode("utf-8")
            ).hexdigest()
        )
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
        inference_id=inference_id,
        operation_job_id=job_id,
        absolute_target=getattr(result, "absolute_target", None),
        burst_provenance=burst_provenance,
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


def _prepare_batch_source_embeddings(
    items: list[dict[str, Any]], job_id: str
) -> list[dict[str, Any]]:
    """Resolve canonical evidence and batch only compatible cache misses."""
    prepared: list[dict[str, Any]] = [{} for _ in items]
    misses: list[int] = []
    existing_by_id: dict[str, dict[str, Any]] = {}
    bulk_read_failed = False
    try:
        existing_batch = chroma_service.get_images([item["photo_id"] for item in items])
        ids = existing_batch.get("ids") or []
        metadatas = existing_batch.get("metadatas") or []
        embeddings = existing_batch.get("embeddings")
        if embeddings is None:
            embeddings = []
        for row_index, photo_id in enumerate(ids):
            existing_by_id[str(photo_id)] = {
                "ids": [photo_id],
                "metadatas": [
                    metadatas[row_index]
                    if row_index < len(metadatas) and metadatas[row_index]
                    else {}
                ],
                "embeddings": [
                    embeddings[row_index] if row_index < len(embeddings) else None
                ],
            }
    except Exception as exc:
        bulk_read_failed = True
        logger.debug("Could not bulk-read canonical source embeddings: %s", exc)
    for index, item in enumerate(items):
        if bulk_read_failed:
            embedding, metadata, metrics = _get_canonical_source_embedding(
                item["photo_id"],
                raw_filepath=item["options"].get("raw_filepath"),
                rendered_image_bytes=item["image_bytes"],
            )
        else:
            existing = existing_by_id.get(item["photo_id"], {})
            metadatas = existing.get("metadatas") or []
            metadata = dict(metadatas[0]) if metadatas else {}
            embedding = source_embeddings.compatible_embedding(
                existing,
                raw_filepath=item["options"].get("raw_filepath"),
                rendered_image_bytes=item["image_bytes"],
            )
            metrics = source_embeddings.cached_source_metrics(metadata)
        if embedding is not None and metrics is not None:
            prepared[index] = {
                "embedding": embedding,
                "source_provenance": str(
                    metadata.get("source_embedding_provenance") or "unknown"
                ),
                "source_metrics": metrics,
                "cache_hit": True,
            }
        else:
            misses.append(index)
    if not misses:
        return prepared

    from services.metadata import get_analysis_service
    import server_lifecycle

    cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)
    batch_size = max(1, operations.recommended_gpu_batch_size())
    for offset in range(0, len(misses), batch_size):
        chunk_indices = misses[offset : offset + batch_size]
        sources: list[source_embeddings.NeutralSource] = []
        metrics_by_item: list[dict[str, float]] = []
        decoded = []
        image_byte_count = 0
        for item_index in chunk_indices:
            item = items[item_index]
            source = source_embeddings.resolve_neutral_source(
                item["image_bytes"], item["options"].get("raw_filepath")
            )
            sources.append(source)
            metrics_by_item.append(
                training_service.compute_exposure_metrics(source.image_bytes)
            )
            decoded.append(source_embeddings.decode_for_embedding(source.image_bytes))
            image_byte_count += len(source.image_bytes)
        valid_positions = [
            position for position, image in enumerate(decoded) if image is not None
        ]
        generated: list[list[float] | None] = [None] * len(decoded)
        if valid_positions:
            maximum_bytes = operations.admission.maximum_capacities["image_bytes"]
            admitted_bytes = max(1, min(image_byte_count, maximum_bytes))
            try:
                with operations.admission.acquire(
                    {
                        "accelerator": 1,
                        "cpu_prepare": 1,
                        "image_bytes": admitted_bytes,
                    },
                    priority=9,
                    cancel_event=cancel_signal,
                ):
                    model = server_lifecycle.get_model()
                    processor = server_lifecycle.get_processor()
                    if model and processor:
                        values = get_analysis_service()._generate_image_embeddings(
                            [decoded[position] for position in valid_positions],
                            model,
                            processor,
                        )
                        for position, value in zip(
                            valid_positions, values or [], strict=False
                        ):
                            generated[position] = value
            finally:
                for image in decoded:
                    if image is not None:
                        image.close()
        for position, item_index in enumerate(chunk_indices):
            item = items[item_index]
            embedding = generated[position]
            source = sources[position]
            metrics = metrics_by_item[position]
            if embedding is None or cancel_signal.is_set():
                prepared[item_index] = None
                continue
            prepared[item_index] = {
                "embedding": embedding,
                "source_provenance": source.provenance,
                "source_metrics": metrics,
                "cache_hit": False,
            }
            try:
                with operations.admission.acquire(
                    {"catalog_write": 1}, priority=9, cancel_event=cancel_signal
                ):
                    _cache_canonical_source_embedding(
                        item["photo_id"],
                        item["filename"],
                        embedding,
                        source,
                        metrics,
                    )
            except InterruptedError:
                raise
            except Exception as exc:
                logger.warning(
                    "Could not cache batch source embedding for photo_id=%s: %s",
                    item["photo_id"],
                    exc,
                    exc_info=True,
                )
    return prepared


def _run_single_style_edit_core(
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
    lens: str | None = None,
    iso: float | None = None,
    aperture: float | None = None,
    shutter_speed: str | float | None = None,
    is_hdr: bool | None = None,
    user_keywords: list[str] | None = None,
    job_id: str | None = None,
    defer_persistence: bool = False,
    policy_override: str | None = None,
    prepared_source: dict[str, Any] | None = None,
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
    if prepared_source is not None:
        clip_embedding = prepared_source.get("embedding")
        cached_metadata = {}
        source_metrics = prepared_source.get("source_metrics")
        source_provenance = str(prepared_source.get("source_provenance") or "unknown")
    else:
        clip_embedding, cached_metadata, source_metrics = (
            _get_canonical_source_embedding(
                photo_id,
                raw_filepath=raw_filepath,
                rendered_image_bytes=image_bytes,
            )
        )
    timings_ms["embedding_lookup"] = round((perf_counter() - stage_started) * 1000.0, 1)
    cache_hit = (
        bool(prepared_source.get("cache_hit"))
        if prepared_source is not None
        else clip_embedding is not None
    )
    canonical_source = None
    if cache_hit and prepared_source is None:
        source_provenance = str(
            cached_metadata.get("source_embedding_provenance") or "unknown"
        )

    if clip_embedding is None and prepared_source is None:
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
    elif prepared_source is None:
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
            lens=lens,
            iso=iso,
            aperture=aperture,
            shutter_speed=shutter_speed,
            is_hdr=is_hdr,
            user_keywords=user_keywords,
            min_confidence=CONFIDENCE_LOW,
            current_settings=options.get("current_settings"),
            style_strength=options.get("style_strength"),
            profile_mode=options.get("profile_mode", "suggest"),
            hdr_mode=options.get("hdr_mode", "suggest"),
            source_provenance=source_provenance,
            source_metrics=source_metrics,
            policy_override=policy_override,
            generation_id=options.get("generation_id"),
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

    # Deliberate policy abstentions are safe skips. Setup/evidence failures remain
    # errors because retrying after repair can produce an edit.
    if result.engine == "none":
        abstention_reason = getattr(result, "abstention_reason", None)
        if abstention_reason in {
            "unsupported_rendering_partition",
            "ambiguous_policy_match",
        }:
            return finish(
                {
                    "status": "skipped",
                    "engine": "none",
                    "photo_id": photo_id,
                    "confidence": 0.0,
                    "matched_examples": 0,
                    "skip_reason": abstention_reason,
                    "message": result.warning or "Editing policy safely abstained.",
                    "source_embedding_cache_hit": cache_hit,
                }
            )
        return finish(
            {
                "status": "error",
                "engine": "none",
                "photo_id": photo_id,
                "confidence": 0.0,
                "matched_examples": 0,
                "error": abstention_reason or "policy_unavailable",
                "message": result.warning or "Style engine could not produce a result.",
                "source_embedding_cache_hit": cache_hit,
            }
        )

    # Ambiguous matches abstain rather than blending or generating a fallback.
    if result.confidence < CONFIDENCE_LOW:
        return finish(
            {
                "status": "skipped",
                "engine": "none",
                "photo_id": photo_id,
                "confidence": round(result.confidence, 3),
                "matched_examples": result.matched_count,
                "skip_reason": "low_confidence",
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
    payload = _success_payload(photo_id, result.recipe, options, warning=result.warning)
    payload["engine"] = result.engine
    payload["confidence"] = round(result.confidence, 3)
    payload["matched_examples"] = result.matched_count
    payload["matched_filenames"] = result.matched_filenames
    payload["source_embedding_cache_hit"] = cache_hit
    if defer_persistence:
        payload["_style_result"] = result
        payload["_source_evidence"] = edit_burst_coherence.BurstEvidence(
            photo_id=photo_id,
            capture_time=capture_time_unix,
            embedding=tuple(float(value) for value in (clip_embedding or [])),
            source_provenance=source_provenance,
            source_metrics=dict(source_metrics or {}),
            camera_make=camera_make,
            camera_model=camera_model,
            camera_profile=camera_profile,
            lens=lens,
            iso=iso,
            aperture=aperture,
            shutter_speed=shutter_speed,
            focal_length=focal_length,
            is_panorama=is_stitched_panorama({"filename": filename}),
            hard_partition_key=result.hard_partition_key,
            policy_id=result.policy_id,
            confidence=result.confidence,
            entropy=result.entropy,
        )
        return finish(payload)

    stage_started = perf_counter()
    with operations.admission.acquire({"catalog_write": 1}, priority=9):
        _persist_edit_recipe(photo_id, filename, result.recipe, options)
        inference_id = _persist_inference(
            photo_id=photo_id,
            recipe=result.recipe,
            options=options,
            result=result,
            engine=result.engine,
            job_id=job_id,
            burst_provenance={
                "selected_tier": "independent",
                "fallback_reason": "single_photo",
            },
        )
    timings_ms["recipe_persist"] = round((perf_counter() - stage_started) * 1000.0, 1)
    payload["edit_inference_id"] = inference_id
    return finish(payload)


@_maintenance_safe_workflow
def _run_single_style_edit(*args, **kwargs) -> dict[str, Any]:
    """Maintenance-safe compatibility wrapper for ordinary single inference."""
    return _run_single_style_edit_core(*args, **kwargs)


def _apply_representative_global_target(
    member_payload: dict[str, Any],
    representative_payload: dict[str, Any],
    options: dict[str, Any],
) -> None:
    """Merge an allowlisted absolute target and reapply member strength safely."""
    member_result = member_payload["_style_result"]
    representative_result = representative_payload["_style_result"]
    merged_target = edit_burst_coherence.merge_global_target(
        representative_result.absolute_target,
        member_result.absolute_target,
    )
    applied = interpolate_absolute_target(
        training_service.normalize_develop_settings_for_style(
            options.get("current_settings") or {}
        ),
        merged_target,
        strength=float(options.get("style_strength", 1.0)),
    )
    recipe = style_engine._canonical_to_edit_recipe(
        applied,
        str(member_result.recipe.get("summary") or ""),
    )
    rendering_intent = member_result.recipe.get("rendering_intent")
    if isinstance(rendering_intent, dict):
        recipe["rendering_intent"] = rendering_intent
    _filter_recipe_crop_rotate(recipe, options)
    member_result.recipe = recipe
    member_result.absolute_target = merged_target
    member_payload["recipe"] = recipe


def _persist_deferred_style_edit(
    *,
    payload: dict[str, Any],
    photo_id: str,
    filename: str,
    options: dict[str, Any],
    job_id: str | None,
    burst_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Persist one already-inferred member without coupling sibling failures."""
    result = payload.pop("_style_result")
    payload.pop("_source_evidence", None)
    started_at = perf_counter()
    with operations.admission.acquire({"catalog_write": 1}, priority=9):
        _persist_edit_recipe(photo_id, filename, result.recipe, options)
        inference_id = _persist_inference(
            photo_id=photo_id,
            recipe=result.recipe,
            options=options,
            result=result,
            engine=result.engine,
            job_id=job_id,
            burst_provenance=burst_provenance,
        )
    timings = dict(payload.get("timings_ms") or {})
    timings["recipe_persist"] = round((perf_counter() - started_at) * 1000.0, 1)
    payload["timings_ms"] = timings
    payload["edit_inference_id"] = inference_id
    payload["burst_coherence"] = {
        "tier": burst_provenance.get("selected_tier", "independent"),
        "group_id": burst_provenance.get("group_id"),
        "group_size": burst_provenance.get("group_size", 1),
        "representative_photo_id": burst_provenance.get("representative_photo_id"),
        "fallback_reason": burst_provenance.get("fallback_reason"),
    }
    return payload


@_maintenance_safe_workflow
def _run_coherent_style_edit_batch(
    items: list[dict[str, Any]], *, job_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Infer a bounded operation batch, decide tiers, then persist each member."""
    batch_started_at = perf_counter()
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    source_prepare_started_at = perf_counter()
    try:
        prepared_sources = _prepare_batch_source_embeddings(items, job_id)
    except InterruptedError:
        prepared_sources = [None] * len(items)
    except Exception as exc:
        prepared_sources = [None] * len(items)
        logger.warning(
            "Batch source preparation failed; falling back to per-photo preparation: %s",
            exc,
            exc_info=True,
        )
    source_prepare_ms = round((perf_counter() - source_prepare_started_at) * 1000.0, 1)
    provisional_evidence = [
        edit_burst_coherence.BurstEvidence(
            photo_id=item["photo_id"],
            capture_time=item.get("capture_time"),
            embedding=tuple(
                float(value)
                for value in ((prepared_sources[index] or {}).get("embedding") or [])
            ),
            source_provenance=str(
                (prepared_sources[index] or {}).get("source_provenance") or "unknown"
            ),
            source_metrics=dict(
                (prepared_sources[index] or {}).get("source_metrics") or {}
            ),
            camera_make=item.get("camera_make"),
            camera_model=item.get("camera_model"),
            camera_profile=item.get("camera_profile"),
            lens=item.get("lens"),
            iso=item.get("iso"),
            aperture=item.get("aperture"),
            shutter_speed=item.get("shutter_speed"),
            focal_length=item.get("focal_length"),
            is_panorama=is_stitched_panorama({"filename": item["filename"]}),
        )
        for index, item in enumerate(items)
    ]
    inference_order = edit_burst_coherence.representative_first_order(
        provisional_evidence, job_id
    )
    results_by_index: list[dict[str, Any] | None] = [None] * len(items)
    for item_index in inference_order:
        item = items[item_index]
        try:
            payload = _run_single_style_edit_core(
                photo_id=item["photo_id"],
                image_bytes=item["image_bytes"],
                filename=item["filename"],
                options=item["options"],
                focal_length=item.get("focal_length"),
                capture_time_unix=item.get("capture_time"),
                camera_make=item.get("camera_make"),
                camera_model=item.get("camera_model"),
                camera_profile=item.get("camera_profile"),
                lens=item.get("lens"),
                iso=item.get("iso"),
                aperture=item.get("aperture"),
                shutter_speed=item.get("shutter_speed"),
                is_hdr=item.get("is_hdr"),
                user_keywords=item.get("user_keywords"),
                job_id=job_id,
                defer_persistence=True,
                prepared_source=prepared_sources[item_index],
            )
        except InterruptedError:
            payload = {
                "status": "error",
                "engine": "none",
                "photo_id": item["photo_id"],
                "error": "canceled",
                "message": "Edit operation canceled while waiting for resources.",
            }
        results_by_index[item_index] = payload
        if payload.get("status") == "success" and payload.get("_source_evidence"):
            pending.append((item, payload))

    results = [result for result in results_by_index if isinstance(result, dict)]

    evidence = [payload["_source_evidence"] for _item, payload in pending]
    decisions, diagnostics = edit_burst_coherence.decide_reuse_tiers(evidence, job_id)
    pressure_level = str(operations.pressure_snapshot().get("level") or "normal")
    if pressure_level in {"constrained", "critical"}:
        adjusted: dict[str, edit_burst_coherence.BurstDecision] = {}
        for photo_id, decision in decisions.items():
            tier = decision.tier
            reason = decision.fallback_reason
            if pressure_level == "critical" and tier != "independent":
                tier = "independent"
                reason = "runtime_pressure_independent_fallback"
            elif pressure_level == "constrained" and tier == "global_target_reuse":
                tier = "policy_coherent"
                reason = "runtime_pressure_exact_reuse_disabled"
            adjusted[photo_id] = edit_burst_coherence.BurstDecision(
                photo_id=decision.photo_id,
                tier=tier,
                fallback_reason=reason,
                group_id=decision.group_id,
                representative_photo_id=decision.representative_photo_id,
                group_size=decision.group_size,
                capture_delta_seconds=decision.capture_delta_seconds,
                cosine_distance=decision.cosine_distance,
                source_metric_deltas=decision.source_metric_deltas,
                policy_agreement=decision.policy_agreement,
            )
        decisions = adjusted
        diagnostics["pressure_adjustment"] = pressure_level
        diagnostics["tier_counts"] = {
            tier: sum(decision.tier == tier for decision in decisions.values())
            for tier in (
                "independent",
                "policy_coherent",
                "global_target_reuse",
            )
        }
    payload_by_photo = {payload["photo_id"]: payload for _item, payload in pending}
    for item, payload in pending:
        decision = decisions[payload["photo_id"]]
        if decision.tier == "global_target_reuse":
            representative = payload_by_photo.get(
                str(decision.representative_photo_id or "")
            )
            if representative is None:
                decision = edit_burst_coherence.BurstDecision(
                    photo_id=decision.photo_id,
                    tier="independent",
                    fallback_reason="representative_result_unavailable",
                    group_id=decision.group_id,
                    representative_photo_id=decision.representative_photo_id,
                    group_size=decision.group_size,
                    capture_delta_seconds=decision.capture_delta_seconds,
                    cosine_distance=decision.cosine_distance,
                    source_metric_deltas=decision.source_metric_deltas,
                    policy_agreement=decision.policy_agreement,
                )
            else:
                _apply_representative_global_target(
                    payload, representative, item["options"]
                )
        provenance = decision.provenance()
        try:
            _persist_deferred_style_edit(
                payload=payload,
                photo_id=item["photo_id"],
                filename=item["filename"],
                options=item["options"],
                job_id=job_id,
                burst_provenance=provenance,
            )
        except Exception as exc:
            payload.pop("_style_result", None)
            payload.pop("_source_evidence", None)
            payload.clear()
            payload.update(
                {
                    "status": "error",
                    "engine": "error",
                    "photo_id": item["photo_id"],
                    "error": "Could not persist generated edit recipe",
                }
            )
            logger.error(
                "Could not persist burst edit inference for photo_id=%s: %s",
                item["photo_id"],
                exc,
                exc_info=True,
            )
    logger.info(
        "Style edit burst diagnostics job_id=%s candidates=%s groups=%s tiers=%s rejections=%s",
        job_id,
        diagnostics.get("candidate_count"),
        diagnostics.get("accepted_group_count"),
        diagnostics.get("tier_counts"),
        diagnostics.get("rejection_reasons"),
    )
    diagnostics["timings_ms"] = {
        "source_embedding_batch": source_prepare_ms,
        "total": round((perf_counter() - batch_started_at) * 1000.0, 1),
    }
    diagnostics["peak_request_image_bytes"] = sum(
        len(item["image_bytes"]) for item in items
    )
    diagnostics["queue_depth"] = len(items)
    diagnostics["independent_policy_predictions"] = len(pending)
    diagnostics["policy_predictions_executed"] = len(pending)
    diagnostics["avoided_policy_predictions"] = 0
    return results, diagnostics


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
                            "masks_applied": False,
                            "applied_to_virtual_copy": bool(
                                item.get("applied_to_virtual_copy", False)
                            ),
                            "applied_copy_name": str(
                                item.get("applied_copy_name") or ""
                            )[:255],
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
        skip_existing = data.get("skip_existing", True)
        if not isinstance(skip_existing, bool):
            raise ValueError("skip_existing must be a boolean")
        inference_ids = [
            str(item.get("edit_inference_id") or "").strip()
            for item in items
            if isinstance(item, dict)
        ]
        existing_statuses = {
            status["inference_id"]: status
            for status in edit_history.get_user_outcome_statuses(
                db_path=config.DB_PATH,
                inference_ids=inference_ids,
            )
        }
        results = []
        failures = []
        new_reviews = 0
        corrected_reviews = 0
        unchanged_reviews = 0
        skipped_reviewed = 0
        with operations.admission.acquire({"catalog_write": 1}, priority=9):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("every outcome item must be an object")
                inference_id = str(item.get("edit_inference_id") or "").strip()
                try:
                    prior = existing_statuses.get(inference_id) or {}
                    if skip_existing and prior.get("reviewed"):
                        results.append(
                            {
                                **prior,
                                "recorded": False,
                                "skipped_existing": True,
                            }
                        )
                        skipped_reviewed += 1
                        continue
                    stored = edit_history.record_user_outcome(
                        db_path=config.DB_PATH,
                        inference_id=inference_id,
                        outcome=str(item.get("outcome") or "").strip(),
                        current_settings=item.get("current_settings"),
                    )
                    stored["prior_outcome"] = prior.get("outcome")
                    stored["skipped_existing"] = False
                    results.append(stored)
                    if stored.get("recorded"):
                        if prior.get("reviewed"):
                            corrected_reviews += 1
                        else:
                            new_reviews += 1
                    else:
                        unchanged_reviews += 1
                except (ValueError, LookupError) as exc:
                    failures.append(
                        {"edit_inference_id": inference_id, "error": str(exc)}
                    )
        return jsonify(
            {
                "results": {
                    "stored": new_reviews + corrected_reviews,
                    "new_reviews": new_reviews,
                    "corrected_reviews": corrected_reviews,
                    "unchanged_reviews": unchanged_reviews,
                    "skipped_reviewed": skipped_reviewed,
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


@style_edit_bp.route("/style_edit/events/outcomes/status", methods=["POST"])
def get_style_edit_outcome_statuses():
    """Return authoritative review status before Lightroom builds a review batch."""
    try:
        data = request.get_json(silent=True) or {}
        inference_ids = data.get("edit_inference_ids")
        if not isinstance(inference_ids, list) or not inference_ids:
            raise ValueError("edit_inference_ids must be a non-empty list")
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        statuses = edit_history.get_user_outcome_statuses(
            db_path=config.DB_PATH,
            inference_ids=inference_ids,
        )
        return jsonify(
            {
                "results": {"checked": len(statuses), "photos": statuses},
                "error": None,
                "warning": None,
            }
        ), 200
    except (ValueError, LookupError) as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error("Failed to query Lightroom edit outcomes: %s", exc, exc_info=True)
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
        job = operations.get_job(config.DB_PATH, job_id, include_items=False)
        if job is None:
            return jsonify({"error": f"operation job not found: {job_id}"}), 404
        if job["kind"] != "edit":
            return jsonify({"error": "job_id does not identify an edit operation"}), 400
        if job["cancel_requested"]:
            return jsonify({"error": "canceled"}), 422
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
        admitted_items = operations.get_job_items(config.DB_PATH, job_id, photo_ids)
        expected_ids = {item["item_id"] for item in admitted_items}
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
        pinned_generation_id = str(
            (job.get("details") or {}).get("generation_id") or ""
        ).strip()
        job_was_pinned = bool(pinned_generation_id)
        if not pinned_generation_id:
            pinned_generation_id = policy_runtime.active_generation_id() or ""
        if not pinned_generation_id:
            return jsonify({"error": "no active learned-policy generation"}), 409
        operations.set_job_state(
            config.DB_PATH,
            job_id,
            "running",
            details={
                "generation_id": pinned_generation_id,
                "policy_algorithm_version": policy_runtime.POLICY_ALGORITHM_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "target_schema_version": TARGET_SCHEMA_VERSION,
            },
        )
        if not policy_runtime._load_generation_artifacts(pinned_generation_id):
            # Activation may have retired and pruned the generation between the
            # active-ID read and durable pin. Pin the now-active generation before
            # loading it; subsequent pruning will observe this job reference.
            replacement_generation_id = policy_runtime.active_generation_id() or ""
            if (
                not job_was_pinned
                and replacement_generation_id
                and replacement_generation_id != pinned_generation_id
            ):
                pinned_generation_id = replacement_generation_id
                operations.set_job_state(
                    config.DB_PATH,
                    job_id,
                    "running",
                    details={"generation_id": pinned_generation_id},
                )
            if (
                not pinned_generation_id
                or not policy_runtime._load_generation_artifacts(pinned_generation_id)
            ):
                return jsonify(
                    {
                        "error": (
                            "the operation's learned-policy generation is unavailable"
                        )
                    }
                ), 409
        options["generation_id"] = pinned_generation_id

    items_json = request.form.get("items_json")
    if items_json is not None:
        try:
            raw_items = json.loads(items_json)
        except (TypeError, json.JSONDecodeError):
            return jsonify(
                {
                    "results": None,
                    "error": "items_json must be valid JSON",
                    "warning": None,
                }
            ), 400
        maximum_batch = min(64, int(config.STYLEAI_INDEX_QUEUE_CAPACITY))
        if (
            not isinstance(raw_items, list)
            or not raw_items
            or len(raw_items) > maximum_batch
            or len(raw_items) != len(photo_ids)
        ):
            return jsonify(
                {
                    "results": None,
                    "error": (
                        "items_json must contain one object per photo and at most "
                        f"{maximum_batch} items"
                    ),
                    "warning": None,
                }
            ), 400
        if not job_id:
            return jsonify(
                {
                    "results": None,
                    "error": "versioned edit batches require an admitted edit operation",
                    "warning": None,
                }
            ), 400

        structured_items: list[dict[str, Any]] = []
        total_image_bytes = 0
        try:
            for index, (file, photo_id, raw_item) in enumerate(
                zip(images, photo_ids, raw_items, strict=True)
            ):
                if not isinstance(raw_item, dict):
                    raise ValueError(f"items_json[{index}] must be an object")
                item_photo_id = str(raw_item.get("photo_id") or "").strip()
                if item_photo_id != photo_id:
                    raise ValueError(
                        f"items_json[{index}].photo_id does not match multipart order"
                    )
                for mode_field in ("profile_mode", "hdr_mode"):
                    raw_mode = str(raw_item.get(mode_field, "suggest") or "suggest")
                    if raw_mode.strip().lower() not in valid_rendering_modes:
                        raise ValueError(
                            f"items_json[{index}].{mode_field} must be one of: "
                            + ", ".join(sorted(valid_rendering_modes))
                        )
                item_options = _extract_options(raw_item)
                item_options["generation_id"] = options.get("generation_id")
                image_bytes = file.read()
                total_image_bytes += len(image_bytes)
                if total_image_bytes > int(config.STYLEAI_METADATA_CACHE_BYTES):
                    raise ValueError(
                        "edit batch exceeds the in-flight image-byte limit"
                    )

                def item_float(key: str) -> float | None:
                    value = raw_item.get(key)
                    if value in (None, ""):
                        return None
                    parsed = float(value)
                    return parsed if math.isfinite(parsed) else None

                keywords = raw_item.get("user_keywords")
                if isinstance(keywords, str):
                    keywords = [
                        value.strip() for value in keywords.split(",") if value.strip()
                    ]
                elif isinstance(keywords, list):
                    keywords = [
                        str(value).strip() for value in keywords if str(value).strip()
                    ]
                else:
                    keywords = None
                structured_items.append(
                    {
                        "photo_id": photo_id,
                        "image_bytes": image_bytes,
                        "filename": file.filename or "",
                        "options": item_options,
                        "focal_length": item_options.get("focal_length"),
                        "capture_time": item_float("capture_time"),
                        "camera_make": item_options.get("camera_make"),
                        "camera_model": item_options.get("camera_model"),
                        "camera_profile": item_options.get("camera_profile"),
                        "lens": item_options.get("lens"),
                        "iso": item_options.get("iso"),
                        "aperture": item_options.get("aperture"),
                        "shutter_speed": item_options.get("shutter_speed"),
                        "is_hdr": item_options.get("is_hdr"),
                        "user_keywords": keywords,
                    }
                )
        except (TypeError, ValueError) as exc:
            return jsonify({"results": None, "error": str(exc), "warning": None}), 400

        operations.set_item_states(
            config.DB_PATH,
            job_id,
            [
                {"item_id": item["photo_id"], "state": "running"}
                for item in structured_items
            ],
        )
        results, diagnostics = _run_coherent_style_edit_batch(
            structured_items, job_id=job_id
        )
        item_updates = []
        for result in results:
            photo_id = str(result.get("photo_id") or "")
            canceled = result.get("error") == "canceled"
            succeeded = result.get("status") == "success"
            skipped = result.get("status") == "skipped"
            item_updates.append(
                {
                    "item_id": photo_id,
                    "state": (
                        "canceled"
                        if canceled
                        else ("committing" if succeeded or skipped else "failed")
                    ),
                    "error": (
                        None
                        if succeeded or skipped or canceled
                        else str(result.get("error") or "Edit failed")
                    ),
                    "result": {
                        "engine": result.get("engine"),
                        "confidence": result.get("confidence"),
                        "burst_coherence": result.get("burst_coherence"),
                        "outcome": "skipped" if skipped else result.get("status"),
                        "skip_reason": result.get("skip_reason"),
                    },
                }
            )
        operations.set_item_states(config.DB_PATH, job_id, item_updates)
        return jsonify(
            {
                "results": {
                    "status": "ok",
                    "contract_version": "style-edit-batch-v1",
                    "batch_size": len(results),
                    "results": results,
                    "diagnostics": diagnostics,
                },
                "error": None,
                "warning": None,
            }
        ), 200

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
    lens = _opt_str("lens")
    shutter_speed = _opt_str("shutter_speed")

    def _shared_float(key: str) -> float | None:
        try:
            value = request.form.get(key)
            parsed = float(value) if value not in (None, "") else None
            return parsed if parsed is not None and math.isfinite(parsed) else None
        except (TypeError, ValueError):
            return None

    iso = _shared_float("iso")
    aperture = _shared_float("aperture")
    raw_is_hdr = request.form.get("is_hdr")
    is_hdr = (
        str(raw_is_hdr).strip().lower() == "true" if raw_is_hdr is not None else None
    )

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
                lens=lens,
                iso=iso,
                aperture=aperture,
                shutter_speed=shutter_speed,
                is_hdr=is_hdr,
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
            skipped = result.get("status") == "skipped"
            operations.set_item_state(
                config.DB_PATH,
                job_id,
                photo_id,
                "canceled"
                if canceled
                else ("committing" if succeeded or skipped else "failed"),
                error=None
                if succeeded or skipped or canceled
                else str(result.get("error") or "Edit failed"),
                result={
                    "engine": result.get("engine"),
                    "confidence": result.get("confidence"),
                    "outcome": "skipped" if skipped else result.get("status"),
                    "skip_reason": result.get("skip_reason"),
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
