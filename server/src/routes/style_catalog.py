"""Editing-policy v2 catalog and upgrade recommendation routes."""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import operations, policy_runtime


style_catalog_bp = Blueprint("style_catalog", __name__)


def _run_upgrade_recommendation_job(job_id: str, limit: int) -> None:
    cancel_signal = operations.JobCancelSignal(config.DB_PATH, job_id)
    with operations.workflow_maintenance_gate.workflow():
        try:
            if cancel_signal.is_set():
                operations.set_job_state(config.DB_PATH, job_id, "canceled")
                return
            operations.set_job_state(config.DB_PATH, job_id, "running")
            styles = policy_runtime.get_upgrade_recommendations(
                top_policies_limit=limit,
                cancel_event=cancel_signal,
            )
            if cancel_signal.is_set():
                operations.set_job_state(config.DB_PATH, job_id, "canceled")
            else:
                operations.set_job_state(
                    config.DB_PATH,
                    job_id,
                    "succeeded",
                    details={"styles": styles, "count": len(styles)},
                )
        except InterruptedError:
            operations.set_job_state(config.DB_PATH, job_id, "canceled")
        except Exception as exc:
            logger.error(
                "Upgrade recommendation job %s failed: %s",
                job_id,
                exc,
                exc_info=True,
            )
            operations.set_job_state(config.DB_PATH, job_id, "failed", error=str(exc))


def _start_upgrade_recommendation_job(job_id: str, limit: int) -> None:
    threading.Thread(
        target=_run_upgrade_recommendation_job,
        args=(job_id, limit),
        name=f"UpgradeRecommendations-{job_id[:8]}",
        daemon=True,
    ).start()


@style_catalog_bp.route("/styles", methods=["GET"])
def list_styles():
    try:
        styles = policy_runtime.list_active_policies()
        return jsonify({"status": "ok", "styles": styles, "count": len(styles)}), 200
    except Exception as exc:
        logger.error("Failed to list editing policies: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@style_catalog_bp.route("/styles/upgrades/recommendations", methods=["GET", "POST"])
def get_upgrade_recommendations():
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        requested_job_id = str(request.args.get("job_id") or "").strip()
        if requested_job_id:
            job = operations.get_job(
                config.DB_PATH, requested_job_id, include_items=False
            )
            if job is None or job["kind"] != "recommendations":
                return jsonify(
                    {
                        "results": None,
                        "error": "Recommendation job was not found",
                        "warning": None,
                    }
                ), 404
            if job["state"] == "succeeded":
                details = job.get("details") or {}
                styles = details.get("styles") or []
                return jsonify(
                    {
                        "results": {
                            "job_id": requested_job_id,
                            "state": "succeeded",
                            "styles": styles,
                            "count": int(details.get("count") or len(styles)),
                        },
                        "error": None,
                        "warning": None,
                    }
                ), 200
            if job["state"] in operations.TERMINAL_STATES:
                return jsonify(
                    {
                        "results": {
                            "job_id": requested_job_id,
                            "state": job["state"],
                        },
                        "error": job.get("error")
                        or f"Recommendation job {job['state']}",
                        "warning": None,
                    }
                ), 409
            return jsonify(
                {
                    "results": {
                        "job_id": requested_job_id,
                        "state": job["state"],
                    },
                    "error": None,
                    "warning": None,
                }
            ), 202

        limit = request.args.get("limit", 100, type=int)
        if request.is_json and request.json:
            limit = request.json.get("limit", limit)
        limit = max(1, min(100, int(limit)))
        job, created = operations.create_job(
            config.DB_PATH,
            kind="recommendations",
            request_fingerprint=f"upgrade-recommendations:{limit}",
            priority=5,
            details={"limit": limit},
            coalesce=True,
        )
        if created:
            _start_upgrade_recommendation_job(job["job_id"], limit)
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
    except Exception as exc:
        logger.error(
            "Failed to get policy upgrade recommendations: %s",
            exc,
            exc_info=True,
        )
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@style_catalog_bp.route("/styles/upgrades/feedback", methods=["POST"])
def record_upgrade_feedback():
    try:
        data = request.get_json(silent=True) or {}
        review_id = str(data.get("review_id") or "").strip()
        policy_id = str(data.get("policy_id") or "").strip()
        raw_labels = data.get("labels")
        if not review_id or not policy_id or not isinstance(raw_labels, list):
            return jsonify(
                {
                    "results": None,
                    "error": "review_id, policy_id, and labels are required",
                    "warning": None,
                }
            ), 400
        labels = []
        for item in raw_labels:
            if not isinstance(item, dict):
                raise ValueError("every feedback label must be an object")
            labels.append(
                {
                    "photo_id": str(
                        item.get("photo_id") or item.get("globalPhotoId") or ""
                    ).strip(),
                    "policy_match": item.get("policy_match"),
                    "useful": item.get("useful"),
                }
            )
        result = policy_runtime.record_upgrade_feedback(
            review_id=review_id,
            policy_id=policy_id,
            labels=labels,
        )
        return jsonify({"results": result, "error": None, "warning": None}), 200
    except (ValueError, LookupError) as exc:
        return jsonify({"results": None, "error": str(exc), "warning": None}), 400
    except Exception as exc:
        logger.error(
            "Failed to record policy upgrade feedback: %s",
            exc,
            exc_info=True,
        )
        return jsonify({"results": None, "error": str(exc), "warning": None}), 500


@style_catalog_bp.route("/styles/<path:policy_id>", methods=["GET"])
def get_style(policy_id: str):
    try:
        policy = policy_runtime.get_active_policy(policy_id)
        if not policy:
            return jsonify({"error": f"Editing policy not found: {policy_id}"}), 404
        return jsonify({"status": "ok", "style": policy}), 200
    except Exception as exc:
        logger.error(
            "Failed to get editing policy %s: %s",
            policy_id,
            exc,
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500


@style_catalog_bp.route("/styles/discover", methods=["POST"])
def discover_styles():
    """Queue discovery so a Lightroom HTTP request never owns model fitting."""
    data = request.get_json() or {}
    photo_ids = data.get("photo_ids")
    try:
        if photo_ids:
            logger.info(
                "Policy discovery rebuilds the complete training generation; "
                "the supplied %d photo IDs are advisory.",
                len(photo_ids),
            )
        return jsonify(
            {
                "status": "accepted",
                "discovery": policy_runtime.request_rebuild(),
            }
        ), 202
    except Exception as exc:
        logger.error("Editing-policy discovery failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@style_catalog_bp.route("/styles/discover/status", methods=["GET"])
def discovery_status():
    return jsonify(
        {
            "status": "ok",
            "discovery": policy_runtime.discovery_status(),
        }
    ), 200


@style_catalog_bp.route("/styles/reset-all", methods=["POST"])
def reset_all_styles():
    try:
        with operations.admission.acquire(
            {"maintenance": 1, "catalog_write": 1}, priority=20
        ):
            count = policy_runtime.reset_policy_state()
        return jsonify({"status": "ok", "removed": count}), 200
    except Exception as exc:
        logger.error("Failed to reset editing policies: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@style_catalog_bp.route("/styles/all_examples", methods=["GET"])
def get_all_examples():
    try:
        policies = policy_runtime.list_active_policies_with_examples()
        return jsonify({"status": "ok", "styles": policies}), 200
    except Exception as exc:
        logger.error(
            "Failed to get policies with examples: %s",
            exc,
            exc_info=True,
        )
        return jsonify({"error": str(exc)}), 500
