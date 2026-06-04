import requests
import json
import time

uuids = [f"uuid_{i}" for i in range(50000)]
data = {
    "photo_ids": uuids,
    "compute_embeddings": True,
    "compute_metadata": False,
    "compute_faces": False,
    "regenerate_metadata": True,
    "catalog_id": "test_catalog"
}

start = time.time()
try:
    resp = requests.post("http://127.0.0.1:19819/index/check-unprocessed", json=data)
    print("Status:", resp.status_code)
    # print("Resp:", len(resp.json()["photo_ids"]))
except Exception as e:
    print("Error:", e)
print("Time taken:", time.time() - start)
