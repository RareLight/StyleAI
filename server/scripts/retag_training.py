#!/usr/bin/env python3
import sys
import os
import json
import logging

os.environ["STYLEAI_IDLE_SHUTDOWN_SECONDS"] = "0"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import config

config.DB_PATH = (
    "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    import services.training as training
    from services.chroma import get_image

    training._ensure_initialized()
    if not training._training_collection:
        logger.error("DB not initialized.")
        return

    logger.info("Fetching all training examples...")
    data = training._training_collection.get(include=["metadatas", "embeddings"])
    if not data or not data.get("ids"):
        logger.info("No training examples to update.")
        return

    ids = data["ids"]
    metadatas = data["metadatas"]
    embeddings = data.get("embeddings", [])

    updated_count = 0

    for i in range(len(ids)):
        photo_id = ids[i]
        meta = metadatas[i]

        # Determine embedding
        emb = embeddings[i] if i < len(embeddings) else None
        if emb is None or (isinstance(emb, list) and len(emb) == 0):
            # Try getting from main DB
            img_data = get_image(photo_id)
            if img_data and img_data.get("embeddings"):
                e = img_data["embeddings"][0]
                if hasattr(e, "tolist"):
                    e = e.tolist()
                emb = e

        if emb is None:
            continue

        scene_tags = training.compute_scene_tags(emb)
        meta["scene_tags"] = json.dumps(scene_tags, ensure_ascii=False)

        old_label = meta.get("label", "Uncategorized")
        if old_label == "Uncategorized" or old_label.strip() == "":
            if scene_tags:
                top_tag = scene_tags[0]
                new_label = (
                    top_tag.replace("scene_", "")
                    .replace("style_", "")
                    .replace("_", " ")
                    .title()
                )
                meta["label"] = new_label
            else:
                meta["label"] = "Uncategorized"

        training._training_collection.update(ids=[photo_id], metadatas=[meta])
        updated_count += 1

        import server_lifecycle

        server_lifecycle._set_last_used()

        if updated_count % 100 == 0:
            logger.info(f"Updated {updated_count}/{len(ids)} examples...")

    logger.info(f"Retagging complete! Processed {updated_count} examples.")

    # Print a summary of labels
    new_data = training._training_collection.get(include=["metadatas"])
    labels_summary = {}
    for m in new_data["metadatas"]:
        lbl = m.get("label", "Uncategorized")
        labels_summary[lbl] = labels_summary.get(lbl, 0) + 1

    logger.info(f"Final label distribution: {labels_summary}")


if __name__ == "__main__":
    main()
