import io
import logging
import subprocess
import os
from PIL import Image

logger = logging.getLogger(__name__)


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
            timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            # Verify it's a valid image
            try:
                with Image.open(io.BytesIO(result.stdout)) as preview:
                    preview.verify()
                return result.stdout
            except Exception:
                pass
        elif result.returncode != 0:
            logger.warning(
                f"Exiftool returned non-zero exit code {result.returncode} for {filepath}"
            )
            if result.stderr:
                logger.debug(
                    f"Exiftool stderr: {result.stderr.decode('utf-8', errors='ignore')}"
                )
    except subprocess.TimeoutExpired as e:
        msg = f"Exiftool timed out after 10 seconds for {filepath}. Check if NAS is spinning up or macOS is prompting for access."
        logger.warning(msg)
        raise TimeoutError(msg) from e
    except FileNotFoundError:
        logger.error("Exiftool is not installed or not in PATH.")
    except Exception as e:
        logger.warning(f"Failed to extract exiftool preview for {filepath}: {e}")
    return None
