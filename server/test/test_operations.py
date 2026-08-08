import threading
import time

import pytest

from core.migrations import run_migrations
from services import operations


@pytest.fixture
def operation_db(tmp_path):
    db_path = str(tmp_path / "styleai.db")
    run_migrations(db_path)
    return db_path


def test_identical_active_jobs_coalesce(operation_db):
    first, created = operations.create_job(
        operation_db,
        kind="index",
        request_fingerprint="same-request",
        item_ids=["p1", "p2"],
    )
    second, created_again = operations.create_job(
        operation_db,
        kind="index",
        request_fingerprint="same-request",
        item_ids=["p1", "p2"],
    )

    assert created is True
    assert created_again is False
    assert second["job_id"] == first["job_id"]


def test_scoped_cancel_does_not_change_another_job(operation_db):
    first, _ = operations.create_job(
        operation_db, kind="index", request_fingerprint="first"
    )
    second, _ = operations.create_job(
        operation_db, kind="index", request_fingerprint="second"
    )

    canceled = operations.request_cancel(operation_db, first["job_id"])

    assert canceled["cancel_requested"] is True
    assert operations.is_cancel_requested(operation_db, first["job_id"]) is True
    assert operations.is_cancel_requested(operation_db, second["job_id"]) is False


def test_job_cancel_signal_tracks_durable_cancel_state(operation_db):
    job, _ = operations.create_job(operation_db, kind="edit")
    signal = operations.JobCancelSignal(operation_db, job["job_id"])

    assert signal.is_set() is False
    operations.request_cancel(operation_db, job["job_id"])
    assert signal.is_set() is True


def test_terminal_job_state_cannot_be_reopened(operation_db):
    job, _ = operations.create_job(operation_db, kind="tag")
    operations.set_job_state(operation_db, job["job_id"], "succeeded")

    with pytest.raises(ValueError, match="terminal"):
        operations.set_job_state(operation_db, job["job_id"], "running")


def test_item_update_requires_existing_job_and_nonempty_item(operation_db):
    with pytest.raises(LookupError, match="not found"):
        operations.set_item_state(operation_db, "missing", "p1", "running")

    job, _ = operations.create_job(operation_db, kind="index")
    with pytest.raises(ValueError, match="item_id is required"):
        operations.set_item_state(operation_db, job["job_id"], "", "running")
    with pytest.raises(LookupError, match="operation item not found"):
        operations.set_item_state(operation_db, job["job_id"], "p1", "running")


def test_cancel_after_completion_does_not_mutate_terminal_job(operation_db):
    job, _ = operations.create_job(operation_db, kind="edit", item_ids=["p1"])
    operations.set_item_state(operation_db, job["job_id"], "p1", "succeeded")
    operations.complete_submission(operation_db, job["job_id"])

    unchanged = operations.request_cancel(operation_db, job["job_id"])

    assert unchanged["state"] == "succeeded"
    assert unchanged["cancel_requested"] is False


def test_submission_finalizes_only_after_every_item_is_terminal(operation_db):
    job, _ = operations.create_job(operation_db, kind="index", item_ids=["p1", "p2"])
    operations.set_item_state(operation_db, job["job_id"], "p1", "succeeded")
    still_running = operations.complete_submission(operation_db, job["job_id"])
    assert still_running["state"] == "queued"

    operations.set_item_state(operation_db, job["job_id"], "p2", "succeeded")
    completed = operations.get_job(operation_db, job["job_id"])
    assert completed["state"] == "succeeded"
    assert completed["details"]["item_state_counts"] == {"succeeded": 2}


def test_lightroom_handoff_preserves_backend_item_result(operation_db):
    job, _ = operations.create_job(operation_db, kind="edit", item_ids=["p1"])
    operations.set_item_state(
        operation_db,
        job["job_id"],
        "p1",
        "committing",
        result={"engine": "policy_v2", "confidence": 0.91},
    )

    operations.set_item_state(operation_db, job["job_id"], "p1", "succeeded")

    stored = operations.get_job(operation_db, job["job_id"])
    assert stored["items"][0]["result"] == {
        "engine": "policy_v2",
        "confidence": 0.91,
    }


def test_cancel_marks_pre_application_items_and_finalizes(operation_db):
    job, _ = operations.create_job(
        operation_db, kind="metadata", item_ids=["p1", "p2", "p3"]
    )
    operations.set_item_state(operation_db, job["job_id"], "p1", "succeeded")
    operations.set_item_state(operation_db, job["job_id"], "p2", "committing")
    operations.complete_submission(operation_db, job["job_id"])

    canceled = operations.request_cancel(operation_db, job["job_id"])

    assert canceled["state"] == "canceled"
    items = operations.get_job(operation_db, job["job_id"])["items"]
    assert {item["item_id"]: item["state"] for item in items} == {
        "p1": "succeeded",
        "p2": "canceled",
        "p3": "canceled",
    }


def test_inflight_item_cancels_instead_of_entering_client_handoff(operation_db):
    job, _ = operations.create_job(operation_db, kind="edit", item_ids=["p1"])
    operations.set_item_state(operation_db, job["job_id"], "p1", "running")
    operations.request_cancel(operation_db, job["job_id"])

    operations.set_item_state(operation_db, job["job_id"], "p1", "committing")

    stored = operations.get_job(operation_db, job["job_id"])
    assert stored["items"][0]["state"] == "canceled"


def test_recovery_marks_jobs_and_items_interrupted(operation_db):
    job, _ = operations.create_job(operation_db, kind="training", item_ids=["p1"])
    operations.set_job_state(operation_db, job["job_id"], "running")
    operations.set_item_state(operation_db, job["job_id"], "p1", "running")

    assert operations.recover_interrupted_jobs(operation_db) == 1
    recovered = operations.get_job(operation_db, job["job_id"])
    assert recovered["state"] == "interrupted"
    assert recovered["items"][0]["state"] == "interrupted"


def test_terminal_job_pruning_keeps_recent_diagnostics(operation_db):
    job_ids = []
    for index in range(4):
        job, _ = operations.create_job(operation_db, kind="edit")
        operations.set_job_state(operation_db, job["job_id"], "succeeded")
        job_ids.append(job["job_id"])

    deleted = operations.prune_terminal_jobs(
        operation_db, max_keep=2, minimum_age_seconds=0
    )

    assert deleted == 2
    assert operations.get_job(operation_db, job_ids[0]) is None
    assert operations.get_job(operation_db, job_ids[1]) is None
    assert operations.get_job(operation_db, job_ids[2]) is not None
    assert operations.get_job(operation_db, job_ids[3]) is not None


def test_resource_claims_are_atomic_and_bounded():
    admission = operations.ResourceAdmission({"accelerator": 1, "cpu": 2})
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_worker():
        with admission.acquire({"accelerator": 1, "cpu": 2}):
            first_entered.set()
            release_first.wait(timeout=2)

    def second_worker():
        with admission.acquire({"accelerator": 1, "cpu": 1}):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    time.sleep(0.05)

    assert second_entered.is_set() is False
    assert admission.snapshot()["in_use"] == {"accelerator": 1, "cpu": 2}

    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert second_entered.is_set() is True
    assert admission.snapshot()["in_use"] == {"accelerator": 0, "cpu": 0}


def test_waiting_resource_claim_can_be_canceled():
    admission = operations.ResourceAdmission({"accelerator": 1})
    cancel = threading.Event()
    waiting_finished = threading.Event()
    errors = []

    def waiting_worker():
        try:
            with admission.acquire({"accelerator": 1}, cancel_event=cancel):
                raise AssertionError("canceled waiter must not be admitted")
        except InterruptedError as exc:
            errors.append(str(exc))
        finally:
            waiting_finished.set()

    with admission.acquire({"accelerator": 1}):
        waiter = threading.Thread(target=waiting_worker)
        waiter.start()
        time.sleep(0.05)
        cancel.set()
        assert waiting_finished.wait(timeout=1)
    waiter.join(timeout=1)

    assert errors == ["resource admission canceled"]
    assert admission.snapshot()["waiting"] == 0


def test_memory_pressure_only_scales_effective_limits_downward():
    maximums = operations.admission.maximum_capacities
    try:
        critical = operations.refresh_system_pressure(available_ratio=0.05, force=True)
        effective = operations.admission.capacities
        assert critical["level"] == "critical"
        assert effective["cpu_prepare"] <= maximums["cpu_prepare"]
        assert effective["image_bytes"] == max(1, maximums["image_bytes"] // 4)
        assert effective["accelerator"] == maximums["accelerator"]

        normal = operations.refresh_system_pressure(available_ratio=0.50, force=True)
        assert normal["level"] == "normal"
        assert operations.admission.capacities == maximums
    finally:
        operations.refresh_system_pressure(available_ratio=0.50, force=True)
