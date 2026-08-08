"""Editing-policy v2 catalog and upgrade recommendation routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from config import logger
from services import operations, policy_runtime


style_catalog_bp = Blueprint("style_catalog", __name__)


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
        limit = request.args.get("limit", 100, type=int)
        if request.is_json and request.json:
            limit = request.json.get("limit", limit)
        results = policy_runtime.get_upgrade_recommendations(top_policies_limit=limit)
        return jsonify(
            {"status": "ok", "results": results, "error": None, "warning": None}
        ), 200
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
