import pytest
import numpy as np

from services.knn_regression import predict_knn_local_regression


def test_knn_regression_aborts_on_sparse_neighborhood(mocker):
    # Mock ChromaDB returning distant neighbors
    mock_query = mocker.patch("services.training.query_similar_training_examples")
    mock_query.return_value = [
        {"distance": 0.20, "embedding": [0.1] * 2340, "capture_time": 100.0}
    ] * 50

    result = predict_knn_local_regression(
        query_embedding=[0.0] * 2340,
        metadata={"camera_profile": "Adobe Standard"},
        current_settings={},
    )
    assert result is None  # Should abort due to max_distance cutoff


def test_knn_regression_aborts_on_burst_duplicates(mocker):
    # Mock ChromaDB returning 50 neighbors all within the same 10s burst window
    mock_query = mocker.patch("services.training.query_similar_training_examples")
    mock_query.return_value = [
        {"distance": 0.05, "embedding": [0.1] * 2340, "capture_time": 100.0 + (i * 0.1)}
        for i in range(50)
    ]

    result = predict_knn_local_regression(
        query_embedding=[0.0] * 2340,
        metadata={"camera_profile": "Adobe Standard"},
        current_settings={},
    )
    # Deduplication should leave only 1 neighbor, which is < min_neighbors
    assert result is None


def test_knn_regression_aborts_on_high_variance(mocker):
    # Mock ChromaDB returning valid, deduplicated neighbors but with wildly conflicting Exposure targets
    mock_query = mocker.patch("services.training.query_similar_training_examples")
    neighbors = []
    for i in range(20):
        neighbors.append({
            "distance": 0.05,
            "embedding": np.random.randn(2340).tolist(),
            "capture_time": float(i * 20),
            "canonical_settings": {"Exposure2012": 2.0 if i % 2 == 0 else -2.0}
        })
    mock_query.return_value = neighbors

    result = predict_knn_local_regression(
        query_embedding=np.random.randn(2340).tolist(),
        metadata={"camera_profile": "Adobe Standard"},
        current_settings={},
    )
    # StdDev of Exposure (-2 to 2) is ~2.0, which is > max_exposure_std (1.25)
    assert result is None


def test_knn_regression_success(mocker):
    # Mock ChromaDB returning a healthy neighborhood
    mock_query = mocker.patch("services.training.query_similar_training_examples")
    neighbors = []
    for i in range(20):
        neighbors.append({
            "distance": 0.05,
            "embedding": np.random.randn(2340).tolist(),
            "capture_time": float(i * 20),
            "canonical_settings": {"Exposure2012": 0.5, "Contrast2012": 10}
        })
    mock_query.return_value = neighbors

    result = predict_knn_local_regression(
        query_embedding=np.random.randn(2340).tolist(),
        metadata={"camera_profile": "Adobe Standard"},
        current_settings={},
    )
    
    assert result is not None
    assert result.engine == "knn_regression"
    assert result.confidence == 1.0
    assert result.matched_count == 20
    assert "global" in result.recipe
