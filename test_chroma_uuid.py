import sys
import os

# Add src to path so we can import services
sys.path.append(os.path.join(os.getcwd(), "server", "src"))

from services import chroma

chroma._ensure_initialized()
if chroma.collection is None:
    print("Database not initialized")
    sys.exit(1)

# Get some existing data to find a valid UUID
data = chroma.collection.get(limit=10, include=["metadatas"])
if not data or not data["ids"]:
    print("No data in DB")
    sys.exit(0)

test_uuid = data["metadatas"][0].get("uuid")
if not test_uuid:
    print("First record has no UUID")
    sys.exit(0)

print(f"Testing with uuid: {test_uuid}")

chunk = [test_uuid, "fake-uuid-123"]
try:
    result = chroma.collection.get(where={"uuid": {"$in": chunk}}, include=["metadatas"])
    print(f"Result count: {len(result['ids'])}")
    print(f"Found IDs: {result['ids']}")
except Exception as e:
    print(f"Error executing $in query: {e}")

