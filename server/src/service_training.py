"""
Edit style training service.

Manages the `edit_training` ChromaDB collection that stores the user's own
Lightroom develop settings as few-shot examples.  When the AI generates a new
edit recipe it queries this collection by CLIP visual similarity and injects
the closest matches as style examples into the LLM prompt.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from config import DB_PATH, logger

# Lazy ChromaDB globals – initialized on first use.
_chroma_client = None
_training_collection = None

COLLECTION_NAME = "edit_training"
EMBEDDING_DIM = 1152  # CLIP ViT-L/14 dimension used by the main image_embeddings collection


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_initialized() -> None:
    global _chroma_client, _training_collection
    if _training_collection is not None:
        return

    import chromadb
    from chromadb.config import Settings

    logger.info("Initializing edit_training ChromaDB collection (lazy)…")
    _chroma_client = chromadb.PersistentClient(
        path=DB_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        _training_collection = _chroma_client.get_collection(name=COLLECTION_NAME)
        logger.info("Loaded existing edit_training collection.")
    except Exception:
        _training_collection = _chroma_client.create_collection(name=COLLECTION_NAME)
        logger.info("Created new edit_training collection.")


def _dummy_embedding() -> List[float]:
    return np.zeros(EMBEDDING_DIM, dtype=np.float32).tolist()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_training_example(
    photo_id: str,
    develop_settings: Dict[str, Any],
    embedding: Optional[List[float]],
    *,
    label: Optional[str] = None,
    filename: Optional[str] = None,
    summary: Optional[str] = None,
) -> None:
    """Store or overwrite a training example.

    Args:
        photo_id:         Stable photo identifier (same as main collection).
        develop_settings: Raw Lightroom develop settings dict captured from the photo.
        embedding:        CLIP embedding for the source photo (1152-d float list).
                          Falls back to a zero-dummy when None.
        label:            Optional user-facing style label (e.g. "Wedding").
        filename:         Original filename for display purposes.
        summary:          Optional short description of the edit style.
    """
    _ensure_initialized()
    if not photo_id:
        raise ValueError("photo_id is required")

    metadata: Dict[str, Any] = {
        "photo_id": photo_id,
        "develop_settings": json.dumps(develop_settings, ensure_ascii=False),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "has_embedding": embedding is not None,
    }
    if label:
        metadata["label"] = label
    if filename:
        metadata["filename"] = filename
    if summary:
        metadata["summary"] = summary

    emb = embedding if embedding is not None else _dummy_embedding()

    # Upsert: update if already present, add otherwise.
    existing = _training_collection.get(ids=[photo_id], include=[])
    if existing and existing.get("ids"):
        _training_collection.update(ids=[photo_id], embeddings=[emb], metadatas=[metadata])
        logger.info("Updated training example photo_id=%s", photo_id)
    else:
        _training_collection.add(ids=[photo_id], embeddings=[emb], metadatas=[metadata])
        logger.info("Added training example photo_id=%s", photo_id)


def delete_training_example(photo_id: str) -> bool:
    """Remove a training example.

    Returns True when the item existed and was deleted, False otherwise.
    """
    _ensure_initialized()
    if not photo_id:
        return False
    existing = _training_collection.get(ids=[photo_id], include=[])
    if not existing or not existing.get("ids"):
        return False
    _training_collection.delete(ids=[photo_id])
    logger.info("Deleted training example photo_id=%s", photo_id)
    return True


def get_training_count() -> int:
    """Return the number of stored training examples."""
    _ensure_initialized()
    result = _training_collection.get(include=[], limit=1_000_000)
    return len(result.get("ids") or [])


def list_training_examples() -> List[Dict[str, Any]]:
    """Return all training examples as a list of dicts (no embeddings)."""
    _ensure_initialized()
    result = _training_collection.get(include=["metadatas"], limit=1_000_000)
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        examples.append({
            "photo_id": pid,
            "filename": meta.get("filename", ""),
            "label": meta.get("label", ""),
            "summary": meta.get("summary", ""),
            "captured_at": meta.get("captured_at", ""),
            "has_embedding": bool(meta.get("has_embedding", False)),
        })
    examples.sort(key=lambda x: x["captured_at"], reverse=True)
    return examples


def query_similar_training_examples(
    query_embedding: List[float],
    n_results: int = 3,
) -> List[Dict[str, Any]]:
    """Return up to n_results training examples closest to query_embedding.

    Each result dict contains:
        photo_id, develop_settings (dict), label, filename, summary, distance.

    Returns an empty list when no training examples exist or embedding is None.
    """
    _ensure_initialized()
    if query_embedding is None:
        return []

    count = get_training_count()
    if count == 0:
        return []

    n_results = min(n_results, count)
    try:
        result = _training_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        logger.error("query_similar_training_examples failed: %s", exc, exc_info=True)
        return []

    ids = (result.get("ids") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        dev_settings_raw = meta.get("develop_settings", "{}")
        try:
            dev_settings = json.loads(dev_settings_raw)
        except (ValueError, TypeError):
            dev_settings = {}

        examples.append({
            "photo_id": pid,
            "develop_settings": dev_settings,
            "label": meta.get("label", ""),
            "filename": meta.get("filename", ""),
            "summary": meta.get("summary", ""),
            "distance": float(distances[i]) if i < len(distances) else 1.0,
        })
    return examples


def clear_all_training_examples() -> int:
    """Delete every training example. Returns the number removed."""
    _ensure_initialized()
    result = _training_collection.get(include=[], limit=1_000_000)
    ids = result.get("ids") or []
    if not ids:
        return 0
    _training_collection.delete(ids=ids)
    logger.info("Cleared all %d training examples.", len(ids))
    return len(ids)
