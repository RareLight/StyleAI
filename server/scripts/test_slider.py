import json
from services.style_engine import _finalize_recipe

target_recipe = {
    "global": {
        "exposure": 1.5,
        "contrast": 20,
    }
}

current_settings_0 = {
    "Exposure2012": 0.0,
    "Contrast2012": 0.0,
}

output_0 = _finalize_recipe(
    recipe=json.loads(json.dumps(target_recipe)),
    query_exposure={"exp_warmth_proxy": 0.5},
    current_settings=current_settings_0,
    style_strength=0.5
)
print(f"50% strength on baseline 0.0: {output_0['global']}")
