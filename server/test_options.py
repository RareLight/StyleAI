import json
from src.utils.request_parsing import _extract_options

data = {
    "images": [{"image": "abc", "photo_id": "1", "filename": "1.jpg", "options": {}}],
    "options": {"audit_llm_inputs": "true", "audit_llm_inputs_path": "~/TempSSD/logs"},
}

global_options = data.get("options", {})
merged_options = dict(global_options)
merged_options.update(data["images"][0].get("options", {}))
photo_options = _extract_options(merged_options)

print(
    "Parsed options:",
    photo_options.get("audit_llm_inputs"),
    photo_options.get("audit_llm_inputs_path"),
)
print("Test:", str(photo_options.get("audit_llm_inputs", "")).lower() == "true")
