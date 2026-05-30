import sys

sys.path.insert(0, "/Users/anna/Documents/Coding/StyleAI/server/src")

from services import chroma
from config import DB_PATH

photo_id = "meta1:a2d44508acf3167296998d36cb590d41"
data = chroma.get_image(photo_id)
print(f"Data for {photo_id}:")
if data.get("embeddings") and len(data["embeddings"]) > 0:
    print("Has embeddings! Length:", len(data["embeddings"][0]))
else:
    print("NO EMBEDDINGS FOUND.")
