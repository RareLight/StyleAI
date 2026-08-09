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


def _physical_memory_gb() -> float:
    """Read physical memory without importing a heavyweight runtime dependency."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        return float(page_size * page_count) / (1024**3)
    except (AttributeError, OSError, ValueError):
        return 0.0


def get_index_resource_limits(
    memory_gb: float | None = None,
    platform_name: str | None = None,
) -> dict[str, int]:
    """Return bounded ingestion limits, tuned for unified-memory Macs.

    SigLIP2, decoded JPEGs, Chroma, and a local LLM share Apple unified memory.
    These tiers prioritize sustained throughput over short-lived batch spikes;
    explicit ``STYLEAI_*`` environment variables remain the escape hatch for
    measured, system-specific tuning.
    """
    platform_name = platform_name or sys.platform
    memory_gb = _physical_memory_gb() if memory_gb is None else memory_gb

    if platform_name == "darwin":
        if memory_gb <= 16:
            return {"gpu_batch_size": 8, "queue_capacity": 32, "http_threads": 8}
        if memory_gb <= 32:
            # M2 Max with 32 GB: enough GPU throughput without MPS/LLM swap.
            return {"gpu_batch_size": 12, "queue_capacity": 48, "http_threads": 12}
        return {"gpu_batch_size": 16, "queue_capacity": 64, "http_threads": 16}

    # Preserve the existing conservative default on CPU/CUDA hosts. CUDA users
    # can set explicit limits after measuring VRAM headroom for their model.
    return {"gpu_batch_size": 12, "queue_capacity": 48, "http_threads": 12}


def get_metadata_cache_limits(
    memory_gb: float | None = None,
    platform_name: str | None = None,
) -> dict[str, int]:
    """Return bounded JPEG-cache limits for staged local-LLM metadata work.

    Cache admission is backpressured rather than evicting an image that has
    already been accepted for metadata generation.  The byte budget matters in
    addition to the item count because Lightroom preview sizes vary widely.
    """
    platform_name = platform_name or sys.platform
    memory_gb = _physical_memory_gb() if memory_gb is None else memory_gb

    if platform_name == "darwin":
        if memory_gb <= 16:
            return {"entries": 32, "bytes": 256 * 1024 * 1024}
        if memory_gb <= 32:
            return {"entries": 48, "bytes": 384 * 1024 * 1024}
        return {"entries": 64, "bytes": 512 * 1024 * 1024}

    return {"entries": 48, "bytes": 384 * 1024 * 1024}


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


_index_resource_limits = get_index_resource_limits()
STYLEAI_INDEX_QUEUE_CAPACITY = _positive_env_int(
    "STYLEAI_INDEX_QUEUE_CAPACITY", _index_resource_limits["queue_capacity"]
)
STYLEAI_GPU_BATCH_SIZE = _positive_env_int(
    "STYLEAI_GPU_BATCH_SIZE", _index_resource_limits["gpu_batch_size"]
)
STYLEAI_HTTP_THREADS = _positive_env_int(
    "STYLEAI_HTTP_THREADS", _index_resource_limits["http_threads"]
)
_metadata_cache_limits = get_metadata_cache_limits()
STYLEAI_METADATA_CACHE_ENTRIES = _positive_env_int(
    "STYLEAI_METADATA_CACHE_ENTRIES", _metadata_cache_limits["entries"]
)
STYLEAI_METADATA_CACHE_BYTES = _positive_env_int(
    "STYLEAI_METADATA_CACHE_BYTES", _metadata_cache_limits["bytes"]
)

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
parser.add_argument("--port", type=int, default=19819, help="Port to run the server on")
parser.add_argument(
    "--force-cpu-clip", action="store_true", help="Force CPU for CLIP inference"
)
parser.add_argument(
    "--idle-shutdown-seconds",
    type=int,
    default=600,
    help="Inactivity timeout before shutting down the local backend (0 disables)",
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
        _torch_device = "cuda" if torch.cuda.is_available() else "cpu"

    return _torch_device


CLIP_MODEL_NAME = "ViT-SO400M-16-SigLIP2-384"
IMAGE_MODEL_ID = "timm/" + CLIP_MODEL_NAME


# --- Prompts for Metadata Generation ---
METADATA_GENERATION_SYSTEM_PROMPT = """You are an expert photography analyst. Output clear, standardized, open-vocabulary keywords describing the image.

Analyze only visible evidence, in this priority:
1. Primary Subject: What the photograph is actually about, as specifically as the evidence supports.
2. Activity and Relationship: Actions, interactions, roles, or behavior that are visibly important.
3. Setting and Context: Location type, environment, occasion, prominent objects, or readable text.
4. Visual Approach: Photographic genre, composition, perspective, and depth cues only when they are visually supported.
5. Lighting and Conditions: Direction, quality, apparent source, weather, and time-of-day cues.
6. Mood and Color: Observable atmosphere, palette, and tonal character.

Rules:
- Use vocabulary appropriate to the photograph; do not force it into a predefined genre list.
- Be specific and objective. No generic filler or unsupported inference.
- Format in Title Case.
- No duplicate terms.
- No special characters (commas only)."""

METADATA_GENERATION_USER_PROMPT_TEMPLATE = """Analyze the uploaded photo and generate the following data:
* Alt text (with context for screen readers)
* Image caption
* Image title
* Keywords

All results should be generated in {language}."""

# --- Local LLM Provider Configuration ---

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

    # On every start create a new log file and keep a bounded history.
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
logger.info(
    "Log file rotation on startup enabled for %s (STYLEAI_LOG_ROTATE_BACKUPS)",
    LOG_PATH,
)
