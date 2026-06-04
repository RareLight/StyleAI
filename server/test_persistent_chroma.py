import os
import shutil
import chromadb

# Clean up
if os.path.exists("test_db"):
    shutil.rmtree("test_db")

print("Initializing persistent client...")
client = chromadb.PersistentClient(path="test_db")
coll = client.get_or_create_collection("test_empty")

uuids = [f"uuid_{i}" for i in range(2000)]

print("Calling get()...")
try:
    res = coll.get(ids=uuids, include=["metadatas"])
    print("Success. Found IDs:", len(res["ids"]))
except Exception as e:
    print("Exception:", e)

print("Done.")
