"""
Indexing and Embedding Pipeline Service.

This module orchestrates the core ingestion pipeline for photos. To maximize hardware
utilization, it employs a `ThreadPoolExecutor` architecture. CPU-bound tasks (JPEG
decoding, hashing, face-detection preprocessing) run concurrently on background
threads, feeding preprocessed data into a thread-safe queue. The main thread (or
dedicated GPU thread) exclusively handles SigLIP2 and InsightFace model inference,
eliminating UI blocking and massive pipeline stalls during bulk catalog indexing.
"""

from config import logger, TORCH_DEVICE
from . import chroma as chroma_service
from .chroma import DatabaseNotReadyError
from .metadata import get_analysis_service
import server_lifecycle as server_lifecycle
from . import exif as exif_service
from . import training as training_service
import gc
import json
from datetime import datetime as time
from PIL import Image
import io
import numpy as np
import torch
from concurrent.futures import ThreadPoolExecutor


def _flatten_keywords(keywords):
    """
    Flatten keywords from various formats to a comma-separated string.

    Handles:
    - Flat list: ["Keyword1", "Keyword2"] -> "Keyword1, Keyword2"
    - Nested dict: {"Category": ["Kw1", "Kw2"], ...} -> "Kw1, Kw2, ..."
    - Already a string: "Keyword1, Keyword2" -> "Keyword1, Keyword2"

    Args:
        keywords: List, dict, or string of keywords

    Returns:
        Comma-separated string of all keywords
    """
    if not keywords:
        return ""

    if isinstance(keywords, str):
        # Already a string, return as-is
        return keywords

    seen_keywords = set()

    def _append_unique(values, text):
        normalized = text.lower()
        if normalized in seen_keywords:
            return
        seen_keywords.add(normalized)
        values.append(text)

    def _normalize_keyword_text(value):
        if isinstance(value, str):
            text = value.strip()
            values = []
            if text:
                _append_unique(values, text)
            return values
        if isinstance(value, dict):
            values = []
            name = value.get("name")
            if isinstance(name, str) and name.strip():
                _append_unique(values, name.strip())
            for field in ("synonyms", "aliases", "synonym_aliases"):
                bucket = value.get(field)
                if isinstance(bucket, list):
                    for entry in bucket:
                        if isinstance(entry, str) and entry.strip():
                            _append_unique(values, entry.strip())
            return values
        return []

    if isinstance(keywords, list):
        # Flat list of strings or structured keyword objects
        flattened = []
        for kw in keywords:
            flattened.extend(_normalize_keyword_text(kw))
        return ", ".join(flattened)

    if isinstance(keywords, dict):
        # Nested dict - recursively collect all keywords
        all_keywords = []

        def collect_keywords(d):
            for key, value in d.items():
                if isinstance(value, list):
                    # Leaf node with keywords (strings or structured keyword objects)
                    for kw in value:
                        all_keywords.extend(_normalize_keyword_text(kw))
                elif isinstance(value, dict) and value:
                    if isinstance(value.get("name"), str):
                        all_keywords.extend(_normalize_keyword_text(value))
                    else:
                        # Nested dict, recurse
                        collect_keywords(value)
                else:
                    # Single keyword value
                    all_keywords.extend(_normalize_keyword_text(value))

        collect_keywords(keywords)
        return ", ".join(all_keywords)

    return ""


def _load_analysis_grayscale(image_bytes: bytes, max_side: int = 512) -> np.ndarray:
    image = Image.open(io.BytesIO(image_bytes))
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    image = image.convert("RGB")
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    return (0.299 * rgb[:, :, 0]) + (0.587 * rgb[:, :, 1]) + (0.114 * rgb[:, :, 2])


def _decode_image(image_bytes: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Defensive resizing to prevent OOM (12GB+ RAM spikes) on massive images.
        # SigLIP2 uses 384px, InsightFace uses 640px, so 1024px is plenty.
        max_dim = 1024
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        return img.convert("RGB")
    except Exception as exc:
        logger.warning("Could not decode image: %s", exc)
        return None


def get_uuids_needing_processing(
    uuids: list[str], options: dict, search_by_lr_uuid: bool = False
) -> list[str]:
    """
    Returns UUIDs that need processing based on selected tasks and existing backend data.
    If search_by_lr_uuid is True, treats uuids as Lightroom native UUIDs and searches the
    metadata 'uuid' field instead of ChromaDB document IDs.
    """
    regenerate_metadata = options.get("regenerate_metadata", True)
    compute_embeddings = options.get("compute_embeddings", True)
    compute_metadata = options.get("compute_metadata", False)

    if not uuids:
        return []

    # Load existing records for all UUIDs in bulk
    existing_records = {}
    chunk_size = 2000
    for i in range(0, len(uuids), chunk_size):
        chunk = uuids[i : i + chunk_size]
        try:
            # ChromaDB handles bulk gets much faster and without massive exception overhead on empty databases
            if search_by_lr_uuid:
                raw = chroma_service.collection.get(
                    where={"uuid": {"$in": chunk}}, include=["metadatas"]
                )
            else:
                raw = chroma_service.collection.get(ids=chunk, include=["metadatas"])

            if raw and raw.get("ids"):
                for idx, pid in enumerate(raw["ids"]):
                    metas = raw.get("metadatas") or [{}] * len(raw["ids"])
                    meta = metas[idx] if idx < len(metas) else {}

                    # If we searched by LR UUID, we need to map the result by the LR UUID to match the chunk
                    if search_by_lr_uuid and "uuid" in meta:
                        existing_records[meta["uuid"]] = meta
                    else:
                        existing_records[pid] = meta
        except Exception:
            pass

    needing_processing = []
    for uuid in uuids:
        is_existing = uuid in existing_records
        existing = existing_records.get(uuid, {})

        needs_embedding = compute_embeddings and (
            regenerate_metadata
            or not is_existing
            or existing.get("has_embedding", True) is False
        )
        has_any_metadata = (
            existing.get("title")
            or existing.get("caption")
            or existing.get("alt_text")
            or existing.get("keywords")
        )
        needs_metadata = compute_metadata and (
            regenerate_metadata or not has_any_metadata
        )

        if needs_embedding or needs_metadata:
            needing_processing.append(uuid)

    return needing_processing


def get_photo_ids_needing_processing(
    photo_ids: list[str], options: dict, search_by_lr_uuid: bool = False
) -> list[str]:
    """Preferred alias for get_uuids_needing_processing with generic photo IDs."""
    return get_uuids_needing_processing(photo_ids, options, search_by_lr_uuid)


def process_image_task(
    image_triplets: list[tuple[bytes, str, str, str | None]], options: dict
) -> tuple[int, int, list[str]]:
    """
    Process a batch of images for indexing.

    Args:
        image_triplets: List of (image_bytes, uuid, filename, lr_uuid) tuples
        options: Dictionary with all processing options

    Returns:
        Tuple of (success_count, failure_count, error_messages, warnings)
    """
    success_count = 0
    failure_count = 0
    error_messages = []
    warnings = []
    total_images = len(image_triplets)

    from server_lifecycle import GLOBAL_CANCEL_EVENT

    if GLOBAL_CANCEL_EVENT.is_set():
        error_messages.append("Batch canceled by watchdog.")
        return 0, total_images, error_messages, warnings

    try:
        global_opts = options[0] if isinstance(options, list) else options
        provider = global_opts.get("provider")
        model_name = global_opts.get("model")
        replace_ss = global_opts.get("replace_ss", False)
        regenerate_metadata = global_opts.get("regenerate_metadata", True)
        compute_embeddings = global_opts.get("compute_embeddings", True)
        compute_metadata = global_opts.get("compute_metadata", False)
        compute_faces = global_opts.get("compute_faces", False)

        logger.info(f"Starting batch processing of {total_images} images...")
        logger.info(
            f"regenerate_metadata={regenerate_metadata}, compute_embeddings={compute_embeddings}, "
            f"compute_metadata={compute_metadata}, compute_faces={compute_faces}"
        )

        # Check existing records if regenerate_metadata is False
        existing_records = {}
        if not regenerate_metadata:
            logger.info(
                "Checking existing records to determine what needs generation..."
            )
            uuids_to_check = [uuid for _, uuid, _, _ in image_triplets]
            try:
                # Use bulk get query to dramatically reduce DB latency
                raw = chroma_service.collection.get(
                    ids=uuids_to_check, include=["metadatas"]
                )
                if raw and raw.get("ids"):
                    for idx, pid in enumerate(raw["ids"]):
                        metas = raw.get("metadatas") or [{}] * len(raw["ids"])
                        meta = metas[idx] if idx < len(metas) else {}
                        existing_records[pid] = meta
            except Exception as e:
                logger.warning(f"Bulk ChromaDB get failed: {e}")

        # Determine what actually needs to be computed for each image.
        # Sets (not lists) because downstream code does `uuid in ...` membership
        # checks inside per-image loops — O(1) vs O(n).
        images_needing_embeddings = set()
        images_needing_metadata = set()

        for idx, (_, uuid, _, _) in enumerate(image_triplets):
            is_existing = uuid in existing_records
            existing = existing_records.get(uuid, {})

            # Check if embedding is needed
            needs_embedding = compute_embeddings and (
                regenerate_metadata
                or not is_existing
                or existing.get("has_embedding", True) is False
            )
            if needs_embedding:
                images_needing_embeddings.add(uuid)

            # Check if metadata is needed
            has_any_metadata = (
                existing.get("title")
                or existing.get("caption")
                or existing.get("alt_text")
                or existing.get("keywords")
            )
            needs_metadata = compute_metadata and (
                regenerate_metadata or not has_any_metadata
            )
            if existing and compute_metadata:
                logger.info(
                    f"UUID {uuid}: has_metadata={has_any_metadata}, regenerate={regenerate_metadata}, needs_metadata={needs_metadata}"
                )
                logger.info(
                    f"  Existing fields: title={bool(existing.get('title'))}, caption={bool(existing.get('caption'))}, "
                    f"alt_text={bool(existing.get('alt_text'))}, keywords={bool(existing.get('keywords'))}"
                )
            if needs_metadata:
                images_needing_metadata.add(uuid)

        logger.info(
            f"Generation needed: {len(images_needing_embeddings)} embeddings, "
            f"{len(images_needing_metadata)} metadata"
        )

        # If nothing needs to be generated and we're not regenerating, skip work.
        # When regenerate_metadata is True we must not early-return: new images (no entry yet)
        # still need to be added to Chroma with at least minimal metadata.
        # Also do NOT early-return when compute_faces is True - we need to process images.
        if (
            not regenerate_metadata
            and len(images_needing_embeddings) == 0
            and len(images_needing_metadata) == 0
        ):
            logger.info(
                "No generation required (regenerate_metadata=False and all fields present). Returning success without changes."
            )
            return len(image_triplets), 0, [], []

        analysis_service = get_analysis_service()
        siglip_model = None
        siglip_processor = None

        if not compute_embeddings:
            logger.info(
                "Embeddings disabled (LLM Only path); actively unloading SigLIP2 to free memory."
            )
            server_lifecycle.unload_model()

        if len(images_needing_embeddings) > 0:
            siglip_model = server_lifecycle.get_model()
            siglip_processor = server_lifecycle.get_processor()

        # Pre-extract EXIF location data for each image (always, when available).
        # Keyed by uuid so it can be passed to analyze_batch for per-image injection.
        # Decode each JPEG to a single PIL.Image up front; downstream helpers
        # (culling, phash, CLIP, face detection) all reuse it instead of decoding
        # the same bytes 4–5 times per photo.
        exif_location_by_uuid: dict[str, dict | None] = {}
        for image_bytes, uuid, filename, lr_uuid in image_triplets:
            try:
                exif_location_by_uuid[uuid] = exif_service.extract_location_tags(
                    image_bytes
                )
            except Exception as exc:
                logger.debug("Could not extract EXIF location for %s: %s", uuid, exc)
                exif_location_by_uuid[uuid] = None

        # Decode each image to PIL concurrently
        with ThreadPoolExecutor(max_workers=min(32, len(image_triplets))) as executor:
            pil_images = list(
                executor.map(
                    _decode_image, [img_bytes for img_bytes, _, _, _ in image_triplets]
                )
            )

        blurred_image_triplets = image_triplets

        # Leave audit trail for indexing thumbnails if enabled
        from services.audit import log_diagnostic_image

        for i, (img_bytes, uid, fname, lr_uuid) in enumerate(blurred_image_triplets):
            if uid in images_needing_metadata:
                opt = options[i] if isinstance(options, list) else options
                if str(opt.get("audit_llm_inputs", "")).lower() == "true":
                    log_diagnostic_image(
                        img_bytes,
                        "indexing",
                        fname,
                        output_dir=opt.get("audit_llm_inputs_path"),
                    )

        # 2. SigLIP2 & LLM via analyze_batch
        try:
            embeddings, metadata_results = analysis_service.analyze_batch(
                blurred_image_triplets,
                options,
                siglip_model,
                siglip_processor,
                images_needing_embeddings,
                images_needing_metadata,
                exif_location_by_uuid or None,
                pil_images,  # Reuse the decoded images (potentially blurred)
            )
        except Exception as e:
            logger.error(f"Error in analyze_batch: {str(e)}", exc_info=True)
            error_messages.append(str(e))
            return 0, total_images, error_messages, warnings

        for i, (image_bytes, uuid, filename, lr_uuid) in enumerate(image_triplets):
            try:
                embedding = embeddings[i] if embeddings is not None else None
                metadata_data = metadata_results[i] if metadata_results else None

                existing = existing_records.get(uuid, {})

                need_embedding = uuid in images_needing_embeddings
                need_metadata = uuid in images_needing_metadata

                # Validate that required new data was generated if needed
                if need_embedding and embedding is None:
                    logger.error(f"Embedding generation failed for {uuid}. Skipping.")
                    error_messages.append(f"{filename}: Embedding generation failed")
                    failure_count += 1
                    continue

                if need_metadata and (not metadata_data or not metadata_data.success):
                    error_txt = (
                        metadata_data.error
                        if metadata_data and metadata_data.error
                        else "Unknown error"
                    )
                    logger.error(
                        f"Metadata generation failed for {uuid}. Reason: {error_txt}"
                    )
                    error_messages.append(f"{filename}: {error_txt}")
                    failure_count += 1
                    continue

                if metadata_data and metadata_data.warning:
                    warnings.append(f"{filename}: {metadata_data.warning}")

                # If nothing needed for this UUID (already complete) and no face processing, skip
                # When compute_faces is True we must not skip - we need to reach face detection
                if not need_embedding and not need_metadata and not regenerate_metadata:
                    logger.info(f"UUID {uuid}: already fully indexed; skipping update.")
                    success_count += 1
                    continue

                # Start with existing metadata if not regenerating
                if not regenerate_metadata and existing:
                    main_metadata = existing.copy()
                    # Update only basic fields that should always be current
                    main_metadata["filename"] = filename
                    main_metadata["photo_id"] = uuid
                    main_metadata["uuid"] = lr_uuid or existing.get("uuid", uuid)
                else:
                    main_metadata = {
                        "filename": filename,
                        "photo_id": uuid,
                        "uuid": lr_uuid or uuid,
                        "provider": provider,
                        "model": model_name,
                    }

                # Prefer explicit capture_time from Lightroom catalog (if provided).
                # `date_time_unix` is a seconds-since-epoch float, `date_time` is
                # an ISO/W3C string kept for backwards compatibility.
                capture_time = None
                opt = options[i] if isinstance(options, list) else options
                catalog_time_unix = opt.get("date_time_unix")
                if catalog_time_unix is not None:
                    try:
                        capture_time = float(catalog_time_unix)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Invalid date_time_unix value for %s: %r",
                            uuid,
                            catalog_time_unix,
                        )
                elif opt.get("date_time"):
                    from datetime import datetime, timezone

                    dt_str = opt["date_time"]
                    try:
                        # Normalize common W3C/ISO forms (e.g. trailing 'Z').
                        normalized = str(dt_str).strip()
                        if normalized.endswith("Z"):
                            normalized = normalized[:-1] + "+00:00"
                        dt_obj = datetime.fromisoformat(normalized)
                        if dt_obj.tzinfo is None:
                            dt_obj = dt_obj.replace(tzinfo=timezone.utc)
                        capture_time = float(dt_obj.timestamp())
                    except Exception as e:
                        logger.warning(
                            "Could not parse date_time for %s: %r (%s)", uuid, dt_str, e
                        )

                if capture_time is not None:
                    main_metadata["capture_time"] = capture_time

                if opt.get("camera_profile"):
                    main_metadata["camera_profile"] = str(opt["camera_profile"])[:128]
                if opt.get("camera_model"):
                    main_metadata["camera_model"] = str(opt["camera_model"])[:64]
                if opt.get("camera_make"):
                    main_metadata["camera_make"] = str(opt["camera_make"])[:64]
                if opt.get("rating") is not None:
                    try:
                        main_metadata["rating"] = int(opt["rating"])
                    except (TypeError, ValueError):
                        main_metadata["rating"] = 0
                if opt.get("pick_status") is not None:
                    try:
                        main_metadata["pick_status"] = int(opt["pick_status"])
                    except (TypeError, ValueError):
                        main_metadata["pick_status"] = 0
                if opt.get("is_edited") is not None:
                    main_metadata["is_edited"] = bool(opt["is_edited"])

                # Update metadata fields if newly generated
                if metadata_data and metadata_data.success:
                    if metadata_data.title:
                        main_metadata["title"] = metadata_data.title
                    if metadata_data.caption:
                        main_metadata["caption"] = metadata_data.caption
                    if metadata_data.alt_text:
                        main_metadata["alt_text"] = metadata_data.alt_text
                    if metadata_data.keywords:
                        main_metadata["keywords"] = json.dumps(metadata_data.keywords)
                        # logger.debug(f"UUID {uuid}: keywords JSON data: {main_metadata['keywords']}")
                        main_metadata["flattened_keywords"] = _flatten_keywords(
                            metadata_data.keywords
                        )
                    if not main_metadata.get("provider"):
                        main_metadata["provider"] = provider
                    if not main_metadata.get("model"):
                        main_metadata["model"] = model_name

                main_metadata["run_date"] = time.now().strftime("%Y-%m-%d %H:%M:%S")

                # Update embedding status
                if embedding is not None:
                    main_metadata["has_embedding"] = True
                    try:
                        scene_tags = training_service.compute_scene_tags(embedding)
                        if scene_tags:
                            main_metadata["scene_tags"] = json.dumps(
                                scene_tags, ensure_ascii=False
                            )
                    except Exception as exc:
                        logger.debug(
                            "Failed to compute zero-shot scene tags during indexing: %s",
                            exc,
                        )
                elif existing and existing.get("has_embedding", False):
                    # Preserve existing embedding - we didn't generate a new one (e.g. only faces)
                    main_metadata["has_embedding"] = True
                else:
                    main_metadata["has_embedding"] = False

                if replace_ss:
                    for key, value in main_metadata.items():
                        if isinstance(value, str):
                            main_metadata[key] = value.replace("ß", "ss")

                # Determine if we need to update the embedding
                # Only update embedding if we generated a new one
                update_embedding = embedding if embedding is not None else None

                if existing and not regenerate_metadata:
                    logger.info(
                        f"UUID {uuid} already exists. Updating (embedding: {update_embedding is not None})."
                    )
                    try:
                        chroma_service.update_image(
                            uuid,
                            main_metadata,
                            embedding=update_embedding,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to update image {uuid} in ChromaDB: {e}",
                            exc_info=True,
                        )
                        error_messages.append(
                            f"{filename}: Database update failed: {str(e)}"
                        )
                        failure_count += 1
                        continue
                elif regenerate_metadata:
                    logger.info(
                        f"UUID {uuid} set to regenerate. Updating (embedding: {update_embedding is not None})."
                    )
                    existing_in_chroma = chroma_service.get_image(uuid)
                    try:
                        if existing_in_chroma and existing_in_chroma.get("ids"):
                            chroma_service.update_image(
                                uuid,
                                main_metadata,
                                embedding=update_embedding,
                            )
                        else:
                            chroma_service.add_image(uuid, embedding, main_metadata)
                    except Exception as e:
                        logger.error(
                            f"Failed to regenerate image {uuid} in ChromaDB: {e}",
                            exc_info=True,
                        )
                        error_messages.append(
                            f"{filename}: Database update failed: {str(e)}"
                        )
                        failure_count += 1
                        continue
                else:
                    # New record
                    if embedding is not None:
                        logger.info(f"UUID {uuid} is new. Indexing with embeddings.")
                    else:
                        logger.info(
                            f"UUID {uuid} is new. Indexing metadata-only entry (no embedding)."
                        )
                    try:
                        chroma_service.add_image(uuid, embedding, main_metadata)
                    except Exception as e:
                        logger.error(
                            f"Failed to add image {uuid} to ChromaDB: {e}",
                            exc_info=True,
                        )
                        error_messages.append(
                            f"{filename}: Database indexing failed: {str(e)}"
                        )
                        failure_count += 1
                        continue

                success_count += 1

            except Exception as e:
                logger.error(f"Error processing image {uuid}: {str(e)}", exc_info=True)
                error_messages.append(f"{filename}: {str(e)}")
                failure_count += 1

        return success_count, failure_count, error_messages, warnings
    except DatabaseNotReadyError as e:
        logger.warning(f"Batch processing aborted: {str(e)}")
        error_messages.append(str(e))
        return 0, total_images, error_messages, warnings
    except Exception as e:
        logger.error(f"Error during batch processing task: {str(e)}", exc_info=True)
        error_messages.append(f"Batch processing error: {str(e)}")
        return 0, total_images, error_messages, warnings
    finally:
        # Free MPS allocator cache between requests. PyTorch holds onto freed
        # blocks aggressively on Apple silicon, which combined with InsightFace
        # and ChromaDB is enough to trip macOS jetsam on smaller-RAM machines.
        if TORCH_DEVICE == "mps":
            try:
                torch.mps.empty_cache()
            except Exception:
                pass

        # Explicitly close Pillow images to instantly free unmanaged C-level memory buffers
        # Without this, iterating through 7200 photos will leak memory while waiting on GC
        if "pil_images" in locals() and pil_images:
            for img in pil_images:
                try:
                    img.close()
                except Exception:
                    pass

        gc.collect()
