import time
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

import config

config.DB_PATH = (
    "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
)
from services import chroma as chroma_service


def test_in_query(uuids):
    print(f"Testing $in query with {len(uuids)} uuids...")
    start = time.time()
    chunk_size = 2000
    found = 0
    for i in range(0, len(uuids), chunk_size):
        chunk = uuids[i : i + chunk_size]
        raw = chroma_service.collection.get(
            where={"uuid": {"$in": chunk}}, include=["metadatas"]
        )
        found += len(raw.get("ids", []))
    print(f"  $in query took {time.time() - start:.2f}s. Found {found}")


def test_fetch_all(uuids):
    print(f"Testing fetch_all approach with {len(uuids)} uuids...")
    start = time.time()
    raw = chroma_service.collection.get(include=["metadatas"])

    # build set
    existing = set()
    for meta in raw.get("metadatas", []):
        if "uuid" in meta:
            existing.add(meta["uuid"])

    found = 0
    for u in uuids:
        if u in existing:
            found += 1

    print(f"  fetch_all took {time.time() - start:.2f}s. Found {found}")


if __name__ == "__main__":
    print("Initializing chroma...")
    chroma_service._ensure_initialized()
    print("Loading all uuids to generate test set...")
    all_data = chroma_service.collection.get(include=["metadatas"])
    all_uuids = [m.get("uuid") for m in all_data.get("metadatas", []) if "uuid" in m]
    print(f"Total uuids in DB: {len(all_uuids)}")

    # Generate some fake uuids to simulate a mix of new and old
    test_uuids = all_uuids[:5000] + [f"fake-uuid-{i}" for i in range(5000)]

    test_in_query(test_uuids)
    test_fetch_all(test_uuids)
