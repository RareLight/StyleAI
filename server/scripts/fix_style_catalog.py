import re

filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/services/style_catalog.py"
with open(filepath, "r") as f:
    content = f.read()

# Replace the update_style_for_example logic that incorrectly passes a single photo_id
old_code = """
    # Check if any style already exists for this camera + profile + genre
    row = conn.execute(
        "SELECT 1 FROM styles WHERE camera_model = ? AND camera_profile = ? AND genre = ? LIMIT 1",
        (cam, profile, primary_genre),
    ).fetchone()

    if not row:
        # First example for this combo — trigger discovery
        logger.info(
            "First training example for %s + %s + %s — triggering style discovery",
            cam,
            profile,
            primary_genre,
        )
        discover_styles_from_examples([photo_id])
    else:
        # Incremental update: re-run discovery for this camera+profile+genre
        all_examples = training_service.list_training_examples()
        combo_ids = [
            ex["photo_id"]
            for ex in all_examples
            if ex.get("camera_model", "").strip() == cam
            and (ex.get("camera_profile") or "default").strip() == profile
            and grouping._primary_genre_with_keywords(
                grouping._safe_json_loads(ex.get("scene_tags"), []),
                grouping._safe_json_loads(ex.get("user_keywords"), []),
            )
            == primary_genre
        ]
        if combo_ids:
            discover_styles_from_examples(combo_ids)
"""

new_code = """
    # Trigger discovery for all examples matching this camera+profile+genre combo
    logger.info(
        "Triggering style discovery for %s + %s + %s",
        cam,
        profile,
        primary_genre,
    )
    all_examples = training_service.list_training_examples()
    combo_ids = [
        ex["photo_id"]
        for ex in all_examples
        if (ex.get("camera_model") or "unknown").strip() == cam
        and (ex.get("camera_profile") or "default").strip() == profile
        and grouping._primary_genre_with_keywords(
            grouping._safe_json_loads(ex.get("scene_tags"), []),
            grouping._safe_json_loads(ex.get("user_keywords"), []),
        )
        == primary_genre
    ]
    if combo_ids:
        discover_styles_from_examples(combo_ids)
"""

content = content.replace(old_code.strip(), new_code.strip())

with open(filepath, "w") as f:
    f.write(content)
print("Fixed style_catalog.py")
