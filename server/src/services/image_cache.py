import time
from threading import Lock
from typing import Optional

from config import STYLEAI_METADATA_CACHE_BYTES, STYLEAI_METADATA_CACHE_ENTRIES

# Cache structure: { uuid: (image_bytes, timestamp) }
_CACHE: dict[str, tuple[bytes, float]] = {}
_CACHE_LOCK = Lock()

# Abandoned cache entries may eventually be reclaimed, but valid queued local
# LLM work can take much longer than ten minutes.  Admission backpressure below
# is the primary memory bound.
_TTL_SECONDS = 4 * 60 * 60

# Hard limit to prevent massive batches from consuming too much RAM before TTL expires
_MAX_CACHE_ENTRIES = STYLEAI_METADATA_CACHE_ENTRIES
_MAX_CACHE_BYTES = STYLEAI_METADATA_CACHE_BYTES


def store_image(uuid: str, image_data: bytes) -> bool:
    """Reserve image bytes without evicting already accepted metadata work."""
    if not uuid or not isinstance(image_data, bytes) or not image_data:
        return False

    from services import operations

    operations.refresh_system_pressure()
    byte_limit = min(_MAX_CACHE_BYTES, operations.admission.capacities["image_bytes"])

    with _CACHE_LOCK:
        _cleanup_stale()
        existing = _CACHE.get(uuid)
        existing_size = len(existing[0]) if existing else 0
        projected_entries = len(_CACHE) + (0 if existing else 1)
        projected_bytes = _cache_bytes() - existing_size + len(image_data)
        if projected_entries > _MAX_CACHE_ENTRIES or projected_bytes > byte_limit:
            return False
        _CACHE[uuid] = (image_data, time.time())
        return True


def get_image(uuid: str) -> Optional[bytes]:
    with _CACHE_LOCK:
        item = _CACHE.get(uuid)
        if item:
            return item[0]
        return None


def pop_image(uuid: str) -> Optional[bytes]:
    with _CACHE_LOCK:
        item = _CACHE.pop(uuid, None)
        if item:
            return item[0]
        return None


def pop_images(uuids: list[str]) -> tuple[list[bytes] | None, list[str]]:
    """Atomically take a batch, consuming nothing when any image is missing."""
    with _CACHE_LOCK:
        _cleanup_stale()
        missing = [uuid for uuid in uuids if uuid not in _CACHE]
        if missing:
            return None, missing
        return [_CACHE.pop(uuid)[0] for uuid in uuids], []


def remove_image(uuid: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(uuid, None)


def clear() -> int:
    with _CACHE_LOCK:
        count = len(_CACHE)
        _CACHE.clear()
        return count


def _cache_bytes() -> int:
    return sum(len(item[0]) for item in _CACHE.values())


def _cleanup_stale() -> None:
    now = time.time()

    # 1. Evict by TTL
    stale_keys = [k for k, v in _CACHE.items() if now - v[1] > _TTL_SECONDS]
    for k in stale_keys:
        _CACHE.pop(k, None)
