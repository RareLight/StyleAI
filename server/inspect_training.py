import sys
from services.training import _training_collection
from services.db import init_db

def inspect():
    try:
        results = _training_collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            print("No training examples found.")
            return

        print(f"Total training examples: {len(results['metadatas'])}")
        profiles = set()
        for meta in results["metadatas"]:
            profiles.add(meta.get("camera_profile"))
        print("Unique Camera Profiles:")
        for p in profiles:
            print(f"- {p}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect()
