import sqlite3

import pytest

from services import policy_store


@pytest.fixture
def store(tmp_path):
    connection = policy_store.connect_policy_store(str(tmp_path / "styleai.db"))
    yield connection
    connection.close()


def _add_model(connection, generation_id, policy_id="policy-1"):
    policy_store.add_policy_model(
        connection,
        generation_id=generation_id,
        policy_id=policy_id,
        hard_partition_key="sdr",
        expert_index=0,
        estimator_type="hierarchical_reduced_rank_ridge",
        artifact_name=f"{generation_id}/{policy_id}.json",
        effective_sample_count=24.5,
    )


def test_clean_policy_schema_is_created_without_legacy_style_tables(store):
    tables = {
        row[0]
        for row in store.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert set(policy_store.V2_TABLES).issubset(tables)
    assert "styles" not in tables
    assert "style_examples" not in tables


def test_generation_activation_is_atomic_and_unique(store):
    first = policy_store.create_generation(
        store,
        generation_id="generation-1",
        algorithm_version="policy-v2",
        feature_schema_version="features-v1",
        target_schema_version="targets-v1",
    )
    _add_model(store, first)
    policy_store.activate_generation(store, first)

    second = policy_store.create_generation(
        store,
        generation_id="generation-2",
        algorithm_version="policy-v2",
        feature_schema_version="features-v1",
        target_schema_version="targets-v1",
    )
    _add_model(store, second)
    policy_store.activate_generation(store, second)

    assert policy_store.get_active_generation(store)["generation_id"] == second
    statuses = dict(
        store.execute(
            "SELECT generation_id, status FROM policy_v2_generations"
        ).fetchall()
    )
    assert statuses == {"generation-1": "retired", "generation-2": "active"}


def test_custom_policy_name_is_persisted_for_active_policy(store):
    generation = policy_store.create_generation(
        store,
        algorithm_version="policy-v2",
        feature_schema_version="features-v1",
        target_schema_version="targets-v1",
    )
    _add_model(store, generation)
    policy_store.activate_generation(store, generation)

    assert policy_store.rename_policy(
        store,
        policy_id="policy-1",
        custom_name="My Soft Contrast",
    )
    assert policy_store.list_policy_custom_names(store) == {
        "policy-1": "My Soft Contrast"
    }


def test_activation_failure_preserves_existing_active_generation(store):
    active = policy_store.create_generation(
        store,
        generation_id="active",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    _add_model(store, active)
    policy_store.activate_generation(store, active)
    empty = policy_store.create_generation(
        store,
        generation_id="empty",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )

    with pytest.raises(ValueError, match="without models"):
        policy_store.activate_generation(store, empty)

    assert policy_store.get_active_generation(store)["generation_id"] == active


def test_recovery_fails_only_interrupted_builds(store):
    active = policy_store.create_generation(
        store,
        generation_id="active",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    _add_model(store, active)
    policy_store.activate_generation(store, active)
    policy_store.create_generation(
        store,
        generation_id="interrupted",
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )

    assert policy_store.recover_incomplete_generations(store) == 1
    assert policy_store.get_active_generation(store)["generation_id"] == active
    status = store.execute(
        "SELECT status FROM policy_v2_generations WHERE generation_id = 'interrupted'"
    ).fetchone()[0]
    assert status == "failed"


def test_reset_removes_all_derived_policy_state(store):
    store.execute(
        """
        INSERT INTO policy_v2_examples (
            photo_id, source_provenance, feature_schema_version,
            source_features_json, target_schema_version, target_values_json,
            created_at, updated_at
        ) VALUES ('photo-1', 'raw_preview', 'f1', '[]', 't1', '{}', 'now', 'now')
        """
    )
    store.execute(
        """
        INSERT INTO policy_v2_custom_names (policy_id, custom_name, updated_at)
        VALUES ('policy-1', 'Custom', 'now')
        """
    )
    store.commit()
    generation = policy_store.create_generation(
        store,
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    _add_model(store, generation)

    policy_store.reset_policy_v2(store)

    assert policy_store.policy_store_stats(store) == {
        "examples": 0,
        "generations": 0,
        "models": 0,
        "memberships": 0,
    }
    assert (
        store.execute("SELECT COUNT(*) FROM policy_v2_custom_names").fetchone()[0] == 0
    )


def test_recommendation_feedback_is_atomic_and_survives_policy_reset(store):
    policy_store.upsert_recommendation_review(
        store,
        review_id="review-1",
        generation_id="generation-1",
        policy_id="policy-1",
        policy_index=0,
        hard_partition_key="sdr",
        target_count=2,
        existing_photo_ids=["trained-1"],
        algorithm_version="v2",
        feature_schema_version="f1",
        recommendation_version="policy-v2",
        candidates=[
            {
                "photo_id": "candidate-1",
                "responsibilities": [0.9, 0.1],
                "assignment_entropy": 0.2,
                "coverage_gain": 0.6,
                "metadata": {"rating": 4},
                "recommended_rank": 1,
            },
            {
                "photo_id": "candidate-2",
                "responsibilities": [0.8, 0.2],
                "assignment_entropy": 0.3,
                "coverage_gain": 0.2,
                "metadata": {},
            },
        ],
    )
    result = policy_store.record_recommendation_feedback(
        store,
        review_id="review-1",
        policy_id="policy-1",
        labels=[
            {
                "photo_id": "candidate-1",
                "policy_match": True,
                "useful": False,
            }
        ],
    )
    policy_store.reset_policy_v2(store)

    assert result == {"updated": 1, "requested": 1}
    reviews = policy_store.list_recommendation_reviews(store)
    assert len(reviews) == 1
    assert reviews[0]["existing_photo_ids"] == ["trained-1"]
    assert reviews[0]["candidates"][0]["policy_match"] is True
    assert reviews[0]["candidates"][0]["useful"] is False


def test_recommendation_feedback_rejects_partial_or_invalid_updates(store):
    policy_store.upsert_recommendation_review(
        store,
        review_id="review-1",
        generation_id="generation-1",
        policy_id="policy-1",
        policy_index=0,
        hard_partition_key="sdr",
        target_count=1,
        existing_photo_ids=[],
        algorithm_version="v2",
        feature_schema_version="f1",
        recommendation_version="policy-v2",
        candidates=[
            {
                "photo_id": "candidate-1",
                "responsibilities": [1.0],
                "assignment_entropy": 0.0,
            }
        ],
    )

    with pytest.raises(LookupError, match="not candidates"):
        policy_store.record_recommendation_feedback(
            store,
            review_id="review-1",
            policy_id="policy-1",
            labels=[
                {
                    "photo_id": "missing",
                    "policy_match": False,
                    "useful": False,
                }
            ],
        )
    with pytest.raises(ValueError, match="must also match"):
        policy_store.record_recommendation_feedback(
            store,
            review_id="review-1",
            policy_id="policy-1",
            labels=[
                {
                    "photo_id": "candidate-1",
                    "policy_match": False,
                    "useful": True,
                }
            ],
        )
    assert policy_store.list_recommendation_reviews(store) == []


def test_artifact_name_must_be_relative_and_safe(store):
    generation = policy_store.create_generation(
        store,
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    with pytest.raises(ValueError, match="safe relative"):
        policy_store.add_policy_model(
            store,
            generation_id=generation,
            policy_id="policy-1",
            hard_partition_key="sdr",
            expert_index=0,
            estimator_type="ridge",
            artifact_name="../outside.json",
        )


def test_foreign_keys_reject_membership_without_example(store):
    generation = policy_store.create_generation(
        store,
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    _add_model(store, generation)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            """
            INSERT INTO policy_v2_memberships (
                generation_id, policy_id, photo_id, responsibility
            ) VALUES (?, 'policy-1', 'missing-photo', 1.0)
            """,
            (generation,),
        )


def test_descriptor_and_coverage_replacement_is_atomic(store):
    generation = policy_store.create_generation(
        store,
        algorithm_version="v2",
        feature_schema_version="f1",
        target_schema_version="t1",
    )
    _add_model(store, generation)
    policy_store.replace_policy_descriptors(
        store,
        generation_id=generation,
        policy_id="policy-1",
        descriptors=[
            {
                "descriptor_kind": "user_keyword",
                "descriptor": "Copper tones",
                "score": 0.8,
                "provenance": "user",
            }
        ],
    )
    policy_store.replace_policy_coverage(
        store,
        generation_id=generation,
        policy_id="policy-1",
        coverage=[
            {
                "dimension_key": "visual_component",
                "bucket_key": "component_000",
                "effective_count": 4.0,
                "coverage_score": 0.8,
            }
        ],
    )

    policy_store.replace_policy_descriptors(
        store,
        generation_id=generation,
        policy_id="policy-1",
        descriptors=[
            {
                "descriptor_kind": "local_tag",
                "descriptor": "Quiet geometry",
                "score": 0.9,
                "provenance": "siglip",
            }
        ],
    )

    descriptor_rows = store.execute(
        "SELECT descriptor FROM policy_v2_descriptors"
    ).fetchall()
    coverage_rows = store.execute(
        "SELECT bucket_key FROM policy_v2_coverage"
    ).fetchall()
    assert [row[0] for row in descriptor_rows] == ["Quiet geometry"]
    assert [row[0] for row in coverage_rows] == ["component_000"]
