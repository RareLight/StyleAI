import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))
import services.training as training_service


def inspect():
    try:
        training_service._ensure_initialized()
        results = training_service._training_collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            print("No training examples found.")
            return

        print(f"Total training examples: {len(results['metadatas'])}")
        profiles = set()
        labels = set()
        for meta in results["metadatas"]:
            profiles.add(meta.get("camera_profile"))
            labels.add(meta.get("label"))
        print("Unique Camera Profiles:")
        for p in profiles:
            print(f"- {p}")
        print("Unique Labels:")
        for l in labels:
            print(f"- {l}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    inspect()
