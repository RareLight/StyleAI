"""Bounded HTTP boundary for developer-only style-refinement evidence."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import config
from config import logger
from services import operations, style_refinement


style_refinement_bp = Blueprint("style_refinement", __name__)


def _envelope(results=None, error=None, warning=None, status=200):
    return jsonify({"results": results, "error": error, "warning": warning}), status


def _data() -> dict:
    value = request.get_json(silent=True) or {}
    if not isinstance(value, dict):
        raise ValueError("request body must be an object")
    return value


def _ids(data: dict) -> list[str]:
    values = data.get("inference_ids")
    if not isinstance(values, list):
        raise ValueError("inference_ids must be an array")
    return values


def _fingerprints(data: dict) -> dict[str, str] | None:
    value = data.get("current_fingerprints")
    if value is not None and not isinstance(value, dict):
        raise ValueError("current_fingerprints must be an object")
    return value


def _current_settings(data: dict) -> dict[str, dict] | None:
    value = data.get("current_settings")
    if value is not None and not isinstance(value, dict):
        raise ValueError("current_settings must be an object")
    if isinstance(value, dict) and any(
        not isinstance(item, dict) for item in value.values()
    ):
        raise ValueError("each current_settings value must be an object")
    return value


@style_refinement_bp.route("/style_refinement/preflight", methods=["POST"])
def refinement_preflight():
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        data = _data()
        result = style_refinement.preflight(
            db_path=config.DB_PATH,
            inference_ids=_ids(data),
            mode=str(data.get("mode") or ""),
            current_fingerprints=_fingerprints(data),
            current_settings=_current_settings(data),
        )
        # Internal prepared recipes are consumed only by create_session.
        result.pop("eligible", None)
        return _envelope(result)
    except ValueError as exc:
        return _envelope(error=str(exc), status=400)
    except Exception as exc:
        logger.error("Style refinement preflight failed: %s", exc, exc_info=True)
        return _envelope(error=str(exc), status=500)


@style_refinement_bp.route("/style_refinement/sessions", methods=["POST"])
def create_refinement_session():
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        data = _data()
        requested_count = data.get("requested_count")
        if requested_count is not None and (
            not isinstance(requested_count, int) or isinstance(requested_count, bool)
        ):
            raise ValueError("requested_count must be an integer")
        # Preference collection is a short workflow admission, not an LLM or
        # accelerator task.  Maintenance remains mutually exclusive.
        with operations.workflow_maintenance_gate.workflow():
            result = style_refinement.create_session(
                db_path=config.DB_PATH,
                inference_ids=_ids(data),
                mode=str(data.get("mode") or ""),
                current_fingerprints=_fingerprints(data),
                current_settings=_current_settings(data),
                requested_count=requested_count,
            )
        return _envelope(result)
    except ValueError as exc:
        return _envelope(error=str(exc), status=400)
    except Exception as exc:
        logger.error(
            "Could not create style refinement session: %s", exc, exc_info=True
        )
        return _envelope(error=str(exc), status=500)


@style_refinement_bp.route("/style_refinement/sessions/<session_id>", methods=["GET"])
def get_refinement_session(session_id: str):
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        result = style_refinement.get_session(
            db_path=config.DB_PATH, session_id=session_id
        )
        if result is None:
            return _envelope(error="Refinement session was not found", status=404)
        return _envelope(result)
    except Exception as exc:
        logger.error("Could not read style refinement session: %s", exc, exc_info=True)
        return _envelope(error=str(exc), status=500)


@style_refinement_bp.route(
    "/style_refinement/sessions/<session_id>/next", methods=["GET"]
)
def next_refinement_pair(session_id: str):
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        result = style_refinement.next_pair(
            db_path=config.DB_PATH, session_id=session_id
        )
        return _envelope({"pair": result, "done": result is None})
    except LookupError as exc:
        return _envelope(error=str(exc), status=404)
    except RuntimeError as exc:
        return _envelope(error=str(exc), status=409)
    except Exception as exc:
        logger.error("Could not read next refinement pair: %s", exc, exc_info=True)
        return _envelope(error=str(exc), status=500)


@style_refinement_bp.route(
    "/style_refinement/sessions/<session_id>/choices", methods=["POST"]
)
def record_refinement_choice(session_id: str):
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        data = _data()
        keepable = data.get("winner_keepable")
        if keepable is not None and not isinstance(keepable, bool):
            raise ValueError("winner_keepable must be a boolean")
        with operations.workflow_maintenance_gate.workflow():
            result = style_refinement.record_choice(
                db_path=config.DB_PATH,
                session_id=session_id,
                pair_id=str(data.get("pair_id") or ""),
                choice=str(data.get("choice") or ""),
                idempotency_key=str(data.get("idempotency_key") or ""),
                winner_keepable=keepable,
            )
        return _envelope(result)
    except ValueError as exc:
        return _envelope(error=str(exc), status=400)
    except LookupError as exc:
        return _envelope(error=str(exc), status=404)
    except RuntimeError as exc:
        return _envelope(error=str(exc), status=409)
    except Exception as exc:
        logger.error("Could not record refinement choice: %s", exc, exc_info=True)
        return _envelope(error=str(exc), status=500)


@style_refinement_bp.route(
    "/style_refinement/sessions/<session_id>/finalize", methods=["POST"]
)
def finalize_refinement_session(session_id: str):
    try:
        if not config.DB_PATH:
            raise RuntimeError("StyleAI database path is not configured")
        with operations.workflow_maintenance_gate.workflow():
            result = style_refinement.finalize_session(
                db_path=config.DB_PATH, session_id=session_id
            )
        return _envelope(result)
    except LookupError as exc:
        return _envelope(error=str(exc), status=404)
    except Exception as exc:
        logger.error("Could not finalize refinement session: %s", exc, exc_info=True)
        return _envelope(error=str(exc), status=500)
