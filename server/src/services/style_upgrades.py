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

    # Prepare normalized candidate matrix C_mat
    raw_embs = [np.asarray(c[1], dtype=np.float32) for c in candidates]
    C_mat = np.array(raw_embs, dtype=np.float32)
    if C_mat.ndim == 1:
        C_mat = C_mat.reshape(1, -1)
    norms = np.linalg.norm(C_mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    C_mat = C_mat / norms

    # Prepare hero scores
    h_scores = np.array([_hero_score(c[2]) for c in candidates], dtype=np.float32)

    # Prepare existing normalized embeddings matrix E_mat
    valid_existing = [
        np.asarray(e, dtype=np.float32)
        for e in existing_embeddings
        if e is not None and len(e) > 0
    ]
    if valid_existing:
        E_mat = np.array(valid_existing, dtype=np.float32)
        if E_mat.ndim == 1:
            E_mat = E_mat.reshape(1, -1)
        e_norms = np.linalg.norm(E_mat, axis=1, keepdims=True)
        e_norms[e_norms == 0.0] = 1.0
        E_mat = E_mat / e_norms
        # Compute min distances from each candidate to any existing embedding
        sims = np.dot(C_mat, E_mat.T)
        sims = np.clip(sims, -1.0, 1.0)
        min_dists = 1.0 - np.max(sims, axis=1)
    else:
        min_dists = np.ones(len(candidates), dtype=np.float32)

    selected_ids: list[str] = []
    selected_indices: set[int] = set()

    for _ in range(target_count):
        # Combine distance with hero score
        div_scores = min_dists + 0.05 * h_scores
        # Mask out already selected
        for idx in selected_indices:
            div_scores[idx] = -1e9

        best_idx = int(np.argmax(div_scores))
        if div_scores[best_idx] <= -1e8:
            break

        selected_indices.add(best_idx)
        selected_ids.append(candidates[best_idx][0])

        if len(selected_ids) == target_count:
            break

        # Update min_dists with distance to the newly chosen embedding
        chosen_vec = C_mat[best_idx]
        new_sims = np.dot(C_mat, chosen_vec)
        new_sims = np.clip(new_sims, -1.0, 1.0)
        new_dists = 1.0 - new_sims
        min_dists = np.minimum(min_dists, new_dists)

    return selected_ids


def get_style_upgrade_recommendations(
    catalog_ids: list[str] | None = None,
    top_styles_limit: int = 100,
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

    # Filter out fully upgraded styles (N >= 50) since they don't need upgrades
    styles = [s for s in styles if int(s.get("example_count", 0)) < 50]

    # Sort styles in ascending order by how many photos are needed to reach the next level!
    # E.g., N=14 (needs 1) or N=48 (needs 2) come first before N=5 (needs 10) or N=20 (needs 30).
    def sort_key(s: dict[str, Any]) -> int:
        count = int(s.get("example_count", 0))
        if count < 15:
            return 15 - count
        else:
            return 50 - count

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
            p_ids = res.get("ids")
            if p_ids is None:
                p_ids = []
            p_embs = res.get("embeddings")
            if p_embs is None:
                p_embs = []
            p_metas = res.get("metadatas")
            if p_metas is None:
                p_metas = []
            for i, pid in enumerate(p_ids):
                emb = p_embs[i] if i < len(p_embs) else None
                meta = dict(p_metas[i]) if i < len(p_metas) and p_metas[i] else {}
                if meta.get("has_embedding", True) and emb is not None:
                    all_photos_pool.append((pid, emb, meta))
        except Exception as e:
            logger.warning(
                f"Failed to pre-fetch image embeddings pool: {e}", exc_info=True
            )

    pool_emb_map = {pid: emb for pid, emb, _ in all_photos_pool}
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
            existing_embeddings: list[Any] = [
                pool_emb_map[ex_id] for ex_id in existing_ids if ex_id in pool_emb_map
            ]
            missing_ex_ids = existing_ids - set(pool_emb_map.keys())
            if missing_ex_ids:
                for ex_id in missing_ex_ids:
                    try:
                        img_data = chroma_service.get_image(ex_id)
                        if (
                            img_data
                            and img_data.get("embeddings") is not None
                            and len(img_data["embeddings"]) > 0
                        ):
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

            # Step B: Within candidate pool, sort by capture time and cluster bursts
            valid_candidates.sort(key=lambda c: (c[2].get("capture_time") or 0.0, c[0]))
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

                    time_b = meta_b.get("capture_time")
                    if time_a is not None and time_b is not None:
                        try:
                            if float(time_b) - float(time_a) > 10.0:
                                break
                        except (TypeError, ValueError):
                            pass
                    elif j - i > 10:
                        break

                    dist = chroma_service._cosine_distance(emb_a, emb_b)
                    if dist is not None and dist <= 0.05:
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
