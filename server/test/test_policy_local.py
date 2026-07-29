import numpy as np

from services.policy_local import LocalResidualCorrector


def _clustered_embeddings(count: int = 80) -> np.ndarray:
    rng = np.random.default_rng(17)
    centers = np.asarray(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]
    )
    labels = np.arange(count) % 2
    embeddings = centers[labels] + rng.normal(0.0, 0.025, (count, 4))
    return embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)


def test_structured_local_residuals_are_enabled_and_reduce_validation_error():
    embeddings = _clustered_embeddings()
    labels = np.arange(len(embeddings)) % 2
    residuals = np.column_stack(
        [
            np.where(labels == 0, 8.0, -8.0),
            np.where(labels == 0, -4.0, 4.0),
        ]
    )

    corrector, diagnostics = LocalResidualCorrector.fit_validated(
        embeddings,
        residuals,
        groups=np.asarray([f"burst-{index}" for index in range(len(embeddings))]),
        photo_ids=np.asarray([f"photo-{index}" for index in range(len(embeddings))]),
        sample_weight=np.ones(len(embeddings)),
        target_scales=np.asarray([20.0, 10.0]),
    )

    assert corrector is not None
    assert diagnostics["enabled"] is True
    assert diagnostics["corrected_normalized_rmse"] < (
        diagnostics["baseline_normalized_rmse"] * 0.95
    )
    prediction = corrector.predict(embeddings[0])
    assert prediction is not None
    np.testing.assert_allclose(prediction, residuals[0], atol=1.5)


def test_unstructured_residuals_do_not_enable_a_local_corrector():
    rng = np.random.default_rng(29)
    embeddings = _clustered_embeddings(120)
    residuals = rng.normal(0.0, 5.0, (len(embeddings), 3))

    corrector, diagnostics = LocalResidualCorrector.fit_validated(
        embeddings,
        residuals,
        groups=np.asarray([f"burst-{index}" for index in range(len(embeddings))]),
        photo_ids=np.asarray([f"photo-{index}" for index in range(len(embeddings))]),
        sample_weight=np.ones(len(embeddings)),
        target_scales=np.asarray([10.0, 10.0, 10.0]),
    )

    assert corrector is None
    assert diagnostics["enabled"] is False


def test_prediction_abstains_for_distant_or_sparse_neighborhoods():
    embeddings = _clustered_embeddings(40)
    residuals = np.tile(np.asarray([3.0, -2.0]), (len(embeddings), 1))
    corrector, _ = LocalResidualCorrector.fit_validated(
        embeddings,
        residuals,
        groups=np.asarray([f"burst-{index}" for index in range(len(embeddings))]),
        photo_ids=np.asarray([f"photo-{index}" for index in range(len(embeddings))]),
        sample_weight=np.ones(len(embeddings)),
        target_scales=np.asarray([10.0, 10.0]),
    )

    assert corrector is not None
    assert corrector.predict(np.asarray([0.0, 0.0, 1.0, 0.0])) is None
    corrector.minimum_neighbors = len(corrector.embeddings) + 1
    assert corrector.predict(embeddings[0]) is None


def test_residual_bank_is_bounded_and_selected_deterministically():
    embeddings = _clustered_embeddings(160)
    labels = np.arange(len(embeddings)) % 2
    residuals = np.column_stack(
        [np.where(labels == 0, 5.0, -5.0), np.zeros(len(embeddings))]
    )
    arguments = {
        "groups": np.asarray([f"burst-{index}" for index in range(len(embeddings))]),
        "photo_ids": np.asarray([f"photo-{index}" for index in range(len(embeddings))]),
        "sample_weight": np.ones(len(embeddings)),
        "target_scales": np.asarray([10.0, 10.0]),
        "maximum_bank_size": 50,
    }

    first, _ = LocalResidualCorrector.fit_validated(
        embeddings,
        residuals,
        **arguments,
    )
    second, _ = LocalResidualCorrector.fit_validated(
        embeddings,
        residuals,
        **arguments,
    )

    assert first is not None
    assert second is not None
    assert len(first.embeddings) == 50
    np.testing.assert_array_equal(first.photo_ids, second.photo_ids)
