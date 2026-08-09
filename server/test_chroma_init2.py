import chromadb
from chromadb.config import Settings
import tempfile
import os

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "nonexistent")
    c1 = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
    c1.get_or_create_collection(name="col1")

print("Success")
