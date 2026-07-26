"""Tests for the Active Style Upgrade Assistant service."""

import pytest
from services import style_upgrades


@pytest.fixture(autouse=True)
def clear_upgrade_recs_cache():
    style_upgrades.invalidate_upgrade_recommendations_cache()
    yield
    style_upgrades.invalidate_upgrade_recommendations_cache()


def test_hero_score():
    """Verify hero shot scoring with rating and fallback metrics."""
    # 5 stars + edited = 3.0 + 1.0 = 4.0
    assert style_upgrades._hero_score({"rating": 5, "is_edited": True}) == 4.0
    # 3 stars unedited = 1.8
    assert style_upgrades._hero_score(
        {"rating": 3, "is_edited": False}
    ) == pytest.approx(1.8)
    # No rating, picked flag + edited = 1.5 + 1.0 = 2.5
    assert style_upgrades._hero_score({"pick_status": 1, "is_edited": True}) == 2.5
    # Rejected flag unedited = -3.0
    assert style_upgrades._hero_score({"pick_status": -1, "is_edited": False}) == -3.0
    # Empty / invalid string values
    assert (
        style_upgrades._hero_score({"rating": "invalid", "pick_status": "none"}) == 0.0
    )


def test_style_recommendations_similarity_filtering():
    """Verify recommendation sampling prioritizes visual similarity to existing style examples over outliers."""
    # Suppose existing embedding is along x-axis [1, 0, 0]
    existing = [[1.0, 0.0, 0.0]]

    # Candidates:
    # cand1 is close to existing [0.99, 0.1, 0] -> sim ~0.99, selected first
    # cand2 is somewhat close [0.8, 0.6, 0] -> sim ~0.80, selected second
    # cand3 is orthogonal [0.0, 1.0, 0.0] -> sim 0.0 < 0.60, filtered out!
    # cand4 is opposite [-1.0, 0.0, 0.0] -> sim -1.0 < 0.60, filtered out!
    candidates = [
        ("very_close", [0.99, 0.1, 0.0], {"rating": 0}),
        ("somewhat_close", [0.8, 0.6, 0.0], {"rating": 0}),
        ("ortho", [0.0, 1.0, 0.0], {"rating": 0}),
        ("opposite", [-1.0, 0.0, 0.0], {"rating": 0}),
    ]

    selected = style_upgrades._select_style_recommendations(
        candidates, existing, target_count=4
    )
    assert len(selected) == 2
    assert selected[0] == "very_close"
    assert selected[1] == "somewhat_close"


def test_style_recommendations_vectorized_edge_cases():
    """Verify vectorized recommendation sampling handles zero vectors and single candidates without errors."""
    import numpy as np

    existing = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
    candidates = [
        ("zero_vec", np.zeros(3, dtype=np.float32), {"rating": 5}),
        ("normal_vec", np.array([0.8, 0.6, 0.0], dtype=np.float32), {"rating": 3}),
    ]
    selected = style_upgrades._select_style_recommendations(
        candidates, existing, target_count=5
    )
    assert len(selected) == 1
    assert "normal_vec" in selected


def test_tier_and_needed_count_calculations(mocker):
    """Verify tier assignment and needed count logic for N=14, N=46, and N=58."""
    mock_styles = [
        {
            "style_id": "style-a",
            "style_name": "Style A (N=14)",
            "example_count": 14,
            "camera_profile": "Adobe Standard",
            "genre": "Portrait",
        },
        {
            "style_id": "style-b",
            "style_name": "Style B (N=46)",
            "example_count": 46,
            "camera_profile": "Adobe Standard",
            "genre": "Landscape",
        },
        {
            "style_id": "style-c",
            "style_name": "Style C (N=58)",
            "example_count": 58,
            "camera_profile": "Adobe Standard",
            "genre": "Street",
        },
    ]

    mocker.patch("services.style_catalog.list_styles", return_value=mock_styles)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")
    mocker.patch("services.chroma.collection", None)

    res = style_upgrades.get_style_upgrade_recommendations()
    styles = res["styles"]
    # Style C (N=58) should be filtered out because it is fully upgraded!
    assert len(styles) == 2

    # Sorted by ascending needed count: Style A (needs 1 to 15), then Style B (needs 4 to 50)
    assert styles[0]["style_id"] == "style-a"
    assert styles[0]["needed_count"] == 1
    assert "Supervised PLS" in styles[0]["target_tier"]
    assert not styles[0]["is_highest_tier"]

    assert styles[1]["style_id"] == "style-b"
    assert styles[1]["needed_count"] == 4
    assert "Elastic Net" in styles[1]["target_tier"]
    assert not styles[1]["is_highest_tier"]


def test_edited_vs_unedited_priority(mocker):
    """Verify that already edited photos are prioritized over unedited photos."""
    mock_style = [
        {
            "style_id": "style-a",
            "style_name": "Style A",
            "example_count": 14,  # Needs 1 -> buffer target 2
            "camera_profile": "Adobe Standard",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")

    # Mock collection with 3 candidates: 1 edited, 2 unedited
    mock_collection = mocker.MagicMock()
    mock_collection.get.return_value = {
        "ids": ["unedited-1", "edited-1", "unedited-2"],
        "embeddings": [
            [0.8, 0.6, 0.0],
            [1.0, 0.0, 0.0],
            [0.7, 0.0, 0.71414],
        ],
        "metadatas": [
            {"camera_profile": "Adobe Standard", "is_edited": False, "rating": 5},
            {"camera_profile": "Adobe Standard", "is_edited": True, "rating": 4},
            {"camera_profile": "Adobe Standard", "is_edited": False, "rating": 4},
        ],
    }
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = [
        r["globalPhotoId"] if isinstance(r, dict) else r
        for r in res["styles"][0]["recommended_photo_ids"]
    ]
    assert len(recs) == 2
    # Even though unedited-1 has a higher star rating (5 stars), edited-1 MUST be selected first!
    assert recs[0] == "edited-1"


def test_upgrade_recommendations_not_truncated_for_good_tier(mocker):
    """Verify that when >15 basic styles exist, styles with 15 <= N < 50 are still returned with default limit=100."""
    mock_styles = []
    # 20 basic styles (N < 15)
    for i in range(20):
        mock_styles.append(
            {
                "style_id": f"basic-{i}",
                "style_name": f"Basic Style {i}",
                "example_count": 5,
                "camera_profile": "Adobe Standard",
            }
        )
    # 5 good styles (15 <= N < 50)
    for i in range(5):
        mock_styles.append(
            {
                "style_id": f"good-{i}",
                "style_name": f"Good Style {i}",
                "example_count": 25,
                "camera_profile": "Adobe Standard",
            }
        )

    mocker.patch("services.style_catalog.list_styles", return_value=mock_styles)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")
    mocker.patch("services.chroma.collection", None)

    res = style_upgrades.get_style_upgrade_recommendations()
    styles = res["styles"]
    assert len(styles) == 25
    good_returned = [s for s in styles if 15 <= s["current_count"] < 50]
    assert len(good_returned) == 5


def test_upgrade_recommendations_sorted_by_needed_count_ascending(mocker):
    """Verify styles are sorted strictly by needed_count in ascending order, ignoring priority tier buckets."""
    mock_styles = [
        {
            "style_id": "needs-10",
            "style_name": "Basic needing 10",
            "example_count": 5,  # 15 - 5 = 10 needed
            "camera_profile": "Adobe Standard",
        },
        {
            "style_id": "needs-2",
            "style_name": "Good needing 2",
            "example_count": 48,  # 50 - 48 = 2 needed
            "camera_profile": "Adobe Standard",
        },
        {
            "style_id": "needs-14",
            "style_name": "Basic needing 14",
            "example_count": 1,  # 15 - 1 = 14 needed
            "camera_profile": "Adobe Standard",
        },
        {
            "style_id": "fully-upgraded",
            "style_name": "Already Best",
            "example_count": 55,  # 0 needed -> filtered out!
            "camera_profile": "Adobe Standard",
        },
    ]

    mocker.patch("services.style_catalog.list_styles", return_value=mock_styles)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")
    mocker.patch("services.chroma.collection", None)

    res = style_upgrades.get_style_upgrade_recommendations()
    styles = res["styles"]

    # The fully upgraded style should be filtered out
    assert len(styles) == 3

    # Order should be strictly ascending by needed_count: 2, 10, 14
    assert styles[0]["style_id"] == "needs-2"
    assert styles[0]["needed_count"] == 2

    assert styles[1]["style_id"] == "needs-10"
    assert styles[1]["needed_count"] == 10

    assert styles[2]["style_id"] == "needs-14"
    assert styles[2]["needed_count"] == 14


def test_chromadb_numpy_array_return_handling(mocker):
    """Verify that when ChromaDB returns numpy arrays for embeddings/metadatas/ids, no truth value ambiguity ValueError is raised."""
    import numpy as np

    mock_style = [
        {
            "style_id": "style-numpy",
            "style_name": "Style Numpy",
            "example_count": 10,  # Needs 5
            "camera_profile": "Adobe Standard",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")

    # Mock ChromaDB returning real numpy arrays instead of Python lists
    mock_collection = mocker.MagicMock()
    mock_collection.get.return_value = {
        "ids": np.array(["photo-1", "photo-2"]),
        "embeddings": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        "metadatas": np.array(
            [
                {"camera_profile": "Adobe Standard", "rating": 4},
                {"camera_profile": "Adobe Standard", "rating": 5},
            ]
        ),
    }
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = res["styles"][0]["recommended_photo_ids"]
    assert len(recs) == 2


def test_embedding_first_recommendations_over_text_divergence(mocker):
    """Verify that diffuse scene_general candidate photos with high visual similarity are admitted, but distinct conflicting regimes are excluded."""
    mock_style = [
        {
            "style_id": "style-embed",
            "style_name": "Style Embed",
            "example_count": 5,  # Has existing examples -> N >= 1
            "camera_profile": "Adobe Standard",
            "genre": "scene_landscape",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    # Existing training example in DB has photo-ex
    mock_conn = mocker.MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        {"style_id": "style-embed", "photo_id": "photo-ex"}
    ]
    mocker.patch("services.style_catalog._ensure_initialized", return_value=mock_conn)
    mocker.patch("services.chroma._ensure_initialized")

    mock_collection = mocker.MagicMock()
    # photo-ex is the training example [1.0, 0.0, 0.0]
    # photo-cand-diffuse has high similarity [0.85, 0.5, 0.0] with diffuse tag 'scene_general'
    # photo-cand-conflict has high similarity [0.85, 0.5, 0.0] but conflicting regime 'scene_studio'
    mock_collection.get.return_value = {
        "ids": ["photo-ex", "photo-cand-diffuse", "photo-cand-conflict"],
        "embeddings": [[1.0, 0.0, 0.0], [0.85, 0.5, 0.0], [0.85, 0.5, 0.0]],
        "metadatas": [
            {"camera_profile": "Adobe Standard", "scene_tags": '["scene_landscape"]'},
            {
                "camera_profile": "Adobe Standard",
                "scene_tags": '["scene_general"]',
                "rating": 5,
            },
            {
                "camera_profile": "Adobe Standard",
                "scene_tags": '["scene_studio"]',
                "rating": 5,
            },
        ],
    }
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = [
        r["globalPhotoId"] if isinstance(r, dict) else r
        for r in res["styles"][0]["recommended_photo_ids"]
    ]
    assert "photo-cand-diffuse" in recs
    assert "photo-cand-conflict" not in recs


def test_dual_gated_screening_rejects_moderate_similarity_cross_talk(mocker):
    """Verify that candidate photos with distinct conflicting regimes are rejected."""
    mock_style = [
        {
            "style_id": "style-dual",
            "style_name": "Style Dual",
            "example_count": 5,
            "camera_profile": "Adobe Standard",
            "genre": "scene_landscape",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    mock_conn = mocker.MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        {"style_id": "style-dual", "photo_id": "photo-ex"}
    ]
    mocker.patch("services.style_catalog._ensure_initialized", return_value=mock_conn)
    mocker.patch("services.chroma._ensure_initialized")

    mock_collection = mocker.MagicMock()
    # photo-ex is [1.0, 0.0, 0.0]
    # photo-cand has moderate similarity [0.70, 0.71, 0.0] (sim ~0.70, which is >= 0.60 but < 0.80)
    # and conflicting text tag 'scene_studio'
    mock_collection.get.return_value = {
        "ids": ["photo-ex", "photo-cand"],
        "embeddings": [[1.0, 0.0, 0.0], [0.70, 0.71, 0.0]],
        "metadatas": [
            {"camera_profile": "Adobe Standard", "scene_tags": '["scene_landscape"]'},
            {
                "camera_profile": "Adobe Standard",
                "scene_tags": '["scene_studio"]',
                "rating": 5,
            },
        ],
    }
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = [
        r["globalPhotoId"] if isinstance(r, dict) else r
        for r in res["styles"][0]["recommended_photo_ids"]
    ]
    # Because similarity is < 0.80 and genres diverge, photo-cand is rejected by dual-gated screening!
    assert "photo-cand" not in recs


def test_vectorized_select_style_recommendations_near_duplicates():
    """Verify vectorized _select_style_recommendations rejects near-duplicates (>0.90 similarity) and selects diverse candidates."""
    import numpy as np

    existing = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
    candidates = [
        ("c1", np.array([0.95, 0.312, 0.0], dtype=np.float32), {"rating": 5}),
        (
            "c1_dup",
            np.array([0.949, 0.315, 0.0], dtype=np.float32),
            {"rating": 4},
        ),  # Near duplicate to c1 (> 0.90)
        (
            "c2",
            np.array([0.70, 0.71, 0.0], dtype=np.float32),
            {"rating": 4},
        ),  # Visually diverse from c1 (sim ~0.88 <= 0.90)
    ]

    selected = style_upgrades._select_style_recommendations(
        candidates, existing, target_count=3
    )
    assert "c1" in selected
    assert "c2" in selected
    assert "c1_dup" not in selected


def test_profiles_and_models_compatibility():
    # Minor name variations
    assert style_upgrades._profiles_compatible("Adobe Standard", "Adobe Standard (v2)")
    assert style_upgrades._profiles_compatible("Nikon Profile A", "  nikon profile a ")
    # HDR styles must remain strictly separated
    assert not style_upgrades._profiles_compatible(
        "Adobe Standard + HDR", "Adobe Standard"
    )
    assert style_upgrades._profiles_compatible(
        "Adobe Standard + HDR", "Adobe Standard + HDR"
    )

    # Models compatibility
    assert style_upgrades._models_compatible("Nikon Z7", "NIKON Z 7")
    assert not style_upgrades._models_compatible("Nikon Z7", "Nikon Z8")


def test_is_stitched_panorama():
    assert style_upgrades._is_stitched_panorama({"filename": "DSC0123-Pano.dng"})
    assert style_upgrades._is_stitched_panorama({"filename": "photo_panorama.jpg"})
    assert style_upgrades._is_stitched_panorama(
        {"keywords": "stitched pano, mountains"}
    )
    assert style_upgrades._is_stitched_panorama(
        {"width": 6000, "height": 2000}
    )  # 3:1 ratio
    assert not style_upgrades._is_stitched_panorama(
        {"filename": "normal_photo.dng", "width": 4000, "height": 3000}
    )


def test_get_style_upgrade_recommendations_partitioned_profile_indexing(mocker):
    """Verify get_style_upgrade_recommendations partitions candidates by profile and excludes stitched panoramas."""
    mock_style = [
        {
            "style_id": "style-profile-opt",
            "style_name": "Style Profile Opt",
            "example_count": 10,
            "camera_profile": "Adobe Standard",
            "genre": "scene_landscape",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")

    mock_collection = mocker.MagicMock()
    mock_collection.get.side_effect = [
        # Pass 1: metadata scan
        {
            "ids": ["p1", "p2", "p_pano"],
            "metadatas": [
                {
                    "camera_profile": "Adobe Standard",
                    "rating": 5,
                    "scene_tags": ["scene_landscape"],
                },
                {
                    "camera_profile": "Nikon Standard",
                    "rating": 5,
                    "scene_tags": ["scene_landscape"],
                },
                {
                    "camera_profile": "Adobe Standard",
                    "filename": "pano-stitch.jpg",
                    "width": 6000,
                    "height": 2000,
                },
            ],
        },
        # Pass 2: embedding fetch for matching candidates only (p1 matches profile & genre; p2 profile mismatch; p_pano is stitched panorama)
        {
            "ids": ["p1"],
            "embeddings": [[1.0, 0.0, 0.0]],
        },
    ]
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = res["styles"][0]["recommended_photo_ids"]
    rec_ids = [r["globalPhotoId"] for r in recs]
    assert "p1" in rec_ids
    assert "p2" not in rec_ids
    assert "p_pano" not in rec_ids


def test_get_style_upgrade_recommendations_catalog_ids_filtering(mocker):
    """Verify get_style_upgrade_recommendations filters candidates by catalog_ids."""
    mock_style = [
        {
            "style_id": "style-cat",
            "style_name": "Style Cat",
            "example_count": 10,
            "camera_profile": "Adobe Standard",
            "genre": "scene_landscape",
        }
    ]
    mocker.patch("services.style_catalog.list_styles", return_value=mock_style)
    mocker.patch("services.style_catalog.get_style_examples", return_value=[])
    mocker.patch("services.chroma._ensure_initialized")

    mock_collection = mocker.MagicMock()
    mock_collection.get.side_effect = [
        {
            "ids": ["p_cat_a", "p_cat_b"],
            "metadatas": [
                {
                    "catalog_id": "cat_a",
                    "camera_profile": "Adobe Standard",
                    "rating": 5,
                    "scene_tags": ["scene_landscape"],
                },
                {
                    "catalog_id": "cat_b",
                    "camera_profile": "Adobe Standard",
                    "rating": 5,
                    "scene_tags": ["scene_landscape"],
                },
            ],
        },
        {
            "ids": ["p_cat_a"],
            "embeddings": [[1.0, 0.0, 0.0]],
        },
    ]
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations(catalog_ids=["cat_a"])
    recs = res["styles"][0]["recommended_photo_ids"]
    rec_ids = [r["globalPhotoId"] for r in recs]
    assert "p_cat_a" in rec_ids
    assert "p_cat_b" not in rec_ids
