import time
from threading import Lock
from typing import Optional

# Cache structure: { uuid: (image_bytes, timestamp) }
_CACHE: dict[str, tuple[bytes, float]] = {}
_CACHE_LOCK = Lock()

# Auto-evict items older than 10 minutes
_TTL_SECONDS = 600 

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
    stale_keys = [k for k, v in _CACHE.items() if now - v[1] > _TTL_SECONDS]
    for k in stale_keys:
        _CACHE.pop(k, None)
