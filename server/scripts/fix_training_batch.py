filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/routes/training.py"
with open(filepath, "r") as f:
    content = f.read()

old_code = """
        try:
            training_service.add_training_example(
                photo_id=photo_id,
                develop_settings=develop_settings,
                embedding=None,
"""

new_code = """
        try:
            embedding = None
            try:
                from services import chroma
                chroma_data = chroma.get_image(photo_id)
                if chroma_data and chroma_data.get("embeddings") and len(chroma_data["embeddings"]) > 0:
                    embedding = chroma_data["embeddings"][0]
            except Exception:
                pass

            training_service.add_training_example(
                photo_id=photo_id,
                develop_settings=develop_settings,
                embedding=embedding,
"""

if old_code.strip() in content:
    content = content.replace(old_code.strip(), new_code.strip())
    with open(filepath, "w") as f:
        f.write(content)
    print("Fixed training.py")
else:
    print("Could not find code block in training.py")
