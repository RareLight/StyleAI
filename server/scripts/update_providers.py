import os
import re


def process_file(filepath):
    with open(filepath, "r") as f:
        content = f.read()

    if "gemini.py" in filepath:
        # Edit generate_metadata (if image_data is list)
        content = re.sub(
            r'contents = \[\s*user_prompt,\s*types\.Part\.from_bytes\(data=request\.image_data, mime_type="image/jpeg"\),\s*\]',
            """contents = [user_prompt]
            if isinstance(request.image_data, list):
                for img in request.image_data:
                    contents.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
            else:
                contents.append(types.Part.from_bytes(data=request.image_data, mime_type="image/jpeg"))""",
            content,
        )
        # Edit generate_edit_recipe
        content = re.sub(
            r'contents=\[\s*user_prompt,\s*types\.Part\.from_bytes\(\s*data=request\.image_data, mime_type="image/jpeg"\s*\),\s*\]',
            """contents=contents""",
            content,
        )
        content = re.sub(
            r"response = self\.client\.models\.generate_content\(",
            """contents = [user_prompt]
            if isinstance(request.image_data, list):
                for img in request.image_data:
                    contents.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
            else:
                contents.append(types.Part.from_bytes(data=request.image_data, mime_type="image/jpeg"))
            
            response = self.client.models.generate_content(""",
            content,
        )
    elif "chatgpt.py" in filepath:
        # replace self._image_to_base64(request.image_data) with a loop
        content = re.sub(
            r'image_b64 = self\._image_to_base64\(request\.image_data\)\s*data_uri = f"data:image/jpeg;base64,\{image_b64\}"',
            """if isinstance(request.image_data, list):
                data_uris = [f"data:image/jpeg;base64,{self._image_to_base64(img)}" for img in request.image_data]
            else:
                data_uris = [f"data:image/jpeg;base64,{self._image_to_base64(request.image_data)}"]""",
            content,
        )
        # replace the single image url dict
        content = re.sub(
            r'\{"type": "image_url", "image_url": \{"url": data_uri\}\}',
            """*([{"type": "image_url", "image_url": {"url": uri}} for uri in data_uris])""",
            content,
        )
    elif "ollama.py" in filepath or "lmstudio.py" in filepath:
        content = re.sub(
            r"images=\[self\._image_to_base64\(request\.image_data\)\]",
            """images=[self._image_to_base64(img) for img in request.image_data] if isinstance(request.image_data, list) else [self._image_to_base64(request.image_data)]""",
            content,
        )

    with open(filepath, "w") as f:
        f.write(content)


base_dir = "/Users/anna/Documents/Coding/StyleAI/server/src/providers"
for p in ["gemini.py", "chatgpt.py", "ollama.py", "lmstudio.py"]:
    process_file(os.path.join(base_dir, p))
print("Updated providers.")
