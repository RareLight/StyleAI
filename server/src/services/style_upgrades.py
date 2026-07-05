"""Active Style Upgrade Assistant service.

Identifies and prioritizes candidate photos from the user's semantic index
to help upgrade their styles to higher ML tiers (Pillar 2: Supervised PLS at N=15,
and Pillar 3: Elastic Net at N=50). Uses Pillar 1 burst deduplication,
Farthest Point Sampling (Max-Min Diversity), and user-aligned Hero Quality Scoring.
"""

import logging
from typing import Any

import numpy as np
from services import chroma as chroma_service
from services import style_catalog

logger = logging.getLogger("styleai")


def _hero_score(meta: dict[str, Any]) -> float:
    """Compute hero shot quality score based on user ratings and fallback heuristics."""
    score = 0.0
    try:
        rating = int(meta.get("rating", 0) or 0)
    except (TypeError, ValueError):
        rating = 0

    if rating > 0:
        # Give 0.4 to 2.0 points for 1 to 5 stars
        score += (rating / 5.0) * 2.0
    else:
        # Fall back to pick status and edit complexity
        try:
            pick = int(meta.get("pick_status", 0) or 0)
        except (TypeError, ValueError):
            pick = 0

        if pick == 1:
            score += 1.0  # Picked flag gets 1 point
        elif pick == -1:
            score -= 2.0  # Rejected flag gets penalty

    if meta.get("is_edited", False):
        score += 0.5

    return score


def _farthest_point_sampling(
    candidates: list[tuple[str, Any, dict[str, Any]]],
    existing_embeddings: list[Any],
    target_count: int,
) -> list[str]:
    """Select up to target_count candidates using Farthest Point Sampling (Max-Min Diversity)."""
    if not candidates or target_count <= 0:
        return []

    selected_ids: list[str] = []
    selected_embs: list[np.ndarray] = [
        np.asarray(e, dtype=np.float32)
        for e in existing_embeddings
        if e is not None and len(e) > 0
    ]

    pool = list(candidates)

    while len(selected_ids) < target_count and pool:
        best_idx = -1
        best_score = -1e9

        for idx, (pid, emb_raw, meta) in enumerate(pool):
            emb = np.asarray(emb_raw, dtype=np.float32)
            if emb.size == 0 or np.allclose(emb, 0.0):
                continue

            if not selected_embs:
                # If no existing embeddings, prioritize by hero score
                d_min = 1.0
            else:
                # Calculate min distance to any selected/existing embedding
                d_min = 1e9
                norm_c = np.linalg.norm(emb)
                if norm_c == 0.0:
                    continue
                for s_emb in selected_embs:
                    norm_s = np.linalg.norm(s_emb)
                    if norm_s == 0.0:
                        continue
                    sim = float(np.dot(emb, s_emb) / (norm_c * norm_s))
                    sim = max(-1.0, min(1.0, sim))
                    dist = 1.0 - sim
                    if dist < d_min:
                        d_min = dist

            h_score = _hero_score(meta)
            # Combine Max-Min diversity distance with hero score
            diversity_score = d_min + 0.05 * h_score

            if diversity_score > best_score:
                best_score = diversity_score
                best_idx = idx

        if best_idx == -1:
            break

        chosen_pid, chosen_emb, _ = pool.pop(best_idx)
        selected_ids.append(chosen_pid)
        chosen_arr = np.asarray(chosen_emb, dtype=np.float32)
        if chosen_arr.size > 0 and not np.allclose(chosen_arr, 0.0):
            selected_embs.append(chosen_arr)

    return selected_ids


def get_style_upgrade_recommendations(
    catalog_ids: list[str] | None = None,
    top_styles_limit: int = 15,
) -> dict[str, Any]:
    """Generate candidate photo recommendations to upgrade styles to higher ML tiers.

    Returns:
        A dict containing a list of styles with their current tiers, needed counts,
        and recommended candidate photo_ids.
    """
    logger.info(
        "Generating style upgrade recommendations (limit=%d)...", top_styles_limit
    )
    try:
        styles = style_catalog.list_styles()
    except Exception as e:
        logger.error(
            f"Failed to list styles for upgrade recommendations: {e}", exc_info=True
        )
        return {"styles": []}

    # Sort styles: prioritize styles that are closest to upgrading to the next tier!
    # E.g., N=14 (needs 1 for PLS) or N=48 (needs 2 for Elastic Net) come first.
    def sort_key(s: dict[str, Any]) -> tuple[int, int]:
        count = int(s.get("example_count", 0))
        if count < 15:
            return (0, 15 - count)  # Closest to 15 first
        elif count < 50:
            return (1, 50 - count)  # Closest to 50 second
        else:
            return (2, 0)  # Already at highest tier last

    styles.sort(key=sort_key)
    if top_styles_limit > 0:
        styles = styles[:top_styles_limit]

    # Pre-fetch all photos from ChromaDB image_embeddings once to avoid repeated DB calls
    chroma_service._ensure_initialized()
    all_photos_pool: list[tuple[str, Any, dict[str, Any]]] = []
    if chroma_service.collection is not None:
        try:
            res = chroma_service.collection.get(
                include=["embeddings", "metadatas"], limit=50_000
            )
            p_ids = res.get("ids") or []
            p_embs = res.get("embeddings") or []
            p_metas = res.get("metadatas") or []
            for i, pid in enumerate(p_ids):
                emb = p_embs[i] if i < len(p_embs) else None
                meta = dict(p_metas[i]) if i < len(p_metas) and p_metas[i] else {}
                if meta.get("has_embedding", True) and emb is not None:
                    all_photos_pool.append((pid, emb, meta))
        except Exception as e:
            logger.warning(
                f"Failed to pre-fetch image embeddings pool: {e}", exc_info=True
            )

    results_list: list[dict[str, Any]] = []

    for style in styles:
        style_id = style.get("style_id", "")
        style_name = style.get("style_name", "Unknown Style")
        current_count = int(style.get("example_count", 0))
        camera_profile = (style.get("camera_profile") or "Default").strip()
        camera_model = (style.get("camera_model") or "").strip()
        genre = (style.get("genre") or "").strip()

        if current_count < 15:
            target_tier = "Pillar 2 Supervised PLS (15 samples)"
            needed_count = 15 - current_count
            is_highest_tier = False
        elif current_count < 50:
            target_tier = "Pillar 3 Elastic Net (50 samples)"
            needed_count = 50 - current_count
            is_highest_tier = False
        else:
            target_tier = "Pillar 3 Elastic Net (Highest Tier)"
            needed_count = 0
            is_highest_tier = True

        recommended_ids: list[str] = []

        if needed_count > 0 and all_photos_pool:
            # Buffer pool size = 2 * needed_count (capped at 100)
            target_recs = min(100, 2 * needed_count)

            # Get existing training examples for this style
            existing_examples = style_catalog.get_style_examples(style_id)
            existing_ids = {ex["photo_id"] for ex in existing_examples}

            # Get embeddings for existing examples
            existing_embeddings: list[Any] = []
            for ex_id in existing_ids:
                try:
                    img_data = chroma_service.get_image(ex_id)
                    if img_data and img_data.get("embeddings"):
                        existing_embeddings.append(img_data["embeddings"][0])
                except Exception:
                    pass

            # Filter candidates from pool
            is_hdr_style = "HDR" in camera_profile
            valid_candidates: list[tuple[str, Any, dict[str, Any]]] = []

            for pid, emb, meta in all_photos_pool:
                if pid in existing_ids:
                    continue

                photo_profile = (meta.get("camera_profile") or "").strip()
                photo_model = (meta.get("camera_model") or "").strip()

                if photo_profile:
                    # Strict profile matching when available
                    if photo_profile != camera_profile:
                        continue
                else:
                    # Legacy fallback: check model and HDR compatibility
                    if camera_profile != "Default" and photo_model and camera_model:
                        if photo_model != camera_model:
                            continue
                    if is_hdr_style:
                        continue

                # Step A: Burst deduplication against existing training examples
                is_burst_dup = False
                for ex_emb in existing_embeddings:
                    dist = chroma_service._cosine_distance(emb, ex_emb)
                    if dist is not None and dist <= 0.05:
                        is_burst_dup = True
                        break
                if is_burst_dup:
                    continue

                valid_candidates.append((pid, emb, meta))

            # Step B: Within candidate pool, cluster bursts and pick hero shots
            surviving_heroes: list[tuple[str, Any, dict[str, Any]]] = []
            clustered_pids: set[str] = set()

            for i, (pid_a, emb_a, meta_a) in enumerate(valid_candidates):
                if pid_a in clustered_pids:
                    continue

                cluster = [(pid_a, emb_a, meta_a)]
                clustered_pids.add(pid_a)

                time_a = meta_a.get("capture_time")

                for j in range(i + 1, len(valid_candidates)):
                    pid_b, emb_b, meta_b = valid_candidates[j]
                    if pid_b in clustered_pids:
                        continue

                    dist = chroma_service._cosine_distance(emb_a, emb_b)
                    if dist is not None and dist <= 0.05:
                        # Check time delta if timestamps available
                        time_b = meta_b.get("capture_time")
                        if time_a is not None and time_b is not None:
                            try:
                                if abs(float(time_a) - float(time_b)) <= 10.0:
                                    cluster.append((pid_b, emb_b, meta_b))
                                    clustered_pids.add(pid_b)
                            except (TypeError, ValueError):
                                cluster.append((pid_b, emb_b, meta_b))
                                clustered_pids.add(pid_b)
                        else:
                            cluster.append((pid_b, emb_b, meta_b))
                            clustered_pids.add(pid_b)

                # Pick hero shot with highest hero score in cluster
                best_hero = max(cluster, key=lambda c: _hero_score(c[2]))
                surviving_heroes.append(best_hero)

            # Step C: Separate edited vs unedited and run Farthest Point Sampling
            edited_pool = [c for c in surviving_heroes if c[2].get("is_edited", False)]
            unedited_pool = [
                c for c in surviving_heroes if not c[2].get("is_edited", False)
            ]

            selected_edited = _farthest_point_sampling(
                edited_pool, existing_embeddings, target_recs
            )
            recommended_ids.extend(selected_edited)

            remaining_slots = target_recs - len(recommended_ids)
            if remaining_slots > 0 and unedited_pool:
                # Update existing embeddings with newly selected edited embeddings
                updated_existing_embs = list(existing_embeddings)
                for pid, emb, _ in edited_pool:
                    if pid in selected_edited:
                        updated_existing_embs.append(emb)
                selected_unedited = _farthest_point_sampling(
                    unedited_pool, updated_existing_embs, remaining_slots
                )
                recommended_ids.extend(selected_unedited)

        results_list.append(
            {
                "style_id": style_id,
                "style_name": style_name,
                "camera_profile": camera_profile,
                "genre": genre,
                "current_count": current_count,
                "target_tier": target_tier,
                "needed_count": needed_count,
                "recommended_photo_ids": recommended_ids,
                "is_highest_tier": is_highest_tier,
            }
        )

    return {"styles": results_list}
