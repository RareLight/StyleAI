import chromadb
from chromadb.config import Settings
import shutil
import os
import time

if os.path.exists("test_db3"):
    shutil.rmtree("test_db3")

client = chromadb.PersistentClient(path="test_db3", settings=Settings(anonymized_telemetry=False))
coll = client.get_or_create_collection(name="test_col3")

print("Collection created")

uuids = [f"id_{i}" for i in range(5000)]
start = time.time()

chunk_size = 2000
for i in range(0, len(uuids), chunk_size):
    chunk = uuids[i:i + chunk_size]
    try:
        raw = coll.get(ids=chunk, include=["metadatas"])
    except Exception as e:
        print("Error:", type(e))

print("Time taken:", time.time() - start)
