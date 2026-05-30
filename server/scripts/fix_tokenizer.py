import re

filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/services/training.py"
with open(filepath, "r") as f:
    content = f.read()

content = content.replace(
    'tokenize_fn = getattr(clip_model, "tokenize", None) or _get_clip_tokenize()',
    'tokenize_fn = getattr(clip_model, "tokenize", None) or server_lifecycle.get_tokenizer()',
)

with open(filepath, "w") as f:
    f.write(content)
print("Fixed tokenizer in training.py")
