import time
from threading import Lock
from typing import Optional

# Cache structure: { uuid: (image_bytes, timestamp) }
_CACHE: dict[str, tuple[bytes, float]] = {}
_CACHE_LOCK = Lock()

# Auto-evict items older than 10 minutes
_TTL_SECONDS = 600

# Hard limit to prevent massive batches from consuming too much RAM before TTL expires
_MAX_CACHE_ENTRIES = 100


def store_image(uuid: str, image_data: bytes) -> None:
    with _CACHE_LOCK:
        _CACHE[uuid] = (image_data, time.time())
        _cleanup_stale()


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


def _cleanup_stale() -> None:
    now = time.time()

    # 1. Evict by TTL
    stale_keys = [k for k, v in _CACHE.items() if now - v[1] > _TTL_SECONDS]
    for k in stale_keys:
        _CACHE.pop(k, None)

    # 2. Evict by count (LRU)
    if len(_CACHE) > _MAX_CACHE_ENTRIES:
        # Sort by timestamp (oldest first)
        sorted_items = sorted(_CACHE.items(), key=lambda x: x[1][1])
        # Calculate how many to remove
        num_to_remove = len(_CACHE) - _MAX_CACHE_ENTRIES
        for i in range(num_to_remove):
            _CACHE.pop(sorted_items[i][0], None)
