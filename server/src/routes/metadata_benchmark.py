"""Developer-only, non-persisting local metadata benchmark endpoint."""

from __future__ import annotations

import base64
import time

from flask import Blueprint, jsonify, request

import config
from config import STYLEAI_METADATA_CACHE_BYTES, logger
from services import operations
from services.metadata_benchmark import round_benchmark_timings, run_benchmark_batch
from utils.request_parsing import _extract_options


metadata_benchmark_bp = Blueprint("metadata_benchmark", __name__)
MAX_BENCHMARK_BATCH = 12


def _error(message: str, status: int):
    return jsonify({"results": None, "error": message, "warning": None}), status


@metadata_benchmark_bp.route("/metadata_benchmark/run_batch", methods=["POST"])
def run_metadata_benchmark_batch():
    """Run one provider/model over bounded inline proxies without persistence."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("request body must be a JSON object", 400)

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return _error("tasks must be a non-empty array", 400)
    if len(tasks) > MAX_BENCHMARK_BATCH:
        return _error(
            f"metadata benchmark batches are limited to {MAX_BENCHMARK_BATCH} photos",
            413,
        )
    if not all(isinstance(task, dict) for task in tasks):
        return _error("every benchmark task must be an object", 400)

    raw_options = data.get("options")
    if not isinstance(raw_options, dict):
        return _error("options must be an object", 400)
    options = _extract_options(raw_options)
    provider = str(options.get("provider") or "").strip()
    model = str(options.get("model") or "").strip()
    if not provider or not model:
        return _error("provider and model are required", 400)
    draft_model = str(options.get("draft_model") or "").strip() or None
    benchmark_variant = str(options.get("benchmark_variant") or "baseline").strip()
    if draft_model and provider != "lmstudio":
        return _error("draft_model is supported only for LM Studio benchmarks", 400)
    if benchmark_variant not in {"baseline", "speculative"}:
        return _error("benchmark_variant must be baseline or speculative", 400)
    if benchmark_variant == "speculative" and not draft_model:
        return _error("speculative benchmark variants require draft_model", 400)
    if benchmark_variant == "baseline" and draft_model:
        return _error("baseline benchmark variants cannot specify draft_model", 400)
    if not any(
        options.get(field)
        for field in (
            "generate_keywords",
            "generate_caption",
            "generate_title",
            "generate_alt_text",
        )
    ):
        return _error("at least one metadata output must be selected", 400)

    job_id = str(data.get("job_id") or "").strip() or None
    cancel_signal = None
    expected_item_ids: set[str] | None = None
    if job_id:
        if not config.DB_PATH:
            return _error("StyleAI database path is not configured", 500)
        job = operations.get_job(config.DB_PATH, job_id, include_items=False)
        if job is None:
            return _error(f"operation job not found: {job_id}", 404)
        if job["kind"] != "metadata_benchmark":
            return _error("operation job is not a metadata benchmark", 409)
        if job["state"] in operations.TERMINAL_STATES or job["cancel_requested"]:
            return _error("metadata benchmark operation is no longer active", 409)
        cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)
        requested_item_ids = [
            str(task.get("operation_item_id") or "").strip() for task in tasks
        ]
        if any(not item_id for item_id in requested_item_ids):
            return _error(
                "operation_item_id is required for operation-backed benchmarks", 400
            )
        expected_item_ids = {
            item["item_id"]
            for item in operations.get_job_items(
                config.DB_PATH, job_id, requested_item_ids
            )
        }
        if set(requested_item_ids) != expected_item_ids:
            return _error("benchmark batch contains unowned operation items", 400)

    decoded_items = []
    seen_photo_ids: set[str] = set()
    total_image_bytes = 0
    for task in tasks:
        photo_id = str(task.get("photo_id") or task.get("uuid") or "").strip()
        if not photo_id:
            return _error("every benchmark task requires photo_id", 400)
        if photo_id in seen_photo_ids:
            return _error("duplicate photo_id values are not allowed", 400)
        seen_photo_ids.add(photo_id)
        image_payload = task.get("image")
        if not isinstance(image_payload, str) or not image_payload:
            return _error(f"benchmark image is required for {photo_id}", 400)
        decode_started = time.perf_counter()
        try:
            image_data = base64.b64decode(image_payload.encode("ascii"), validate=True)
        except Exception:
            return _error(f"benchmark image is not valid base64 for {photo_id}", 400)
        if not image_data:
            return _error(f"benchmark image is empty for {photo_id}", 400)
        total_image_bytes += len(image_data)
        byte_limit = min(
            STYLEAI_METADATA_CACHE_BYTES,
            operations.admission.capacities["image_bytes"],
        )
        if total_image_bytes > byte_limit:
            return _error("benchmark image batch exceeds the byte budget", 413)
        raw_task_options = task.get("options") or {}
        if not isinstance(raw_task_options, dict):
            return _error(f"benchmark options must be an object for {photo_id}", 400)
        try:
            model_index = int(task.get("model_index") or 0)
            photo_index = int(task.get("photo_index") or 0)
        except (TypeError, ValueError):
            return _error("benchmark progress indexes must be integers", 400)
        if job_id and (model_index < 1 or photo_index < 1):
            return _error(
                "model_index and photo_index are required for operation-backed benchmarks",
                400,
            )
        task_options = _extract_options({**raw_options, **raw_task_options})
        # Per-photo context may vary, but the compared provider/model must stay
        # fixed for every item in this batch.
        task_options["provider"] = provider
        task_options["model"] = model
        location_data = raw_task_options.get("location_data")
        if isinstance(location_data, dict):
            task_options["location_data"] = {
                str(key): value
                for key, value in location_data.items()
                if isinstance(value, (str, int, float)) and not isinstance(value, bool)
            }
        decoded_items.append(
            {
                "photo_id": photo_id,
                "source_photo_id": str(task.get("source_photo_id") or photo_id),
                "filename": str(task.get("filename") or "photo.jpg"),
                "operation_item_id": str(task.get("operation_item_id") or ""),
                "model_index": model_index,
                "photo_index": photo_index,
                "image_data": image_data,
                "decode_ms": round((time.perf_counter() - decode_started) * 1000.0, 3),
                "options": task_options,
            }
        )

    operations.refresh_system_pressure()
    claim = {
        "accelerator": 1,
        "llm": 1,
        "cpu_prepare": min(
            len(decoded_items), operations.admission.capacities["cpu_prepare"]
        ),
        "image_bytes": total_image_bytes,
    }
    admission_started = time.perf_counter()
    try:
        with operations.admission.acquire(
            claim, priority=5, cancel_event=cancel_signal
        ):
            admission_wait_ms = round(
                (time.perf_counter() - admission_started) * 1000.0, 3
            )
            if job_id:
                operations.set_job_state(config.DB_PATH, job_id, "running")

            def publish_item_progress(
                item: dict, _batch_index: int, _batch_total: int
            ) -> None:
                if not job_id:
                    return
                operations.set_job_state(
                    config.DB_PATH,
                    job_id,
                    "running",
                    details={
                        "current_model_index": item["model_index"],
                        "current_photo_index": item["photo_index"],
                    },
                )
                operations.set_item_state(
                    config.DB_PATH,
                    job_id,
                    item["operation_item_id"],
                    "running",
                )

            results = run_benchmark_batch(
                decoded_items,
                options,
                cancel_signal=cancel_signal,
                on_item_started=publish_item_progress,
            )
    except InterruptedError:
        return _error("metadata benchmark operation has been canceled", 409)
    except Exception as exc:
        logger.error("Metadata benchmark batch failed", exc_info=True)
        return _error(str(exc), 500)

    for result in results:
        result["timing"]["admission_wait_ms"] = admission_wait_ms
        result["timing"] = round_benchmark_timings(result["timing"])

    if job_id:
        by_photo_id = {result["photo_id"]: result for result in results}
        updates = []
        for item in decoded_items:
            result = by_photo_id[item["photo_id"]]
            succeeded = result["status"] == "succeeded"
            updates.append(
                {
                    "item_id": item["operation_item_id"],
                    "state": "succeeded" if succeeded else "failed",
                    "error": None if succeeded else result.get("error"),
                }
            )
        operations.set_item_states(config.DB_PATH, job_id, updates)

    success_count = sum(result["status"] == "succeeded" for result in results)
    return jsonify(
        {
            "results": {
                "status": "processed",
                "provider": provider,
                "model": model,
                "success_count": success_count,
                "failure_count": len(results) - success_count,
                "items": results,
            },
            "error": None,
            "warning": None,
        }
    )
