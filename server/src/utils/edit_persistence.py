"""Edit-recipe persistence helpers shared across route blueprints.

Extracted from routes.edit so that style-edit routes do not create a
hard dependency on the edit blueprint.
"""

from __future__ import annotations

import json
from datetime import datetime

from services import chroma as chroma_service


def _has_items(values) -> bool:
    if values is None:
        return False
    try:
        return len(values) > 0
    except TypeError:
        return False


def _persist_edit_recipe(
    photo_id: str, filename: str | None, recipe: dict, options: dict
) -> None:
    """Persist a generated edit recipe into ChromaDB alongside the photo record."""
    existing = chroma_service.get_image(photo_id)
    existing_has_ids = existing is not None and _has_items(existing.get("ids"))
    existing_has_metadatas = existing is not None and _has_items(
        existing.get("metadatas")
    )
    existing_has_embeddings = existing is not None and _has_items(
        existing.get("embeddings")
    )

    existing_meta = (
        dict(existing["metadatas"][0])
        if existing_has_ids and existing_has_metadatas
        else {}
    )
    existing_embedding = None
    if existing_has_ids and existing_has_embeddings:
        try:
            existing_embedding = existing["embeddings"][0]
        except (IndexError, KeyError, TypeError):
            existing_embedding = None

    metadata = existing_meta.copy()
    if filename:
        metadata["filename"] = filename
    metadata["edit_recipe"] = json.dumps(recipe, ensure_ascii=False)
    metadata["edit_summary"] = recipe.get("summary", "")
    metadata["edit_warnings"] = json.dumps(
        recipe.get("warnings", []), ensure_ascii=False
    )
    metadata["edit_model"] = (
        options.get("model") or metadata.get("edit_model") or metadata.get("model")
    )
    if options.get("provider"):
        metadata["edit_provider"] = options["provider"]
    metadata["edit_run_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata.setdefault("provider", options.get("provider"))
    metadata.setdefault("model", options.get("model"))
    metadata.setdefault(
        "has_embedding", bool(existing_meta.get("has_embedding", False))
    )

    if existing_has_ids:
        chroma_service.update_image(photo_id, metadata, embedding=existing_embedding)
    else:
        chroma_service.add_image(photo_id, existing_embedding, metadata)


def _success_payload(
    photo_id: str, recipe: dict, options: dict, warning: str | None = None
) -> dict:
    """Build the standard success envelope for an edit response."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "status": "success",
        "photo_id": photo_id,
        "uuid": photo_id,
        "edit": recipe,
        "edit_summary": recipe.get("summary", ""),
        "edit_warnings": recipe.get("warnings", []),
        "edit_model": options.get("model"),
        "edit_rundate": now,
    }
    if warning:
        payload["warning"] = warning
    return payload
