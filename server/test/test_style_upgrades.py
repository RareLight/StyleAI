"""Tests for the Active Style Upgrade Assistant service."""

from services import style_upgrades


def test_hero_score():
    """Verify hero shot scoring with rating and fallback metrics."""
    # 5 stars + edited = 2.0 + 0.5 = 2.5
    assert style_upgrades._hero_score({"rating": 5, "is_edited": True}) == 2.5
    # 3 stars unedited = 1.2
    assert style_upgrades._hero_score({"rating": 3, "is_edited": False}) == 1.2
    # No rating, picked flag + edited = 1.0 + 0.5 = 1.5
    assert style_upgrades._hero_score({"pick_status": 1, "is_edited": True}) == 1.5
    # Rejected flag unedited = -2.0
    assert style_upgrades._hero_score({"pick_status": -1, "is_edited": False}) == -2.0
    # Empty / invalid string values
    assert (
        style_upgrades._hero_score({"rating": "invalid", "pick_status": "none"}) == 0.0
    )


def test_farthest_point_sampling_diversity():
    """Verify Farthest Point Sampling maximizes minimum distance (Max-Min diversity)."""
    # Suppose existing embedding is along x-axis [1, 0, 0]
    existing = [[1.0, 0.0, 0.0]]

    # Candidates:
    # cand1 is close to existing [0.99, 0.1, 0]
    # cand2 is orthogonal [0, 1, 0] -> farthest!
    # cand3 is opposite [-1, 0, 0] -> even farther!
    candidates = [
        ("close", [0.99, 0.1, 0.0], {"rating": 0}),
        ("ortho", [0.0, 1.0, 0.0], {"rating": 0}),
        ("opposite", [-1.0, 0.0, 0.0], {"rating": 0}),
    ]

    selected = style_upgrades._farthest_point_sampling(
        candidates, existing, target_count=2
    )
    assert len(selected) == 2
    assert selected[0] == "opposite"  # Farthest first
    assert selected[1] == "ortho"  # Second farthest


def test_farthest_point_sampling_vectorized_edge_cases():
    """Verify vectorized farthest point sampling handles zero vectors and single candidates without errors."""
    import numpy as np

    existing = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
    candidates = [
        ("zero_vec", np.zeros(3, dtype=np.float32), {"rating": 5}),
        ("normal_vec", np.array([0.0, 1.0, 0.0], dtype=np.float32), {"rating": 3}),
    ]
    selected = style_upgrades._farthest_point_sampling(
        candidates, existing, target_count=5
    )
    assert len(selected) == 2
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
    assert len(styles) == 3

    # Sorted by closest to upgrading: Style A (needs 1 to 15), Style B (needs 4 to 50), Style C (0 needed)
    s_a = next(s for s in styles if s["style_id"] == "style-a")
    assert s_a["needed_count"] == 1
    assert "Supervised PLS" in s_a["target_tier"]
    assert not s_a["is_highest_tier"]

    s_b = next(s for s in styles if s["style_id"] == "style-b")
    assert s_b["needed_count"] == 4
    assert "Elastic Net" in s_b["target_tier"]
    assert not s_b["is_highest_tier"]

    s_c = next(s for s in styles if s["style_id"] == "style-c")
    assert s_c["needed_count"] == 0
    assert s_c["is_highest_tier"]
    assert s_c["recommended_photo_ids"] == []


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
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        "metadatas": [
            {"camera_profile": "Adobe Standard", "is_edited": False, "rating": 5},
            {"camera_profile": "Adobe Standard", "is_edited": True, "rating": 3},
            {"camera_profile": "Adobe Standard", "is_edited": False, "rating": 4},
        ],
    }
    mocker.patch("services.chroma.collection", mock_collection)

    res = style_upgrades.get_style_upgrade_recommendations()
    recs = res["styles"][0]["recommended_photo_ids"]
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
