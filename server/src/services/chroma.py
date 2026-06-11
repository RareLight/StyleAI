import chromadb
from chromadb.config import Settings
from chromadb.errors import InternalError as ChromaInternalError
import json
import threading
import numpy as np
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


# InsightFace embeddings are 512-dimensional
FACE_EMBEDDING_DIM = 512

# Max limit for get() when counting; Chroma may apply a default limit otherwise
STATS_GET_LIMIT = 2_000_000

PHOTO_ID_FIELD = "photo_id"
LEGACY_UUID_FIELD = "uuid"
CATALOG_IDS_FIELD = "catalog_ids"


def _parse_catalog_ids(metadata):
    """Parse catalog_ids from metadata (JSON list string). Return set of catalog id strings."""
    if not metadata:
        return set()
    raw = metadata.get(CATALOG_IDS_FIELD)
    if not raw:
        return set()
    if isinstance(raw, list):
        return set(str(x) for x in raw if x)
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return set(str(x) for x in parsed if x) if isinstance(parsed, list) else set()
    except (TypeError, ValueError):
        return set()


def _serialize_catalog_ids(catalog_ids_set):
    """Serialize set of catalog ids to JSON list string for ChromaDB metadata."""
    return json.dumps(sorted(catalog_ids_set)) if catalog_ids_set else "[]"


def _add_catalog_id(photo_id, catalog_id):
    """Ensure catalog_id is in the photo's catalog_ids list; update metadata only."""
    if not catalog_id or not photo_id:
        return
    _ensure_initialized()
    if collection is None:
        return
    try:
        data = collection.get(ids=[photo_id], include=["metadatas", "embeddings"])
    except ChromaInternalError:
        return
    if not data or not data.get("ids"):
        return
    meta = dict(data["metadatas"][0]) if data.get("metadatas") else {}
    ids_set = _parse_catalog_ids(meta)
    ids_set.add(str(catalog_id).strip())
    meta[CATALOG_IDS_FIELD] = _serialize_catalog_ids(ids_set)
    meta = _ensure_photo_metadata(photo_id, meta)
    embedding = _first_result_item(data.get("embeddings"))
    if embedding is not None:
        collection.update(ids=[photo_id], metadatas=[meta], embeddings=[embedding])
    else:
        collection.update(ids=[photo_id], metadatas=[meta])


def _remove_catalog_id(photo_id, catalog_id):
    """Remove catalog_id from the photo's catalog_ids list; update metadata only. Does not delete the photo."""
    if not catalog_id or not photo_id:
        return
    _ensure_initialized()
    if collection is None:
        return
    try:
        data = collection.get(ids=[photo_id], include=["metadatas", "embeddings"])
    except ChromaInternalError:
        return
    if not data or not data.get("ids"):
        return
    meta = dict(data["metadatas"][0]) if data.get("metadatas") else {}
    ids_set = _parse_catalog_ids(meta)
    ids_set.discard(str(catalog_id).strip())
    meta[CATALOG_IDS_FIELD] = _serialize_catalog_ids(ids_set)
    meta = _ensure_photo_metadata(photo_id, meta)
    embedding = _first_result_item(data.get("embeddings"))
    if embedding is not None:
        collection.update(ids=[photo_id], metadatas=[meta], embeddings=[embedding])
    else:
        collection.update(ids=[photo_id], metadatas=[meta])


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
    process restarted (config.DB_PATH lost) the next request that carries a
    db_path re-binds the backend transparently. If `db_path` differs from
    the currently-active one, the chroma client is reset and re-opened
    against the new location (same semantics as the /initialize route).
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
            logger.info("Switching catalog database: %s -> %s", config.DB_PATH, db_path)
            reset_chroma_client()
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
def add_image(photo_id, embedding, metadata, *, legacy_uuid=None, catalog_id=None):
    """Add a new image record to the Chroma collection.

    embedding may be None for metadata-only records; in that case we add
    a dummy zero vector with the expected dimensionality (1152) to satisfy
    ChromaDB's requirements while still allowing metadata-only storage.

    Note: Metadata-only entries are marked with has_embedding=False in their
    metadata and are filtered out of semantic search results in services/search.py.
    They can still be found via metadata keyword searches.

    If catalog_id is provided, the photo is associated with that catalog (soft state).
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
    if catalog_id:
        metadata[CATALOG_IDS_FIELD] = _serialize_catalog_ids({str(catalog_id).strip()})
    try:
        if embedding is None:
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
def update_image(
    photo_id, metadata, embedding=None, *, legacy_uuid=None, catalog_id=None
):
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
    if catalog_id:
        _add_catalog_id(photo_id, catalog_id)


def get_image(photo_id, *, legacy_uuid=None, catalog_id=None):
    _ensure_initialized()
    if collection is None:
        return {"ids": [], "metadatas": [], "embeddings": []}
    photo_id = _normalize_photo_id(photo_id, legacy_uuid)
    if not photo_id:
        return {"ids": [], "metadatas": [], "embeddings": []}
    try:
        data = collection.get(ids=[photo_id], include=["metadatas", "embeddings"])
    except ChromaInternalError as e:
        logger.debug(
            "ChromaDB get_image: index not yet built (empty collection): %s", e
        )
        return {"ids": [], "metadatas": [], "embeddings": []}
    if catalog_id and data and data.get("ids"):
        meta = (data.get("metadatas") or [None])[0]
        ids_set = _parse_catalog_ids(meta)
        if str(catalog_id).strip() not in ids_set:
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
    except ChromaInternalError:
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


def query_images(query_embedding, n_results, where_clause=None, catalog_id=None):
    _ensure_initialized()
    if collection is None:
        return {"ids": [[]], "distances": [[]], "metadatas": [[]]}
    try:
        # Over-fetch when filtering by catalog so we have enough after post-filter
        n_fetch = (int(n_results) * 2 + 100) if catalog_id else n_results
        result = collection.query(
            where=where_clause,
            query_embeddings=query_embedding,
            n_results=min(n_fetch, STATS_GET_LIMIT),
            include=["metadatas", "distances"],
        )
        if (
            not catalog_id
            or not result
            or not result.get("ids")
            or not result["ids"][0]
        ):
            return result
        catalog_id_str = str(catalog_id).strip()
        keep = []
        ids0 = result["ids"][0]
        dist0 = result["distances"][0] if result.get("distances") else []
        meta0 = result["metadatas"][0] if result.get("metadatas") else []
        for i, pid in enumerate(ids0):
            m = meta0[i] if i < len(meta0) else {}
            if catalog_id_str in _parse_catalog_ids(m):
                keep.append(i)
            if len(keep) >= n_results:
                break
        result["ids"] = [[ids0[j] for j in keep]]
        result["distances"] = [[dist0[j] for j in keep]] if dist0 else [[]]
        result["metadatas"] = [[meta0[j] for j in keep]] if meta0 else [[]]
        return result
    except Exception as e:
        logger.error(f"Error querying images: {e}", exc_info=True)
        return {"ids": [[]], "distances": [[]], "metadatas": [[]]}


def get_image_count():
    """Return total number of indexed images (photos) in the collection."""
    _ensure_initialized()
    if collection is None:
        return 0
    return len(collection.get(include=[], limit=STATS_GET_LIMIT)["ids"])


def get_image_metadata_stats(catalog_id=None):
    """
    Return counts of images by metadata presence (no embeddings loaded).
    Returns dict: total, with_embedding, with_title, with_caption, with_keywords.
    If catalog_id is set, only count photos whose catalog_ids contain that catalog.
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
    result = collection.get(include=["metadatas"], limit=STATS_GET_LIMIT)
    metadatas = result.get("metadatas", []) or []
    catalog_id_str = str(catalog_id).strip() if catalog_id else None
    total = 0
    with_embedding = 0
    with_title = 0
    with_caption = 0
    with_keywords = 0
    for idx, m in enumerate(metadatas):
        if catalog_id_str is not None:
            ids_set = _parse_catalog_ids(m)
            if catalog_id_str not in ids_set:
                continue
        total += 1
        if m.get("has_embedding", True):
            with_embedding += 1
        if (m.get("title") or "").strip():
            with_title += 1
        if (m.get("caption") or "").strip():
            with_caption += 1
        if (m.get("keywords") or m.get("flattened_keywords") or "").strip():
            with_keywords += 1
    return {
        "total": total,
        "with_embedding": with_embedding,
        "with_title": with_title,
        "with_caption": with_caption,
        "with_keywords": with_keywords,
    }


# Batch size for sync_claim: one get + one or two updates per batch instead of per photo
SYNC_CLAIM_BATCH_SIZE = 200


def sync_claim(catalog_id, photo_ids):
    """Add catalog_id to each photo's catalog_ids (claim existing backend photos for this catalog).
    Used for migration: unclaimed photos become visible to this catalog.
    Returns {"claimed": N, "errors": M}. Uses batched get/update for speed.
    Deduplicates photo_ids so Chroma get() is not given duplicate IDs (e.g. virtual copies share file-based id).
    """
    _ensure_initialized()
    if collection is None:
        return {"claimed": 0, "errors": 0}
    if not catalog_id:
        return {"claimed": 0, "errors": 0}
    catalog_id_str = str(catalog_id).strip()
    # Deduplicate: same photo_id can appear multiple times (virtual copies, same file)
    seen = set()
    unique = []
    for pid in photo_ids or []:
        pid = str(pid).strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        unique.append(pid)
    photo_ids = unique
    claimed = 0
    errors = 0
    for start in range(0, len(photo_ids), SYNC_CLAIM_BATCH_SIZE):
        chunk = photo_ids[start : start + SYNC_CLAIM_BATCH_SIZE]
        try:
            data = collection.get(ids=chunk, include=["metadatas", "embeddings"])
            if not data or not data.get("ids"):
                continue
            ids = data["ids"]
            metadatas = data.get("metadatas") or [{}] * len(ids)
            embeddings = data.get("embeddings")
            if embeddings is not None and isinstance(embeddings, np.ndarray):
                embeddings = list(embeddings)
            elif embeddings is None:
                embeddings = [None] * len(ids)
            update_ids = []
            update_metadatas = []
            update_embeddings = []
            no_emb_ids = []
            no_emb_metadatas = []
            for i, pid in enumerate(ids):
                meta = dict(metadatas[i]) if i < len(metadatas) else {}
                ids_set = _parse_catalog_ids(meta)
                ids_set.add(catalog_id_str)
                meta[CATALOG_IDS_FIELD] = _serialize_catalog_ids(ids_set)
                meta = _ensure_photo_metadata(pid, meta)
                emb = embeddings[i] if i < len(embeddings) else None
                if emb is not None:
                    update_ids.append(pid)
                    update_metadatas.append(meta)
                    update_embeddings.append(
                        emb if not isinstance(emb, np.ndarray) else emb.tolist()
                    )
                else:
                    no_emb_ids.append(pid)
                    no_emb_metadatas.append(meta)
            if update_ids:
                collection.update(
                    ids=update_ids,
                    metadatas=update_metadatas,
                    embeddings=update_embeddings,
                )
                claimed += len(update_ids)
            if no_emb_ids:
                collection.update(ids=no_emb_ids, metadatas=no_emb_metadatas)
                claimed += len(no_emb_ids)
        except Exception as e:
            logger.warning(
                "sync_claim batch failed for chunk %s..%s: %s",
                start,
                start + len(chunk),
                e,
            )
            errors += len(chunk)
    return {"claimed": claimed, "errors": errors}


def sync_cleanup(catalog_id, active_photo_ids):
    """Disassociate catalog_id from photos that are no longer in active_photo_ids.
    Does not delete any documents; only updates catalog_ids metadata.
    Returns {"checked": N, "disassociated": M}.
    """
    _ensure_initialized()
    if collection is None:
        return {"checked": 0, "disassociated": 0}
    if not catalog_id:
        return {"checked": 0, "disassociated": 0}
    active = set(active_photo_ids) if active_photo_ids is not None else set()
    result = collection.get(include=["metadatas"], limit=STATS_GET_LIMIT)
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    checked = 0
    disassociated = 0
    catalog_id_str = str(catalog_id).strip()
    for i, meta in enumerate(metadatas):
        pid = ids[i] if i < len(ids) else None
        if not pid:
            continue
        ids_set = _parse_catalog_ids(meta)
        if catalog_id_str not in ids_set:
            continue
        checked += 1
        if pid not in active:
            _remove_catalog_id(pid, catalog_id_str)
            disassociated += 1
    return {"checked": checked, "disassociated": disassociated}


def get_all_image_ids(has_embedding=None, catalog_id=None):
    """Get all image IDs, optionally filtered by embedding status and/or catalog_id.

    Args:
        has_embedding: If True, only return IDs with real embeddings.
                      If False, only return IDs with dummy embeddings.
                      If None, return all IDs.
        catalog_id: If set, only return IDs whose catalog_ids metadata contains this catalog.
    """
    _ensure_initialized()
    if collection is None:
        return []
    need_metadata = has_embedding is not None or catalog_id is not None
    if not need_metadata:
        result = collection.get(include=[], limit=STATS_GET_LIMIT)
        return result["ids"]
    result = collection.get(include=["metadatas"], limit=STATS_GET_LIMIT)
    filtered_ids = []
    catalog_id_str = str(catalog_id).strip() if catalog_id else None
    for i, metadata in enumerate(result["metadatas"]):
        if has_embedding is not None:
            has_emb = metadata.get("has_embedding", True) if metadata else True
            if has_emb != has_embedding:
                continue
        if catalog_id_str is not None:
            ids_set = _parse_catalog_ids(metadata)
            if catalog_id_str not in ids_set:
                continue
        filtered_ids.append(result["ids"][i])
    return filtered_ids


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _embedding_to_array(embedding):
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
    if embedding_a is None or embedding_b is None:
        return None
    norm_a = np.linalg.norm(embedding_a)
    norm_b = np.linalg.norm(embedding_b)
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    similarity = float(np.dot(embedding_a, embedding_b) / (norm_a * norm_b))
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity


# --- Face embeddings collection API --0
