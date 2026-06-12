import requests
import time

uuids = [f"uuid_{i}" for i in range(50000)]

print("Sending request to check-unprocessed with 50,000 UUIDs...")
start = time.time()
try:
    res = requests.post(
        "http://127.0.0.1:19819/index/check-unprocessed",
        json={"photo_ids": uuids, "tasks": {"analyze": True}},
    )
    print("Status:", res.status_code)
    data = res.json()
    print("Returned UUIDs:", len(data.get("photo_ids", [])))
except Exception as e:
    print("Exception:", e)
print(f"Elapsed: {time.time() - start:.2f}s")
