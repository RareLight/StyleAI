filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/services/training.py"
with open(filepath, "r") as f:
    content = f.read()

# 1. Update signature of add_training_example
old_sig = """
    iso: float | None = None,
    aperture: float | None = None,
    shutter_speed: str | None = None,
) -> None:
"""
new_sig = """
    iso: float | None = None,
    aperture: float | None = None,
    shutter_speed: str | None = None,
    skip_discovery: bool = False,
) -> None:
"""
content = content.replace(old_sig.strip(), new_sig.strip())

# 2. Add skip_discovery check before calling style_catalog.update_style_for_example
old_call = """
    # Hook for auto-discovery
    from services import style_catalog

    style_catalog.update_style_for_example(
"""
new_call = """
    # Hook for auto-discovery
    if not skip_discovery:
        from services import style_catalog

        style_catalog.update_style_for_example(
"""
content = content.replace(old_call.strip(), new_call.strip())

with open(filepath, "w") as f:
    f.write(content)
print("Fixed training.py signature")

filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/routes/training.py"
with open(filepath, "r") as f:
    content = f.read()

# 1. Pass skip_discovery=True in add_training_batch
old_call_route = """
            training_service.add_training_example(
                photo_id=photo_id,
                develop_settings=develop_settings,
                embedding=embedding,
                label=label,
                filename=filename,
                summary=summary,
                image_bytes=None,
                focal_length=focal_length,
                capture_time_unix=capture_time_unix,
                camera_make=camera_make,
                camera_model=camera_model,
                camera_profile=camera_profile,
                user_keywords=user_keywords,
                iso=iso,
                aperture=aperture,
                shutter_speed=shutter_speed,
            )
"""
new_call_route = """
            training_service.add_training_example(
                photo_id=photo_id,
                develop_settings=develop_settings,
                embedding=embedding,
                label=label,
                filename=filename,
                summary=summary,
                image_bytes=None,
                focal_length=focal_length,
                capture_time_unix=capture_time_unix,
                camera_make=camera_make,
                camera_model=camera_model,
                camera_profile=camera_profile,
                user_keywords=user_keywords,
                iso=iso,
                aperture=aperture,
                shutter_speed=shutter_speed,
                skip_discovery=True,
            )
"""
content = content.replace(old_call_route.strip(), new_call_route.strip())

# 2. Call recalculate_all_styles after the loop
old_end = """
    success_count = sum(1 for r in results if r["status"] == "ok")
    total_count = training_service.get_training_count()

    return jsonify(
"""
new_end = """
    success_count = sum(1 for r in results if r["status"] == "ok")
    total_count = training_service.get_training_count()

    # Trigger a single full recalculation of all styles after the batch completes
    try:
        from services import style_catalog
        style_catalog.recalculate_all_styles()
    except Exception as exc:
        logger.error("Failed to recalculate styles after batch: %s", exc)

    return jsonify(
"""
content = content.replace(old_end.strip(), new_end.strip())

with open(filepath, "w") as f:
    f.write(content)
print("Fixed routes/training.py")
