"""Tests for services/style_catalog.py."""

from __future__ import annotations

import pytest

from services import style_catalog as sc
from services import training as training_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_catalog_state(monkeypatch):
    """Reset the lazy SQLite connection before every test."""
    sc._connection = None
    sc._db_path = None


@pytest.fixture
def temp_db_path(tmp_path):
    """Provide a temporary directory to act as DB_PATH."""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def mock_db_path(monkeypatch, temp_db_path):
    """Patch config.DB_PATH to the temporary directory."""
    monkeypatch.setattr("config.DB_PATH", temp_db_path)


@pytest.fixture
def sample_style():
    return {
        "style_id": "nikon-z7-architecture",
        "style_name": "NIKON Z 7 — Architecture",
        "camera_make": "NIKON CORPORATION",
        "camera_model": "NIKON Z 7",
        "camera_profile": "Nikon Z7 Linear",
        "genre": "scene_architecture",
        "subgenre": None,
        "description": "Test description",
        "example_count": 2,
        "mean_exposure_dna": {"exp_luminance_mean": 0.45},
        "scene_distribution": {"scene_architecture": 1.0},
        "develop_variance": {"exposure": 0.01},
        "example_photo_ids": ["photo_1", "photo_2"],
        "confidence_threshold": 0.45,
        "created_at": "2026-01-01 00:00:00",
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_upsert_and_get_style(sample_style):
    sc.upsert_style(sample_style)
    fetched = sc.get_style("nikon-z7-architecture")
    assert fetched is not None
    assert fetched["style_name"] == "NIKON Z 7 — Architecture"
    assert fetched["example_photo_ids"] == ["photo_1", "photo_2"]


def test_list_styles(sample_style):
    sc.upsert_style(sample_style)
    styles = sc.list_styles()
    assert len(styles) == 1
    assert styles[0]["style_id"] == "nikon-z7-architecture"


def test_delete_style(sample_style):
    sc.upsert_style(sample_style)
    assert sc.delete_style("nikon-z7-architecture") is True
    assert sc.get_style("nikon-z7-architecture") is None


def test_delete_nonexistent_style():
    assert sc.delete_style("does-not-exist") is False


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_all_styles(sample_style):
    sc.upsert_style(sample_style)
    count = sc.reset_all_styles()
    assert count == 1
    assert sc.list_styles() == []


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


def test_export_import_roundtrip(sample_style):
    sc.upsert_style(sample_style)
    exported = sc.export_styles_json()
    assert exported["version"] == "2.0-style-catalog"
    assert len(exported["styles"]) == 1

    # Clear and re-import
    sc.reset_all_styles()
    result = sc.import_styles_json(exported, merge=False)
    assert result["status"] == "success"
    assert result["imported"] == 1

    fetched = sc.get_style("nikon-z7-architecture")
    assert fetched is not None
    assert fetched["style_name"] == "NIKON Z 7 — Architecture"


def test_import_invalid_data():
    result = sc.import_styles_json({"styles": []}, merge=False)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Migration status
# ---------------------------------------------------------------------------


def test_migrate_skips_when_no_examples(monkeypatch):
    monkeypatch.setattr(training_service, "list_training_examples", lambda: [])
    result = sc.migrate_legacy_training()
    assert result["status"] == "skipped"


def test_migrate_records_success():
    # We don't have real training examples in the temp DB,
    # so just verify the migration_log table is written.
    conn = sc._ensure_initialized()
    conn.execute(
        "INSERT INTO style_migration_log (migrated_at, source_examples, styles_created, status) VALUES (?, ?, ?, ?)",
        ("2026-01-01", 10, 2, "success"),
    )
    conn.commit()
    result = sc.migrate_legacy_training()
    assert result["status"] == "already_migrated"


# ---------------------------------------------------------------------------
# Style matching
# ---------------------------------------------------------------------------


def test_find_matching_styles_exact_camera_and_genre(sample_style):
    sc.upsert_style(sample_style)
    # Exact camera + profile match should score very high
    matches = sc.find_matching_styles(
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        scene_tags=["scene_architecture"],
        exposure_metrics={
            "exp_lum_mean": 0.45,
            "exp_contrast": 0.30,
            "exp_warmth_proxy": 0.28,
        },
        camera_profile="Nikon Z7 Linear",
    )
    assert len(matches) == 1
    style, confidence = matches[0]
    assert style["style_id"] == "nikon-z7-architecture"
    assert confidence > 0.70


def test_find_matching_styles_profile_mismatch(sample_style):
    sc.upsert_style(sample_style)
    # Same camera but different profile should score lower (0.7 camera component)
    matches = sc.find_matching_styles(
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        scene_tags=["scene_architecture"],
        camera_profile="Nikon Z7 AgX-Like",
    )
    assert len(matches) == 1
    style, confidence = matches[0]
    # Different profile should incur the 0.4x penalty and drop below CONFIDENCE_LOW (0.30)
    assert confidence < 0.30


def test_find_matching_styles_no_match():
    matches = sc.find_matching_styles(
        camera_make="Canon",
        camera_model="EOS R5",
        scene_tags=["scene_sports"],
    )
    assert matches == []


# ---------------------------------------------------------------------------
# Integration hook
# ---------------------------------------------------------------------------


def test_get_style_recipe_computes_mean(monkeypatch):
    sc._ensure_initialized()
    # Create a style with two linked examples
    style = {
        "style_id": "test-recipe",
        "style_name": "Test Recipe",
        "camera_make": "NIKON",
        "camera_model": "Z7",
        "genre": "scene_landscape",
        "example_count": 2,
        "example_photo_ids": ["p1", "p2"],
        "mean_exposure_dna": {},
        "scene_distribution": {},
        "develop_variance": {},
    }
    sc.upsert_style(style)

    # Mock linked examples with canonical settings
    monkeypatch.setattr(
        training_service,
        "list_training_examples",
        lambda: [
            {
                "photo_id": "p1",
                "canonical_settings": '{"exposure": -0.3, "contrast": 15}',
            },
            {
                "photo_id": "p2",
                "canonical_settings": '{"exposure": -0.1, "contrast": 13}',
            },
        ],
    )

    recipe = sc.get_style_recipe("test-recipe")
    assert recipe["exposure"] == pytest.approx(-0.2)
    assert recipe["contrast"] == pytest.approx(14.0)


def test_update_style_for_example_triggers_discovery(monkeypatch, sample_style):
    # Ensure catalog is initialised
    sc._ensure_initialized()

    # Mock training list so it returns two examples (minimum for a style)
    examples = [
        {
            "photo_id": "photo_1",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_architecture"]',
            "canonical_settings": '{"exposure": -0.3, "contrast": 15}',
            "exp_luminance_mean": "0.45",
        },
        {
            "photo_id": "photo_2",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_architecture"]',
            "canonical_settings": '{"exposure": -0.2, "contrast": 14}',
            "exp_luminance_mean": "0.48",
        },
    ]
    monkeypatch.setattr(training_service, "list_training_examples", lambda: examples)

    # Mock _fetch_rich_examples to return the same data
    monkeypatch.setattr(sc, "_fetch_rich_examples", lambda pids: examples)

    sc.update_style_for_example(
        photo_id="photo_1",
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        camera_profile="Nikon Z7 Linear",
        scene_tags=["scene_architecture"],
        exposure_metrics={"exp_luminance_mean": 0.45},
    )

    styles = sc.list_styles()
    assert len(styles) >= 1


def test_find_matching_styles_with_user_keywords(sample_style):
    # Style is scene_architecture; user keywords say "macro" → should still match
    sc.upsert_style(sample_style)
    # With no keywords, exact match
    matches_no_kw = sc.find_matching_styles(
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        scene_tags=["scene_architecture"],
        camera_profile="Nikon Z7 Linear",
    )
    conf_no_kw = matches_no_kw[0][1]

    # With matching keywords, should get bonus
    style_with_kw = dict(sample_style)
    style_with_kw["style_id"] = "nikon-z7-arch-kw"
    style_with_kw["user_keywords"] = '["architecture", "urban"]'
    sc.upsert_style(style_with_kw)

    matches_kw = sc.find_matching_styles(
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        scene_tags=["scene_architecture"],
        camera_profile="Nikon Z7 Linear",
        user_keywords=["architecture"],
    )
    # Should find the keyword-matching style with higher confidence
    assert len(matches_kw) == 2
    # The keyword-matching style should score higher due to +0.10 bonus
    best_style, best_conf = matches_kw[0]
    assert best_conf >= conf_no_kw


def test_user_keywords_override_genre_in_update(monkeypatch):
    sc._ensure_initialized()

    # Example with AI tag "scene_portrait" but user keyword "macro"
    examples = [
        {
            "photo_id": "photo_1",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_portrait"]',
            "user_keywords": '["macro"]',
            "canonical_settings": '{"exposure": -0.3, "contrast": 15}',
            "exp_luminance_mean": "0.45",
        },
        {
            "photo_id": "photo_2",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_portrait"]',
            "user_keywords": '["macro"]',
            "canonical_settings": '{"exposure": -0.2, "contrast": 14}',
            "exp_luminance_mean": "0.48",
        },
    ]
    monkeypatch.setattr(training_service, "list_training_examples", lambda: examples)
    monkeypatch.setattr(sc, "_fetch_rich_examples", lambda pids: examples)

    sc.update_style_for_example(
        photo_id="photo_1",
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        camera_profile="Nikon Z7 Linear",
        scene_tags=["scene_portrait"],
        user_keywords=["macro"],
        exposure_metrics={"exp_luminance_mean": 0.45},
    )

    styles = sc.list_styles()
    # Should create a scene_macro style, not scene_portrait
    genres = [s["genre"] for s in styles]
    assert "scene_macro" in genres


def test_update_style_for_example_incremental_update(monkeypatch, sample_style):
    sc._ensure_initialized()
    # Insert existing style to trigger the else branch
    sc.upsert_style(sample_style)

    examples = [
        {
            "photo_id": "photo_1",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_architecture"]',
            "canonical_settings": '{"exposure": -0.3}',
        },
        {
            "photo_id": "photo_2",
            "camera_make": "NIKON CORPORATION",
            "camera_model": "NIKON Z 7",
            "camera_profile": "Nikon Z7 Linear",
            "scene_tags": '["scene_architecture"]',
            "canonical_settings": '{"exposure": -0.2}',
        },
    ]
    monkeypatch.setattr(training_service, "list_training_examples", lambda: examples)
    monkeypatch.setattr(sc, "_fetch_rich_examples", lambda pids: examples)

    sc.update_style_for_example(
        photo_id="photo_1",
        camera_make="NIKON CORPORATION",
        camera_model="NIKON Z 7",
        camera_profile="Nikon Z7 Linear",
        scene_tags=["scene_architecture"],
    )

    styles = sc.list_styles()
    assert len(styles) >= 1


def test_discover_styles_no_duplicate_hdr_suffix(monkeypatch):
    sc._ensure_initialized()
    examples = [
        {
            "photo_id": "photo_hdr_1",
            "camera_make": "NIKON",
            "camera_model": "Z8",
            "camera_profile": "Adobe Standard + HDR",
            "scene_tags": '["scene_landscape"]',
            "canonical_settings": '{"exposure": 0.0}',
        },
        {
            "photo_id": "photo_hdr_2",
            "camera_make": "NIKON",
            "camera_model": "Z8",
            "camera_profile": "Adobe Standard + HDR",
            "scene_tags": '["scene_landscape"]',
            "canonical_settings": '{"exposure": 0.0}',
        },
    ]
    monkeypatch.setattr(training_service, "list_training_examples", lambda: examples)
    monkeypatch.setattr(sc, "_fetch_rich_examples", lambda pids: examples)

    sc.discover_styles_from_examples()
    styles = sc.list_styles()
    hdr_styles = [
        s for s in styles if "Adobe Standard + HDR" in s.get("style_name", "")
    ]
    assert len(hdr_styles) >= 1
    for s in hdr_styles:
        assert "(HDR) (HDR)" not in s["style_name"]
        assert "+ HDR (HDR)" not in s["style_name"]


def test_fetch_rich_examples_includes_embeddings(monkeypatch):
    import numpy as np

    class MockCollection:
        def get(self, ids, include):
            assert "embeddings" in include
            assert "metadatas" in include
            return {
                "ids": ["pid1"],
                "metadatas": [{"camera_profile": "Adobe Standard"}],
                "embeddings": np.array([[0.1, 0.2]], dtype=np.float32),
            }

    monkeypatch.setattr(training_service, "_training_collection", MockCollection())
    res = sc._fetch_rich_examples(["pid1"])
    assert len(res) == 1
    assert res[0]["photo_id"] == "pid1"
    assert np.allclose(res[0]["embedding"], [0.1, 0.2])


def test_filter_style_examples_by_genre_excludes_panoramas_and_mismatches():
    examples = [
        {
            "photo_id": "p_portrait",
            "scene_tags": ["scene_people"],
            "filename": "portrait.jpg",
        },
        {
            "photo_id": "p_pano",
            "scene_tags": ["scene_people"],
            "filename": "portrait-pano.jpg",
        },
        {
            "photo_id": "p_landscape",
            "scene_tags": ["scene_landscape"],
            "filename": "landscape.jpg",
        },
    ]

    filtered = sc._filter_style_examples_by_genre("scene_people", examples)
    assert len(filtered) == 1
    assert filtered[0]["photo_id"] == "p_portrait"
