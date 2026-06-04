import chromadb
from chromadb.config import Settings
import shutil
import os

if os.path.exists("test_db4"):
    shutil.rmtree("test_db4")

client = chromadb.PersistentClient(path="test_db4", settings=Settings(anonymized_telemetry=False))
coll = client.get_or_create_collection(name="test_col4")

try:
    print(coll.get(include=["metadatas"]))
    print("Success get all")
except Exception as e:
    print("Error getting all:", type(e))

