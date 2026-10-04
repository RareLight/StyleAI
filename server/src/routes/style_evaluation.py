"""Durable developer-facing applied-edit quality evaluation routes."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import threading

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import applied_edit_reporting, operations


style_evaluation_bp = Blueprint("style_evaluation", __name__)


def _request_options(data: dict) -> dict:
    created_after = data.get("created_after")
    if created_after is not None:
        created_after = str(created_after).strip() or None
        if created_after is not None:
            try:
                datetime.fromisoformat(created_after.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("created_after must be an ISO-8601 timestamp") from exc
    confidence_bin_count = data.get(
        "confidence_bin_count",
        applied_edit_reporting.DEFAULT_CONFIDENCE_BIN_COUNT,
    )
    minimum_reviewed = data.get(
        "minimum_reviewed_per_generation",
        applied_edit_reporting.DEFAULT_MINIMUM_REVIEWED_PER_GENERATION,
    )
    if not isinstance(confidence_bin_count, int) or isinstance(
        confidence_bin_count, bool
    ):
        raise ValueError("confidence_bin_count must be an integer")
    if not isinstance(minimum_reviewed, int) or isinstance(minimum_reviewed, bool):
        raise ValueError("minimum_reviewed_per_generation must be an integer")
    if confidence_bin_count < 2 or confidence_bin_count > 20:
        raise ValueError("confidence_bin_count must be between 2 and 20")
    if minimum_reviewed < 1:
        raise ValueError("minimum_reviewed_per_generation must be positive")
    return {
        "created_after": created_after,
        "confidence_bin_count": confidence_bin_count,
        "minimum_reviewed_per_generation": minimum_reviewed,
    }


def _run_evaluation_job(job_id: str, options: dict) -> None:
    cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)
    try:
        operations.set_job_state(config.DB_PATH, job_id, "running")
        with operations.workflow_maintenance_gate.workflow(cancel_event=cancel_signal):
            with operations.admission.acquire(
                {"cpu_prepare": 1},
                priority=3,
                cancel_event=cancel_signal,
            ):
                result = applied_edit_reporting.generate_applied_edit_report(
                    db_path=config.DB_PATH,
                    cancel_check=cancel_signal.is_set,
                    **options,
                )
        if cancel_signal.is_set():
            operations.set_job_state(config.DB_PATH, job_id, "canceled")
            return
        operations.set_job_state(
            config.DB_PATH,
            job_id,
            "succeeded",
            details={
                "report_path": result["report_path"],
                "summary": result["summary"],
            },
        )
    except InterruptedError:
        operations.set_job_state(config.DB_PATH, job_id, "canceled")
    except Exception as exc:
        logger.error(
            "Applied-edit evaluation job %s failed: %s",
            job_id,
            exc,
            exc_info=True,
        )
        operations.set_job_state(config.DB_PATH, job_id, "failed", error=str(exc))


def _start_evaluation_job(job_id: str, options: dict) -> None:
    threading.Thread(
        target=_run_evaluation_job,
        args=(job_id, options),
        name=f"AppliedEditEvaluation-{job_id[:8]}",
        daemon=True,
    ).start()


@style_evaluation_bp.route("/style_edit/evaluation", methods=["GET", "POST"])
def applied_edit_evaluation():
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        job_id = str(request.args.get("job_id") or "").strip()
        if job_id:
            job = operations.get_job(config.DB_PATH, job_id, include_items=False)
            if job is None or job["kind"] != "applied_edit_evaluation":
                return jsonify(
                    {
                        "results": None,
                        "error": "Applied-edit evaluation job was not found",
                        "warning": None,
                    }
                ), 404
            details = job.get("details") or {}
            result = {"job_id": job_id, "state": job["state"]}
            if job["state"] == "succeeded":
                result.update(
                    {
                        "report_path": details.get("report_path"),
                        "summary": details.get("summary") or {},
                    }
                )
                return jsonify({"results": result, "error": None, "warning": None}), 200
            if job["state"] in operations.TERMINAL_STATES:
                return jsonify(
                    {
                        "results": result,
                        "error": job.get("error") or f"Evaluation job {job['state']}",
                        "warning": None,
                    }
                ), 409
            return jsonify({"results": result, "error": None, "warning": None}), 202

        if request.method != "POST":
            raise ValueError("job_id is required when polling an evaluation")
        data = request.get_json(silent=True) or {}
        options = _request_options(data)
        encoded = json.dumps(options, sort_keys=True, separators=(",", ":"))
        fingerprint = (
            "applied-edit-evaluation:"
            + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        )
        job, created = operations.create_job(
            config.DB_PATH,
            kind="applied_edit_evaluation",
            request_fingerprint=fingerprint,
            priority=3,
            details={"options": options},
            coalesce=True,
        )
        if created:
            _start_evaluation_job(job["job_id"], options)
        return jsonify(
            {
                "results": {
                    "job_id": job["job_id"],
                    "state": job["state"],
                    "attached": not created,
                },
                "error": None,
                "warning": None,
            }
        ), 202 if created else 200
    except ValueError as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error("Failed to evaluate applied edits: %s", exc, exc_info=True)
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500
