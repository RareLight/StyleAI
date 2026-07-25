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
        "scene_tags": '["scene_portrait", "scene_people"]',
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
    # Deep sky astrophotography -> astrophotography
    assert (
        sg._primary_genre_with_keywords(["scene_unknown"], ["deep_sky_astrophotography"])
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
    assert "deep_sky_astrophotography" in sg._DYNAMIC_GENRE_CACHE
    assert sg._DYNAMIC_GENRE_CACHE["deep_sky_astrophotography"] == "scene_astrophotography"


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
    assert genre == "scene_macro"


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
    # Should use user keyword "macro" → scene_macro, not scene_portrait
    key = ("default", "scene_macro")
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


def test_refined_taxonomy_avoids_broad_studio_overrides():
    # 1. Still life winter landscape should map to landscape/nature, not studio
    winter_kws = {
        "Genre": ["Still Life", "Nature"],
        "Sceneries": ["Winter Landscape"],
        "Weather": ["Snowy"],
    }
    assert sg._primary_genre_with_keywords([], winter_kws) in {
        "scene_landscape",
        "scene_nature",
    }

    # 2. Kitchen birthday party candid should map to portrait/event, not studio
    birthday_kws = {
        "Activities": ["Celebrating", "Blowing Candles"],
        "Genre": ["Candid", "Portrait"],
        "Location": ["Kitchen", "Indoor"],
    }
    assert sg._primary_genre_with_keywords([], birthday_kws) in (
        "scene_portrait",
        "scene_event",
    )

    # 3. Living room interior should map to architecture, not studio
    interior_kws = {
        "Genre": ["Interior Photography"],
        "Location": ["Living Room", "Home"],
    }
    assert sg._primary_genre_with_keywords([], interior_kws) == "scene_architecture"

    # 4. LEGO toy product shot should remain studio
    lego_kws = {
        "Activities": ["Building"],
        "Genre": ["Product Photography", "Toy Photography"],
        "Objects": ["Lego Bricks"],
    }
    assert sg._primary_genre_with_keywords([], lego_kws) == "scene_studio"


def test_pet_taxonomy_and_priority_extraction():
    # 1. Cat pet portrait with conflicting outdoor/nature scenery tags should map to portrait due to animals priority
    cat_kws = {
        "Animals": ["Cat", "Tabby", "Feline"],
        "Sceneries": ["Outdoor", "Garden", "Nature"],
        "Genre": ["Pet Portrait"],
    }
    assert sg._primary_genre_with_keywords([], cat_kws) == "scene_portrait"

    # 2. Dog breed (Golden Retriever) playing in grass should map to portrait
    dog_kws = {
        "Animals": ["Golden Retriever", "Dog", "Canine"],
        "Location": ["Park", "Field"],
    }
    assert sg._primary_genre_with_keywords([], dog_kws) == "scene_portrait"

    # 3. Kitten indoors should map to portrait without leaking into studio/architecture
    kitten_kws = {
        "Animals": ["Kitten"],
        "Setting": ["Indoor", "Living Room"],
    }
    assert sg._primary_genre_with_keywords([], kitten_kws) == "scene_portrait"


def test_wildlife_macro_insects():
    # Macro photography of dragonfly or bee should map to scene_macro, not studio or landscape
    insect_kws = {
        "Animals": ["Dragonfly", "Bee", "Insect"],
        "Genre": ["Macro Photography"],
        "Setting": ["Garden", "Outdoor"],
    }
    assert sg._primary_genre_with_keywords([], insect_kws) == "scene_macro"


def test_macro_pet_lego_precedence_general_taxonomy():
    # 1. Macro specialty ("insect portrait") routes to scene_macro rather than human portrait
    assert sg._primary_genre_with_keywords([], ["insect portrait"]) == "scene_macro"
    # 2. Studio / toy specialty ("lego architecture") routes to scene_studio rather than architecture
    assert sg._primary_genre_with_keywords([], ["lego architecture"]) == "scene_studio"
    # 3. Animate subject ("dog outdoor") routes to scene_portrait rather than background landscape
    assert sg._primary_genre_with_keywords([], ["dog outdoor"]) == "scene_portrait"
    # 4. AI scene tag fallback where primary tag is setting but animate subject exists
    assert (
        sg._primary_genre_with_keywords(["scene_landscape", "dog"], [])
        == "scene_portrait"
    )


def test_landscape_prominence_over_secondary_activity_tags():
    # A landscape photograph with secondary action/sports tags preserves scene_landscape
    assert (
        sg._primary_genre_with_keywords(["scene_landscape", "sports", "action"], [])
        == "scene_landscape"
    )
    # A landscape photograph with secondary street/ferris wheel tags preserves scene_landscape
    assert (
        sg._primary_genre_with_keywords(["scene_landscape", "street"], [])
        == "scene_landscape"
    )


def test_animate_subject_overrides_nature_and_wildlife():
    # 1. Groups of people outdoors tagged nature + event route to scene_event
    assert (
        sg._primary_genre_with_keywords(["scene_nature", "scene_event"], [])
        == "scene_event"
    )
    # 2. Pets/people outdoors tagged wildlife + portrait route to scene_portrait
    assert (
        sg._primary_genre_with_keywords(["scene_wildlife", "scene_portrait"], [])
        == "scene_portrait"
    )
    # 3. Nature keywords preserve scene_nature instead of being mapped to scene_landscape
    assert sg._primary_genre_with_keywords([], ["nature"]) == "scene_nature"


def test_astrophotography_and_specialized_canonical_overrides():
    # 1. Astrophotography/night sky over landscape routes to astrophotography
    assert (
        sg._primary_genre_with_keywords(
            ["scene_landscape", "scene_astrophotography"], []
        )
        == "scene_astrophotography"
    )
    # 2. Street photography over generic exterior routes to street
    assert (
        sg._primary_genre_with_keywords(["scene_exterior", "scene_street"], [])
        == "scene_street"
    )


def test_top_vision_tag_confidence_horizon_ignores_tail_noise():
    # Tail predictions at rank 4-10 (e.g., scene_wildlife at index 7) must not override top event/exterior tags
    noisy_tags = [
        "scene_exterior",
        "scene_event",
        "wedding",
        "outdoor",
        "park",
        "mountain",
        "forest",
        "scene_portrait",
    ]
    assert sg._primary_genre_with_keywords(noisy_tags, []) == "scene_event"


def test_pet_portrait_and_landscape_overrides_in_nature_and_macro():
    # 1. Pet portrait shot on a macro lens must resolve to scene_portrait, not macro
    meta_macro_lens = {"lens": "105mm Macro f/2.8"}
    assert (
        sg._primary_genre_with_keywords(
            ["scene_exterior", "dog", "grass"], [], exif_metadata=meta_macro_lens
        )
        == "scene_portrait"
    )
    # 2. Landscape shot with primary tag scene_nature resolves to scene_landscape
    assert sg._primary_genre_with_keywords(
        ["scene_nature", "scene_landscape", "mountain"], []
    )


def test_systematic_pro_workflow_categorization_audits():
    # 1. Milky Way photo with night EXIF prior (shutter=15s, iso=6400) resolves to astrophotography
    night_exif = {"shutter_speed": "15", "iso": 6400}
    assert (
        sg._primary_genre_with_keywords(
            ["scene_astrophotography", "stars", "milky way"],
            [],
            exif_metadata=night_exif,
        )
        == "scene_astrophotography"
    )
    # 2. Action sports photo in a park (scene_nature + scene_action) resolves to action
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature", "scene_action", "runner", "forest"], []
        )
        == "scene_action"
    )
    # 3. Outdoor wedding shot against a landscape resolves to scene_event
    assert (
        sg._primary_genre_with_keywords(
            ["scene_landscape", "scene_event", "wedding", "mountain"], []
        )
        == "scene_event"
    )
    # 4. Night portrait shot in ambient night resolves to scene_portrait
    assert (
        sg._primary_genre_with_keywords(["scene_night", "scene_portrait", "person"], [])
        == "scene_portrait"
    )
    # 5. Action occurring during an event resolves to scene_event (event precedence)
    assert (
        sg._primary_genre_with_keywords(
            ["scene_action", "scene_event", "dance floor", "wedding"], []
        )
        == "scene_event"
    )


def test_dynamic_semantic_mapping_persists_to_sqlite(monkeypatch):
    # Clear memory cache for test keyword
    sg._DYNAMIC_GENRE_CACHE.pop("deep_sky_astrophotography", None)
    mapped = sg._dynamic_semantic_mapping("deep_sky_astrophotography")
    assert mapped == "scene_astrophotography"


def test_clear_semantic_genre_cache():
    sg._DYNAMIC_GENRE_CACHE["dummy_kw"] = "scene_portrait"
    sg.clear_semantic_genre_cache()
    assert "dummy_kw" not in sg._DYNAMIC_GENRE_CACHE


def test_exif_priors_and_vision_regimes():
    # 1. Macro lens portrait evaluated by vision model -> scene_portrait (hardware is prior, vision subject rules)
    meta_macro_lens = {"lens": "105mm f/2.8 Macro", "focal_length": 105.0}
    assert (
        sg._primary_genre_with_keywords(
            ["scene_portrait"], [], exif_metadata=meta_macro_lens
        )
        == "scene_portrait"
    )

    # 2. Extreme long exposure astrophotography EXIF deterministic fact
    meta_astro = {"shutter_speed": 15.0, "iso": 6400}
    assert (
        sg._primary_genre_with_keywords(["scene_general"], [], exif_metadata=meta_astro)
        == "scene_night"
    )

    # 3. Studio tabletop shot with flash at ISO 100
    meta_studio = {"flash": True, "iso": 100}
    priors = sg._evaluate_exif_priors(meta_studio)
    assert priors.get("scene_studio", 0.0) >= 0.20

    # 4. Close-up flower shot outdoor on macro lens tagged scene_nature -> scene_macro
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature"], [], exif_metadata=meta_macro_lens
        )
        == "scene_macro"
    )

    # 5. Outdoor close-up tagged with both scene_nature and scene_macro -> scene_macro
    assert (
        sg._primary_genre_with_keywords(["scene_nature", "scene_macro"], [])
        == "scene_macro"
    )

    # 6. Wildlife shot on 105mm macro lens tagged scene_wildlife -> scene_wildlife
    assert (
        sg._primary_genre_with_keywords(
            ["scene_wildlife"], [], exif_metadata=meta_macro_lens
        )
        == "scene_wildlife"
    )

    # 7. Scenic landscape vista shot on 105mm macro lens tagged scene_landscape -> scene_landscape
    assert (
        sg._primary_genre_with_keywords(
            ["scene_landscape"], [], exif_metadata=meta_macro_lens
        )
        == "scene_landscape"
    )


def test_group_examples_by_profile_genre_uses_exif_priors():
    examples = [
        {
            "photo_id": "p1",
            "camera_profile": "Adobe Standard",
            "scene_tags": ["scene_general"],
            "shutter_speed": 15.0,
            "iso": 6400,
        }
    ]
    groups = sg.group_examples_by_profile_genre(examples)
    assert ("Adobe Standard", "scene_night") in groups


def test_parse_exif_string_values():
    assert sg._parse_shutter_seconds("1/200 sec") == 0.005
    assert sg._parse_shutter_seconds("15 sec") == 15.0
    assert sg._parse_exif_float("ISO 6400") == 6400.0
    assert sg._parse_exif_float("85 mm") == 85.0
    priors = sg._evaluate_exif_priors(
        {"shutter_speed": "15 sec", "iso": "ISO 6400", "focal_length": "85 mm"}
    )
    assert priors.get("scene_night", 0.0) >= 0.4


def test_nature_wildlife_landscape_precedence():
    # 1. Telephoto bird photo should not trigger macro EXIF prior and should classify as wildlife
    priors = sg._evaluate_exif_priors({"focal_length": "400 mm", "lens": "400mm f/4"})
    assert "scene_macro" not in priors
    genre = sg._primary_genre_with_keywords(["scene_exterior"], ["bird", "wildlife"])
    assert genre == "scene_wildlife"

    # 2. Landscape with action/motion terms should stay landscape
    genre_land = sg._primary_genre_with_keywords(
        ["scene_landscape"], ["landscape", "running water"]
    )
    assert genre_land == "scene_landscape"

    # 3. Nature fallback should map secondary landscape and macro tags cleanly
    assert (
        sg._primary_genre_with_keywords(["scene_nature", "mountain", "valley"], [])
        == "scene_landscape"
    )
    assert (
        sg._primary_genre_with_keywords(["scene_nature", "flower", "petal"], [])
        == "scene_macro"
    )


def test_llm_noise_filtering_and_visual_centroids():
    import numpy as np

    # 1. Verify LLM noise tags are stripped out
    multi_tags = [
        "portrait orientation",
        "dramatic lighting",
        "action shot",
        "landscape",
    ]
    filtered = sg._filter_llm_noise_keywords(multi_tags)
    assert filtered == ["landscape"]
    genre = sg._primary_genre_with_keywords([], multi_tags)
    assert genre == "scene_landscape"

    # 2. Verify Cold-Start (<5 samples) falls back and does not produce centroids
    sparse_examples = [
        {
            "camera_profile": "Adobe Standard",
            "scene_tags": ["portrait"],
            "embedding": [1.0, 0.0, 0.0],
        }
        for _ in range(3)
    ]
    centroids = sg._compute_catalog_genre_centroids(sparse_examples, min_samples=5)
    assert "scene_portrait" not in centroids

    # 3. Verify >=5 samples computes normalized centroid and verify_genre_with_visual_centroid works
    dense_examples = [
        {
            "camera_profile": "Adobe Standard",
            "scene_tags": ["portrait"],
            "embedding": np.array([1.0, 0.0], dtype=np.float32),
        }
        for _ in range(6)
    ]
    centroids = sg._compute_catalog_genre_centroids(dense_examples, min_samples=5)
    assert "scene_portrait" in centroids

    # Verify matching visual centroid arbitrates correctly
    verified = sg.verify_genre_with_visual_centroid(
        "scene_unknown",
        np.array([1.0, 0.0], dtype=np.float32),
        centroids,
        similarity_threshold=0.60,
    )
    assert verified == "scene_portrait"


def test_unified_genre_compatibility():
    is_compat, is_ambig = sg.is_genre_compatible("scene_portrait", "scene_people")
    assert is_compat is True
    assert is_ambig is False

    is_compat, is_ambig = sg.is_genre_compatible("scene_portrait", "scene_landscape")
    assert is_compat is False
    assert is_ambig is False

    is_compat, is_ambig = sg.is_genre_compatible("scene_portrait", "scene_unknown")
    assert is_compat is True
    assert is_ambig is True


def test_unified_visual_membership():
    import numpy as np

    style_embeddings = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
        ],
        dtype=np.float32,
    )

    photo_emb_good = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert (
        sg.verify_photo_visual_membership(
            photo_emb_good, style_embeddings=style_embeddings, min_similarity=0.45
        )
        is True
    )

    photo_emb_bad = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert (
        sg.verify_photo_visual_membership(
            photo_emb_bad, style_embeddings=style_embeddings, min_similarity=0.45
        )
        is False
    )

    photo_emb_med = np.array([0.5, 0.866, 0.0], dtype=np.float32)
    assert (
        sg.verify_photo_visual_membership(
            photo_emb_med,
            style_embeddings=style_embeddings,
            min_similarity=0.45,
            require_strict_if_ambiguous=False,
        )
        is True
    )
    assert (
        sg.verify_photo_visual_membership(
            photo_emb_med,
            style_embeddings=style_embeddings,
            min_similarity=0.45,
            require_strict_if_ambiguous=True,
        )
        is False
    )


def test_cat_portrait_vision_tags_resolve_to_portrait():
    assert sg._primary_genre(["scene_wildlife", "cat", "feline"]) == "scene_portrait"
    assert sg._primary_genre(["wildlife", "kitten"]) == "scene_portrait"


def test_nature_swallowing_precedence():
    assert sg._primary_genre(["scene_nature", "cat"]) == "scene_portrait"
    assert sg._primary_genre(["scene_nature", "wedding"]) == "scene_event"
    assert sg._primary_genre(["scene_nature", "mountain vista"]) == "scene_landscape"


def test_landscape_hijacked_by_wildlife():
    assert sg._primary_genre(["scene_landscape", "bird"]) == "scene_wildlife"


# ---------------------------------------------------------------------------
# Regression: keyword fallback must run before vision-level nature guards
# (commit 56a98a1 moved them after, causing nature to swallow specialized genres)
# ---------------------------------------------------------------------------


def test_macro_keyword_not_swallowed_by_nature_vision():
    """Macro user keyword must win over nature vision tags."""
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature", "scene_nature", "scene_nature"],
            ["macro", "flower closeup"],
        )
        == "scene_macro"
    )


def test_landscape_keyword_not_swallowed_by_nature_vision():
    """Landscape keyword must win over nature vision tags."""
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature", "scene_landscape"],
            ["mountain scenery"],
        )
        == "scene_landscape"
    )


def test_wildlife_keyword_not_swallowed_by_nature_vision():
    """Wildlife keyword must win when mixed with forest/nature keywords."""
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature"],
            ["forest", "wildlife tracking"],
        )
        == "scene_wildlife"
    )


def test_pure_nature_still_returns_nature():
    """Pure nature keywords without specialized alternatives must still return nature."""
    assert (
        sg._primary_genre_with_keywords(
            ["scene_nature"],
            ["wilderness"],
        )
        == "scene_nature"
    )


# ---------------------------------------------------------------------------
# Audit fixes: operator precedence, longest-match, bucket consistency, filter
# ---------------------------------------------------------------------------


def test_get_broad_genre_longest_match_car_race():
    """'car race' should match 'race' (scene_action) not 'car' (scene_studio)."""
    assert sg._get_broad_genre("car race") == "scene_action"


def test_get_broad_genre_longest_match_race_car():
    """'race car' should match 'race' (scene_action) not 'car' (scene_studio)."""
    assert sg._get_broad_genre("race car") == "scene_action"


def test_get_broad_genre_longest_match_candid_street():
    """'candid street' should match 'candid street' (scene_street) not 'candid' (scene_event)."""
    assert sg._get_broad_genre("candid street photography") == "scene_street"


def test_get_broad_genre_exact_still_works():
    """Exact keyword matches must still resolve correctly."""
    assert sg._get_broad_genre("macro") == "scene_macro"
    assert sg._get_broad_genre("wedding") == "scene_event"
    assert sg._get_broad_genre("scene_portrait") == "scene_portrait"


def test_dynamic_buckets_wildlife_not_in_nature():
    """Wildlife-related keywords should map to scene_wildlife, not scene_nature via dynamic buckets."""
    # If dynamic buckets are consistent, a keyword close to 'wildlife' should
    # not land in scene_nature
    mapped = sg._dynamic_semantic_mapping("bird watching")
    assert mapped != "scene_nature", (
        f"'bird watching' mapped to scene_nature instead of a wildlife/portrait bucket"
    )


def test_landscape_branch_background_setting_guard():
    """Operator precedence fix: landscape branch must check background_settings guard.

    When primary_mapped is scene_landscape but first_tag is a background setting,
    the landscape-specific tier_order_subjects should NOT be used.
    """
    # scene_golden_hour primary tag with landscape secondary — should use the
    # general 'else' tier_order_subjects (which includes architecture, street, etc.)
    # not the landscape-specific one (which only has studio, macro, event, portrait, astro)
    result = sg._primary_genre_with_keywords(
        ["scene_golden_hour", "scene_architecture", "scene_landscape"],
        [],
    )
    # With the precedence fix, scene_golden_hour IS in background_settings,
    # so the landscape branch guard fires and we fall into the else branch
    # which includes scene_architecture as a candidate
    assert result == "scene_architecture"


def test_pet_close_up_overrides_macro():
    """Pet portraits tagged as close_up (macro) should be overridden by dog/cat tags."""
    result = sg._primary_genre_with_keywords(["close_up", "dog"], [])
    assert result == "scene_portrait"


def test_sports_overrides_wildlife():
    """Action sports should override wildlife if tagged together."""
    result = sg._primary_genre_with_keywords(["wildlife", "surfing"], [])
    assert result == "scene_action"


def test_expanded_keywords_landscape():
    """New landscape keywords like meadow should map to scene_landscape."""
    result = sg._primary_genre_with_keywords(["nature", "meadow"], [])
    assert result == "scene_landscape"
    assert sg._get_broad_genre("meadow") == "scene_landscape"


def test_expanded_keywords_macro():
    """New macro keywords like mushroom should map to scene_macro."""
    result = sg._primary_genre_with_keywords(["nature", "mushroom"], [])
    assert result == "scene_macro"
    assert sg._get_broad_genre("mushroom") == "scene_macro"


def test_exif_lens_strips_macro_genre():
    """If a non-macro lens EXIF is provided, scene_macro is stripped from consideration."""
    exif_metadata = {"lens": "50mm f/1.4"}
    result = sg._primary_genre_with_keywords(["close_up", "insect"], [], exif_metadata)
    assert result == "scene_nature"


def test_exif_lens_allows_macro_genre():
    """If a macro/micro/mc lens EXIF is provided, scene_macro is permitted."""
    exif_metadata = {"lens": "100mm f/2.8 Macro"}
    result = sg._primary_genre_with_keywords(["close_up", "insect"], [], exif_metadata)
    assert result == "scene_macro"

    exif_metadata = {"lens": "NIKKOR Z MC 105mm f/2.8 VR S"}
    result = sg._primary_genre_with_keywords(["close_up", "insect"], [], exif_metadata)
    assert result == "scene_macro"

    exif_metadata = {"lens": "AF-S Micro-Nikkor 60mm f/2.8G ED"}
    result = sg._primary_genre_with_keywords(["close_up", "insect"], [], exif_metadata)
    assert result == "scene_macro"


def test_extended_horizon_catches_buried_subjects():
    """Extended top_vision_tags horizon allows pet subjects buried by environment tags to trigger portrait overrides."""
    tags = ["nature", "grass", "outdoors", "sunny", "trees", "dog"]
    result = sg._primary_genre_with_keywords(tags, [])
    assert result == "scene_portrait"


def test_focal_length_crop_factors_sony():
    assert sg._get_35mm_equivalent_focal_length("Sony", "ILCE-7M3", 50) == 50.0
    assert sg._get_35mm_equivalent_focal_length("Sony", "ILCE-6400", 50) == 75.0
    assert sg._get_35mm_equivalent_focal_length("Sony", "a6000", 50) == 75.0


def test_focal_length_crop_factors_fuji():
    assert sg._get_35mm_equivalent_focal_length("Fujifilm", "X-T4", 56) == 84.0
    assert sg._get_35mm_equivalent_focal_length("Fujifilm", "GFX 100", 110) == 86.9


def test_focal_length_crop_factors_mft():
    assert sg._get_35mm_equivalent_focal_length("OM Digital Solutions", "OM-1", 45) == 90.0
    assert sg._get_35mm_equivalent_focal_length("Olympus", "E-M1 Mark III", 25) == 50.0
    assert sg._get_35mm_equivalent_focal_length("Panasonic", "DC-G9", 25) == 50.0
    assert sg._get_35mm_equivalent_focal_length("Panasonic", "DC-S5", 50) == 50.0


def test_focal_length_crop_factors_nikon():
    assert sg._get_35mm_equivalent_focal_length("NIKON CORPORATION", "NIKON Z 8", 50) == 50.0
    assert sg._get_35mm_equivalent_focal_length("NIKON CORPORATION", "NIKON Z 50", 50) == 75.0
    assert sg._get_35mm_equivalent_focal_length("NIKON CORPORATION", "NIKON D850", 50) == 50.0
    assert sg._get_35mm_equivalent_focal_length("NIKON CORPORATION", "NIKON D500", 50) == 75.0


def test_focal_length_crop_factors_canon():
    assert sg._get_35mm_equivalent_focal_length("Canon", "Canon EOS R5", 50) == 50.0
    assert sg._get_35mm_equivalent_focal_length("Canon", "Canon EOS R7", 50) == 80.0
    assert sg._get_35mm_equivalent_focal_length("Canon", "Canon EOS 5D Mark IV", 50) == 50.0
    assert sg._get_35mm_equivalent_focal_length("Canon", "Canon EOS 80D", 50) == 80.0


def test_exif_priors_use_35mm_equivalent():
    # 45mm on MFT = 90mm -> portrait (should be 0.15)
    priors = sg._evaluate_exif_priors({"camera_make": "Olympus", "camera_model": "E-M1", "focal_length": 45})
    assert priors.get("scene_portrait") == 0.15
    # 12mm on MFT = 24mm -> landscape/architecture
    priors = sg._evaluate_exif_priors({"camera_make": "Olympus", "camera_model": "E-M1", "focal_length": 12})
    assert priors.get("scene_landscape") == 0.15


# ---------------------------------------------------------------------------
# Audit Gap Tests: Added to cover issues identified in classification audit
# ---------------------------------------------------------------------------


def test_event_not_hijacked_by_portrait_lens_exif():
    """Candid event photo taken with 85mm portrait lens should stay scene_event."""
    meta_portrait_lens = {"focal_length": 85.0}
    assert (
        sg._primary_genre_with_keywords(
            ["scene_event", "scene_portrait"], [], exif_metadata=meta_portrait_lens
        )
        == "scene_event"
    )
    # Also test with explicit event keyword + portrait lens
    assert (
        sg._primary_genre_with_keywords(
            ["scene_portrait"], ["wedding"], exif_metadata=meta_portrait_lens
        )
        == "scene_event"
    )


def test_generic_keywords_do_not_hijack_vision_tags():
    """Generic keywords like 'vacation' or 'trip' must not override specific vision tags."""
    assert (
        sg._primary_genre_with_keywords(["scene_portrait"], ["vacation"])
        == "scene_portrait"
    )
    assert (
        sg._primary_genre_with_keywords(["scene_landscape"], ["trip"])
        == "scene_landscape"
    )


def test_step6_substring_no_false_positives():
    """Step 6 word-boundary matching must not have substring false positives."""
    result = sg._primary_genre_with_keywords(["scene_unknown"], ["groom"])
    assert result != "scene_architecture", f"'groom' falsely matched 'room' -> {result}"

    result = sg._primary_genre_with_keywords(["scene_unknown"], ["mushroom"])
    assert result != "scene_architecture", f"'mushroom' falsely matched 'room' -> {result}"

    result = sg._primary_genre_with_keywords(["scene_unknown"], ["greenhouse"])
    assert result != "scene_architecture", f"'greenhouse' falsely matched 'house' -> {result}"

    # Legitimate exact matches should still work
    result = sg._primary_genre_with_keywords(["scene_unknown"], ["room"])
    assert result == "scene_architecture"
    result = sg._primary_genre_with_keywords(["scene_unknown"], ["indoor"])
    assert result == "scene_architecture"


def test_buried_wildlife_subject_deep_horizon():
    """Wildlife subject tags buried under nature noise at rank 8+ should still be found."""
    tags = [
        "scene_nature", "tree", "grass", "outdoors",
        "vegetation", "wilderness", "green", "bird",
    ]
    result = sg._primary_genre_with_keywords(tags, [])
    assert result == "scene_wildlife", f"Buried 'bird' at rank 8 not found: {result}"


def test_stitched_panorama_exclusion():
    """Stitched panoramas must be filtered out by classify_photo_genre."""
    meta_pano_suffix = {"filename": "DSC_1234-Pano.jpg", "scene_tags": ["scene_landscape"]}
    assert sg.classify_photo_genre(meta_pano_suffix) is None

    meta_pano_tag = {"scene_tags": ["scene_landscape", "panorama"]}
    assert sg.classify_photo_genre(meta_pano_tag) is None


