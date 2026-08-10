import re

with open("server/src/routes/index.py", "r") as f:
    content = f.read()

content = content.replace(
    'return jsonify({"error": "Duplicate photo IDs are not allowed"}), 400',
    'import logging; logging.warning(f"Duplicate photo IDs detected: {supplied_ids}"); return jsonify({"error": "Duplicate photo IDs are not allowed"}), 400'
)

with open("server/src/routes/index.py", "w") as f:
    f.write(content)
