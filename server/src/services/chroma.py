import threading
import time
from functools import wraps
from config import logger


def retry_on_lock(max_retries=3, initial_delay=0.5):
    """Decorator to retry ChromaDB write operations if the underlying SQLite database is locked."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if (
                        attempt == max_retries
                        or "database is locked" not in str(e).lower()
                    ):
                        raise
                    logger.warning(
                        f"ChromaDB locked, retrying {func.__name__} in {delay}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
            return func(*args, **kwargs)

        return wrapper

    return decorator


# --- ChromaDB Client and Collection Initialization (Lazy) ---
chroma_client = None
collection = None


class DatabaseNotReadyError(Exception):
    """Raised when a database modification is attempted but the DB_PATH is not yet set."""

    pass


class CatalogOwnershipError(RuntimeError):
    """Raised when a running backend is asked to rebind its database path."""


STATS_GET_LIMIT = 2_000_000
COLLECTION_PAGE_SIZE = 1000

PHOTO_ID_FIELD = "photo_id"
LEGACY_UUID_FIELD = "uuid"


def _normalize_photo_id(photo_id=None, legacy_uuid=None):
    pid = photo_id or legacy_uuid
    if pid is None:
        return None
    pid = str(pid).strip()
    return pid or None


def _ensure_photo_metadata(photo_id, metadata, legacy_uuid=None):
    out = dict(metadata or {})
    out[PHOTO_ID_FIELD] = photo_id
    # Keep legacy field for older clients/filters.
    out.setdefault(LEGACY_UUID_FIELD, legacy_uuid or photo_id)
    return out


def _first_result_item(values, default=None):
    """Return first item from Chroma results without truthiness checks."""
    import numpy as np

    if values is None:
        return default
    if isinstance(values, np.ndarray):
        if values.size == 0:
            return default
        return values[0]
    try:
        return values[0]
    except (IndexError, KeyError, TypeError):
        return default


def _ensure_initialized():
    """Initialize ChromaDB client and collections on first use (lazy loading)."""
    global chroma_client, collection
    if chroma_client is not None:
        return

    import config

    if not config.DB_PATH:
        logger.debug("ChromaDB initialization skipped: DB_PATH not set yet.")
        return

    import chromadb
    from chromadb.config import Settings

    logger.info(f"Initializing ChromaDB client at {config.DB_PATH} (lazy)...")
    chroma_client = chromadb.PersistentClient(
        path=config.DB_PATH, settings=Settings(anonymized_telemetry=False)
    )

    # No embedding_function is passed: all callers supply pre-computed vectors
    # explicitly via embeddings=[...], so ChromaDB's built-in embedding is unused.
    collection = chroma_client.get_or_create_collection(name="image_embeddings")
    logger.info("Initialized ChromaDB image_embeddings collection.")


def reset_chroma_client():
    """Reset the global ChromaDB client and collections so they can be re-initialized with a new DB_PATH."""
    global chroma_client, collection
    logger.info("Resetting ChromaDB client for re-initialization.")
    chroma_client = None
    collection = None


# Serializes concurrent ensure_db_path calls so two requests racing after a
# fresh start (config.DB_PATH is None) don't both try to construct a client.
_db_path_lock = threading.Lock()


def ensure_db_path(db_path: str) -> bool:
    """Make sure the backend is bound to `db_path` and ready to serve queries.

    Returns True if any switch/init happened, False if the path was already active.

    Acts as the recovery path used by the per-request middleware: if the
    process restarted without a database binding, the first request binds it.
    A live backend is intentionally owned by exactly one Lightroom catalog;
    it must never switch to a different catalog database in-process.
    """
    if not db_path:
        return False

    import config

    if config.DB_PATH == db_path and chroma_client is not None:
        return False

    with _db_path_lock:
        # Re-check inside the lock — another thread may have just bound it.
        if config.DB_PATH == db_path and chroma_client is not None:
            return False

        if config.DB_PATH and config.DB_PATH != db_path:
            raise CatalogOwnershipError(
                "This backend is already bound to a different Lightroom catalog. "
                "Stop it before opening another catalog."
            )
        elif not config.DB_PATH:
            logger.info("Binding backend to db_path from request: %s", db_path)

        config.update_log_path(db_path)
        _ensure_initialized()
        return True


def unload_collections():
    """Unload the ChromaDB collections and client to free memory."""
    global chroma_client, collection
    if chroma_client is None:
        return
    logger.info("Unloading ChromaDB collections...")
    chroma_client = None
    collection = None
    import gc

    gc.collect()
    logger.info("Unloaded ChromaDB collections.")


@retry_on_lock(max_retries=3, initial_delay=0.5)
def add_image(photo_id, embedding, metadata, *, legacy_uuid=None):
    """Add a new image record to the Chroma collection.

    embedding may be None for metadata-only records; in that case we add
    a dummy zero vector with the expected dimensionality (1152) to satisfy
    ChromaDB's requirements while still allowing metadata-only storage.

    Note: Metadata-only entries are marked with has_embedding=False in their
    metadata and are excluded from normal visual-index reads.
    They can still be found via metadata keyword searches.
    """
    _ensure_initialized()
    if collection is None:
        raise DatabaseNotReadyError(
            "Cannot add image: database not initialized (DB_PATH missing)."
        )
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        raise ValueError("photo_id is required")
    metadata = _ensure_photo_metadata(photo_id, metadata, legacy_uuid=legacy_uuid)
    try:
        if embedding is None:
            import numpy as np

            # Add metadata-only record with a dummy zero embedding
            # The collection expects 1152-dimensional embeddings (from vision model)
            dummy_embedding = np.zeros(1152, dtype=np.float32).tolist()
            collection.upsert(
                embeddings=[dummy_embedding], metadatas=[metadata], ids=[photo_id]
            )
        else:
            collection.upsert(
                embeddings=[embedding], metadatas=[metadata], ids=[photo_id]
            )
    except Exception as e:
        # Surface a helpful log message and re-raise so callers can decide what to do.
        logger.error(
            f"Failed to add image {photo_id} to ChromaDB (embedding provided: {embedding is not None}): {e}",
            exc_info=True,
        )
        raise


@retry_on_lock(max_retries=3, initial_delay=0.5)
def update_image(photo_id, metadata, embedding=None, *, legacy_uuid=None):
    _ensure_initialized()
    if collection is None:
        raise DatabaseNotReadyError(
            "Cannot update image: database not initialized (DB_PATH missing)."
        )
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        raise ValueError("photo_id is required")
    metadata = _ensure_photo_metadata(photo_id, metadata, legacy_uuid=legacy_uuid)
    if embedding is not None:
        collection.update(ids=[photo_id], metadatas=[metadata], embeddings=[embedding])
    else:
        collection.update(ids=[photo_id], metadatas=[metadata])


def get_image(photo_id, *, legacy_uuid=None):
    _ensure_initialized()
    if collection is None:
        return {"ids": [], "metadatas": [], "embeddings": []}
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        return {"ids": [], "metadatas": [], "embeddings": []}
    try:
        data = collection.get(ids=[photo_id], include=["metadatas", "embeddings"])
    except Exception as e:
        if type(e).__name__ != "InternalError" or not getattr(
            type(e), "__module__", ""
        ).startswith("chromadb"):
            raise
        logger.debug(
            "ChromaDB get_image: index not yet built (empty collection): %s", e
        )
        return {"ids": [], "metadatas": [], "embeddings": []}
    return data


def delete_image(photo_id, *, legacy_uuid=None):
    _ensure_initialized()
    if collection is None:
        return
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        return
    collection.delete(ids=[photo_id])


# Keys that hold AI-generated metadata; cleared by clear_image_metadata so the photo stays indexed.
AI_METADATA_KEYS = frozenset(
    {
        "title",
        "caption",
        "keywords",
        "alt_text",
        "model",
        "run_date",
        "tokens_used",
        "flattened_keywords",
        "edit_recipe",
        "edit_summary",
        "edit_warnings",
        "edit_model",
        "edit_provider",
        "edit_run_date",
    }
)


def clear_image_metadata(photo_id, *, legacy_uuid=None):
    """
    Clear only AI-generated metadata for an image. Keeps the document and embedding
    in the main collection so the photo remains searchable; use when
    the user discards a suggestion and may regenerate later.
    Returns True if the main collection had the photo (and metadata was cleared), False otherwise.
    """
    _ensure_initialized()
    if collection is None:
        return False
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        return False
    # Main collection: get current, strip AI fields, update (keep embedding)
    try:
        data = collection.get(ids=[photo_id], include=["metadatas", "embeddings"])
    except Exception as e:
        if type(e).__name__ != "InternalError" or not getattr(
            type(e), "__module__", ""
        ).startswith("chromadb"):
            raise
        return False
    if not data or not data.get("ids"):
        logger.debug(
            "clear_image_metadata: photo_id %s not in main collection", photo_id
        )
        return False
    meta = dict(data["metadatas"][0]) if data.get("metadatas") else {}
    embedding = _first_result_item(data.get("embeddings"))
    for key in AI_METADATA_KEYS:
        meta.pop(key, None)
    meta = _ensure_photo_metadata(photo_id, meta, legacy_uuid=legacy_uuid)
    if embedding is not None:
        collection.update(ids=[photo_id], metadatas=[meta], embeddings=[embedding])
    else:
        collection.update(ids=[photo_id], metadatas=[meta])
    return True


def query_images(query_embedding, n_results, where_clause=None):
    _ensure_initialized()
    if collection is None:
        return {"ids": [[]], "distances": [[]], "metadatas": [[]]}
    try:
        result = collection.query(
            where=where_clause,
            query_embeddings=query_embedding,
            n_results=min(n_results, STATS_GET_LIMIT),
            include=["metadatas", "distances"],
        )
        return result
    except Exception as e:
        logger.error(f"Error querying images: {e}", exc_info=True)
        return {"ids": [[]], "distances": [[]], "metadatas": [[]]}


def get_image_count():
    """Return total number of indexed images (photos) in the collection."""
    _ensure_initialized()
    if collection is None:
        return 0
    return collection.count()


def _iter_collection_pages(include):
    """Yield bounded Chroma pages without imposing a catalog-size ceiling."""
    if collection is None:
        return
    offset = 0
    while True:
        page = collection.get(
            include=include, limit=COLLECTION_PAGE_SIZE, offset=offset
        )
        ids = page.get("ids")
        if ids is None:
            ids = []
        if not ids:
            break
        yield page
        offset += len(ids)
        if len(ids) < COLLECTION_PAGE_SIZE:
            break


def get_image_metadata_stats():
    """
    Return statistics on metadata presence across the collection.
    """
    _ensure_initialized()
    if collection is None:
        return {
            "total": 0,
            "with_embedding": 0,
            "with_title": 0,
            "with_caption": 0,
            "with_keywords": 0,
        }
    total = 0
    with_embedding = 0
    with_title = 0
    with_caption = 0
    with_keywords = 0
    for page in _iter_collection_pages(["metadatas"]):
        for metadata in page.get("metadatas") or []:
            metadata = metadata or {}
            total += 1
            if metadata.get("has_embedding", True):
                with_embedding += 1
            if (metadata.get("title") or "").strip():
                with_title += 1
            if (metadata.get("caption") or "").strip():
                with_caption += 1
            if (
                metadata.get("keywords") or metadata.get("flattened_keywords") or ""
            ).strip():
                with_keywords += 1
    return {
        "total": total,
        "with_embedding": with_embedding,
        "with_title": with_title,
        "with_caption": with_caption,
        "with_keywords": with_keywords,
    }


def get_all_image_ids(has_embedding=None):
    """Get all image IDs, optionally filtered by embedding status.

    Args:
        has_embedding: If True, only return IDs with real embeddings.
                      If False, only return IDs with dummy embeddings.
                      If None, return all IDs.
    """
    _ensure_initialized()
    if collection is None:
        return []
    filtered_ids = []
    include = ["metadatas"] if has_embedding is not None else []
    for page in _iter_collection_pages(include):
        ids = page.get("ids") or []
        metadatas = page.get("metadatas") or []
        for index, photo_id in enumerate(ids):
            if has_embedding is None:
                filtered_ids.append(photo_id)
                continue
            metadata = metadatas[index] if index < len(metadatas) else {}
            has_emb = metadata.get("has_embedding", True) if metadata else True
            if has_emb != has_embedding:
                continue
            filtered_ids.append(photo_id)
    return filtered_ids


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _embedding_to_array(embedding):
    """Safely convert a stored embedding to a numpy array."""
    import numpy as np

    if embedding is None:
        return None
    try:
        arr = np.asarray(embedding, dtype=np.float32)
    except Exception:
        return None
    if arr.size == 0 or np.allclose(arr, 0.0):
        return None
    return arr


def _cosine_distance(embedding_a, embedding_b):
    """Calculate cosine distance between two arrays. 1.0 means opposite, 0.0 means identical."""
    import numpy as np

    if embedding_a is None or embedding_b is None:
        return None
    norm_a = np.linalg.norm(embedding_a)
    norm_b = np.linalg.norm(embedding_b)
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    similarity = float(np.dot(embedding_a, embedding_b) / (norm_a * norm_b))
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity
