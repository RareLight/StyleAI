import json
from types import SimpleNamespace

import numpy as np
import pytest

import config
from services import policy_runtime
from services import source_embeddings
from services.policy_evaluation import make_synthetic_policy_dataset
from services.policy_local import LocalResidualCorrector
from services.policy_targets import flatten_absolute_target
from services.rendering_state import rendering_state_from_settings


def _examples(count=12, *, camera_profile="Adobe Color", photo_prefix="photo"):
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
                "photo_id": f"{photo_prefix}-{index:03d}",
                "embedding": embedding.tolist(),
                "metadata": {
                    "source_provenance": "raw_preview",
                    "source_embedding_provenance": "raw_preview",
                    "source_embedding_fingerprint": f"fingerprint-{index}",
                    "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
                    "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
                    "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
                    "camera_profile": camera_profile,
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
        lambda **_kwargs: examples,
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
        "weighted_target_median",
        "reduced_rank_ridge",
        "weighted_pls",
        "multitask_elastic_net",
    }
    assert (
        artifact.validation["estimator_selection"]["selected_estimator"]
        == artifact.estimator_name
    )
    assert len(artifact.local_correctors) == len(artifact.policy_ids)
    assert len(artifact.validation["local_residual_correction"]) == len(
        artifact.policy_ids
    )
    assert artifact.example_embeddings[0].dtype == np.float32
    assert policies[0]["estimator_name"] == artifact.estimator_name
    assert policies[0]["local_correction_enabled"] is False
    assert policies[0]["training_status"] == "Global conditional policy"
    assert all(
        name.startswith("image_embedding_") for name in artifact.coverage.feature_names_
    )
    assert prediction is not None
    assert prediction.policy_id == policies[0]["policy_id"]
    assert prediction.applied == prediction.target

    policy_runtime.invalidate_runtime_cache()
    reloaded = policy_runtime.list_active_policies()
    assert reloaded == policies


def test_incompatible_target_schema_requires_rebuild(policy_database, monkeypatch):
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: _examples(),
    )
    result = policy_runtime.rebuild_active_generation(seed=9)
    connection = policy_runtime.policy_store.connect_policy_store(str(policy_database))
    try:
        connection.execute(
            "UPDATE policy_v2_generations SET target_schema_version = ? "
            "WHERE generation_id = ?",
            ("policy-target-v2", result["generation_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    policy_runtime.invalidate_runtime_cache()

    assert policy_runtime.has_active_generation() is False


def test_incompatible_algorithm_version_requires_rebuild(policy_database, monkeypatch):
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: _examples(),
    )
    result = policy_runtime.rebuild_active_generation(seed=9)
    connection = policy_runtime.policy_store.connect_policy_store(str(policy_database))
    try:
        connection.execute(
            "UPDATE policy_v2_generations SET algorithm_version = ? "
            "WHERE generation_id = ?",
            ("editing-policy-v2.6", result["generation_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    policy_runtime.invalidate_runtime_cache()

    assert policy_runtime.has_active_generation() is False


def test_rendering_auto_uses_effective_metadata_for_features_and_calibration(
    monkeypatch,
):
    current = rendering_state_from_settings(
        {"CameraProfile": "Current", "HDREditMode": 0},
        camera_make="Example",
        camera_model="Camera",
    )
    target = rendering_state_from_settings(
        {"CameraProfile": "Target", "HDREditMode": 1},
        camera_make="Example",
        camera_model="Camera",
    )
    artifact = SimpleNamespace(
        feature_names=("feature",),
        generation_id="generation",
        policy_ids=("policy",),
        policy_names=("Policy",),
        partition_key=policy_runtime.rendering_partition_key(target),
        target_keys=("global.exposure",),
        example_photo_ids=(("photo",),),
    )
    monkeypatch.setattr(
        policy_runtime,
        "_load_active_artifacts",
        lambda: {artifact.partition_key: artifact},
    )
    monkeypatch.setattr(
        policy_runtime,
        "_load_active_rendering_selector",
        lambda: SimpleNamespace(
            select=lambda **_kwargs: {
                "schema_version": "rendering-state-v1",
                "current": current,
                "proposed": target,
                "effective": target,
                "profile_mode": "auto",
                "hdr_mode": "auto",
                "abstention_reason": None,
            }
        ),
    )
    observed = {}

    def source_row(metadata, _embedding):
        observed["source_metadata"] = metadata
        return np.asarray([1.0]), ("feature",)

    def predict_artifact(_artifact, **kwargs):
        observed["prediction_metadata"] = kwargs["metadata"]
        return policy_runtime.PartitionArtifactPrediction(
            policy_index=0,
            confidence=1.0,
            entropy=0.0,
            flat_target={"global.exposure": 0.5},
        )

    monkeypatch.setattr(policy_runtime, "_source_row", source_row)
    monkeypatch.setattr(policy_runtime, "predict_partition_artifact", predict_artifact)
    monkeypatch.setattr(policy_runtime, "_custom_policy_names", lambda: {})

    prediction = policy_runtime.predict_absolute_edit(
        embedding=[1.0],
        metadata={
            "camera_make": "Example",
            "camera_model": "Camera",
            "rendering_state": current,
        },
        current_settings={"Exposure2012": 0.0},
        profile_mode="auto",
        hdr_mode="auto",
        source_provenance="raw_preview",
    )

    assert prediction is not None
    assert observed["source_metadata"]["rendering_state"] == target
    assert observed["prediction_metadata"]["rendering_state"] == target


def test_inference_does_not_cross_camera_profile_partitions(
    policy_database, monkeypatch
):
    examples = [
        *_examples(camera_profile="Adobe Color", photo_prefix="color"),
        *_examples(camera_profile="Camera Standard", photo_prefix="standard"),
    ]
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
    )
    policy_runtime.rebuild_active_generation(seed=9)
    artifacts = policy_runtime._load_active_artifacts()
    color_artifact = artifacts["sdr|adobe color"]

    assert (
        policy_runtime.predict_absolute_edit(
            embedding=examples[0]["embedding"],
            metadata={**examples[0]["metadata"], "camera_profile": "Untrained Profile"},
            current_settings={},
        )
        is None
    )
    assert (
        policy_runtime.predict_absolute_edit(
            embedding=examples[-1]["embedding"],
            metadata=examples[-1]["metadata"],
            current_settings={},
            policy_override=color_artifact.policy_ids[0],
        )
        is None
    )


def test_successive_rebuilds_bound_generation_and_example_history(
    policy_database, monkeypatch
):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
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


def test_partition_estimator_skips_elastic_net_for_wide_embedding_features():
    dataset = make_synthetic_policy_dataset(
        seed=19,
        n_examples=48,
        n_source_features=policy_runtime.MAX_ELASTIC_NET_FEATURES + 1,
        n_targets=4,
        n_policies=1,
    )

    name, _, validation = policy_runtime._cross_validated_estimator(
        dataset.source_features,
        dataset.target_values,
        dataset.burst_group_ids,
        np.ones(len(dataset.source_features)),
    )

    assert name != "multitask_elastic_net"
    assert "multitask_elastic_net" not in validation["candidates"]
    assert "multitask_elastic_net" in validation["skipped_estimators"]


def test_policy_initialization_uses_cross_fitted_target_residuals():
    dataset = make_synthetic_policy_dataset(
        seed=61,
        n_examples=240,
        n_source_features=48,
        n_targets=6,
        n_policies=2,
    )

    labels = policy_runtime._cross_fitted_residual_labels(
        dataset.source_features,
        dataset.target_values,
        dataset.burst_group_ids,
        np.ones(len(dataset.source_features)),
        n_policies=2,
        expert_factory=policy_runtime.default_estimator_factories()[
            "reduced_rank_ridge"
        ],
        seed=17,
    )
    repeated = policy_runtime._cross_fitted_residual_labels(
        dataset.source_features,
        dataset.target_values,
        dataset.burst_group_ids,
        np.ones(len(dataset.source_features)),
        n_policies=2,
        expert_factory=policy_runtime.default_estimator_factories()[
            "reduced_rank_ridge"
        ],
        seed=17,
    )

    np.testing.assert_array_equal(labels, repeated)
    assert set(labels) == {0, 1}


def test_discovery_validation_sample_is_bounded_group_safe_and_deterministic():
    groups = np.asarray([f"group-{index // 2}" for index in range(1600)])

    first = policy_runtime._bounded_group_sample(groups, maximum=601)
    second = policy_runtime._bounded_group_sample(groups, maximum=601)

    np.testing.assert_array_equal(first, second)
    assert len(first) <= 601
    selected = set(int(index) for index in first)
    for index in first:
        paired = int(index) + 1 if int(index) % 2 == 0 else int(index) - 1
        assert paired in selected


def test_validated_local_correction_is_applied_before_target_clamping(
    policy_database,
    monkeypatch,
):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
    )
    policy_runtime.rebuild_active_generation(seed=9)
    artifact = next(iter(policy_runtime._load_active_artifacts().values()))
    embeddings = np.stack(
        [np.asarray(example["embedding"], dtype=np.float64) for example in examples]
    )
    target_count = len(artifact.target_keys)
    artifact.local_correctors[0] = LocalResidualCorrector(
        embeddings=embeddings,
        residuals=np.full((len(embeddings), target_count), 1e6),
        groups=np.asarray([f"group-{index}" for index in range(len(embeddings))]),
        photo_ids=np.asarray([example["photo_id"] for example in examples]),
        sample_weight=np.ones(len(embeddings)),
        target_scales=np.ones(target_count),
        minimum_neighbors=1,
    )

    prediction = policy_runtime.predict_absolute_edit(
        embedding=examples[4]["embedding"],
        metadata=examples[4]["metadata"],
        current_settings={},
    )

    assert prediction is not None
    flattened = flatten_absolute_target(
        prediction.target,
        include_applicability=True,
    )
    for key in artifact.target_keys:
        assert flattened[key] == pytest.approx(artifact.slider_bounds[0][key][1])


def test_upgrade_recommendations_batch_candidate_assignment(
    policy_database,
    monkeypatch,
):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
    )
    policy_runtime.rebuild_active_generation(seed=9)
    artifact = next(iter(policy_runtime._load_active_artifacts().values()))
    assignment_batch_sizes = []
    original_assignments = artifact.mixture.assignments

    def tracked_assignments(source):
        assignment_batch_sizes.append(len(source))
        return original_assignments(source)

    monkeypatch.setattr(artifact.mixture, "assignments", tracked_assignments)

    class FakeCollection:
        def __init__(self):
            self.query_count = 0
            self.get_count = 0
            self.query_kwargs = None

        def count(self):
            return 100

        def query(self, **kwargs):
            self.query_count += 1
            self.query_kwargs = kwargs
            return {
                "ids": [["candidate-a", "candidate-b"]],
                "metadatas": [[{}, {}]],
                "distances": [[0.01, 0.02]],
            }

        def get(self, **_kwargs):
            self.get_count += 1
            return {
                "ids": ["candidate-a", "candidate-b"],
                "metadatas": [examples[3]["metadata"], examples[7]["metadata"]],
                "embeddings": [
                    examples[3]["embedding"],
                    examples[7]["embedding"],
                ],
            }

    from services import chroma as chroma_service

    collection = FakeCollection()
    monkeypatch.setattr(chroma_service, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(chroma_service, "collection", collection)

    payloads = policy_runtime.get_upgrade_recommendations()

    assert payloads
    assert collection.query_count == 1
    assert collection.get_count == 1
    assert collection.query_kwargs["include"] == ["distances"]
    assert assignment_batch_sizes == [2]


def test_upgrade_recommendations_skip_complete_policies_before_query(
    policy_database,
    monkeypatch,
):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
    )
    policy_runtime.rebuild_active_generation(seed=9)

    class FakeCollection:
        def __init__(self):
            self.query_count = 0

        def count(self):
            return 100

        def query(self, **_kwargs):
            self.query_count += 1
            return {"ids": [], "distances": []}

    from services import chroma as chroma_service

    collection = FakeCollection()
    monkeypatch.setattr(chroma_service, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(chroma_service, "collection", collection)

    payloads = policy_runtime.get_upgrade_recommendations(target_examples_per_policy=1)

    assert payloads == []
    assert collection.query_count == 0


def test_upgrade_recommendation_budget_is_applied_after_eligibility(
    policy_database,
    monkeypatch,
):
    from types import SimpleNamespace
    from services import chroma as chroma_service
    from services import policy_feedback

    artifact = SimpleNamespace(
        partition_key="default",
        generation_id="generation-1",
        policy_ids=["complete", "needs-examples"],
        policy_names=["Complete", "Needs Examples"],
        image_anchors=[
            [np.asarray([1.0, 0.0])],
            [np.asarray([0.0, 1.0])],
        ],
        example_photo_ids=[[], []],
        example_embeddings=[
            np.asarray([[1.0, 0.0]]),
            np.asarray([[0.0, 1.0]]),
        ],
        mixture=SimpleNamespace(
            training_responsibilities_=np.asarray(
                [
                    [1.0, 0.0],
                    [1.0, 0.0],
                    [1.0, 0.0],
                    [1.0, 0.0],
                    [0.0, 1.0],
                ]
            )
        ),
        local_correctors=[None, None],
        descriptors=[{}, {}],
        camera_profile="Adobe Color",
        estimator_name="ridge",
    )
    monkeypatch.setattr(
        policy_runtime, "_load_active_artifacts", lambda: {"default": artifact}
    )
    monkeypatch.setattr(policy_runtime, "_custom_policy_names", lambda: {})
    monkeypatch.setattr(
        policy_feedback,
        "capture_recommendation_review",
        lambda **_kwargs: "review-1",
    )

    class FakeCollection:
        def __init__(self):
            self.query_embeddings = None

        def count(self):
            return 10

        def query(self, **kwargs):
            self.query_embeddings = kwargs["query_embeddings"]
            return {"ids": [[]], "distances": [[]]}

    collection = FakeCollection()
    monkeypatch.setattr(chroma_service, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(chroma_service, "collection", collection)

    payloads = policy_runtime.get_upgrade_recommendations(
        top_policies_limit=1,
        target_examples_per_policy=3,
    )

    assert [payload["policy_id"] for payload in payloads] == ["needs-examples"]
    assert collection.query_embeddings == [[0.0, 1.0]]


def test_failed_candidate_preserves_active_generation(policy_database, monkeypatch):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
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


def test_canceled_candidate_preserves_active_generation(policy_database, monkeypatch):
    examples = _examples()
    monkeypatch.setattr(
        policy_runtime.training_service,
        "list_training_examples_with_embeddings",
        lambda **_kwargs: examples,
    )
    first = policy_runtime.rebuild_active_generation(seed=4)

    with pytest.raises(InterruptedError, match="canceled"):
        policy_runtime.rebuild_active_generation(
            seed=5,
            cancel_requested=lambda: True,
        )

    policy_runtime.invalidate_runtime_cache()
    assert (
        policy_runtime._load_active_artifacts().popitem()[1].generation_id
        == first["generation_id"]
    )
