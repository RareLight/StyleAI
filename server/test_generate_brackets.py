import requests
import time

url = "http://127.0.0.1:19819/generate_brackets"
# use an existing image file from the logs, like /var/folders/vf/6978ccy17691xzkl485vmv6w0000gn/T/2026-05-30_085736_3897.tif if it exists,
# or create a dummy 50MP TIFF
import numpy as np
import cv2

print("Creating dummy 50MP 16-bit TIFF image...")
dummy_image = np.zeros((5000, 10000, 3), dtype=np.uint16)
cv2.imwrite("dummy.tif", dummy_image)

print("Sending request...")
start_time = time.time()
try:
    with open("dummy.tif", "rb") as f:
        response = requests.post(url, files={"image": f}, timeout=120)
    print(f"Status Code: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
print(f"Time taken: {time.time() - start_time:.2f} seconds")
