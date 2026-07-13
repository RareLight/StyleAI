#!/usr/bin/env python3
"""Sync and enrich metadata across training examples and search index without re-indexing.

This script enriches all ChromaDB training_examples with complete metadata fields
(dimensions, aspect ratio, EXIF tags, keywords) from the main search index and
computes unified multi-tiered genre labels.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import config

config.args.idle_shutdown_seconds = 0
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sync_training_metadata")


def main():
    import services.chroma as chroma
    import services.style_grouping as style_grouping
    import services.training as training

    training._ensure_initialized()
    chroma._ensure_initialized()

    if not training._training_collection or not chroma.collection:
        logger.error("Database collections could not be initialized.")
        return

    logger.info("Fetching all training examples from ChromaDB...")
    t_data = training._training_collection.get(include=["metadatas"])
    if not t_data or not t_data.get("ids"):
        logger.info("No training examples found in training collection.")
        return

    t_ids = t_data["ids"]
    t_metas = t_data["metadatas"]

    logger.info("Fetching matching photos from main search index...")
    main_data = chroma.collection.get(ids=t_ids, include=["metadatas"])
    main_map = {}
    if main_data and main_data.get("ids"):
        for i, pid in enumerate(main_data["ids"]):
            if i < len(main_data["metadatas"]) and main_data["metadatas"][i]:
                main_map[pid] = main_data["metadatas"][i]

    updated_ids = []
    updated_metas = []

    for i, pid in enumerate(t_ids):
        meta = dict(t_metas[i]) if i < len(t_metas) and t_metas[i] else {}
        changed = False

        if pid in main_map:
            mm = main_map[pid]
            for k, val_main in mm.items():
                if k in ("label", "summary", "style_name", "photo_id"):
                    continue
                if val_main is not None and val_main not in ("", "[]", "null", "None"):
                    if meta.get(k) != val_main:
                        meta[k] = val_main
                        changed = True

        # Re-compute unified multi-tiered genre label if needed
        genre = style_grouping.classify_photo_genre(meta, None)
        if genre and genre not in ("scene_unknown", "scene_general"):
            old_label = meta.get("label", "")
            new_label = (
                genre.replace("scene_", "")
                .replace("style_", "")
                .replace("_", " ")
                .title()
            )
            if old_label != new_label:
                meta["label"] = new_label
                changed = True

        if changed:
            updated_ids.append(pid)
            updated_metas.append(meta)

    if updated_ids:
        logger.info("Updating %d training examples in ChromaDB...", len(updated_ids))
        batch_size = 500
        for idx in range(0, len(updated_ids), batch_size):
            training._training_collection.update(
                ids=updated_ids[idx : idx + batch_size],
                metadatas=updated_metas[idx : idx + batch_size],
            )
        logger.info("Metadata sync complete!")
    else:
        logger.info("All training examples are already up to date.")

    logger.info("Re-discovering style catalog from unified training examples...")
    from services import style_catalog

    style_catalog.discover_styles_from_examples(None)
    logger.info("Style catalog discovery and cleanup complete!")


if __name__ == "__main__":
    main()
