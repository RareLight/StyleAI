import re

with open("index.py", "r") as f:
    content = f.read()

replacement = """        import concurrent.futures
        analyze_future = None
        vertex_future = None
        per_image_futures = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # 1. analyze_batch
            analyze_future = executor.submit(
                analysis_service.analyze_batch,
                image_triplets,
                options,
                siglip_model,
                siglip_processor,
                images_needing_embeddings,
                images_needing_metadata,
                exif_location_by_uuid or None,
                pil_images,
            )

            # 2. Vertex AI
            if images_needing_vertexai and vertexai_service.is_available(vertex_project_id, vertex_location):
                def _do_vertexai():
                    res = {}
                    vertex_uuids = []
                    vertex_bytes = []
                    for img_bytes, uid, _ in image_triplets:
                        if uid in images_needing_vertexai:
                            vertex_uuids.append(uid)
                            vertex_bytes.append(img_bytes)
                    if vertex_bytes:
                        logger.info(f"Generating Vertex AI embeddings for {len(vertex_bytes)} images...")
                        v_res = vertexai_service.get_image_embeddings(
                            vertex_bytes,
                            vertex_project_id=vertex_project_id,
                            vertex_location=vertex_location,
                        )
                        for u, emb in zip(vertex_uuids, v_res):
                            if emb is not None:
                                res[u] = emb
                    return res
                vertex_future = executor.submit(_do_vertexai)

            # 3. Faces & Culling (Per-image)
            for i, (img_bytes, uid, fname) in enumerate(image_triplets):
                p_img = pil_images[i]
                def _process_single(u=uid, b=img_bytes, p=p_img):
                    res = {"culling": None, "phash": "", "faces": None, "faces_error": None}
                    
                    if p is not None:
                        res["culling"] = _compute_culling_metrics(p)
                        res["phash"] = _compute_perceptual_hash(p)
                    
                    if compute_faces and b:
                        if not regenerate_metadata and chroma_service.faces_checked_for_photo(u):
                            pass
                        else:
                            try:
                                res["faces"] = face_service.detect_faces(b, pil_image=p)
                            except Exception as e:
                                res["faces_error"] = e
                    return res
                per_image_futures.append(executor.submit(_process_single))
            
            # Wait for all to finish
            try:
                embeddings, metadata_results = analyze_future.result()
            except Exception as e:
                logger.error(f"Error in analyze_batch: {str(e)}", exc_info=True)
                error_messages.append(str(e))
                return 0, total_images, error_messages, warnings

            vertex_embeddings_by_uuid = {}
            if vertex_future:
                try:
                    vertex_embeddings_by_uuid = vertex_future.result()
                except Exception as e:
                    logger.error(f"vertexai failed: {e}", exc_info=True)
                    error_messages.append(f"Vertex AI error: {e}")

            per_image_results = [f.result() for f in per_image_futures]
"""

# Replace the block from `try:` analyze_batch to Vertex AI
pattern = re.compile(
    r"        try:\n            embeddings, metadata_results = analysis_service\.analyze_batch\([\s\S]*?error_messages\.append\(f\"Vertex AI error: \{e\}\"\)\n            else:\n                logger\.warning\(\"Vertex AI requested but not available/configured\.\"\)\n                warnings\.append\(\n                    \"Vertex AI requested but not available or correctly configured \(check Project ID and authentication\)\.\"\n                \)",
    re.MULTILINE,
)

content = pattern.sub(replacement, content)

# Now replace culling logic
culling_pattern = re.compile(
    r"                # Technical culling metrics are cheap enough to compute on every pass\.\n                if pil_image is not None:\n                    main_metadata\.update\(_compute_culling_metrics\(pil_image\)\)\n                    phash_hex = _compute_perceptual_hash\(pil_image\)\n                else:\n                    phash_hex = \"\"\n                if phash_hex:\n                    main_metadata\[\"cull_phash\"\] = phash_hex\n                    main_metadata\[\"phash\"\] = phash_hex"
)

culling_replacement = """                # Retrieve threaded culling metrics
                culling_res = per_image_results[i]["culling"]
                if culling_res:
                    main_metadata.update(culling_res)
                phash_hex = per_image_results[i]["phash"]
                if phash_hex:
                    main_metadata["cull_phash"] = phash_hex
                    main_metadata["phash"] = phash_hex"""

content = culling_pattern.sub(culling_replacement, content)

# Now replace face detection logic
face_pattern = re.compile(
    r"                            face_results = face_service\.detect_faces\(\n                                image_bytes, pil_image=pil_image\n                            \)"
)
face_replacement = """                            if per_image_results[i]["faces_error"]:
                                raise per_image_results[i]["faces_error"]
                            face_results = per_image_results[i]["faces"]"""

content = face_pattern.sub(face_replacement, content)

with open("index.py", "w") as f:
    f.write(content)
print("Updated index.py successfully")
