import base64

from flask import Blueprint, jsonify, request

from config import logger
from services.metadata import get_analysis_service
from utils.edit_persistence import _persist_edit_recipe, _success_payload
from utils.request_parsing import _extract_options, _extract_photo_ids
from utils.image_processing import apply_face_blur
from services import face as face_service


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

    image_bytes = file.read()

    # Check for HDR brackets
    file_dark = request.files.get("image_dark")
    file_bright = request.files.get("image_bright")

    image_dark_bytes = file_dark.read() if file_dark else None
    image_bright_bytes = file_bright.read() if file_bright else None

    if image_dark_bytes and image_bright_bytes:
        image_data = [image_dark_bytes, image_bytes, image_bright_bytes]
    else:
        image_data = image_bytes

    if options.get("blurFacesForCloud"):
        try:
            sensitivity = options.get("faceBlurSensitivity", "balanced").lower()
            min_det_score = 0.5
            if sensitivity == "high":
                min_det_score = 0.3
            elif sensitivity == "low":
                min_det_score = 0.7
            faces = face_service.detect_faces(image_bytes, min_det_score=min_det_score)
            if faces:
                bboxes = [f["bbox"] for f in faces]
                image_bytes = apply_face_blur(image_bytes, bboxes)
                # Apply blur to brackets as well
                if isinstance(image_data, list):
                    image_data = [apply_face_blur(img, bboxes) for img in image_data]
                else:
                    image_data = image_bytes
            options["blur_faces"] = True
        except Exception as e:
            logger.error(f"Failed to blur faces in edit route: {e}")

    # Leave audit trail for any images sent to LLM
    if str(options.get("audit_llm_inputs", "")).lower() == "true":
        from services.audit import log_diagnostic_image
        import base64

        brackets_dict = None
        if isinstance(image_data, list):
            brackets_dict = {
                "dark": base64.b64encode(image_data[0]).decode("ascii"),
                "bright": base64.b64encode(image_data[2]).decode("ascii"),
            }
        log_diagnostic_image(
            image_data[1] if isinstance(image_data, list) else image_data,
            "aiedit",
            file.filename,
            brackets_dict,
            output_dir=options.get("audit_llm_inputs_path"),
        )

    analysis_service = get_analysis_service()
    response = analysis_service.generate_edit_recipe_single(
        photo_id, image_data, options
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

    image_bytes = base64.b64decode(image_b64.encode("ascii"))
    options = _extract_options(data)

    if options.get("blurFacesForCloud"):
        try:
            sensitivity = options.get("faceBlurSensitivity", "balanced").lower()
            min_det_score = 0.5
            if sensitivity == "high":
                min_det_score = 0.3
            elif sensitivity == "low":
                min_det_score = 0.7
            faces = face_service.detect_faces(image_bytes, min_det_score=min_det_score)
            if faces:
                bboxes = [f["bbox"] for f in faces]
                image_bytes = apply_face_blur(image_bytes, bboxes)
            options["blur_faces"] = True
        except Exception as e:
            logger.error(f"Failed to blur faces in edit_base64 route: {e}")

    analysis_service = get_analysis_service()
    response = analysis_service.generate_edit_recipe_single(
        photo_id, image_bytes, options
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
