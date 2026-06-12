import chromadb
from chromadb.config import Settings
import shutil
import os

if os.path.exists("test_db2"):
    shutil.rmtree("test_db2")

client = chromadb.PersistentClient(
    path="test_db2", settings=Settings(anonymized_telemetry=False)
)
coll = client.get_or_create_collection(name="test_col2")
print("Collection created")

# Simulate user completely deleting the database manually while server is running
shutil.rmtree("test_db2")
print("Database deleted from disk")

try:
    print(coll.get(ids=["1", "2", "3"], include=["metadatas"]))
    print("Success")
except Exception as e:
    print("Exception type:", type(e))
    print("Exception:", e)
