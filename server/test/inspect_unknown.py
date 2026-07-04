import json
import chromadb


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

        unknown_count = 0
        keywords_tally = {}
        scene_tags_tally = {}
        filenames = []

        for meta in results["metadatas"]:
            label = meta.get("label", "")
            # Sometimes genre extraction sets it to "Unknown" if it couldn't map it
            if "unknown" in label.lower():
                unknown_count += 1

                # Keywords
                kw_str = meta.get("user_keywords", "[]")
                kws = json.loads(kw_str)
                for kw in kws:
                    keywords_tally[kw] = keywords_tally.get(kw, 0) + 1

                # Scene tags
                tags_str = meta.get("scene_tags", "[]")
                tags = json.loads(tags_str)
                for t in tags:
                    scene_tags_tally[t] = scene_tags_tally.get(t, 0) + 1

                if len(filenames) < 10:
                    filenames.append(meta.get("filename", "unknown_file"))

        print(f"Total 'Unknown' examples: {unknown_count}")
        print("\nTop user keywords for 'Unknown':")
        for k, v in sorted(keywords_tally.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]:
            print(f"  - {k}: {v}")

        print("\nTop AI scene tags for 'Unknown':")
        for k, v in sorted(scene_tags_tally.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]:
            print(f"  - {k}: {v}")

        print("\nSample filenames:")
        for f in filenames:
            print(f"  - {f}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    inspect()
