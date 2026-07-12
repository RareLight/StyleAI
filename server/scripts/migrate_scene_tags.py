#!/usr/bin/env python3
import sys
import os
import json
import logging

# Ensure src/ is in the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import config

# Allow setting DB_PATH from env or use default fallback if not set
if not config.DB_PATH:
    # Attempt to auto-detect if not passed via env
    import glob

    db_candidates = glob.glob(os.path.expanduser("~/Pictures/Lightroom/*/styleai.db"))
    if db_candidates:
        config.DB_PATH = db_candidates[0]
    else:
        config.DB_PATH = (
            "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
        )

config.args.idle_shutdown_seconds = 0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    import services.chroma as chroma_service
    import services.training as training
    import server_lifecycle

    chroma_service._ensure_initialized()
    training._ensure_initialized()
    if not chroma_service.collection:
        logger.error("DB not initialized or not found.")
        return

    logger.info("Fetching all indexed photos...")
    # Fetch in batches if necessary, but for 12k photos we can try fetching all
    try:
        data = chroma_service.collection.get(include=["metadatas", "embeddings"])
    except Exception as e:
        logger.error(f"Failed to fetch data from ChromaDB: {e}")
        return

    if not data or not data.get("ids"):
        logger.info("No photos found in database.")
        return

    ids = data["ids"]
    metadatas = data["metadatas"]
    embeddings = data.get("embeddings", [])

    updated_count = 0
    total = len(ids)

    logger.info(f"Found {total} photos. Retagging using updated canonical regimes...")

    for i in range(total):
        photo_id = ids[i]
        meta = metadatas[i]
        emb = embeddings[i] if i < len(embeddings) else None

        if emb is None or (isinstance(emb, list) and len(emb) == 0):
            continue

        if hasattr(emb, "tolist"):
            emb = emb.tolist()

        # Re-compute tags using our newly expanded dictionary
        scene_tags = training.compute_scene_tags(emb)
        if scene_tags:
            meta["scene_tags"] = json.dumps(scene_tags, ensure_ascii=False)

            # Bulk update ChromaDB
            chroma_service.collection.update(ids=[photo_id], metadatas=[meta])
            updated_count += 1

            server_lifecycle._set_last_used()

        if (i + 1) % 500 == 0:
            logger.info(f"Processed {i + 1}/{total} photos...")

    logger.info(f"Retagging complete! Successfully updated {updated_count} photos.")


if __name__ == "__main__":
    main()
