"""Status and scoped-cancellation routes for catalog-local operations."""

from flask import Blueprint, jsonify, request

import config
from services import operations


operations_bp = Blueprint("operations", __name__)
_ALLOWED_KINDS = frozenset(
    {
        "index",
        "metadata",
        "training",
        "discovery",
        "edit",
        "recommendations",
        "backup",
        "prune",
        "maintenance",
    }
)


def _db_path() -> str:
    if not config.DB_PATH:
        raise RuntimeError("StyleAI database path is not configured")
    return config.DB_PATH


@operations_bp.route("/operations", methods=["GET"])
def active_operations():
    operations.refresh_system_pressure()
    return jsonify(
        {
            "status": "ok",
            "jobs": operations.list_active_jobs(_db_path()),
            "resources": operations.admission.snapshot(),
            "system_pressure": operations.pressure_snapshot(),
        }
    )


@operations_bp.route("/operations", methods=["POST"])
def create_operation():
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or "").strip()
    if kind not in _ALLOWED_KINDS:
        return jsonify({"error": f"unsupported operation kind: {kind}"}), 400
    item_ids = data.get("item_ids") or []
    if not isinstance(item_ids, list) or len(item_ids) > 100_000:
        return jsonify({"error": "item_ids must be a list of at most 100000 IDs"}), 400
    job, created = operations.create_job(
        _db_path(),
        kind=kind,
        request_fingerprint=data.get("request_fingerprint"),
        priority=int(data.get("priority") or 0),
        details=data.get("details") if isinstance(data.get("details"), dict) else None,
        item_ids=item_ids,
        coalesce=data.get("coalesce", True) is not False,
    )
    return jsonify({"status": "accepted" if created else "attached", "job": job}), (
        202 if created else 200
    )


@operations_bp.route("/operations/<job_id>", methods=["GET"])
def operation_status(job_id: str):
    include_items = request.args.get("include_items", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    job = operations.get_job(_db_path(), job_id, include_items=include_items)
    if job is None:
        return jsonify({"error": f"operation job not found: {job_id}"}), 404
    return jsonify({"status": "ok", "job": job})


@operations_bp.route("/operations/<job_id>/cancel", methods=["POST"])
def cancel_operation(job_id: str):
    try:
        job = operations.request_cancel(_db_path(), job_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"status": "accepted", "job": job}), 202


@operations_bp.route("/operations/<job_id>/items", methods=["POST"])
def update_operation_items(job_id: str):
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or not items or len(items) > 500:
        return jsonify({"error": "items must be a non-empty list of at most 500"}), 400
    try:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("every item update must be an object")
            operations.set_item_state(
                _db_path(),
                job_id,
                str(item.get("item_id") or "").strip(),
                str(item.get("state") or "").strip(),
                error=item.get("error"),
                result=item.get("result")
                if isinstance(item.get("result"), dict)
                else None,
                request_fingerprint=item.get("request_fingerprint"),
            )
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok", "job": operations.get_job(_db_path(), job_id)})


@operations_bp.route("/operations/<job_id>/complete", methods=["POST"])
def complete_operation(job_id: str):
    try:
        job = operations.complete_submission(_db_path(), job_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"status": "ok", "job": job})
