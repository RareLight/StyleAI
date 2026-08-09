"""Catalog-local operation state and atomic resource admission.

The registry makes accepted work observable and independently cancelable.  The
admission controller owns process-wide resource ceilings so opening another
Lightroom task cannot multiply the machine's effective concurrency.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import json
import os
import sqlite3
import threading
from time import monotonic, time
from typing import Any, Iterator, Mapping
from uuid import uuid4

import config


ACTIVE_STATES = frozenset({"queued", "preparing", "running", "committing"})
TERMINAL_STATES = frozenset({"succeeded", "failed", "canceled", "interrupted"})
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES


class JobCancelSignal:
    """Thread-event compatible view of one durable operation's cancel flag."""

    def __init__(self, db_path: str, job_id: str):
        self._db_path = db_path
        self._job_id = job_id

    def is_set(self) -> bool:
        return is_cancel_requested(self._db_path, self._job_id)


def _database_file(db_path: str) -> str:
    if not db_path:
        raise ValueError("db_path is required")
    return os.path.join(db_path, "styles.sqlite")


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(_database_file(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _decode_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _job_payload(row: sqlite3.Row, items: list[dict[str, Any]] | None = None) -> dict:
    payload = {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "state": row["state"],
        "priority": int(row["priority"]),
        "request_fingerprint": row["request_fingerprint"],
        "cancel_requested": bool(row["cancel_requested"]),
        "details": _decode_json(row["details_json"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
    if items is not None:
        payload["items"] = items
    return payload


def _item_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "item_id": row["item_id"],
        "state": row["state"],
        "request_fingerprint": row["request_fingerprint"],
        "result": _decode_json(row["result_json"]),
        "error": row["error"],
        "updated_at": row["updated_at"],
    }


def create_job(
    db_path: str,
    *,
    kind: str,
    request_fingerprint: str | None = None,
    priority: int = 0,
    details: Mapping[str, Any] | None = None,
    item_ids: list[str] | None = None,
    coalesce: bool = True,
) -> tuple[dict[str, Any], bool]:
    """Create a durable job or attach to an identical active request."""
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        raise ValueError("kind is required")
    fingerprint = str(request_fingerprint or "").strip() or None
    now = time()
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if coalesce and fingerprint:
            placeholders = ",".join("?" for _ in ACTIVE_STATES)
            row = connection.execute(
                f"""
                SELECT * FROM operation_jobs
                WHERE kind = ? AND request_fingerprint = ?
                  AND state IN ({placeholders})
                ORDER BY created_at ASC LIMIT 1
                """,
                (normalized_kind, fingerprint, *sorted(ACTIVE_STATES)),
            ).fetchone()
            if row is not None:
                connection.commit()
                return _job_payload(row), False

        job_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO operation_jobs (
                job_id, kind, state, priority, request_fingerprint,
                details_json, created_at
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                job_id,
                normalized_kind,
                int(priority),
                fingerprint,
                json.dumps(dict(details or {}), sort_keys=True),
                now,
            ),
        )
        for item_id in dict.fromkeys(str(value).strip() for value in item_ids or []):
            if not item_id:
                continue
            connection.execute(
                """
                INSERT INTO operation_job_items (
                    job_id, item_id, state, updated_at
                ) VALUES (?, ?, 'queued', ?)
                """,
                (job_id, item_id, now),
            )
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        connection.commit()
        return _job_payload(row), True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_job(db_path: str, job_id: str, *, include_items: bool = True) -> dict | None:
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        items = None
        if include_items:
            item_rows = connection.execute(
                """
                SELECT item_id, state, request_fingerprint, result_json, error, updated_at
                FROM operation_job_items WHERE job_id = ? ORDER BY rowid
                """,
                (job_id,),
            ).fetchall()
            items = [_item_payload(item) for item in item_rows]
        return _job_payload(row, items)
    finally:
        connection.close()


def get_job_item(db_path: str, job_id: str, item_id: str) -> dict[str, Any] | None:
    """Fetch one operation item through its `(job_id, item_id)` primary key."""
    items = get_job_items(db_path, job_id, [item_id])
    return items[0] if items else None


def get_job_items(
    db_path: str,
    job_id: str,
    item_ids: list[str],
) -> list[dict[str, Any]]:
    """Fetch only the bounded item IDs required by the current request."""
    normalized_ids = list(
        dict.fromkeys(str(item_id or "").strip() for item_id in item_ids)
    )
    normalized_ids = [item_id for item_id in normalized_ids if item_id]
    if not normalized_ids:
        return []
    placeholders = ",".join("?" for _ in normalized_ids)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT item_id, state, request_fingerprint, result_json, error, updated_at
            FROM operation_job_items
            WHERE job_id = ? AND item_id IN ({placeholders})
            """,
            (job_id, *normalized_ids),
        ).fetchall()
        by_id = {row["item_id"]: _item_payload(row) for row in rows}
        return [by_id[item_id] for item_id in normalized_ids if item_id in by_id]
    finally:
        connection.close()


def list_active_jobs(db_path: str) -> list[dict[str, Any]]:
    connection = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        rows = connection.execute(
            f"""
            SELECT * FROM operation_jobs WHERE state IN ({placeholders})
            ORDER BY priority DESC, created_at ASC
            """,
            tuple(sorted(ACTIVE_STATES)),
        ).fetchall()
        return [_job_payload(row) for row in rows]
    finally:
        connection.close()


def has_active_work(db_path: str | None = None) -> bool:
    if any(operations > 0 for operations in admission.snapshot()["in_use"].values()):
        return True
    effective_path = db_path or config.DB_PATH
    return bool(effective_path and list_active_jobs(effective_path))


def set_job_state(
    db_path: str,
    job_id: str,
    state: str,
    *,
    error: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in ALL_STATES:
        raise ValueError(f"invalid job state: {state}")
    now = time()
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"operation job not found: {job_id}")
        if row["state"] in TERMINAL_STATES and row["state"] != state:
            raise ValueError("terminal operation state cannot be changed")
        started_at = row["started_at"]
        completed_at = row["completed_at"]
        if state in {"preparing", "running", "committing"} and started_at is None:
            started_at = now
        if state in TERMINAL_STATES:
            completed_at = now
        merged_details = _decode_json(row["details_json"])
        merged_details.update(dict(details or {}))
        connection.execute(
            """
            UPDATE operation_jobs
            SET state = ?, error = ?, details_json = ?,
                started_at = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (
                state,
                error,
                json.dumps(merged_details, sort_keys=True),
                started_at,
                completed_at,
                job_id,
            ),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return _job_payload(updated)
    finally:
        connection.close()


def set_item_state(
    db_path: str,
    job_id: str,
    item_id: str,
    state: str,
    *,
    error: str | None = None,
    result: Mapping[str, Any] | None = None,
    request_fingerprint: str | None = None,
) -> None:
    if state not in ALL_STATES:
        raise ValueError(f"invalid item state: {state}")
    normalized_job_id = str(job_id or "").strip()
    normalized_item_id = str(item_id or "").strip()
    if not normalized_job_id:
        raise ValueError("job_id is required")
    if not normalized_item_id:
        raise ValueError("item_id is required")
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        parent = connection.execute(
            "SELECT state, cancel_requested FROM operation_jobs WHERE job_id = ?",
            (normalized_job_id,),
        ).fetchone()
        if parent is None:
            raise LookupError(f"operation job not found: {normalized_job_id}")
        if parent["state"] in TERMINAL_STATES:
            raise ValueError("terminal operation job items cannot be changed")
        existing = connection.execute(
            """
            SELECT state FROM operation_job_items
            WHERE job_id = ? AND item_id = ?
            """,
            (normalized_job_id, normalized_item_id),
        ).fetchone()
        if existing is None:
            raise LookupError(
                f"operation item not found: {normalized_job_id}/{normalized_item_id}"
            )
        if existing is not None and existing["state"] in TERMINAL_STATES:
            if existing["state"] == state:
                return
            if existing["state"] == "canceled" and parent["cancel_requested"]:
                return
            raise ValueError("terminal operation item state cannot be changed")
        if parent["cancel_requested"] and state in {
            "queued",
            "preparing",
            "committing",
        }:
            state = "canceled"
            error = error or "Operation canceled before client handoff"
        connection.execute(
            """
            UPDATE operation_job_items
            SET state = ?,
                request_fingerprint = COALESCE(?, request_fingerprint),
                result_json = COALESCE(?, result_json), error = ?, updated_at = ?
            WHERE job_id = ? AND item_id = ?
            """,
            (
                state,
                request_fingerprint,
                json.dumps(dict(result), sort_keys=True)
                if result is not None
                else None,
                error,
                time(),
                normalized_job_id,
                normalized_item_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    finalize_if_ready(db_path, normalized_job_id)


def _item_state_counts(connection: sqlite3.Connection, job_id: str) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT state, COUNT(*) AS count FROM operation_job_items
        WHERE job_id = ? GROUP BY state
        """,
        (job_id,),
    ).fetchall()
    return {str(row["state"]): int(row["count"]) for row in rows}


def finalize_if_ready(db_path: str, job_id: str) -> dict[str, Any] | None:
    """Finalize a submitted job once every admitted item is terminal."""
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"operation job not found: {job_id}")
        if row["state"] in TERMINAL_STATES:
            return _job_payload(row)
        details = _decode_json(row["details_json"])
        if not details.get("submission_complete"):
            return None
        counts = _item_state_counts(connection, job_id)
        if any(counts.get(state, 0) for state in ACTIVE_STATES):
            return None
        if not counts:
            terminal_state = "canceled" if row["cancel_requested"] else "failed"
            error = None if row["cancel_requested"] else "operation contained no items"
        elif counts.get("failed", 0) or counts.get("interrupted", 0):
            terminal_state = "failed"
            error = "one or more operation items failed"
        elif row["cancel_requested"] or counts.get("canceled", 0):
            terminal_state = "canceled"
            error = None
        else:
            terminal_state = "succeeded"
            error = None
    finally:
        connection.close()
    return set_job_state(
        db_path,
        job_id,
        terminal_state,
        error=error,
        details={"item_state_counts": counts},
    )


def complete_submission(db_path: str, job_id: str) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"operation job not found: {job_id}")
        details = _decode_json(row["details_json"])
        details["submission_complete"] = True
        connection.execute(
            "UPDATE operation_jobs SET details_json = ? WHERE job_id = ?",
            (json.dumps(details, sort_keys=True), job_id),
        )
        connection.commit()
    finally:
        connection.close()
    finalized = finalize_if_ready(db_path, job_id)
    return finalized or get_job(db_path, job_id, include_items=False)


def request_cancel(db_path: str, job_id: str) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"operation job not found: {job_id}")
        if row["state"] in TERMINAL_STATES:
            return _job_payload(row)
        connection.execute(
            "UPDATE operation_jobs SET cancel_requested = 1 WHERE job_id = ?",
            (job_id,),
        )
        connection.execute(
            """
            UPDATE operation_job_items
            SET state = 'canceled', error = 'Operation canceled before execution',
                updated_at = ?
            WHERE job_id = ? AND state IN ('queued', 'preparing', 'committing')
            """,
            (time(), job_id),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        payload = _job_payload(updated)
    finally:
        connection.close()
    finalized = finalize_if_ready(db_path, job_id)
    return finalized or payload


def is_cancel_requested(db_path: str, job_id: str) -> bool:
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT cancel_requested FROM operation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        connection.close()


def recover_interrupted_jobs(db_path: str) -> int:
    """Mark nonterminal jobs interrupted after an unclean process lifetime."""
    connection = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        now = time()
        cursor = connection.execute(
            f"""
            UPDATE operation_jobs
            SET state = 'interrupted', completed_at = ?,
                error = COALESCE(error, 'Backend session ended before completion')
            WHERE state IN ({placeholders})
            """,
            (now, *sorted(ACTIVE_STATES)),
        )
        connection.execute(
            f"""
            UPDATE operation_job_items
            SET state = 'interrupted', updated_at = ?,
                error = COALESCE(error, 'Backend session ended before completion')
            WHERE state IN ({placeholders})
            """,
            (now, *sorted(ACTIVE_STATES)),
        )
        connection.commit()
        return max(0, int(cursor.rowcount))
    finally:
        connection.close()


def prune_terminal_jobs(
    db_path: str,
    *,
    max_keep: int = 2000,
    minimum_age_seconds: float = 30 * 24 * 60 * 60,
) -> int:
    """Bound the operational ledger while retaining recent diagnostic history."""
    keep = max(1, int(max_keep))
    cutoff = time() - max(0.0, float(minimum_age_seconds))
    connection = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        cursor = connection.execute(
            f"""
            DELETE FROM operation_jobs
            WHERE state IN ({placeholders}) AND completed_at < ?
              AND job_id NOT IN (
                  SELECT job_id FROM operation_jobs
                  WHERE state IN ({placeholders})
                  ORDER BY completed_at DESC LIMIT ?
              )
            """,
            (*sorted(TERMINAL_STATES), cutoff, *sorted(TERMINAL_STATES), keep),
        )
        connection.commit()
        return max(0, int(cursor.rowcount))
    finally:
        connection.close()


@dataclass(frozen=True)
class _Ticket:
    sequence: int
    priority: int
    created_at: float
    claim: Mapping[str, int]


class WorkflowMaintenanceGate:
    """Writer-preferring, reentrant barrier between live work and maintenance.

    Resource ceilings alone cannot make a restore safe: an indexing worker can
    hold the accelerator while a restore holds ``catalog_write``, then commit
    output computed from the database that has just been replaced.  This gate
    lets normal resource claims overlap, but drains all of them before admitting
    a maintenance claim and blocks new work until maintenance finishes.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_workflows = 0
        self._maintenance_owner: int | None = None
        self._maintenance_depth = 0
        self._maintenance_waiters = 0
        self._local = threading.local()

    def _shared_depth(self) -> int:
        return int(getattr(self._local, "shared_depth", 0))

    @contextmanager
    def workflow(
        self, *, cancel_event: threading.Event | None = None
    ) -> Iterator[None]:
        thread_id = threading.get_ident()
        bypass = False
        with self._condition:
            if self._maintenance_owner == thread_id:
                bypass = True
            elif self._shared_depth() > 0:
                self._local.shared_depth = self._shared_depth() + 1
                bypass = True
            else:
                while self._maintenance_owner is not None or self._maintenance_waiters:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("workflow admission canceled")
                    self._condition.wait(timeout=0.1)
                self._active_workflows += 1
                self._local.shared_depth = 1
        try:
            yield
        finally:
            if not (bypass and self._maintenance_owner == thread_id):
                with self._condition:
                    depth = self._shared_depth()
                    if depth > 1:
                        self._local.shared_depth = depth - 1
                    elif depth == 1:
                        self._local.shared_depth = 0
                        self._active_workflows -= 1
                        self._condition.notify_all()

    @contextmanager
    def maintenance(
        self, *, cancel_event: threading.Event | None = None
    ) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            if self._maintenance_owner == thread_id:
                self._maintenance_depth += 1
            else:
                if self._shared_depth() > 0:
                    raise RuntimeError(
                        "cannot upgrade an active workflow admission to maintenance"
                    )
                self._maintenance_waiters += 1
                try:
                    while self._maintenance_owner is not None or self._active_workflows:
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("maintenance admission canceled")
                        self._condition.wait(timeout=0.1)
                    self._maintenance_owner = thread_id
                    self._maintenance_depth = 1
                finally:
                    self._maintenance_waiters -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                if self._maintenance_owner == thread_id:
                    self._maintenance_depth -= 1
                    if self._maintenance_depth == 0:
                        self._maintenance_owner = None
                        self._condition.notify_all()

    def snapshot(self) -> dict[str, int | bool]:
        with self._condition:
            return {
                "active_workflows": self._active_workflows,
                "maintenance_active": self._maintenance_owner is not None,
                "maintenance_waiting": self._maintenance_waiters,
            }


class ResourceAdmission:
    """Atomically admit resource vectors with bounded, aging-aware fairness."""

    def __init__(
        self,
        capacities: Mapping[str, int],
        *,
        maintenance_gate: WorkflowMaintenanceGate | None = None,
    ):
        self._maximums = {key: max(1, int(value)) for key, value in capacities.items()}
        self._capacities = dict(self._maximums)
        self._in_use = {key: 0 for key in self._capacities}
        self._condition = threading.Condition()
        self._waiting: list[_Ticket] = []
        self._sequence = 0
        self._maintenance_gate = maintenance_gate

    @property
    def capacities(self) -> dict[str, int]:
        with self._condition:
            return dict(self._capacities)

    @property
    def maximum_capacities(self) -> dict[str, int]:
        with self._condition:
            return dict(self._maximums)

    def update_capacities(self, capacities: Mapping[str, int]) -> None:
        """Adjust effective ceilings without exceeding detected hardware limits."""
        with self._condition:
            for resource, raw_amount in capacities.items():
                if resource not in self._maximums:
                    raise ValueError(f"unknown resource: {resource}")
                self._capacities[resource] = max(
                    1, min(int(raw_amount), self._maximums[resource])
                )
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "capacities": dict(self._capacities),
                "in_use": dict(self._in_use),
                "waiting": len(self._waiting),
            }

    def _validate(self, claim: Mapping[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for resource, raw_amount in claim.items():
            if resource not in self._capacities:
                raise ValueError(f"unknown resource: {resource}")
            amount = int(raw_amount)
            if amount <= 0 or amount > self._maximums[resource]:
                raise ValueError(f"invalid {resource} claim: {amount}")
            normalized[resource] = amount
        return normalized

    def _fits(self, claim: Mapping[str, int]) -> bool:
        return all(
            self._in_use[resource] + amount <= self._capacities[resource]
            for resource, amount in claim.items()
        )

    def _winner(self) -> _Ticket | None:
        now = monotonic()
        eligible = [ticket for ticket in self._waiting if self._fits(ticket.claim)]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda ticket: (
                ticket.priority + ((now - ticket.created_at) / 5.0),
                -ticket.sequence,
            ),
        )

    @contextmanager
    def acquire(
        self,
        claim: Mapping[str, int],
        *,
        priority: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[None]:
        normalized = self._validate(claim)
        gate_context = nullcontext()
        if self._maintenance_gate is not None:
            if "maintenance" in normalized:
                gate_context = self._maintenance_gate.maintenance(
                    cancel_event=cancel_event
                )
            else:
                gate_context = self._maintenance_gate.workflow(
                    cancel_event=cancel_event
                )
        with gate_context:
            with self._condition:
                self._sequence += 1
                ticket = _Ticket(self._sequence, int(priority), monotonic(), normalized)
                self._waiting.append(ticket)
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        self._waiting.remove(ticket)
                        self._condition.notify_all()
                        raise InterruptedError("resource admission canceled")
                    if self._winner() is ticket:
                        self._waiting.remove(ticket)
                        for resource, amount in normalized.items():
                            self._in_use[resource] += amount
                        break
                    self._condition.wait(timeout=0.1)
            try:
                yield
            finally:
                with self._condition:
                    for resource, amount in normalized.items():
                        self._in_use[resource] -= amount
                    self._condition.notify_all()


_cpu_capacity = max(1, min(config.STYLEAI_HTTP_THREADS - 2, (os.cpu_count() or 4) // 2))
workflow_maintenance_gate = WorkflowMaintenanceGate()
admission = ResourceAdmission(
    {
        "cpu_prepare": _cpu_capacity,
        "accelerator": 1,
        "llm": max(1, config.STYLEAI_LLM_CONCURRENCY),
        "catalog_write": 1,
        "training_upload": 1,
        "maintenance": 1,
        "image_bytes": config.STYLEAI_METADATA_CACHE_BYTES,
    },
    maintenance_gate=workflow_maintenance_gate,
)

_pressure_lock = threading.Lock()
_pressure_last_sample = 0.0
_pressure_state: dict[str, Any] = {
    "level": "normal",
    "available_ratio": None,
    "sampled_at": None,
}


def refresh_system_pressure(
    *,
    available_ratio: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Lower effective concurrency under memory pressure; never scale above startup limits."""
    global _pressure_last_sample, _pressure_state
    now = monotonic()
    with _pressure_lock:
        if not force and available_ratio is None and now - _pressure_last_sample < 1.0:
            return dict(_pressure_state)
        if available_ratio is None:
            try:
                import psutil

                memory = psutil.virtual_memory()
                available_ratio = float(memory.available) / max(
                    1.0, float(memory.total)
                )
            except Exception:
                available_ratio = 1.0
        available_ratio = max(0.0, min(1.0, float(available_ratio)))
        if available_ratio < 0.07:
            level = "critical"
            cpu_fraction = 0.25
            byte_fraction = 0.25
        elif available_ratio < 0.12:
            level = "constrained"
            cpu_fraction = 0.5
            byte_fraction = 0.5
        else:
            level = "normal"
            cpu_fraction = 1.0
            byte_fraction = 1.0

        maximums = admission.maximum_capacities
        admission.update_capacities(
            {
                "cpu_prepare": max(1, int(maximums["cpu_prepare"] * cpu_fraction)),
                "image_bytes": max(1, int(maximums["image_bytes"] * byte_fraction)),
            }
        )
        _pressure_last_sample = now
        _pressure_state = {
            "level": level,
            "available_ratio": round(available_ratio, 4),
            "sampled_at": time(),
        }
        return dict(_pressure_state)


def pressure_snapshot() -> dict[str, Any]:
    with _pressure_lock:
        return dict(_pressure_state)


def recommended_gpu_batch_size() -> int:
    pressure = refresh_system_pressure()
    base = max(1, int(config.STYLEAI_GPU_BATCH_SIZE))
    if pressure["level"] == "critical":
        return max(2, base // 3)
    if pressure["level"] == "constrained":
        return max(4, (base * 2) // 3)
    return base
