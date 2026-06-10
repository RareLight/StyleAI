from flask import Blueprint, request, jsonify
import logging
import base64
import cv2
import numpy as np

logger = logging.getLogger(__name__)

bp = Blueprint('image_processing', __name__)

def prophoto_to_linear(img_16bit):
    """Convert 16-bit ProPhoto RGB to linear float."""
    img_float = img_16bit.astype(np.float32) / 65535.0
    return np.where(img_float < 0.03125, img_float / 16.0, img_float ** 1.8)

def linear_to_srgb_8bit(img_linear):
    """Convert linear float to 8-bit sRGB."""
    img = np.clip(img_linear, 0.0, 1.0)
    img = np.where(img <= 0.0031308, img * 12.92, 1.055 * (img ** (1.0 / 2.4)) - 0.055)
    return (img * 255.0).astype(np.uint8)

def srgb_to_linear(img_8bit):
    """Convert 8-bit sRGB to linear float."""
    img_float = img_8bit.astype(np.float32) / 255.0
    return np.where(img_float <= 0.04045, img_float / 12.92, ((img_float + 0.055) / 1.055) ** 2.4)

def apply_ev_shift(img_linear, ev_shift):
    """Apply an EV shift to linear float data and return 8-bit sRGB JPEG bytes."""
    shifted = img_linear * (2.0 ** ev_shift)
    srgb = linear_to_srgb_8bit(shifted)
    
    # OpenCV uses BGR internally for encoding, so if we read it as BGR, 
    # the transformations apply equally to channels, so we can just encode directly.
    success, buffer = cv2.imencode('.jpg', srgb)
    if not success:
        raise ValueError("Failed to encode image to JPEG")
    return base64.b64encode(buffer).decode('utf-8')

from services.audit import log_diagnostic_image

@bp.route('/generate_brackets', methods=['POST'])
def generate_brackets():
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided', 'results': None, 'warning': None}), 400

    file = request.files['image']
    file_bytes_raw = file.read()
    file_bytes = np.frombuffer(file_bytes_raw, np.uint8)
    
    # Attempt to read as unchanged (which preserves 16-bit TIFF depth if present)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_UNCHANGED)
    
    if img is None:
        # Save original file so we can inspect why OpenCV failed
        audit_llm_inputs = str(request.form.get("audit_llm_inputs", "")).lower() == "true"
        audit_path = request.form.get("audit_llm_inputs_path")
        if audit_llm_inputs:
            log_diagnostic_image(file_bytes_raw, 'aiedit', file.filename, output_dir=audit_path)
        return jsonify({'error': 'Failed to decode image', 'results': None, 'warning': None}), 400

    try:
        if img.dtype == np.uint16:
            # It's a 16-bit TIFF in ProPhoto RGB
            logger.info("Processing 16-bit TIFF for bracketing")
            linear = prophoto_to_linear(img)
            
            base_b64 = apply_ev_shift(linear, 0.0)
            dark_b64 = apply_ev_shift(linear, -2.0)
            bright_b64 = apply_ev_shift(linear, 2.0)
        else:
            # It's an 8-bit JPEG in sRGB
            logger.info("Processing 8-bit JPEG for bracketing")
            linear = srgb_to_linear(img)
            
            base_b64 = apply_ev_shift(linear, 0.0)
            dark_b64 = apply_ev_shift(linear, -2.0)
            bright_b64 = apply_ev_shift(linear, 2.0)
        
        # Leave audit trail for debugging if enabled
        audit_llm_inputs = str(request.form.get("audit_llm_inputs", "")).lower() == "true"
        audit_path = request.form.get("audit_llm_inputs_path")
        
        if audit_llm_inputs:
            log_diagnostic_image(file_bytes_raw, 'aiedit', file.filename, {
                'base': base_b64,
                'dark': dark_b64,
                'bright': bright_b64
            }, output_dir=audit_path)
            
        results = {
            'base': base_b64,
            'dark': dark_b64,
            'bright': bright_b64
        }
        
        return jsonify({'results': results, 'error': None, 'warning': None}), 200

    except Exception as e:
        logger.error(f"Error generating brackets: {e}", exc_info=True)
        return jsonify({'error': str(e), 'results': None, 'warning': None}), 500
