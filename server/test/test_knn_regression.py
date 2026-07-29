import numpy as np

from services.knn_regression import predict_knn_local_regression


EMBEDDING_DIM = 32


def _neighbors(
    count: int = 20,
    *,
    canonical_settings=None,
    distance: float = 0.05,
    capture_step: float = 20.0,
):
    rng = np.random.default_rng(17)
    rows = []
    for index in range(count):
        settings = (
            canonical_settings(index)
            if callable(canonical_settings)
            else dict(canonical_settings or {"exposure": 0.5, "contrast": 10.0})
        )
        rows.append(
            {
                "photo_id": f"photo-{index:03d}",
                "distance": distance,
                "embedding": rng.normal(size=EMBEDDING_DIM).tolist(),
                "capture_time": 100.0 + index * capture_step,
                "rating": index % 5,
                "pick_status": 0,
                "canonical_settings": settings,
            }
        )
    return rows


def _predict(mocker, neighbors, **kwargs):
    mocker.patch(
        "services.training.query_similar_training_examples",
        return_value=neighbors,
    )
    return predict_knn_local_regression(
        query_embedding=np.random.default_rng(29).normal(size=EMBEDDING_DIM).tolist(),
        metadata={"camera_profile": "Adobe Standard"},
        current_settings={},
        **kwargs,
    )


def test_knn_regression_aborts_on_sparse_neighborhood(mocker):
    assert _predict(mocker, _neighbors(distance=0.20)) is None


def test_knn_regression_does_not_merge_visually_distinct_photos_in_same_ten_seconds(
    mocker,
):
    neighbors = _neighbors(capture_step=0.1)

    result = _predict(mocker, neighbors)

    assert result is not None
    assert result.matched_count == 20


def test_knn_regression_aborts_on_true_burst_duplicates(mocker):
    embedding = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    neighbors = _neighbors(capture_step=0.1)
    for row in neighbors:
        row["embedding"] = embedding

    assert _predict(mocker, neighbors) is None


def test_knn_regression_aborts_on_canonical_exposure_variance(mocker):
    neighbors = _neighbors(
        canonical_settings=lambda index: {"exposure": 2.0 if index % 2 == 0 else -2.0}
    )

    assert _predict(mocker, neighbors, max_exposure_std=1.25) is None


def test_knn_regression_uses_canonical_defaults_and_clamps_predictions(
    mocker,
    monkeypatch,
):
    captured_targets = {}

    class Model:
        def __init__(self, **_kwargs):
            pass

        def fit(self, _source, targets, *, sample_weight=None):
            captured_targets["values"] = targets.copy()
            captured_targets["weights"] = sample_weight
            return self

        def predict(self, source):
            result = np.mean(captured_targets["values"], axis=0, keepdims=True)
            result[:, :] = 99.0
            return np.repeat(result, len(source), axis=0)

    monkeypatch.setattr("services.knn_regression.ReducedRankRidge", Model)
    neighbors = _neighbors(
        canonical_settings=lambda index: (
            {"crop": {"right": 1.0}, "exposure": float(index % 2)}
            if index == 0
            else {"exposure": float(index % 2)}
        )
    )

    result = _predict(mocker, neighbors)

    assert result is not None
    assert result.recipe["global"]["crop"]["right"] == 1.0
    assert result.recipe["global"]["exposure"] <= 1.0
    assert result.confidence < 1.0


def test_knn_regression_success(mocker):
    result = _predict(mocker, _neighbors())

    assert result is not None
    assert result.engine == "knn_regression"
    assert 0.0 < result.confidence < 1.0
    assert result.matched_count == 20
    assert "global" in result.recipe
