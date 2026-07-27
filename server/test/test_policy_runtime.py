import json

import numpy as np
import pytest

import config
from services import policy_runtime
from services.policy_evaluation import make_synthetic_policy_dataset


def _examples(count=12):
    rows = []
    for index in range(count):
        angle = 0.12 * index
        embedding = np.asarray(
            [np.cos(angle), np.sin(angle), 0.2 + index / 100.0],
            dtype=np.float64,
        )
        embedding /= np.linalg.norm(embedding)
        rows.append(
            {
                "photo_id": f"photo-{index:03d}",
                "embedding": embedding.tolist(),
                "metadata": {
                    "camera_profile": "Adobe Color",
                    "camera_make": "Example",
                    "camera_model": "Camera",
                    "lens": "Prime",
                    "capture_time": float(index * 60),
                    "exp_luminance_mean": 0.25 + index / 100.0,
                    "exp_contrast": 0.3 + index / 200.0,
                    "canonical_settings": json.dumps(
                        {
                            "exposure": -0.3 + index * 0.05,
                            "contrast": -10.0 + index * 2.0,
                            "white_balance": "As Shot",
                        }
                    ),
                },
            }
        )
    return rows


@pytest.fixture
def policy_database(tmp_path, monkeypatch):
    database = tmp_path / "styleai.db"
    database.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(database))
    policy_runtime.invalidate_runtime_cache()
    yield database
    policy_runtime.invalidate_runtime_cache()


def test_generation_round_trip_and_absolute_inference(policy_database, monkeypatch):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda: examples,
    )

    result = policy_runtime.rebuild_active_generation(seed=9)
    policies = policy_runtime.list_active_policies()
    prediction = policy_runtime.predict_absolute_edit(
        embedding=examples[4]["embedding"],
        metadata=examples[4]["metadata"],
        current_settings={"Exposure2012": -2.0, "Contrast2012": 80.0},
        strength=1.0,
    )

    assert result["partition_count"] == 1
    assert result["policy_count"] == 1
    assert policies[0]["recommendation_version"] == "policy-v2"
    artifact = next(iter(policy_runtime._load_active_artifacts().values()))
    assert artifact.estimator_name in {
        "reduced_rank_ridge",
        "weighted_pls",
        "multitask_elastic_net",
    }
    assert (
        artifact.validation["estimator_selection"]["selected_estimator"]
        == artifact.estimator_name
    )
    assert all(
        name.startswith("image_embedding_") for name in artifact.coverage.feature_names_
    )
    assert prediction is not None
    assert prediction.policy_id == policies[0]["policy_id"]
    assert prediction.applied == prediction.target

    policy_runtime.invalidate_runtime_cache()
    reloaded = policy_runtime.list_active_policies()
    assert reloaded == policies


def test_successive_rebuilds_bound_generation_and_example_history(
    policy_database, monkeypatch
):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda: examples,
    )

    generation_ids = [
        policy_runtime.rebuild_active_generation(seed=seed)["generation_id"]
        for seed in (1, 2, 3)
    ]

    connection = policy_runtime.policy_store.connect_policy_store(str(policy_database))
    try:
        generations = connection.execute(
            "SELECT generation_id, status FROM policy_v2_generations"
        ).fetchall()
        assert len(generations) == 1
        assert generations[0]["status"] == "active"
        assert generations[0]["generation_id"] == generation_ids[-1]
        assert connection.execute("SELECT COUNT(*) FROM policy_v2_examples").fetchone()[
            0
        ] == len(examples)
    finally:
        connection.close()
    assert not (
        policy_database / policy_runtime.MODEL_DIRECTORY_NAME / generation_ids[0]
    ).exists()


def test_partition_estimator_selection_uses_grouped_held_out_accuracy():
    dataset = make_synthetic_policy_dataset(
        seed=17,
        n_examples=120,
        n_source_features=16,
        n_targets=6,
        n_policies=1,
    )

    name, _, validation = policy_runtime._cross_validated_estimator(
        dataset.source_features,
        dataset.target_values,
        dataset.burst_group_ids,
        np.ones(len(dataset.source_features)),
    )

    assert name == "multitask_elastic_net"
    assert (
        validation["candidates"][name]["normalized_rmse"]
        < validation["candidates"]["reduced_rank_ridge"]["normalized_rmse"]
    )


def test_failed_candidate_preserves_active_generation(policy_database, monkeypatch):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda: examples,
    )
    first = policy_runtime.rebuild_active_generation(seed=4)
    monkeypatch.setattr(
        policy_runtime,
        "_fit_partition",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fit failed")),
    )

    with pytest.raises(RuntimeError, match="fit failed"):
        policy_runtime.rebuild_active_generation(seed=5)

    policy_runtime.invalidate_runtime_cache()
    assert policy_runtime.list_active_policies()
    assert (
        policy_runtime._load_active_artifacts().popitem()[1].generation_id
        == first["generation_id"]
    )
