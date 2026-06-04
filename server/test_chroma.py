import chromadb
from chromadb.config import Settings
import shutil
import os

if os.path.exists("test_db"):
    shutil.rmtree("test_db")

client = chromadb.PersistentClient(path="test_db", settings=Settings(anonymized_telemetry=False))
coll = client.get_or_create_collection(name="test_col")
print("Collection created")
try:
    print(coll.get(ids=["1", "2", "3"], include=["metadatas"]))
    print("Success")
except Exception as e:
    print("Exception type:", type(e))
    print("Exception:", e)
