import base64

from flask import Blueprint, jsonify, request

from config import logger
from services.metadata import get_analysis_service
from utils.edit_persistence import _persist_edit_recipe, _success_payload
from utils.request_parsing import _extract_options, _extract_photo_ids


edit_bp = Blueprint("edit", __name__)


@edit_bp.route("/edit", methods=["POST"])
def generate_edit_recipe():
    logger.info("Edit recipe request received")
    images = request.files.getlist("image")
    photo_ids = _extract_photo_ids(request.form)
    options = _extract_options(request.form)

    if not images or not photo_ids or len(images) != len(photo_ids):
        return jsonify(
            {
                "error": "Mismatch between number of images and photo IDs, or no images provided"
            }
        ), 400
    if len(images) != 1:
        return jsonify(
            {
                "error": "The /edit endpoint currently supports exactly one photo per request"
            }
        ), 400

    file = images[0]
    photo_id = photo_ids[0]
    if not file or not photo_id:
        return jsonify({"error": "Missing file or photo_id"}), 400

    analysis_service = get_analysis_service()
    response = analysis_service.generate_edit_recipe_single(
        photo_id, file.read(), options
    )
    if not response.success or not response.recipe:
        return jsonify(
            {"status": "error", "error": response.error or "Edit generation failed"}
        ), 500

    _persist_edit_recipe(photo_id, file.filename, response.recipe, options)
    payload = _success_payload(
        photo_id, response.recipe, options, warning=response.warning
    )
    payload["input_tokens"] = response.input_tokens
    payload["output_tokens"] = response.output_tokens
    return jsonify(payload), 200


@edit_bp.route("/edit_base64", methods=["POST"])
def generate_edit_recipe_base64():
    logger.info("Edit recipe base64 request received")
    data = request.get_json() or {}
    image_b64 = data.get("image")
    photo_id = data.get("photo_id") or data.get("uuid")
    filename = data.get("filename")

    if not image_b64 or not photo_id or not filename:
        return jsonify(
            {"error": "Missing required fields: image, photo_id, filename"}
        ), 400

    options = _extract_options(data)
    analysis_service = get_analysis_service()
    response = analysis_service.generate_edit_recipe_single(
        photo_id, base64.b64decode(image_b64.encode("ascii")), options
    )
    if not response.success or not response.recipe:
        return jsonify(
            {"status": "error", "error": response.error or "Edit generation failed"}
        ), 500

    _persist_edit_recipe(photo_id, filename, response.recipe, options)
    payload = _success_payload(
        photo_id, response.recipe, options, warning=response.warning
    )
    payload["input_tokens"] = response.input_tokens
    payload["output_tokens"] = response.output_tokens
    return jsonify(payload), 200
