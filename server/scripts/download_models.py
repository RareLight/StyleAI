#!/usr/bin/env python3
"""
Utility script to pre-download models to the local huggingface cache.
Run this before starting the server to avoid long download times on first startup.
"""

import sys
import os
import logging

# Add src to path so we can import config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("Bootstrapping models for StyleAI...")

    # 1. Download OpenCLIP model (SigLIP2)
    try:
        from huggingface_hub import hf_hub_download

        logger.info(f"Downloading OpenCLIP model: {config.IMAGE_MODEL_ID}")
        # Downloading the safetensors and config ensures it hits the local cache later
        hf_hub_download(
            repo_id=config.IMAGE_MODEL_ID,
            filename="open_clip_model.safetensors",
        )
        hf_hub_download(
            repo_id=config.IMAGE_MODEL_ID,
            filename="open_clip_config.json",
        )

        # Add tokenizer caching
        from transformers import AutoTokenizer

        logger.info(f"Downloading tokenizer for: {config.IMAGE_MODEL_ID}")
        AutoTokenizer.from_pretrained(config.IMAGE_MODEL_ID)

        logger.info("OpenCLIP model cached successfully!")
    except Exception as e:
        logger.error(f"Failed to cache OpenCLIP model: {e}")

    # 2. Download InsightFace model (buffalo_l)
    try:
        from insightface.app import FaceAnalysis

        logger.info("Downloading InsightFace buffalo_l models...")
        # FaceAnalysis implicitly downloads buffalo_l to ~/.insightface/models/ if not present
        app = FaceAnalysis(name="buffalo_l")
        app.prepare(ctx_id=0, det_size=(640, 640))
        logger.info("InsightFace models cached successfully!")
    except Exception as e:
        logger.error(f"Failed to cache InsightFace models: {e}")

    logger.info("Done.")


if __name__ == "__main__":
    main()
