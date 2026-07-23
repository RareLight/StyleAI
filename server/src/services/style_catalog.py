"""Style Catalog service.

Manages a SQLite-backed catalog of auto-discovered editing styles.
Each style is a collection of training examples grouped by camera model
and genre, optionally split into subgenres by develop-variance.

Responsibilities
----------------
- Schema init / migrations
- CRUD for style profiles
- Auto-discovery from training examples
- Style matching for target photos
- Per-style + global reset
- JSON export / import for portability
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from config import logger
from services import style_grouping as grouping
from services import training as training_service

CURRENT_GROUPING_RULE_VERSION = "8"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Schema is now managed by server/src/migrations/versions/001_initial_schema.py

# ---------------------------------------------------------------------------
# Lazy init
# ---------------------------------------------------------------------------

_db_path: str | None = None
_local = threading.local()
_schema_init_lock = threading.Lock()
_schema_initialized = False


def _get_db_file() -> str:
    """Return the absolute path to the styles SQLite file."""
    import config

    if not config.DB_PATH:
        raise RuntimeError("DB_PATH not set — cannot locate style catalog")
    return os.path.join(config.DB_PATH, "styles.sqlite")


def _ensure_initialized() -> sqlite3.Connection:
    """Lazy-init the SQLite connection + schema per thread."""
    global _db_path, _schema_initialized

    db_file = _get_db_file()

    conn = getattr(_local, "connection", None)
    if conn is not None and _db_path == db_file:
        return conn

    # Only run migrations once across all threads, or if db path changes (e.g. in tests)
    with _schema_init_lock:
        if not _schema_initialized or _db_path != db_file:
            logger.info("Initialising style catalog SQLite at %s", db_file)
            os.makedirs(os.path.dirname(db_file), exist_ok=True)

            from core.migrations import run_migrations

            try:
                run_migrations(os.path.dirname(db_file))
            except Exception as e:
                logger.error(f"Failed to run migrations for style catalog: {e}")

            _db_path = db_file
            _schema_initialized = True

    # Open a new connection for this thread
    conn = sqlite3.connect(db_file, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    _local.connection = conn

    try:
        ver_row = conn.execute(
            "SELECT rule_value FROM grouping_rule_state WHERE rule_key = 'GROUPING_RULE_VERSION'"
        ).fetchone()
        db_ver = str(ver_row["rule_value"]) if ver_row else "0"
        if db_ver != CURRENT_GROUPING_RULE_VERSION:
            grouping.clear_semantic_genre_cache()
            conn.execute(
                "INSERT OR REPLACE INTO grouping_rule_state (rule_key, rule_value, updated_at) VALUES ('GROUPING_RULE_VERSION', ?, datetime('now'))",
                (CURRENT_GROUPING_RULE_VERSION,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO grouping_rule_state (rule_key, rule_value, updated_at) VALUES ('NEEDS_REDISCOVERY', '1', datetime('now'))"
            )
            conn.commit()

        row = conn.execute(
            "SELECT rule_value FROM grouping_rule_state WHERE rule_key = 'NEEDS_REDISCOVERY'"
        ).fetchone()
        if row and str(row["rule_value"]) == "1":
            conn.execute(
                "UPDATE grouping_rule_state SET rule_value = '0', updated_at = datetime('now') WHERE rule_key = 'NEEDS_REDISCOVERY'"
            )
            conn.commit()
            logger.info("Automatic post-migration rediscovery triggered.")
            try:
                grouping.clear_semantic_genre_cache()
                discover_styles_from_examples(None)
            except Exception as e:
                logger.warning("Post-migration rediscovery failed: %s", e)
    except Exception:
        pass

    return conn


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slugify(text: str) -> str:
    """Create a URL-safe style_id slug."""
    safe = text.lower().replace(" ", "-").replace("_", "-")
    safe = "".join(c for c in safe if c.isalnum() or c == "-")
    return safe.strip("-")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("mean_exposure_dna", "scene_distribution", "develop_variance"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key] = {}
    return d


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def upsert_style(style: dict[str, Any]) -> None:
    """Insert or overwrite a style profile and its example junctions."""
    conn = _ensure_initialized()

    style_id = style["style_id"]
    with conn:
        # Priority 8 / Rename Style: Retrieve existing user_style_name if present
        existing_row = conn.execute(
            "SELECT user_style_name FROM styles WHERE style_id = ?", (style_id,)
        ).fetchone()
        existing_user_name = existing_row["user_style_name"] if existing_row else None

        conn.execute("DELETE FROM styles WHERE style_id = ?", (style_id,))
        conn.execute("DELETE FROM style_examples WHERE style_id = ?", (style_id,))

        conn.execute(
            """
            INSERT INTO styles (
                style_id, style_name, user_style_name, camera_make, camera_model, camera_profile, genre, subgenre,
                description, example_count, mean_exposure_dna, scene_distribution,
                develop_variance, confidence_threshold, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                style_id,
                style.get("style_name", ""),
                style.get("user_style_name", existing_user_name),
                style.get("camera_make"),
                style.get("camera_model"),
                style.get("camera_profile"),
                style.get("genre", ""),
                style.get("subgenre"),
                style.get("description", ""),
                style.get("example_count", 0),
                json.dumps(style.get("mean_exposure_dna", {}), ensure_ascii=False),
                json.dumps(style.get("scene_distribution", {}), ensure_ascii=False),
                json.dumps(style.get("develop_variance", {}), ensure_ascii=False),
                style.get("confidence_threshold", 0.45),
                style.get("created_at", _now()),
                _now(),
            ),
        )

        for photo_id in style.get("example_photo_ids", []):
            conn.execute(
                "INSERT INTO style_examples (style_id, photo_id) VALUES (?, ?)",
                (style_id, photo_id),
            )

    logger.info(
        "Upserted style %s with %d examples", style_id, style.get("example_count", 0)
    )
    try:
        from services import style_upgrades

        style_upgrades.invalidate_upgrade_recommendations_cache()
    except Exception:
        pass


def delete_style(style_id: str) -> bool:
    """Remove a style and its example links. Returns True if it existed."""
    conn = _ensure_initialized()
    with conn:
        cur = conn.execute("DELETE FROM styles WHERE style_id = ?", (style_id,))

    deleted = cur.rowcount > 0
    if deleted:
        logger.info("Deleted style %s", style_id)
        try:
            from services import style_upgrades

            style_upgrades.invalidate_upgrade_recommendations_cache()
        except Exception:
            pass
    return deleted


def get_style(style_id: str) -> dict[str, Any] | None:
    """Fetch a single style by id, or None."""
    conn = _ensure_initialized()
    row = conn.execute(
        "SELECT * FROM styles WHERE style_id = ?", (style_id,)
    ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    # Apply display name override if custom name is present
    if d.get("user_style_name"):
        d["style_name"] = d["user_style_name"]

    # Attach example photo_ids
    rows = conn.execute(
        "SELECT photo_id FROM style_examples WHERE style_id = ?", (style_id,)
    ).fetchall()
    d["example_photo_ids"] = [r["photo_id"] for r in rows]
    return d


def list_styles() -> list[dict[str, Any]]:
    """Return all styles, ordered by name."""
    conn = _ensure_initialized()
    rows = conn.execute("SELECT * FROM styles ORDER BY style_name").fetchall()

    results = []
    for r in rows:
        d = _row_to_dict(r)
        if d.get("user_style_name"):
            d["style_name"] = d["user_style_name"]
        results.append(d)

    return results


def _filter_style_examples_by_genre(
    style_genre: str, examples: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Lightweight view-time filter for style training examples.

    Enforces that training examples attached to a style are not stitched panoramas
    and are semantically compatible with the style's canonical genre.
    """
    from services import style_grouping

    clean = []
    for ex in examples:
        if style_grouping.is_stitched_panorama(ex):
            continue
        p_genre = style_grouping.classify_photo_genre(ex, None)
        if p_genre and style_genre:
            compat, ambiguous = style_grouping.is_genre_compatible(style_genre, p_genre)
            if not compat and not ambiguous:
                continue
        clean.append(ex)
    return clean


def get_all_styles_with_examples() -> list[dict[str, Any]]:
    """Return all styles with their associated example_photo_ids attached."""
    styles = list_styles()
    conn = _ensure_initialized()
    rows = conn.execute("SELECT style_id, photo_id FROM style_examples").fetchall()

    all_examples = training_service.list_training_examples()
    by_id = {ex["photo_id"]: ex for ex in all_examples}

    # Group raw photo_ids by style_id
    raw_map: dict[str, list[str]] = {}
    for r in rows:
        raw_map.setdefault(r["style_id"], []).append(r["photo_id"])

    examples_map = {}
    for s in styles:
        sid = s["style_id"]
        s_genre = s.get("genre", "")
        raw_pids = raw_map.get(sid, [])
        raw_examples = [by_id[pid] for pid in raw_pids if pid in by_id]
        clean_examples = _filter_style_examples_by_genre(s_genre, raw_examples)
        examples_map[sid] = [
            {
                "globalPhotoId": ex["photo_id"],
                "lr_uuid": ex.get("lr_uuid") or ex.get("uuid") or "",
            }
            for ex in clean_examples
        ]

    for s in styles:
        s["examples"] = examples_map.get(s["style_id"], [])

    return styles


def get_style_examples(style_id: str) -> list[dict[str, Any]]:
    """Return the full training-example dicts linked to a style."""
    conn = _ensure_initialized()
    style_row = conn.execute(
        "SELECT genre FROM styles WHERE style_id = ?", (style_id,)
    ).fetchone()
    s_genre = style_row["genre"] if style_row else ""

    photo_ids = [
        r["photo_id"]
        for r in conn.execute(
            "SELECT photo_id FROM style_examples WHERE style_id = ?", (style_id,)
        ).fetchall()
    ]
    if not photo_ids:
        return []

    # Fetch from training service
    all_examples = training_service.list_training_examples()
    by_id = {ex["photo_id"]: ex for ex in all_examples}
    raw_examples = [by_id[pid] for pid in photo_ids if pid in by_id]
    return _filter_style_examples_by_genre(s_genre, raw_examples)


def get_style_recipe(style_id: str) -> dict[str, Any]:
    """Return the mean develop recipe for a style.

    Computes the average of canonical settings across all examples
    linked to the style.  Returns {} if no examples exist.
    """
    examples = get_style_examples(style_id)
    if not examples:
        return {}

    settings_list: list[dict[str, float]] = []
    for ex in examples:
        raw = ex.get("canonical_settings", "{}")
        try:
            settings = json.loads(raw) if isinstance(raw, str) else dict(raw)
            settings_list.append(
                {
                    k: float(v)
                    for k, v in settings.items()
                    if isinstance(v, (int, float))
                }
            )
        except (ValueError, TypeError):
            continue

    if not settings_list:
        return {}

    # Compute mean for each key
    keys = set()
    for s in settings_list:
        keys.update(s.keys())

    mean_settings: dict[str, float] = {}
    for key in keys:
        vals = [s[key] for s in settings_list if key in s]
        if vals:
            mean_settings[key] = round(sum(vals) / len(vals), 4)

    return mean_settings


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------


def discover_styles_from_examples(
    photo_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Auto-discover style profiles from training examples.

    If *photo_ids* is None, uses all training examples in the collection.
    Returns the list of newly created/updated style dicts.
    """
    _ensure_initialized()
    grouping.clear_semantic_genre_cache()

    # 1. Gather examples
    all_examples = training_service.list_training_examples()
    if not all_examples:
        logger.info("No training examples found — nothing to discover.")
        return []

    if photo_ids:
        by_id = {ex["photo_id"]: ex for ex in all_examples}
        examples = [by_id[pid] for pid in photo_ids if pid in by_id]
    else:
        examples = all_examples

    # Need richer metadata than list_training_examples() provides.
    # Pull full metadatas from ChromaDB.
    rich_examples = _fetch_rich_examples([ex["photo_id"] for ex in examples])

    # 2. Group by (camera_profile, primary_genre)
    groups = grouping.group_examples_by_profile_genre(rich_examples)

    created_styles: list[dict[str, Any]] = []

    for (camera_profile, genre), group_ex in groups.items():
        if len(group_ex) < 2:
            continue  # Need at least 2 examples for a meaningful style

        # 3. Aggregate all examples into a single style group
        # We bypass subgenre splitting because Predictive ML needs pooled data
        sg = grouping._build_subgroup(group_ex, subgenre=None)

        profile = camera_profile
        clean_genre = grouping.generate_style_name(genre, None)
        style_name = clean_genre
        if profile and str(profile) != "Default" and str(profile) not in style_name:
            style_name = f"{style_name} • {profile}"
        if "HDR" in str(profile) and "HDR" not in style_name:
            style_name = f"{style_name} (HDR)"

        style_id = _slugify(f"{profile}_{genre}")

        style = {
            "style_id": style_id,
            "style_name": style_name,
            "camera_make": "",
            "camera_model": "",
            "camera_profile": profile,
            "genre": genre,
            "subgenre": "",
            "description": grouping.generate_style_description(
                sg["mean_develop_settings"],
                genre,
                sg["scene_distribution"],
                camera_profile=profile,
            ),
            "example_count": len(sg["example_photo_ids"]),
            "mean_exposure_dna": sg["mean_exposure_dna"],
            "scene_distribution": sg["scene_distribution"],
            "develop_variance": sg["variance"],
            "example_photo_ids": sg["example_photo_ids"],
            "confidence_threshold": 0.45,
            "created_at": _now(),
        }

        upsert_style(style)
        # Update the training examples in ChromaDB with the new style label and description
        try:
            training_service.update_training_example_labels(
                photo_ids=sg["example_photo_ids"],
                label=style_name,
                summary=style["description"],
            )
        except Exception as exc:
            logger.warning(
                f"Failed to propagate style labels to training examples: {exc}"
            )

        created_styles.append(style)

    if photo_ids is None:
        active_style_ids = {s["style_id"] for s in created_styles}
        for old_s in list_styles():
            if old_s["style_id"] not in active_style_ids:
                logger.info(
                    "Removing outdated/orphaned style %s (genre: %s)",
                    old_s["style_id"],
                    old_s.get("genre"),
                )
                delete_style(old_s["style_id"])

    logger.info(
        "Discovered %d styles from %d examples", len(created_styles), len(examples)
    )

    def _run_post_discovery_bg_tasks():
        try:
            from services import style_summary

            style_summary.summarize_catalog_styles()
        except Exception as exc:
            logger.warning(f"Failed to generate catalog style summaries: {exc}")
        try:
            from services import predictive_engine

            predictive_engine.train_style_models()
        except Exception as exc:
            logger.warning(f"Failed to train predictive ML models: {exc}")

    import threading

    threading.Thread(
        target=_run_post_discovery_bg_tasks,
        name="StyleDiscoveryBG",
        daemon=True,
    ).start()

    return created_styles


def _fetch_rich_examples(photo_ids: list[str]) -> list[dict[str, Any]]:
    """Pull full metadata and embeddings from the training collection for a list of photo_ids."""
    try:
        result = training_service._training_collection.get(
            ids=photo_ids, include=["metadatas", "embeddings"]
        )
    except Exception as exc:
        logger.warning("Failed to fetch rich training metadata: %s", exc)
        return []

    ids = result.get("ids")
    ids = ids if ids is not None else []
    metadatas = result.get("metadatas")
    metadatas = metadatas if metadatas is not None else []
    embeddings = result.get("embeddings")
    embeddings = embeddings if embeddings is not None else []
    training_service._enrich_and_sync_metadatas_from_main_index(ids, metadatas)
    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        meta["photo_id"] = pid
        meta["embedding"] = embeddings[i] if i < len(embeddings) else None
        examples.append(meta)
    return examples


# ---------------------------------------------------------------------------
# Style matching
# ---------------------------------------------------------------------------


def find_matching_styles(
    camera_make: str | None,
    camera_model: str | None,
    scene_tags: list[str],
    exposure_metrics: dict[str, float] | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
    top_k: int = 3,
) -> list[tuple[dict[str, Any], float]]:
    """Find the best-matching style(s) for a target photo.

    Scoring:
        profile_exact = 1.0 if exact profile match, 0.0 otherwise
        genre_exact   = 1.0 if exact, 0.7 if related
        keywords      = bonus for keyword overlap
        exposure      = 0.25 weight on exposure DNA proximity

    Penalty:
        If profile_exact == 0.0, the total score is multiplied by 0.4.

    Returns: [(style_dict, confidence), ...] sorted descending.
    """
    from services import style_grouping as grouping

    all_styles = list_styles()
    if not all_styles:
        return []

    # User keywords override AI scene tags for genre classification
    primary_genre = grouping._primary_genre_with_keywords(
        scene_tags, user_keywords or []
    )
    exposure_metrics = exposure_metrics or {}
    profile = (camera_profile or "").strip()
    keywords = set((user_keywords or []))

    scored: list[tuple[dict[str, Any], float]] = []
    for style in all_styles:
        score = 0.0

        # Profile component (40%)
        profile_score = 0.0
        style_profile = (style.get("camera_profile") or "").strip()
        if profile and style_profile:
            if profile.lower() == style_profile.lower():
                profile_score = 1.0
        score += 0.40 * profile_score

        # Genre component (35%)
        genre_score = 0.0
        style_genre = style.get("genre", "")
        if primary_genre == style_genre:
            genre_score = 1.0
        elif primary_genre.replace("scene_", "") in style_genre.replace("scene_", ""):
            genre_score = 0.7
        score += 0.35 * genre_score

        # Keyword overlap bonus (up to +0.10)
        if keywords:
            style_keywords = set(
                grouping._safe_json_loads(style.get("user_keywords"), [])
            )
            if style_keywords:
                overlap = len(keywords & style_keywords) / max(
                    len(keywords), len(style_keywords)
                )
                score += 0.10 * overlap

        # Exposure component (25%)
        exp_score = 0.5  # neutral
        mean_dna = style.get("mean_exposure_dna") or {}
        if mean_dna and exposure_metrics:
            deltas = []
            for key in ("exp_luminance_mean", "exp_contrast", "exp_warmth_proxy"):
                qv = exposure_metrics.get(key)
                sv = mean_dna.get(key)
                if qv is not None and sv is not None:
                    deltas.append(abs(float(qv) - float(sv)))
            if deltas:
                mean_delta = sum(deltas) / len(deltas)
                exp_score = max(0.0, 1.0 - mean_delta / 0.3)
        score += 0.25 * exp_score

        # Apply profile mismatch penalty
        if profile_score == 0.0:
            score *= 0.4

        scored.append((style, round(score, 3)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Reset / migration
# ---------------------------------------------------------------------------


def reset_style(style_id: str) -> bool:
    """Remove a style profile (but leave training examples untouched)."""
    return delete_style(style_id)


def reset_all_styles() -> int:
    """Clear the entire style catalog. Returns number of styles removed."""
    conn = _ensure_initialized()
    cur = conn.execute("SELECT COUNT(*) FROM styles")
    count = cur.fetchone()[0]
    conn.execute("DELETE FROM style_examples")
    conn.execute("DELETE FROM styles")
    conn.execute("DELETE FROM style_migration_log")
    conn.commit()
    logger.info("Cleared all %d styles from catalog", count)
    return count


def migrate_legacy_training() -> dict[str, Any]:
    """One-time migration: re-cluster all existing training examples into styles.

    Returns a summary dict with counts and status.
    """
    conn = _ensure_initialized()

    # Check if already migrated
    row = conn.execute(
        "SELECT * FROM style_migration_log ORDER BY migration_id DESC LIMIT 1"
    ).fetchone()
    if row and row["status"] == "success":
        logger.info("Legacy migration already completed at %s", row["migrated_at"])
        return {"status": "already_migrated", "migrated_at": row["migrated_at"]}

    # Count source examples
    all_examples = training_service.list_training_examples()
    source_count = len(all_examples)

    if source_count == 0:
        conn.execute(
            "INSERT INTO style_migration_log (migrated_at, source_examples, styles_created, status) VALUES (?, ?, ?, ?)",
            (_now(), 0, 0, "skipped"),
        )
        conn.commit()
        return {"status": "skipped", "reason": "no_training_examples"}

    # Clear any existing styles first
    reset_all_styles()

    # Run discovery on all examples
    styles = discover_styles_from_examples()

    conn.execute(
        "INSERT INTO style_migration_log (migrated_at, source_examples, styles_created, status) VALUES (?, ?, ?, ?)",
        (_now(), source_count, len(styles), "success"),
    )
    conn.commit()

    logger.info(
        "Legacy migration complete: %d examples → %d styles",
        source_count,
        len(styles),
    )
    return {
        "status": "success",
        "source_examples": source_count,
        "styles_created": len(styles),
    }


# ---------------------------------------------------------------------------
# Export / Import (JSON)
# ---------------------------------------------------------------------------


def export_styles_json() -> dict[str, Any]:
    """Export the full style catalog as a portable JSON document."""
    styles = list_styles()
    for style in styles:
        style["examples"] = get_style_examples(style["style_id"])
    return {
        "version": "2.0-style-catalog",
        "export_date": datetime.now().isoformat(),
        "styles": styles,
    }


def import_styles_json(data: dict[str, Any], merge: bool = True) -> dict[str, Any]:
    """Import styles from a JSON export.

    Args:
        data: The JSON dict produced by export_styles_json().
        merge: If True, upserts styles without clearing existing ones.
               If False, clears the catalog before importing.

    Returns:
        Summary dict with import counts.
    """
    incoming = data.get("styles", [])
    if not incoming:
        return {"status": "error", "reason": "no_styles_in_data"}

    if not merge:
        reset_all_styles()

    imported = 0
    skipped = 0
    for style in incoming:
        # Ensure required keys
        if not style.get("style_id") or not style.get("style_name"):
            skipped += 1
            continue
        # Sanitize — remove runtime fields we recompute
        for key in ("examples",):
            style.pop(key, None)
        upsert_style(style)
        imported += 1

    logger.info("Imported %d styles (skipped %d)", imported, skipped)
    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Integration hooks for training service
# ---------------------------------------------------------------------------


def rename_style(style_id: str, new_name: str) -> bool:
    """Rename a style by setting its user_style_name."""
    conn = _ensure_initialized()
    with conn:
        cursor = conn.execute(
            "UPDATE styles SET user_style_name = ?, updated_at = ? WHERE style_id = ?",
            (new_name, _now(), style_id),
        )
        return cursor.rowcount > 0


def update_style_for_example(
    photo_id: str,
    camera_make: str | None,
    camera_model: str | None,
    scene_tags: list[str],
    exposure_metrics: dict[str, float] | None = None,
    camera_profile: str | None = None,
    user_keywords: list[str] | None = None,
) -> None:
    """Update the style catalog when a new training example is added.

    If this is the first example for a (camera + profile + genre) combo,
    triggers auto-discovery for that photo.  User keywords override AI
    scene tags for genre classification.
    """
    _ensure_initialized()

    exif_meta = {
        "camera_make": camera_make,
        "camera_model": camera_model,
        "camera_profile": camera_profile,
        "user_keywords": user_keywords,
        "scene_tags": scene_tags,
    }
    if exposure_metrics:
        exif_meta.update(exposure_metrics)
    primary_genre = grouping._primary_genre_with_keywords(
        scene_tags, user_keywords or [], exif_meta
    )
    cam = (camera_model or "unknown").strip()
    profile = grouping._profile_name(camera_profile)

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
        and grouping._profile_name(ex.get("camera_profile")) == profile
        and grouping._primary_genre_with_keywords(
            grouping._safe_json_loads(ex.get("scene_tags"), []),
            grouping._safe_json_loads(ex.get("user_keywords"), []),
            ex,
        )
        == primary_genre
    ]
    if combo_ids:
        discover_styles_from_examples(combo_ids)
