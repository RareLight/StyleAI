import os
import glob
from datetime import datetime
import base64

AUDIT_DIR = os.path.expanduser("~/TempSSD/logs")

def log_diagnostic_image(image_bytes: bytes, process_name: str, original_filename: str, brackets: dict = None, output_dir: str = None):
    """
    Temporary debugging feature: keep last 50 processed images per process.
    image_bytes: the main image payload.
    process_name: string like 'aiedit', 'indexing' to distinguish the pipeline.
    original_filename: name of the source image.
    brackets: optional dict mapping suffix to base64 string, e.g. {'base': '...', 'dark': '...'}
    output_dir: custom directory to save audit logs. If None, falls back to ~/TempSSD/logs.
    """
    if not output_dir:
        output_dir = AUDIT_DIR
        
    try:
        audit_path = os.path.expanduser(output_dir)
        os.makedirs(audit_path, exist_ok=True)
        
        # We find files containing process_name to prune them per-process
        all_files = glob.glob(os.path.join(audit_path, f"*_{process_name}_original.*"))
        all_files.sort(key=os.path.getmtime, reverse=True)
        
        # Prune older than 49 (to keep 50 max)
        for old_file in all_files[49:]:
            prefix = old_file.rsplit("_original.", 1)[0]
            # Prune everything matching prefix
            for f in glob.glob(prefix + "*"):
                try:
                    os.remove(f)
                except OSError:
                    pass

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_name = os.path.splitext(os.path.basename(original_filename))[0]
        prefix = os.path.join(audit_path, f"{timestamp}_{process_name}_{safe_name}")
        
        ext = ".tif" if original_filename.lower().endswith((".tif", ".tiff")) else ".jpg"
        with open(f"{prefix}_original{ext}", "wb") as f:
            f.write(image_bytes)
            f.flush()
            os.fsync(f.fileno())
            
        if brackets:
            for suffix, b64_str in brackets.items():
                if b64_str:
                    with open(f"{prefix}_{suffix}.jpg", "wb") as f:
                        f.write(base64.b64decode(b64_str))
                        f.flush()
                        os.fsync(f.fileno())
                        
    except Exception as e:
        import logging
        logging.getLogger("styleai-server").error(f"Failed to write audit trail: {e}")
