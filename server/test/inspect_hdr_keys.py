import sys
import os
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))
import services.training as training_service
from services.db import init_db


def inspect():
    try:
        init_db()
        training_service._ensure_initialized()
        results = training_service._training_collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            print("No training examples found.")
            return

        print(f"Total training examples: {len(results['metadatas'])}")

        # Look for any develop setting key containing 'hdr' (case insensitive)
        found_hdr_keys = set()
        for meta in results["metadatas"]:
            dev_settings_str = meta.get("develop_settings", "{}")
            dev_settings = json.loads(dev_settings_str)
            for k, v in dev_settings.items():
                if "hdr" in k.lower():
                    found_hdr_keys.add(f"{k}: {type(v).__name__} = {v}")

        print("Found HDR related keys in develop_settings:")
        for k in found_hdr_keys:
            print(f"- {k}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    inspect()
