import chromadb
import json


def inspect():
    try:
        client = chromadb.PersistentClient(
            path="/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
        )
        collection = client.get_collection("edit_training")
        results = collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            print("No training examples found.")
            return

        print(f"Total training examples: {len(results['metadatas'])}")

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
