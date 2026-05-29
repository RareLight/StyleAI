"""Flask blueprint for the Style Catalog REST API.

Endpoints
---------
GET  /styles                    → List all styles
GET  /styles/<id>               → Get style details + example photo_ids
POST /styles/discover           → Auto-discover from selected photo_ids
POST /styles/<id>/reset         → Remove one style
POST /styles/reset-all          → Global reset
POST /styles/migrate            → One-time legacy migration
GET  /styles/export             → JSON export
POST /styles/import             → JSON import (body: JSON payload)
GET  /styles/match              → Find matching styles for a photo
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from config import logger
from services import style_catalog as catalog_service

style_catalog_bp = Blueprint("style_catalog", __name__)


# ---------------------------------------------------------------------------
# GET /styles
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles", methods=["GET"])
def list_styles():
    try:
        styles = catalog_service.list_styles()
        return jsonify({"status": "ok", "styles": styles, "count": len(styles)}), 200
    except Exception as exc:
        logger.error("Failed to list styles: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /styles/<style_id>
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/<path:style_id>", methods=["GET"])
def get_style(style_id: str):
    try:
        style = catalog_service.get_style(style_id)
        if not style:
            return jsonify({"error": f"Style not found: {style_id}"}), 404
        return jsonify({"status": "ok", "style": style}), 200
    except Exception as exc:
        logger.error("Failed to get style %s: %s", style_id, exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /styles/discover
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/discover", methods=["POST"])
def discover_styles():
    """Request body: { "photo_ids": ["id1", "id2", ...] } (optional).

    If photo_ids is omitted, discovers from ALL training examples.
    """
    data = request.get_json() or {}
    photo_ids = data.get("photo_ids")
    try:
        styles = catalog_service.discover_styles_from_examples(photo_ids)
        return (
            jsonify(
                {
                    "status": "ok",
                    "styles_created": len(styles),
                    "styles": styles,
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error("Style discovery failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /styles/<style_id>/reset
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/<path:style_id>/reset", methods=["POST"])
def reset_style(style_id: str):
    try:
        deleted = catalog_service.reset_style(style_id)
        if deleted:
            return jsonify({"status": "ok", "style_id": style_id}), 200
        return jsonify({"error": f"Style not found: {style_id}"}), 404
    except Exception as exc:
        logger.error("Failed to reset style %s: %s", style_id, exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /styles/reset-all
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/reset-all", methods=["POST"])
def reset_all_styles():
    try:
        count = catalog_service.reset_all_styles()
        return jsonify({"status": "ok", "removed": count}), 200
    except Exception as exc:
        logger.error("Failed to reset all styles: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /styles/migrate
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/migrate", methods=["POST"])
def migrate_styles():
    try:
        result = catalog_service.migrate_legacy_training()
        return jsonify({"status": "ok", **result}), 200
    except Exception as exc:
        logger.error("Migration failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /styles/export
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/export", methods=["GET"])
def export_styles():
    try:
        data = catalog_service.export_styles_json()
        return jsonify(data), 200
    except Exception as exc:
        logger.error("Export failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST /styles/import
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/import", methods=["POST"])
def import_styles():
    data = request.get_json() or {}
    if not data or not data.get("styles"):
        return jsonify({"error": "Missing 'styles' array in body"}), 400
    merge = str(data.get("merge", "true")).lower() == "true"
    try:
        result = catalog_service.import_styles_json(data, merge=merge)
        return jsonify({"status": "ok", **result}), 200
    except Exception as exc:
        logger.error("Import failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /styles/match
# ---------------------------------------------------------------------------


@style_catalog_bp.route("/styles/match", methods=["GET"])
def match_styles():
    """Query params:
    camera_make, camera_model, primary_genre, user_keywords,
    lum_mean, contrast, warmth_proxy
    """
    camera_make = request.args.get("camera_make") or None
    camera_model = request.args.get("camera_model") or None
    scene_tags = []
    primary_genre = request.args.get("primary_genre")
    if primary_genre:
        scene_tags.append(primary_genre)

    user_keywords_raw = request.args.get("user_keywords")
    user_keywords = (
        [k.strip() for k in user_keywords_raw.split(",") if k.strip()]
        if user_keywords_raw
        else []
    )

    exposure_metrics: dict[str, float] = {}
    for key in ("lum_mean", "contrast", "warmth_proxy"):
        raw = request.args.get(key)
        if raw is not None:
            try:
                exposure_metrics[f"exp_{key}"] = float(raw)
            except (TypeError, ValueError):
                pass

    try:
        matches = catalog_service.find_matching_styles(
            camera_make=camera_make,
            camera_model=camera_model,
            scene_tags=scene_tags,
            exposure_metrics=exposure_metrics if exposure_metrics else None,
            user_keywords=user_keywords,
            top_k=3,
        )
        return (
            jsonify(
                {
                    "status": "ok",
                    "matches": [
                        {"style": style, "confidence": conf} for style, conf in matches
                    ],
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error("Style matching failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
