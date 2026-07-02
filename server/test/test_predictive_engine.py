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
