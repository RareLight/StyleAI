import numpy as np
from services import predictive_engine


def test_extract_features():
    embedding = [0.1] * 768
    metadata = {
        "camera_profile": "Nikon Z7 Linear",
        "exp_luminance_mean": 0.6,
        "exp_contrast": 0.4,
    }
    features = predictive_engine._extract_features(embedding, metadata)
    assert features[0] == "Nikon Z7 Linear"
    assert features[1] == 0.1
    # Check that it appended defaults for missing metrics
    assert len(features) == 1 + 768 + 8


def test_pipeline_preprocessor():
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import StandardScaler, OneHotEncoder

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0]),
            ("num", StandardScaler(), slice(1, None)),
        ],
        remainder="passthrough",
    )

    X = np.array(
        [["Profile A", 0.1, 0.5], ["Profile B", 0.2, 0.6], ["Profile A", 0.3, 0.7]],
        dtype=object,
    )

    X_transformed = preprocessor.fit_transform(X)
    # 2 profile categories + 2 numerical features = 4 columns
    assert X_transformed.shape == (3, 4)
    # The categorical features are first
    assert X_transformed[0, 0] == 1.0  # Profile A
    assert X_transformed[0, 1] == 0.0  # Profile B
    assert X_transformed[1, 0] == 0.0
    assert X_transformed[1, 1] == 1.0


def test_predict_edits_universal_clamping(monkeypatch):
    from unittest.mock import MagicMock

    mock_model = MagicMock()
    # Simulate regression predicting extreme outliers: -130 Highlights, +120 Shadows
    mock_model.predict.return_value = np.array([[-130.0, 120.0]])

    meta_info = {
        "target_keys": ["highlights", "shadows"],
        "tier": "ml_direct",
        "slider_bounds": {
            "highlights": {"min": -80.0, "max": 0.0},
            "shadows": {"min": 0.0, "max": 60.0},
        },
    }

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("joblib.load", lambda path: mock_model)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr("json.load", lambda fp: meta_info)

    res = predictive_engine.predict_edits("style_test", [0.1] * 768, {})
    assert res is not None
    assert res["highlights"] == -80.0
    assert res["shadows"] == 60.0


def test_get_default_val():
    assert predictive_engine._get_default_val("crop_right") == 1.0
    assert predictive_engine._get_default_val("crop_bottom") == 1.0
    assert predictive_engine._get_default_val("cg_blending") == 50.0
    assert predictive_engine._get_default_val("exposure") == 0.0
    assert predictive_engine._get_default_val("curve_master_y_0") == 0.0
    assert predictive_engine._get_default_val("curve_master_y_15") == 255.0


def test_unflatten_canonical_settings_crop_averaging():
    # Even if aspect ratio deviates significantly, enforce width == height by averaging instead of deleting
    flat = {
        "exposure": 0.5,
        "crop_left": 0.1,
        "crop_right": 0.9,
        "crop_top": 0.2,
        "crop_bottom": 0.6,  # width=0.8, height=0.4
    }
    recipe = predictive_engine.unflatten_canonical_settings(flat)
    assert "crop" in recipe
    # avg_dim = (0.8 + 0.4) / 2 = 0.6
    # center_x = 0.5 -> left=0.2, right=0.8
    # center_y = 0.4 -> top=0.1, bottom=0.7
    assert round(recipe["crop"]["left"], 2) == 0.2
    assert round(recipe["crop"]["right"], 2) == 0.8
    assert round(recipe["crop"]["top"], 2) == 0.1
    assert round(recipe["crop"]["bottom"], 2) == 0.7


def test_curate_training_cluster_burst_deduplication():
    # Construct 4 photos in a burst (capture times within 10s, nearly identical embeddings)
    emb_base = np.array([0.1] * 768)
    emb_base = emb_base / np.linalg.norm(emb_base)

    # Photo 1: 3 stars, not picked
    meta1 = {"capture_time": 1000.0, "rating": 3, "pick_status": 0}
    can1 = {"exposure": 0.5}

    # Photo 2: 5 stars, not picked
    meta2 = {"capture_time": 1002.0, "rating": 5, "pick_status": 0}
    can2 = {"exposure": 0.6}

    # Photo 3: 5 stars, picked (should win as single hero shot due to pick tie-breaker)
    meta3 = {"capture_time": 1005.0, "rating": 5, "pick_status": 1}
    can3 = {"exposure": 0.7}

    # Photo 4: Separate scene 1 hour later (1000 + 3600), different embedding
    emb_other = np.zeros(768)
    emb_other[0] = 1.0
    meta4 = {"capture_time": 4600.0, "rating": 4, "pick_status": 0}
    can4 = {"exposure": 0.2}

    valid_examples = [
        (emb_base, meta1, can1),
        (emb_base, meta2, can2),
        (emb_base, meta3, can3),
        (emb_other, meta4, can4),
    ]

    curated, weights = predictive_engine._curate_training_cluster(valid_examples)
    assert len(curated) == 2
    assert len(weights) == 2

    # Verify the winner of the burst is Photo 3 (exposure 0.7)
    assert curated[0][2]["exposure"] == 0.7
    assert weights[0] == 1.0

    # Verify separate scene is preserved
    assert curated[1][2]["exposure"] == 0.2
    assert weights[1] == 1.0


def test_curate_training_cluster_density_weighting():
    # When multiple photos tie on rating, pick status, and complexity, they share normalized density weight
    emb = np.array([0.1] * 768)
    emb = emb / np.linalg.norm(emb)

    valid_examples = [
        (
            emb,
            {"capture_time": 100.0, "rating": 5, "pick_status": 1},
            {"exposure": 1.0},
        ),
        (
            emb,
            {"capture_time": 101.0, "rating": 5, "pick_status": 1},
            {"exposure": 1.0},
        ),
    ]

    curated, weights = predictive_engine._curate_training_cluster(valid_examples)
    assert len(curated) == 2
    assert weights[0] == 0.5
    assert weights[1] == 0.5
    assert sum(weights) == 1.0


def test_weighted_pls_regression():
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X = np.array([[0.0], [1.0], [2.0], [3.0]])
    Y = np.array([[0.0], [1.0], [2.0], [3.0]])
    weights = [1.0, 1.0, 1.0, 10.0]  # Heavily weight the last point

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "pls",
                predictive_engine.WeightedPLSRegression(n_components=1, scale=False),
            ),
        ]
    )
    model.fit(X, Y, pls__sample_weight=weights)
    pred = model.predict(np.array([[3.0]]))
    assert pred.shape == (1, 1)


def test_train_single_style_model_selection(monkeypatch):
    from unittest.mock import MagicMock

    mock_collection = MagicMock()
    # Mock 20 examples (Pillar 2: 15 <= N < 50 -> WeightedPLSRegression)
    ids_20 = [f"id_{i}" for i in range(20)]
    metas_20 = [
        {"canonical_settings": '{"exposure": 0.5}', "camera_profile": "Linear"}
        for _ in range(20)
    ]
    embs_20 = [[0.1] * 768 for _ in range(20)]

    mock_collection.get.return_value = {
        "ids": ids_20,
        "metadatas": metas_20,
        "embeddings": embs_20,
    }
    monkeypatch.setattr(
        predictive_engine.training_service,
        "_training_collection",
        mock_collection,
    )

    dumped_models = {}
    monkeypatch.setattr(
        "joblib.dump",
        lambda obj, path: dumped_models.update({path: obj}),
    )
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: MagicMock())

    predictive_engine._train_single_style("style_pls", ids_20)
    # Check that model was dumped and has 'pls' step
    assert len(dumped_models) == 1
    model = list(dumped_models.values())[0]
    assert "pls" in model.named_steps
    assert isinstance(
        model.named_steps["pls"],
        predictive_engine.WeightedPLSRegression,
    )

    # Now mock 55 examples (Pillar 3: N >= 50 -> ElasticNet)
    dumped_models.clear()
    ids_55 = [f"id_{i}" for i in range(55)]
    metas_55 = [
        {"canonical_settings": '{"exposure": 0.5}', "camera_profile": "Linear"}
        for _ in range(55)
    ]
    embs_55 = [[0.1] * 768 for _ in range(55)]
    mock_collection.get.return_value = {
        "ids": ids_55,
        "metadatas": metas_55,
        "embeddings": embs_55,
    }

    predictive_engine._train_single_style("style_elasticnet", ids_55)
    assert len(dumped_models) == 1
    model_en = list(dumped_models.values())[0]
    assert "elasticnet" in model_en.named_steps
    from sklearn.linear_model import ElasticNet

    assert isinstance(model_en.named_steps["elasticnet"], ElasticNet)


def test_cluster_bursts_vectorized():
    """Verify that vectorized _cluster_bursts accurately groups photos within delta_t <= 10s and cosine similarity >= 0.95."""
    emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    emb_a_near = np.array([0.999, 0.04, 0.0], dtype=np.float32)  # High similarity to A
    emb_b = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    valid_examples = [
        (emb_a, {"capture_time": 1000.0, "rating": 4}, {"exposure": 0.5}),
        (
            emb_a_near,
            {"capture_time": 1005.0, "rating": 5},
            {"exposure": 0.5},
        ),  # Within 10s burst of A
        (
            emb_b,
            {"capture_time": 1008.0, "rating": 3},
            {"exposure": -0.2},
        ),  # Within 10s but different visual
    ]

    curated, weights = predictive_engine._curate_training_cluster(valid_examples)
    # A and A_near should cluster together; B should remain separate
    assert len(curated) == 2
    # The hero shot from cluster A should be the one with rating 5 (emb_a_near)
    hero_ratings = [c[1].get("rating") for c in curated]
    assert 5 in hero_ratings
    assert 3 in hero_ratings
