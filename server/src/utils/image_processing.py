import io
import logging
from PIL import Image, ImageFilter, ImageDraw

logger = logging.getLogger(__name__)


def apply_face_blur(
    image_bytes: bytes, face_bounding_boxes: list[list[float]]
) -> bytes:
    """
    Applies a heavy gaussian blur in an elliptical shape to the specified bounding boxes in the image.

    Args:
        image_bytes: The original JPEG image bytes.
        face_bounding_boxes: A list of bounding boxes, where each box is [x1, y1, x2, y2].

    Returns:
        The new JPEG image bytes with blurred faces.
    """
    if not face_bounding_boxes:
        return image_bytes

    try:
        image = Image.open(io.BytesIO(image_bytes))

        # Ensure we can draw/process it
        if image.mode != "RGB":
            image = image.convert("RGB")

        # For each face, crop the region, blur it, and paste it back using an elliptical mask
        for bbox in face_bounding_boxes:
            if len(bbox) >= 4:
                # InsightFace returns floats, round to ints and ensure within bounds
                x1 = max(0, int(bbox[0]))
                y1 = max(0, int(bbox[1]))
                x2 = min(image.width, int(bbox[2]))
                y2 = min(image.height, int(bbox[3]))

                if x2 > x1 and y2 > y1:
                    # Crop the face
                    face_region = image.crop((x1, y1, x2, y2))

                    # Calculate blur radius based on face size to ensure strong anonymization
                    face_width = x2 - x1
                    face_height = y2 - y1
                    radius = max(10, int(min(face_width, face_height) * 0.15))

                    # Apply a heavy Gaussian blur to the cropped region
                    blurred_face = face_region.filter(
                        ImageFilter.GaussianBlur(radius=radius)
                    )

                    # Create an elliptical mask
                    mask = Image.new("L", (face_width, face_height), 0)
                    draw = ImageDraw.Draw(mask)
                    draw.ellipse((0, 0, face_width, face_height), fill=255)

                    # Paste back onto the main image using the mask
                    image.paste(blurred_face, (x1, y1), mask)

        # Re-encode to bytes
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()

    except Exception as e:
        logger.error(f"Failed to blur faces: {e}", exc_info=True)
        # Fallback to the original image if blurring fails
        return image_bytes
import subprocess
import os

def extract_exiftool_preview(filepath: str) -> bytes | None:
    """Extracts the embedded preview JPEG from a raw file using exiftool."""
    if not filepath:
        return None
        
    if not os.path.exists(filepath):
        logger.warning(f"File does not exist: {filepath}")
        return None
        
    if not os.access(filepath, os.R_OK):
        msg = f"Permission denied accessing file (check macOS Privacy settings or NAS permissions): {filepath}"
        logger.warning(msg)
        raise PermissionError(msg)
        
    try:
        # Exiftool -b -PreviewImage outputs binary jpeg to stdout
        # Timeout helps with spinning rust NAS or macOS permission prompt hangs
        result = subprocess.run(
            ["exiftool", "-b", "-PreviewImage", filepath],
            capture_output=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout:
            # Verify it's a valid image
            try:
                Image.open(io.BytesIO(result.stdout)).verify()
                return result.stdout
            except Exception:
                pass
        elif result.returncode != 0:
            logger.warning(f"Exiftool returned non-zero exit code {result.returncode} for {filepath}")
            if result.stderr:
                logger.debug(f"Exiftool stderr: {result.stderr.decode('utf-8', errors='ignore')}")
    except subprocess.TimeoutExpired as e:
        msg = f"Exiftool timed out after 10 seconds for {filepath}. Check if NAS is spinning up or macOS is prompting for access."
        logger.warning(msg)
        raise TimeoutError(msg) from e
    except FileNotFoundError:
        logger.error("Exiftool is not installed or not in PATH.")
    except Exception as e:
        logger.warning(f"Failed to extract exiftool preview for {filepath}: {e}")
    return None
