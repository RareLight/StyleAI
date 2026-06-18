"""
Face detection service using InsightFace.
Provides lightweight face detection for privacy blurring.
"""

from __future__ import annotations

import io
from typing import Any

import config
import numpy as np
from PIL import Image

from config import logger

# Lazy-loaded FaceAnalysis app
_face_app = None


def _get_face_app():
    """Lazy-load InsightFace FaceAnalysis (detection only)."""
    global _face_app
    if _face_app is not None:
        return _face_app
    try:
        from insightface.app import FaceAnalysis

        import sys

        providers = ["CPUExecutionProvider"]
        if sys.platform == "darwin":
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        elif sys.platform == "win32" or sys.platform == "linux":
            try:
                import torch

                if torch.cuda.is_available():
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            except ImportError:
                pass

        root = config.args.insightface_root
        _face_app = FaceAnalysis(
            name="buffalo_l",
            root=root,
            providers=providers,
            allowed_modules=["detection"],
        )
        _face_app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=0.25)
        logger.info(
            "InsightFace FaceAnalysis (detection-only) loaded with det_thresh=0.25."
        )
        return _face_app
    except Exception as e:
        logger.error(f"Failed to load InsightFace: {e}", exc_info=True)
        raise


def unload_face_app():
    """Unload the InsightFace model to free memory."""
    global _face_app
    if _face_app is None:
        return
    logger.info("Unloading InsightFace FaceAnalysis model...")
    _face_app = None
    import gc

    gc.collect()
    logger.info("Unloaded InsightFace model.")


def detect_faces(
    image_bytes: bytes,
    pil_image: "Image.Image | None" = None,
    min_det_score: float = 0.5,
) -> list[dict[str, Any]]:
    """
    Detect faces in an image for privacy blurring.

    Args:
        image_bytes: Raw image bytes (JPEG/PNG etc.)
        pil_image: Optional already-decoded RGB PIL.Image. When provided, the
            JPEG is not re-decoded here.
        min_det_score: Minimum confidence threshold to accept a detected face.

    Returns:
        List of dicts with keys:
        - bbox: [x1, y1, x2, y2]
        - det_score: detector confidence if available
    """
    app = _get_face_app()
    if pil_image is not None:
        source = pil_image
    else:
        img_temp = Image.open(io.BytesIO(image_bytes))
        img_temp.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        source = img_temp.convert("RGB")
    img = np.array(source)
    faces = app.get(img)

    # Filter by confidence threshold
    faces = [f for f in faces if getattr(f, "det_score", 0.0) >= min_det_score]

    results = []
    for face in faces:
        bbox = getattr(face, "bbox", None)
        if bbox is not None and len(bbox) >= 4:
            x1, y1, x2, y2 = [int(round(x)) for x in bbox[:4]]
            h, w = img.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 > x1 and y2 > y1:
                results.append(
                    {
                        "bbox": [x1, y1, x2, y2],
                        "det_score": float(getattr(face, "det_score", 0.0) or 0.0),
                    }
                )

    return results
