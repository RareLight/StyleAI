import chromadb
from chromadb.config import Settings
import tempfile
import os

with tempfile.TemporaryDirectory() as d:
    # First client
    c1 = chromadb.PersistentClient(path=d, settings=Settings(anonymized_telemetry=False))
    c1.get_or_create_collection(name="col1")
    
    # Second client in same process
    c2 = chromadb.PersistentClient(path=d, settings=Settings(anonymized_telemetry=False))
    c2.get_or_create_collection(name="col2")

print("Success")
