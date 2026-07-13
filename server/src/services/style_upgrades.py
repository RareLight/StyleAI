"""Active Style Upgrade Assistant service.

Identifies and prioritizes candidate photos from the user's semantic index
to help upgrade their styles to higher ML tiers (Pillar 2: Supervised PLS at N=15,
and Pillar 3: Elastic Net at N=50). Uses Pillar 1 burst deduplication,
Farthest Point Sampling (Max-Min Diversity), and user-aligned Hero Quality Scoring.
"""

import logging
import math
import re
import threading
import time
from typing import Any

import numpy as np
from services import chroma as chroma_service
from services import style_catalog
from services import style_grouping

logger = logging.getLogger("styleai")

_recs_cache: dict[str, Any] = {}
_recs_cache_timestamp: float = 0.0
_recs_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300.0  # 5 minutes safety fallback


def invalidate_upgrade_recommendations_cache() -> None:
    """Invalidate cached style upgrade recommendations."""
    global _recs_cache, _recs_cache_timestamp
    with _recs_cache_lock:
        _recs_cache.clear()
        _recs_cache_timestamp = 0.0
    logger.debug("Style upgrade recommendations cache invalidated.")


def _hero_score(meta: dict[str, Any]) -> float:
    """Compute hero shot quality score based on user ratings and fallback heuristics."""
    score = 0.0
    try:
        rating = int(meta.get("rating", 0) or 0)
    except (TypeError, ValueError):
        rating = 0

    if rating > 0:
        # Give 0.6 to 3.0 points for 1 to 5 stars
        score += (rating / 5.0) * 3.0
    else:
        # Fall back to pick status and edit complexity
        try:
            pick = int(meta.get("pick_status", 0) or 0)
        except (TypeError, ValueError):
            pick = 0

        if pick == 1:
            score += 1.5  # Picked flag gets 1.5 points
        elif pick == -1:
            score -= 3.0  # Rejected flag gets strong penalty

    if meta.get("is_edited", False) or meta.get("has_develop_settings", False):
        score += 1.0

    return score


def _normalize_profile_for_comparison(profile: str) -> str:
    if not profile:
        return ""
    from services import style_grouping as grouping

    return grouping._profile_name(profile).lower()


def _profiles_compatible(style_profile: str, photo_profile: str) -> bool:
    if not style_profile or not photo_profile:
        return False
    from services import style_grouping as grouping

    return (
        grouping._profile_name(style_profile).lower()
        == grouping._profile_name(photo_profile).lower()
    )


def _models_compatible(style_model: str, photo_model: str) -> bool:
    if not style_model or not photo_model:
        return False
    m_style = re.sub(r"[^a-zA-Z0-9]", "", style_model).lower()
    m_photo = re.sub(r"[^a-zA-Z0-9]", "", photo_model).lower()
    return m_style == m_photo


def _is_stitched_panorama(meta: dict[str, Any]) -> bool:
    """Check if a photo is a stitched panorama delegating to unified style_grouping."""
    from services import style_grouping

    return style_grouping.is_stitched_panorama(meta)


def _check_genre_mismatch(style_genre: str, p_genre: str, meta: dict[str, Any]) -> bool:
    """Check if the candidate photo's primary editing genre conflicts with the style's genre."""
    from services import style_grouping

    if style_grouping.is_stitched_panorama(meta):
        return True

    if not p_genre or p_genre in ("scene_unknown", "scene_general"):
        p_genre = style_grouping.classify_photo_genre(meta, None) or "scene_unknown"

    if not style_genre or style_genre in ("scene_unknown", "scene_general"):
        return False
    if not p_genre or p_genre in ("scene_unknown", "scene_general"):
        return False
    return p_genre != style_genre


def _select_style_recommendations(
    candidates: list[tuple[str, Any, dict[str, Any]]],
    existing_embeddings: list[Any],
    target_count: int,
    genre_centroid: np.ndarray | None = None,
) -> list[str]:
    """Select up to target_count candidate photos using Burst Deduplication and Maximal Marginal Relevance (MMR) Farthest Point Sampling."""
    if not candidates or target_count <= 0:
        return []

    valid_existing = [
        np.asarray(e, dtype=np.float32)
        for e in existing_embeddings
        if e is not None and len(e) > 0
    ]

    # Step 1: Burst clustering & deduplication (Δt <= 10s and cosine similarity >= 0.95)
    # Group candidates by burst cluster and keep only the highest hero quality shot per burst
    burst_clusters: list[list[tuple[str, np.ndarray, dict[str, Any], float]]] = []
    for pid, emb, meta in candidates:
        arr = np.asarray(emb, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        h_score = _hero_score(meta)
        cap_time = 0.0
        try:
            raw_time = meta.get("capture_time") or meta.get("dateTimeOriginal") or 0
            cap_time = float(raw_time)
        except (TypeError, ValueError):
            cap_time = 0.0

        matched_cluster = False
        for cluster in burst_clusters:
            rep_pid, rep_emb, rep_meta, rep_h = cluster[0]
            sim = float(np.dot(arr, rep_emb))
            rep_time = 0.0
            try:
                rep_time = float(
                    rep_meta.get("capture_time")
                    or rep_meta.get("dateTimeOriginal")
                    or 0
                )
            except (TypeError, ValueError):
                rep_time = 0.0
            if (
                cap_time > 0
                and rep_time > 0
                and abs(cap_time - rep_time) <= 10.0
                and sim >= 0.95
            ):
                cluster.append((pid, arr, meta, h_score))
                matched_cluster = True
                break
            elif (cap_time == 0.0 or rep_time == 0.0) and sim >= 0.99:
                cluster.append((pid, arr, meta, h_score))
                matched_cluster = True
                break
        if not matched_cluster:
            burst_clusters.append([(pid, arr, meta, h_score)])

    deduped_candidates: list[tuple[str, np.ndarray, dict[str, Any], float]] = []
    for cluster in burst_clusters:
        best_item = max(cluster, key=lambda item: item[3])
        deduped_candidates.append(best_item)

    # Step 2: Compute relevance scores against existing style distribution or genre centroid
    scored_pool: list[tuple[float, str, np.ndarray, dict[str, Any]]] = []

    if valid_existing:
        E_mat = np.array(valid_existing, dtype=np.float32)
        if E_mat.ndim == 1:
            E_mat = E_mat.reshape(1, -1)

        # Adaptive similarity gating (outlier prevention)
        if len(E_mat) >= 3:
            centroid = np.mean(E_mat, axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 0:
                centroid = centroid / norm
            internal_sims = np.dot(E_mat, centroid)
            mu_sim = float(np.mean(internal_sims))
            sigma_sim = float(np.std(internal_sims))
            sim_floor = max(0.58, mu_sim - 2.5 * sigma_sim)
        else:
            sim_floor = 0.60

        C_mat = np.array([c[1] for c in deduped_candidates], dtype=np.float32)
        if C_mat.ndim == 1:
            C_mat = C_mat.reshape(1, -1)
        sim_matrix = C_mat @ E_mat.T
        max_sims = np.max(sim_matrix, axis=1)

        for i, (pid, emb, meta, h_score) in enumerate(deduped_candidates):
            max_sim = float(max_sims[i])
            if max_sim < sim_floor:
                continue
            relevance = max_sim + 0.10 * h_score
            scored_pool.append((relevance, pid, emb, meta))
    else:
        for pid, emb, meta, h_score in deduped_candidates:
            if genre_centroid is not None and len(genre_centroid) > 0:
                sim = float(np.dot(genre_centroid, emb))
                if sim < 0.58:
                    continue
                relevance = sim + 0.10 * h_score
            else:
                relevance = h_score
            scored_pool.append((relevance, pid, emb, meta))

    if not scored_pool:
        return []

    # Step 3: Maximal Marginal Relevance (MMR) Farthest Point Sampling for diversity across visual space
    selected_ids: list[str] = []
    selected_embs: list[np.ndarray] = []
    remaining = list(scored_pool)
    lambda_param = 0.85  # 85% relevance to signature style, 15% visual diversity

    while remaining and len(selected_ids) < target_count:
        best_idx = -1
        best_mmr = -1e9

        for i, (rel, pid, emb, meta) in enumerate(remaining):
            if not selected_embs:
                mmr_score = rel
            else:
                sel_mat = np.array(selected_embs, dtype=np.float32)
                if sel_mat.ndim == 1:
                    sel_mat = sel_mat.reshape(1, -1)
                max_sim_to_selected = float(np.max(sel_mat @ emb))
                if max_sim_to_selected > 0.90:
                    continue
                mmr_score = (
                    lambda_param * rel - (1.0 - lambda_param) * max_sim_to_selected
                )

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx == -1:
            break

        rel, chosen_pid, chosen_emb, _ = remaining.pop(best_idx)
        selected_ids.append(chosen_pid)
        selected_embs.append(chosen_emb)

    return selected_ids


_farthest_point_sampling = _select_style_recommendations


def get_style_upgrade_recommendations(
    catalog_ids: list[str] | None = None,
    top_styles_limit: int = 100,
) -> dict[str, Any]:
    """Generate candidate photo recommendations to upgrade styles to higher ML tiers.

    Returns:
        A dict containing a list of styles with their current tiers, needed counts,
        and recommended candidate photo_ids.
    """
    global _recs_cache, _recs_cache_timestamp
    cache_key = f"{catalog_ids}:{top_styles_limit}"
    with _recs_cache_lock:
        if (
            _recs_cache_timestamp > 0
            and (time.time() - _recs_cache_timestamp) < _CACHE_TTL_SECONDS
        ):
            if cache_key in _recs_cache:
                logger.debug("Returning cached style upgrade recommendations.")
                return _recs_cache[cache_key]

    logger.info(
        "Generating style upgrade recommendations (limit=%d)...", top_styles_limit
    )
    try:
        all_styles = style_catalog.list_styles()
    except Exception as e:
        logger.error(
            f"Failed to list styles for upgrade recommendations: {e}", exc_info=True
        )
        return {"styles": []}

    style_genre_map = {
        s.get("style_id", ""): (s.get("genre") or "").strip() for s in all_styles
    }

    # Filter out fully upgraded styles (N >= 50) since they don't need upgrades
    styles = [s for s in all_styles if int(s.get("example_count", 0)) < 50]

    # Sort styles in ascending order by how many photos are needed to reach the next level!
    def sort_key(s: dict[str, Any]) -> int:
        count = int(s.get("example_count", 0))
        if count < 15:
            return 15 - count
        else:
            return 50 - count

    styles.sort(key=sort_key)
    if top_styles_limit > 0:
        styles = styles[:top_styles_limit]

    # Batch pre-fetch all style examples once outside the loop
    all_style_examples_map: dict[str, set[str]] = {}
    try:
        conn = style_catalog._ensure_initialized()
        rows = conn.execute("SELECT style_id, photo_id FROM style_examples").fetchall()
        for r in rows:
            all_style_examples_map.setdefault(r["style_id"], set()).add(r["photo_id"])
    except Exception as e:
        logger.warning(f"Failed to batch pre-fetch style examples: {e}", exc_info=True)

    # =========================================================================
    # PASS 1: Pre-fetch ALL metadata (fast) and filter candidates by profile
    # =========================================================================
    chroma_service._ensure_initialized()
    photos_by_norm_profile: dict[
        str, list[tuple[str, dict[str, Any], str, str, float]]
    ] = {}

    if chroma_service.collection is not None:
        try:
            res = chroma_service.collection.get(include=["metadatas"], limit=50_000)
            p_ids = res.get("ids")
            if p_ids is None:
                p_ids = []
            p_metas = res.get("metadatas")
            if p_metas is None:
                p_metas = []
            for i, pid in enumerate(p_ids):
                meta = dict(p_metas[i]) if i < len(p_metas) and p_metas[i] else {}
                if not meta.get("has_embedding", True):
                    continue
                if style_grouping.is_stitched_panorama(meta):
                    continue
                p_genre = (
                    style_grouping.classify_photo_genre(meta, None) or "scene_unknown"
                )
                photo_profile = (meta.get("camera_profile") or "").strip()
                norm_profile = style_grouping._profile_name(photo_profile).lower()
                photo_model = (meta.get("camera_model") or "").strip()
                norm_model = re.sub(r"[^a-zA-Z0-9]", "", photo_model).lower()
                h_score = _hero_score(meta)

                photos_by_norm_profile.setdefault(norm_profile, []).append(
                    (pid, meta, p_genre, norm_model, h_score)
                )

            for bucket in photos_by_norm_profile.values():
                bucket.sort(key=lambda x: x[4], reverse=True)
        except Exception as e:
            logger.warning(
                f"Failed to pre-fetch image metadata pool: {e}", exc_info=True
            )

    needed_photo_ids: set[str] = set()
    already_recommended_pids: set[str] = set()
    style_prelim_candidates_map: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    for style in styles:
        style_id = style.get("style_id", "")
        current_count = int(style.get("example_count", 0))
        camera_profile = (style.get("camera_profile") or "Default").strip()
        camera_model = (style.get("camera_model") or "").strip()
        genre = (style.get("genre") or "").strip()

        norm_style_profile = style_grouping._profile_name(camera_profile).lower()
        norm_style_model = re.sub(r"[^a-zA-Z0-9]", "", camera_model).lower()
        is_hdr_style = "HDR" in camera_profile

        # Add existing examples to the fetch list so we can compute centroids/E_mat
        existing_ids = all_style_examples_map.get(style_id, set())
        needed_photo_ids.update(existing_ids)

        candidate_pool = list(photos_by_norm_profile.get(norm_style_profile, []))
        if norm_style_profile != "default":
            if "" in photos_by_norm_profile:
                candidate_pool.extend(photos_by_norm_profile[""])
            if "default" in photos_by_norm_profile:
                candidate_pool.extend(photos_by_norm_profile["default"])

        candidates = []

        for pid, meta, p_genre, norm_photo_model, _ in candidate_pool:
            if pid in existing_ids or pid in already_recommended_pids:
                continue

            photo_profile = (meta.get("camera_profile") or "").strip()
            if (
                not photo_profile
                or style_grouping._profile_name(photo_profile).lower() == "default"
            ):
                if (
                    camera_profile != "Default"
                    and norm_style_model
                    and norm_photo_model
                ):
                    if norm_style_model != norm_photo_model:
                        continue
                if is_hdr_style:
                    continue

            is_compat, _ = style_grouping.is_genre_compatible(genre, p_genre)
            if is_compat:
                candidates.append((pid, meta))
                needed_photo_ids.add(pid)
                if len(candidates) >= 250:
                    break

        style_prelim_candidates_map[style_id] = candidates

    # =========================================================================
    # PASS 2: Fetch only the necessary embeddings (lazy, minimal memory footprint)
    # =========================================================================
    pool_emb_map: dict[str, np.ndarray] = {}
    if chroma_service.collection is not None and needed_photo_ids:
        try:
            needed_list = list(needed_photo_ids)
            chunk_size = 5000
            for i in range(0, len(needed_list), chunk_size):
                chunk = needed_list[i : i + chunk_size]
                res = chroma_service.collection.get(ids=chunk, include=["embeddings"])
                e_ids = res.get("ids")
                if e_ids is None:
                    e_ids = []
                e_embs = res.get("embeddings")
                if e_embs is None:
                    e_embs = []
                for j, pid in enumerate(e_ids):
                    if j < len(e_embs) and e_embs[j] is not None:
                        arr = np.asarray(e_embs[j], dtype=np.float32)
                        norm = float(np.linalg.norm(arr))
                        if norm > 0:
                            pool_emb_map[pid] = arr / norm
        except Exception as e:
            logger.warning(
                f"Failed to fetch lazy image embeddings pool: {e}", exc_info=True
            )

    # Compute genre_centroids exclusively from established training examples
    genre_emb_lists: dict[str, list[np.ndarray]] = {}
    for style_id, existing_ids in all_style_examples_map.items():
        style_genre = style_genre_map.get(style_id, "")
        if style_genre and style_genre != "scene_unknown":
            for pid in existing_ids:
                if pid in pool_emb_map:
                    genre_emb_lists.setdefault(style_genre, []).append(
                        pool_emb_map[pid]
                    )

    genre_centroids: dict[str, np.ndarray] = {}
    for g, embs in genre_emb_lists.items():
        c = np.mean(embs, axis=0)
        norm = float(np.linalg.norm(c))
        if norm > 0:
            genre_centroids[g] = c / norm

    # =========================================================================
    # PASS 3: Generate recommendations per style
    # =========================================================================
    results_list: list[dict[str, Any]] = []

    for style in styles:
        style_id = style.get("style_id", "")
        style_name = style.get("style_name", "Unknown Style")
        current_count = int(style.get("example_count", 0))
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
        prelim_candidates_tuples = style_prelim_candidates_map.get(style_id, [])

        if needed_count > 0 and (
            prelim_candidates_tuples or all_style_examples_map.get(style_id, set())
        ):
            # Buffer pool size = 1.5 * needed_count rounded up (capped at 100)
            target_recs = min(100, math.ceil(1.5 * needed_count))

            # Get existing training examples for this style from pre-fetched map
            existing_ids = all_style_examples_map.get(style_id, set())

            # Get embeddings for existing examples
            existing_embeddings: list[Any] = [
                pool_emb_map[ex_id] for ex_id in existing_ids if ex_id in pool_emb_map
            ]

            E_mat = None
            if existing_embeddings:
                E_mat = np.array(existing_embeddings, dtype=np.float32)
                if E_mat.ndim == 1:
                    E_mat = E_mat.reshape(1, -1)

            # Hydrate candidates with embeddings
            prelim_candidates: list[tuple[str, Any, dict[str, Any]]] = []
            for pid, meta in prelim_candidates_tuples:
                if pid in pool_emb_map and pid not in already_recommended_pids:
                    prelim_candidates.append((pid, pool_emb_map[pid], meta))

            valid_candidates: list[tuple[str, Any, dict[str, Any]]] = []
            if E_mat is not None and len(E_mat) > 0 and prelim_candidates:
                C_mat = np.array([c[1] for c in prelim_candidates], dtype=np.float32)
                if C_mat.ndim == 1:
                    C_mat = C_mat.reshape(1, -1)
                sim_matrix = C_mat @ E_mat.T
                max_sims = np.max(sim_matrix, axis=1)

                for i, (pid, emb, meta) in enumerate(prelim_candidates):
                    max_sim = float(max_sims[i])
                    # Reject exact duplicates / burst shots
                    if (1.0 - max_sim) <= 0.05:
                        continue
                    # Reject candidate if it is visually/semantically unrelated to the style
                    if max_sim < 0.45:
                        continue
                    valid_candidates.append((pid, emb, meta))
            else:
                centroid = genre_centroids.get(genre)
                for pid, emb, meta in prelim_candidates:
                    if centroid is not None and len(centroid) > 0:
                        sim = float(np.dot(centroid, emb))
                        if sim < 0.45:
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

                    sim = float(np.dot(emb_a, emb_b))
                    if (1.0 - sim) <= 0.05:
                        cluster.append((pid_b, emb_b, meta_b))
                        clustered_pids.add(pid_b)

                # Pick hero shot with highest hero score in cluster
                best_hero = max(cluster, key=lambda c: _hero_score(c[2]))
                surviving_heroes.append(best_hero)

            # Step C: Separate edited vs unedited and select recommendations by visual similarity and hero quality
            edited_pool = [c for c in surviving_heroes if c[2].get("is_edited", False)]
            unedited_pool = [
                c for c in surviving_heroes if not c[2].get("is_edited", False)
            ]

            selected_edited = _select_style_recommendations(
                edited_pool,
                existing_embeddings,
                target_recs,
                genre_centroid=genre_centroids.get(genre),
            )
            recommended_ids.extend(selected_edited)

            remaining_slots = target_recs - len(recommended_ids)
            if remaining_slots > 0 and unedited_pool:
                # Update existing embeddings with newly selected edited embeddings
                updated_existing_embs = list(existing_embeddings)
                for pid, emb, _ in edited_pool:
                    if pid in selected_edited:
                        updated_existing_embs.append(emb)
                selected_unedited = _select_style_recommendations(
                    unedited_pool,
                    updated_existing_embs,
                    remaining_slots,
                    genre_centroid=genre_centroids.get(genre),
                )
                recommended_ids.extend(selected_unedited)

            already_recommended_pids.update(recommended_ids)

        meta_map = {pid: meta for pid, meta in prelim_candidates_tuples}
        recommended_objects = [
            {
                "globalPhotoId": pid,
                "lr_uuid": meta_map.get(pid, {}).get("uuid")
                or meta_map.get(pid, {}).get("lr_uuid")
                or "",
            }
            for pid in recommended_ids
        ]

        results_list.append(
            {
                "style_id": style_id,
                "style_name": style_name,
                "camera_profile": camera_profile,
                "genre": genre,
                "current_count": current_count,
                "target_tier": target_tier,
                "needed_count": needed_count,
                "recommended_photo_ids": recommended_objects,
                "is_highest_tier": is_highest_tier,
            }
        )

    res = {"styles": results_list}
    with _recs_cache_lock:
        _recs_cache[cache_key] = res
        _recs_cache_timestamp = time.time()
    return res
