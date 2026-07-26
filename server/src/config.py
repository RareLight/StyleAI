import argparse
import logging
import sys
import os

# In windowless environments (like pythonw.exe on Windows), sys.stdout and sys.stderr are None.
# We redirect them to devnull to prevent crashes in libraries (logging, traceback, etc.)
# that attempt to write to them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# --- Configuration Constants ---
STYLEAI_LLM_CONCURRENCY = int(os.environ.get("STYLEAI_LLM_CONCURRENCY", 1))

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="StyleAI Server")
parser.add_argument(
    "--db-path", type=str, help="Path to the ChromaDB database folder", required=False
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable debug mode with auto-reloading and debug log level",
)
parser.add_argument(
    "--host", type=str, default="127.0.0.1", help="Bind address for the server"
)
parser.add_argument("--port", type=int, default=19819, help="Port to run the server on")
parser.add_argument(
    "--force-cpu-clip", action="store_true", help="Force CPU for CLIP inference"
)
parser.add_argument(
    "--idle-shutdown-seconds",
    type=int,
    default=600,
    help="Inactivity timeout before unloading models",
)
parser.add_argument(
    "--backup-interval",
    type=int,
    default=86400,
    help="Seconds between automatic database backups",
)
parser.add_argument(
    "--backup-max-keep",
    type=int,
    default=14,
    help="Maximum number of automated backups to retain",
)
parser.add_argument(
    "--disable-backup",
    action="store_true",
    help="Disable automated database backups entirely",
)
parser.add_argument(
    "--log-rotate-backups",
    type=int,
    default=3,
    help="Maximum number of log backups to keep",
)
parser.add_argument(
    "--insightface-root",
    type=str,
    default=os.path.expanduser("~/.insightface"),
    help="Directory for InsightFace models",
)
args = parser.parse_args()

# --- Constants ---
DB_PATH = args.db_path


# --- Model & Path Definitions ---
# Platform-specific device selection:
# - macOS: Use Metal GPU (MPS) if available
# - Windows: CPU-only for now to avoid VRAM issues with open_clip on CUDA and local LLMs using CUDA
force_cpu_clip = args.force_cpu_clip

_torch_device = None


def get_torch_device():
    global _torch_device
    if _torch_device is not None:
        return _torch_device

    import torch
    import sys

    if force_cpu_clip:
        _torch_device = "cpu"
    elif sys.platform == "darwin":  # macOS
        _torch_device = "mps" if torch.backends.mps.is_available() else "cpu"
    elif sys.platform == "win32":  # Windows
        _torch_device = "cpu"
    else:
        # Linux (e.g. Docker): CPU; set CUDA in container if needed
        _torch_device = "cuda" if torch.cuda.is_available() else "cpu"

    return _torch_device


CLIP_MODEL_NAME = "ViT-SO400M-16-SigLIP2-384"
IMAGE_MODEL_ID = "timm/" + CLIP_MODEL_NAME


# --- Prompts for Metadata Generation ---
METADATA_GENERATION_SYSTEM_PROMPT = """You are an expert photography analyst. Output clear, standardized keywords describing the image.

Focus on: nature, landscapes, macro, portraits (incl. pets), family gatherings, candids.

Analyze and tag based on this priority:
1. Location/Scenery: Geographic setting, biome, landscape features.
2. Genre/Mood: Explicitly include genre (e.g., Portrait, Environmental Portrait, Landscape) and emotional tone.
3. Lighting/Weather: Primary light source (e.g., window light, direct sun, flash) and weather.
4. Activities: Actions, ceremonies, event moments.
5. Subjects: People (roles, expressions), animals/plants (specific species/breeds).
6. Objects/Context: Landmarks, vehicles, prominent props, readable text.

Rules:
- Be specific and objective. No generic filler.
- Format in Title Case.
- No duplicate terms.
- No special characters (commas only)."""

METADATA_GENERATION_USER_PROMPT_TEMPLATE = """Analyze the uploaded photo and generate the following data:
* Alt text (with context for screen readers)
* Image caption
* Image title
* Keywords

All results should be generated in {language}."""

# --- LLM Provider Configuration ---
# Environment variables or default values for external LLM providers

# Default provider selection (can be overridden per request)
DEFAULT_METADATA_PROVIDER = "ollama"

# Metadata Generation Settings
DEFAULT_METADATA_LANGUAGE = "English"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_KEYWORD_CATEGORIES = [
    "People",
    "Activities",
    "Objects",
    "Locations",
    "Events",
    "Colors",
    "Mood",
    "Technical",
    "Composition",
]

LMSTUDIO_HOST = "localhost:1234"
OLLAMA_BASE_URL = "http://localhost:11434"


# --- Logger Setup ---
def get_current_log_path() -> str:
    """Returns the log path based on the current DB_PATH, or the default if not set."""
    if DB_PATH:
        # Use dynamic DB_PATH context if available
        return os.path.join(os.path.dirname(DB_PATH) or ".", "styleai-server.log")

    # Default paths determined at startup
    if sys.platform == "darwin":  # macOS
        return os.path.join(
            os.path.expanduser("~"), "Library", "Logs", "StyleAI", "service.log"
        )
    elif sys.platform == "win32":  # Windows
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "StyleAI",
            "logs",
            "styleai-server.log",
        )
    else:
        return os.path.join(os.getcwd(), "styleai-server.log")


LOG_PATH = get_current_log_path()


def update_log_path(new_db_path: str):
    """Updates the global LOG_PATH and reconfigures the file logging handler."""
    global DB_PATH, LOG_PATH
    DB_PATH = new_db_path
    new_log_path = get_current_log_path()

    if new_log_path == LOG_PATH:
        return

    LOG_PATH = new_log_path

    # Ensure directory exists
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    except Exception:
        pass

    # logging.basicConfig is a no-op once handlers are configured, so we must
    # swap the FileHandler on the root logger manually.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)

    new_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    new_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    new_handler.setLevel(logging.DEBUG if args.debug else logging.INFO)
    root.addHandler(new_handler)

    logger.info(f"Log path context updated to: {LOG_PATH}")


try:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
except Exception:
    pass

log_level = logging.DEBUG if args.debug else logging.INFO


# When running locally (not in Docker), on every start create a new log file and keep N backups.
# In Docker we keep a single file so container logs stay simple.
def _is_running_in_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("container") == "docker"


def _rotate_log_on_startup(log_path: str, backup_count: int) -> None:
    """Shift existing log files: .log -> .log.1, .log.1 -> .log.2, ...; remove .log.backup_count."""
    if backup_count <= 0 or not os.path.isfile(log_path):
        return
    base = log_path + "."
    # Remove oldest backup if it exists
    oldest = base + str(backup_count)
    try:
        if os.path.isfile(oldest):
            os.remove(oldest)
    except OSError:
        pass
    # Shift backups: .log.(n-1) -> .log.n, ..., .log.1 -> .log.2
    for i in range(backup_count - 1, 0, -1):
        src = base + str(i)
        dst = base + str(i + 1)
        try:
            if os.path.isfile(src):
                os.rename(src, dst)
        except OSError:
            pass
    # Current log -> .log.1
    try:
        os.rename(log_path, base + "1")
    except OSError:
        pass


def _file_log_handler():
    # Ensure log directory exists before creating the handler
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    except Exception:
        pass

    if _is_running_in_docker():
        return logging.FileHandler(LOG_PATH, encoding="utf-8")
    # Local: on every start create a new log file; keep N backups (STYLEAI_LOG_ROTATE_BACKUPS).
    try:
        backup_count = args.log_rotate_backups
    except ValueError:
        backup_count = 3
    backup_count = max(1, min(backup_count, 20))
    _rotate_log_on_startup(LOG_PATH, backup_count)
    return logging.FileHandler(LOG_PATH, encoding="utf-8")


# Configure logging with UTF-8 encoding to handle Unicode characters
handlers = [_file_log_handler()]
if sys.stdout is not None:
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=handlers,
)
logger = logging.getLogger("styleai-server")
if not _is_running_in_docker():
    logger.info(
        "Log file rotation on startup enabled for %s (STYLEAI_LOG_ROTATE_BACKUPS)",
        LOG_PATH,
    )


# Debug caching for LLM requests
DEBUG_CACHE_DIR = os.path.expanduser(
    "~/Library/Application Support/StyleAI/debug_cache"
)
os.makedirs(DEBUG_CACHE_DIR, exist_ok=True)
