import base64
from datetime import datetime
import glob
import os
from pathlib import Path
import sys

from config import logger


MAX_CAPTURE_GROUPS = 50
MAX_CAPTURE_BYTES = 512 * 1024 * 1024


def get_default_audit_dir() -> Path:
    """Return a platform-neutral, local StyleAI diagnostic-data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StyleAI" / "debug"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base / "StyleAI" / "debug"
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "StyleAI" / "debug"


def resolve_audit_dir(output_dir: str | None = None) -> Path:
    """Resolve and validate a diagnostic capture directory without creating it."""
    candidate = Path(output_dir).expanduser() if output_dir else get_default_audit_dir()
    candidate = candidate.resolve(strict=False)
    if candidate == Path(candidate.anchor) or candidate == Path.home().resolve():
        raise ValueError(
            "Diagnostic capture directory must be a dedicated subdirectory"
        )
    return candidate


def _capture_prefix(original_file: Path) -> str:
    marker = "_original."
    return (
        original_file.name.split(marker, 1)[0]
        if marker in original_file.name
        else original_file.stem
    )


def _prune_capture_groups(audit_path: Path) -> None:
    originals = sorted(
        audit_path.glob("*_original.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    groups: list[tuple[float, list[Path], int]] = []
    for original in originals:
        prefix = _capture_prefix(original)
        members = [Path(path) for path in glob.glob(str(audit_path / f"{prefix}*"))]
        size = sum(path.stat().st_size for path in members if path.is_file())
        groups.append((original.stat().st_mtime, members, size))

    kept_bytes = 0
    for index, (_mtime, members, size) in enumerate(groups):
        should_remove = (
            index >= MAX_CAPTURE_GROUPS or kept_bytes + size > MAX_CAPTURE_BYTES
        )
        if should_remove:
            for path in members:
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    logger.warning(
                        "Could not prune diagnostic capture %s", path, exc_info=True
                    )
        else:
            kept_bytes += size


def get_capture_info(output_dir: str | None = None) -> dict:
    audit_path = resolve_audit_dir(output_dir)
    if not audit_path.is_dir():
        return {"path": str(audit_path), "capture_count": 0, "bytes": 0}
    originals = list(audit_path.glob("*_original.*"))
    files: set[Path] = set()
    for original in originals:
        prefix = _capture_prefix(original)
        files.update(Path(path) for path in glob.glob(str(audit_path / f"{prefix}*")))
    return {
        "path": str(audit_path),
        "capture_count": len(originals),
        "bytes": sum(path.stat().st_size for path in files if path.is_file()),
    }


def clear_diagnostic_captures(output_dir: str | None = None) -> dict:
    """Delete only recognized diagnostic capture groups from the target directory."""
    audit_path = resolve_audit_dir(output_dir)
    info = get_capture_info(output_dir)
    if not audit_path.is_dir():
        return {**info, "deleted_files": 0}
    deleted_files = 0
    originals = list(audit_path.glob("*_original.*"))
    for original in originals:
        prefix = _capture_prefix(original)
        for raw_path in glob.glob(str(audit_path / f"{prefix}*")):
            path = Path(raw_path)
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files += 1
            except OSError:
                logger.warning(
                    "Could not clear diagnostic capture %s", path, exc_info=True
                )
    return {**get_capture_info(output_dir), "deleted_files": deleted_files}


def log_diagnostic_image(
    image_bytes: bytes,
    process_name: str,
    original_filename: str,
    brackets: dict | None = None,
    output_dir: str | None = None,
):
    """Write one debug-only diagnostic capture and enforce bounded retention."""
    try:
        audit_path = resolve_audit_dir(output_dir)
        audit_path.mkdir(parents=True, exist_ok=True)
        _prune_capture_groups(audit_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_name = Path(original_filename).stem
        prefix = audit_path / f"{timestamp}_{process_name}_{safe_name}"

        ext = (
            ".tif" if original_filename.lower().endswith((".tif", ".tiff")) else ".jpg"
        )
        with open(f"{prefix}_original{ext}", "wb") as capture_file:
            capture_file.write(image_bytes)
            capture_file.flush()
            os.fsync(capture_file.fileno())

        for suffix, b64_str in (brackets or {}).items():
            if b64_str:
                with open(f"{prefix}_{suffix}.jpg", "wb") as capture_file:
                    capture_file.write(base64.b64decode(b64_str))
                    capture_file.flush()
                    os.fsync(capture_file.fileno())

        _prune_capture_groups(audit_path)
    except Exception:
        logger.error("Failed to write diagnostic image capture", exc_info=True)
