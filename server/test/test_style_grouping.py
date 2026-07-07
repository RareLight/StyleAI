"""Tests for services/style_grouping.py."""

from __future__ import annotations

import pytest

from services import style_grouping as sg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def example_nikon_arch():
    return {
        "photo_id": "nikon_1",
        "camera_make": "NIKON CORPORATION",
        "camera_model": "NIKON Z 7",
        "camera_profile": "Nikon Profile A",
        "scene_tags": '["scene_architecture", "scene_exterior"]',
        "canonical_settings": '{"exposure": -0.3, "contrast": 15, "temperature": 5200, "highlights": -25, "shadows": 35, "clarity": 20}',
        "exp_luminance_mean": "0.45",
    }


@pytest.fixture
def example_nikon_arch_2():
    return {
        "photo_id": "nikon_2",
        "camera_make": "NIKON CORPORATION",
        "camera_model": "NIKON Z 7",
        "camera_profile": "Nikon Profile A",
        "scene_tags": '["scene_architecture", "scene_street"]',
        "canonical_settings": '{"exposure": -0.2, "contrast": 14, "temperature": 5300, "highlights": -20, "shadows": 32, "clarity": 18}',
        "exp_luminance_mean": "0.48",
    }


@pytest.fixture
def example_nikon_portrait():
    return {
        "photo_id": "nikon_3",
        "camera_make": "NIKON CORPORATION",
        "camera_model": "NIKON Z 7",
        "camera_profile": "Nikon Profile B",
        "scene_tags": '["scene_portrait", "scene_studio"]',
        "canonical_settings": '{"exposure": 0.1, "contrast": 5, "temperature": 5500, "highlights": -10, "shadows": 20, "clarity": 8}',
        "exp_luminance_mean": "0.60",
    }


@pytest.fixture
def example_sony_landscape():
    return {
        "photo_id": "sony_1",
        "camera_make": "SONY",
        "camera_model": "ILCE-7M3",
        "camera_profile": "Sony Profile A",
        "scene_tags": '["scene_landscape", "scene_golden_hour"]',
        "canonical_settings": '{"exposure": -0.5, "contrast": 20, "temperature": 6000, "highlights": -30, "shadows": 40, "clarity": 25}',
        "exp_luminance_mean": "0.35",
    }


# ---------------------------------------------------------------------------
# group_examples_by_profile_genre
# ---------------------------------------------------------------------------


def test_group_examples_splits_by_camera_profile_and_genre(
    example_nikon_arch,
    example_nikon_arch_2,
    example_nikon_portrait,
    example_sony_landscape,
):
    examples = [
        example_nikon_arch,
        example_nikon_arch_2,
        example_nikon_portrait,
        example_sony_landscape,
    ]
    groups = sg.group_examples_by_profile_genre(examples)

    assert len(groups) == 3

    # Nikon + Nikon Profile A + Architecture
    key_arch = ("Nikon Profile A", "scene_architecture")
    assert key_arch in groups

    # Nikon + Nikon Profile B + Portrait
    key_portrait = ("Nikon Profile B", "scene_portrait")
    assert key_portrait in groups

    # Sony + Sony Profile A + Landscape
    key_land = ("Sony Profile A", "scene_landscape")
    assert key_land in groups
    assert len(groups[key_land]) == 1


def test_group_examples_handles_unknown_genre():
    examples = [
        {
            "photo_id": "test_1",
            "camera_make": "Canon",
            "camera_model": "EOS R5",
            "scene_tags": "[]",
            "canonical_settings": "{}",
        }
    ]
    groups = sg.group_examples_by_profile_genre(examples)
    key = ("default", "scene_unknown")
    assert key in groups
    assert len(groups[key]) == 1


def test_dynamic_semantic_mapping():
    # Clear cache for isolated testing
    sg._DYNAMIC_GENRE_CACHE.clear()

    # Known explicitly mapped keyword should return instantly without semantic embedding
    assert (
        sg._primary_genre_with_keywords(["scene_unknown"], ["portrait"])
        == "scene_portrait"
    )

    # Semantic mapping tests (these simulate unknown keywords)
    # Stargazing -> astrophotography
    assert (
        sg._primary_genre_with_keywords(["scene_unknown"], ["stargazing"])
        == "scene_astrophotography"
    )

    # Food -> studio (commercial/products/food)
    assert (
        sg._primary_genre_with_keywords(["scene_unknown"], ["food"]) == "scene_studio"
    )

    # Concert -> event
    assert (
        sg._primary_genre_with_keywords(["scene_unknown"], ["concert"]) == "scene_event"
    )

    # Test caching (should be instantaneous and read from dict)
    assert "stargazing" in sg._DYNAMIC_GENRE_CACHE
    assert sg._DYNAMIC_GENRE_CACHE["stargazing"] == "scene_astrophotography"


# ---------------------------------------------------------------------------
# split_subgenres
# ---------------------------------------------------------------------------


def test_split_subgenres_no_split_when_low_variance(
    example_nikon_arch, example_nikon_arch_2
):
    group = [example_nikon_arch, example_nikon_arch_2]
    subgroups = sg.split_subgenres(group, variance_threshold=0.50)
    assert len(subgroups) == 1
    assert subgroups[0]["subgenre"] is None


def test_split_subgenres_splits_high_variance():
    # Need at least 2 examples per bucket for a valid split.
    ex1 = {
        "photo_id": "a",
        "scene_tags": '["scene_portrait"]',
        "canonical_settings": '{"exposure": -1.0, "contrast": 30, "temperature": 4800, "highlights": -40, "shadows": 50, "clarity": 30}',
        "exp_luminance_mean": "0.30",
    }
    ex2 = {
        "photo_id": "b",
        "scene_tags": '["scene_portrait"]',
        "canonical_settings": '{"exposure": -0.9, "contrast": 28, "temperature": 4900, "highlights": -38, "shadows": 48, "clarity": 28}',
        "exp_luminance_mean": "0.32",
    }
    ex3 = {
        "photo_id": "c",
        "scene_tags": '["scene_portrait"]',
        "canonical_settings": '{"exposure": 1.0, "contrast": 5, "temperature": 6500, "highlights": 0, "shadows": 10, "clarity": 5}',
        "exp_luminance_mean": "0.70",
    }
    ex4 = {
        "photo_id": "d",
        "scene_tags": '["scene_portrait"]',
        "canonical_settings": '{"exposure": 0.9, "contrast": 6, "temperature": 6400, "highlights": 2, "shadows": 12, "clarity": 6}',
        "exp_luminance_mean": "0.68",
    }
    subgroups = sg.split_subgenres([ex1, ex2, ex3, ex4], variance_threshold=0.02)
    # High variance should trigger split by exposure bucket
    assert len(subgroups) == 2
    subgenre_names = {sg["subgenre"] for sg in subgroups}
    assert "dramatic" in subgenre_names
    assert "bright" in subgenre_names


def test_split_subgenres_by_secondary_tag():
    # Need at least 2 examples per secondary tag group for a valid split.
    ex1 = {
        "photo_id": "a",
        "scene_tags": '["scene_portrait", "scene_studio"]',
        "canonical_settings": '{"exposure": 0.0, "contrast": 50}',
    }
    ex2 = {
        "photo_id": "b",
        "scene_tags": '["scene_portrait", "scene_studio"]',
        "canonical_settings": '{"exposure": 0.0, "contrast": 48}',
    }
    ex3 = {
        "photo_id": "c",
        "scene_tags": '["scene_portrait", "scene_exterior"]',
        "canonical_settings": '{"exposure": 0.0, "contrast": 5}',
    }
    ex4 = {
        "photo_id": "d",
        "scene_tags": '["scene_portrait", "scene_exterior"]',
        "canonical_settings": '{"exposure": 0.0, "contrast": 7}',
    }
    subgroups = sg.split_subgenres([ex1, ex2, ex3, ex4], variance_threshold=0.02)
    # Should split by secondary tag: studio vs exterior
    assert len(subgroups) == 2
    subgenre_names = {sg["subgenre"] for sg in subgroups}
    assert "scene_studio" in subgenre_names
    assert "scene_exterior" in subgenre_names


# ---------------------------------------------------------------------------
# generate_style_name
# ---------------------------------------------------------------------------


def test_generate_style_name_simple():
    name = sg.generate_style_name("scene_architecture", None)
    assert name == "Architecture"


def test_generate_style_name_with_subgenre():
    name = sg.generate_style_name("scene_architecture", "scene_street")
    assert name == "Architecture (Street)"


def test_generate_style_name_unknown_genre():
    name = sg.generate_style_name("scene_unknown", None)
    assert name == "Unknown"


def test_generate_style_name_with_profile():
    name = sg.generate_style_name("scene_landscape", None)
    assert "Landscape" in name


# ---------------------------------------------------------------------------
# generate_style_description
# ---------------------------------------------------------------------------


def test_generate_style_description_high_contrast():
    desc = sg.generate_style_description(
        {"contrast": 20, "temperature": 5200, "clarity": 20, "dehaze": 12},
        "scene_architecture",
        {"scene_architecture": 0.8, "scene_exterior": 0.2},
    )
    assert "high-contrast" in desc
    assert "punchy clarity" in desc
    assert "strong dehaze" in desc
    assert "architecture" in desc
    assert "exterior" in desc


def test_generate_style_description_with_profile():
    desc = sg.generate_style_description(
        {"contrast": 20, "temperature": 5200, "clarity": 20},
        "scene_architecture",
        {"scene_architecture": 1.0},
        camera_profile="Nikon Z7 Linear",
    )
    assert "Nikon Z7 Linear" in desc
    assert "architecture" in desc


def test_generate_style_description_low_contrast_warm():
    desc = sg.generate_style_description(
        {"contrast": 2, "temperature": 6000, "clarity": 3},
        "scene_portrait",
        {"scene_portrait": 1.0},
    )
    assert "medium-contrast" in desc
    assert "warm" in desc
    assert "portrait" in desc


def test_generate_style_description_no_settings():
    desc = sg.generate_style_description(
        {},
        "scene_landscape",
        {},
    )
    assert "landscape" in desc


# ---------------------------------------------------------------------------
# User keywords
# ---------------------------------------------------------------------------


def test_primary_genre_with_keywords_overrides_scene_tags():
    # User keywords should override AI scene tags
    genre = sg._primary_genre_with_keywords(["scene_portrait"], ["macro", "nature"])
    assert genre == "scene_nature"


def test_primary_genre_with_keywords_falls_back_to_scene_tags():
    # When no keywords match, fall back to AI scene tags
    genre = sg._primary_genre_with_keywords(["scene_landscape"], ["xyz123nonsense"])
    assert genre == "scene_landscape"


def test_group_examples_uses_user_keywords_for_genre():
    examples = [
        {
            "photo_id": "test_1",
            "camera_make": "Canon",
            "camera_model": "EOS R5",
            "scene_tags": '["scene_portrait"]',  # AI says portrait
            "user_keywords": '["macro"]',  # User says macro
            "canonical_settings": "{}",
        }
    ]
    groups = sg.group_examples_by_profile_genre(examples)
    # Should use user keyword "macro" → scene_nature, not scene_portrait
    key = ("default", "scene_nature")
    assert key in groups


def test_primary_genre_with_dict_keywords_and_case_insensitivity():
    # When user keywords are stored as JSON dict (e.g. from LLM tagging), should extract values and match case-insensitively
    dict_keywords = {
        "Activities": ["Looking Up"],
        "Animals": ["English Springer Spaniel", "Dog"],
        "Genre": ["Pet Photography", "Portrait"],
    }
    genre = sg._primary_genre_with_keywords([], dict_keywords)
    # Pet Photography / Dog -> scene_portrait per user rule
    assert genre == "scene_portrait"


def test_group_examples_uses_fallback_keyword_keys():
    examples = [
        {
            "photo_id": "test_lego",
            "camera_profile": "Nikon Z7",
            "scene_tags": None,
            "flattened_keywords": '["Lego", "Toy Photography", "Product Shot"]',
            "canonical_settings": "{}",
        }
    ]
    groups = sg.group_examples_by_profile_genre(examples)
    key = ("Nikon Z7", "scene_studio")
    assert key in groups
