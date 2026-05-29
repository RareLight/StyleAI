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
from datetime import datetime
from typing import Any

from config import logger
from services import style_grouping as grouping
from services import training as training_service

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS styles (
    style_id            TEXT PRIMARY KEY,
    style_name          TEXT NOT NULL,
    camera_make         TEXT,
    camera_model        TEXT,
    camera_profile      TEXT,
    genre               TEXT NOT NULL,
    subgenre            TEXT,
    description         TEXT,
    example_count       INTEGER DEFAULT 0,
    mean_exposure_dna   TEXT,           -- JSON
    scene_distribution  TEXT,           -- JSON
    develop_variance    TEXT,           -- JSON
    confidence_threshold REAL DEFAULT 0.45,
    created_at          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS style_examples (
    style_id            TEXT NOT NULL,
    photo_id            TEXT NOT NULL,
    PRIMARY KEY (style_id, photo_id),
    FOREIGN KEY (style_id) REFERENCES styles(style_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS style_migration_log (
    migration_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    migrated_at         TEXT,
    source_examples     INTEGER,
    styles_created      INTEGER,
    status              TEXT
);
"""

# ---------------------------------------------------------------------------
# Lazy init
# ---------------------------------------------------------------------------

_db_path: str | None = None
_connection: sqlite3.Connection | None = None


def _get_db_file() -> str:
    """Return the absolute path to the styles SQLite file."""
    import config

    if not config.DB_PATH:
        raise RuntimeError("DB_PATH not set — cannot locate style catalog")
    return os.path.join(config.DB_PATH, "styles.sqlite")


def _ensure_initialized() -> sqlite3.Connection:
    """Lazy-init the SQLite connection + schema."""
    global _db_path, _connection

    db_file = _get_db_file()
    if _connection is not None and _db_path == db_file:
        return _connection

    logger.info("Initialising style catalog SQLite at %s", db_file)
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    conn = sqlite3.connect(db_file, check_same_thread=False)
    conn.executescript(SCHEMA_SQL)
    conn.row_factory = sqlite3.Row
    # Migrate: ensure camera_profile column exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(styles)")]
    if "camera_profile" not in cols:
        conn.execute("ALTER TABLE styles ADD COLUMN camera_profile TEXT")
        conn.commit()
    _connection = conn
    _db_path = db_file
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
    conn.execute("DELETE FROM styles WHERE style_id = ?", (style_id,))
    conn.execute("DELETE FROM style_examples WHERE style_id = ?", (style_id,))

    conn.execute(
        """
        INSERT INTO styles (
            style_id, style_name, camera_make, camera_model, camera_profile, genre, subgenre,
            description, example_count, mean_exposure_dna, scene_distribution,
            develop_variance, confidence_threshold, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            style_id,
            style.get("style_name", ""),
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

    conn.commit()
    logger.info(
        "Upserted style %s with %d examples", style_id, style.get("example_count", 0)
    )


def delete_style(style_id: str) -> bool:
    """Remove a style and its example links. Returns True if it existed."""
    conn = _ensure_initialized()
    cur = conn.execute("DELETE FROM styles WHERE style_id = ?", (style_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        logger.info("Deleted style %s", style_id)
    return deleted


def get_style(style_id: str) -> dict[str, Any] | None:
    """Fetch a single style by id, or None."""
    conn = _ensure_initialized()
    row = conn.execute(
        "SELECT * FROM styles WHERE style_id = ?", (style_id,)
    ).fetchone()
    if not row:
        return None
    style = _row_to_dict(row)
    # Attach example photo_ids
    rows = conn.execute(
        "SELECT photo_id FROM style_examples WHERE style_id = ?", (style_id,)
    ).fetchall()
    style["example_photo_ids"] = [r["photo_id"] for r in rows]
    return style


def list_styles() -> list[dict[str, Any]]:
    """Return all styles, ordered by name."""
    conn = _ensure_initialized()
    rows = conn.execute("SELECT * FROM styles ORDER BY style_name").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_style_examples(style_id: str) -> list[dict[str, Any]]:
    """Return the full training-example dicts linked to a style."""
    conn = _ensure_initialized()
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
    return [by_id[pid] for pid in photo_ids if pid in by_id]


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

    # 2. Group by (camera_make, camera_model, camera_profile, primary_genre)
    groups = grouping.group_examples_by_camera_genre(rich_examples)

    created_styles: list[dict[str, Any]] = []

    for (camera_make, camera_model, camera_profile, genre), group_ex in groups.items():
        if len(group_ex) < 2:
            continue  # Need at least 2 examples for a meaningful style

        # 3. Subgenre splitting
        subgroups = grouping.split_subgenres(group_ex)

        for sg in subgroups:
            subgenre = sg["subgenre"]
            profile = sg.get("camera_profile") or camera_profile
            style_name = grouping.generate_style_name(
                camera_model, genre, subgenre, camera_profile=profile
            )
            style_id = _slugify(style_name)

            # Ensure uniqueness
            existing = get_style(style_id)
            suffix = 1
            original_id = style_id
            while existing:
                style_id = f"{original_id}-{suffix}"
                existing = get_style(style_id)
                suffix += 1

            style = {
                "style_id": style_id,
                "style_name": style_name,
                "camera_make": camera_make,
                "camera_model": camera_model,
                "camera_profile": profile,
                "genre": genre,
                "subgenre": subgenre,
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
            created_styles.append(style)

    logger.info(
        "Discovered %d styles from %d examples", len(created_styles), len(examples)
    )
    return created_styles


def _fetch_rich_examples(photo_ids: list[str]) -> list[dict[str, Any]]:
    """Pull full metadata from the training collection for a list of photo_ids."""
    try:
        result = training_service._training_collection.get(
            ids=photo_ids, include=["metadatas"]
        )
    except Exception as exc:
        logger.warning("Failed to fetch rich training metadata: %s", exc)
        return []

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    examples = []
    for i, pid in enumerate(ids):
        meta = dict(metadatas[i]) if i < len(metadatas) else {}
        meta["photo_id"] = pid
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
        camera_exact  = 1.0 if exact match, 0.5 if same make, 0.0 otherwise
        profile_exact = 1.0 if exact profile match, 0.7 if same model different profile
        genre_exact   = 1.0 if exact, 0.7 if related
        keywords      = bonus for keyword overlap
        exposure      = 0.25 weight on exposure DNA proximity

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

        # Camera component (40%)
        cam_score = 0.0
        if camera_model and style.get("camera_model"):
            if camera_model.strip().lower() == style["camera_model"].strip().lower():
                cam_score = 1.0
                # Profile bonus/penalty within same camera model
                style_profile = (style.get("camera_profile") or "").strip()
                if profile and style_profile:
                    if profile.lower() == style_profile.lower():
                        cam_score = 1.0  # exact camera + profile
                    else:
                        cam_score = 0.7  # same camera, different profile
            elif camera_make and style.get("camera_make"):
                if camera_make.strip().lower() == style["camera_make"].strip().lower():
                    cam_score = 0.5
        score += 0.40 * cam_score

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
    conn = _ensure_initialized()

    # User keywords take precedence over AI scene tags for genre
    primary_genre = grouping._primary_genre_with_keywords(
        scene_tags, user_keywords or []
    )
    cam = (camera_model or "unknown").strip()
    profile = (camera_profile or "default").strip()

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
