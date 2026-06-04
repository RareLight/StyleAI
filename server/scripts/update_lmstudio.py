import re

filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/providers/lmstudio.py"
with open(filepath, "r") as f:
    content = f.read()

content = re.sub(
    r"image_handle = client\.files\.prepare_image\(request\.image_data\)",
    """if isinstance(request.image_data, list):
                    image_handles = [client.files.prepare_image(img) for img in request.image_data]
                else:
                    image_handles = [client.files.prepare_image(request.image_data)]""",
    content,
)

content = re.sub(r"images=\[image_handle\]", "images=image_handles", content)

with open(filepath, "w") as f:
    f.write(content)
print("Updated lmstudio.py")
