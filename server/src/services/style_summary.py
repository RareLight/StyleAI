import json
import os

import config
from config import logger
from . import style_catalog as catalog_service


def _summary_file() -> str:
    return os.path.join(config.DB_PATH or ".", "signature_style.json")


def get_signature_style_summary() -> str | None:
    path = _summary_file()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as summary_file:
                return json.load(summary_file).get("summary")
        except (OSError, ValueError):
            logger.warning("Could not read signature style summary", exc_info=True)
    return None


def _configured_summary_runner() -> tuple[str, str] | None:
    """Return an explicitly configured local provider/model pair."""
    configured = os.environ.get("STYLEAI_SUMMARY_MODEL", "").strip()
    if not configured:
        return None
    if "::" in configured:
        provider, model = configured.split("::", 1)
    else:
        provider, model = config.DEFAULT_METADATA_PROVIDER, configured
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in ("ollama", "lmstudio") or not model:
        logger.warning("Ignoring invalid STYLEAI_SUMMARY_MODEL=%s", configured)
        return None
    return provider, model


def summarize_catalog_styles() -> str | None:
    """Generate optional signature prose through the configured local provider."""
    styles = catalog_service.list_styles()
    if not styles:
        return "No editing styles discovered yet."

    prompt_lines = [
        "Synthesize the Lightroom editing styles below into a concise 2-3 paragraph "
        "Signature Style Summary.",
        "Focus on recurring contrast, exposure, color grading, tone curves, and mood.",
        "Return the prose in the JSON caption field.",
        "",
    ]
    for style in styles:
        prompt_lines.append(
            f"- {style.get('style_name', 'Unknown')} "
            f"({style.get('genre', 'Unknown')}): {style.get('description', '')}"
        )

    runner = _configured_summary_runner()
    if runner is None:
        logger.info(
            "Signature summary skipped; STYLEAI_SUMMARY_MODEL is not configured."
        )
        return None

    provider, model = runner
    try:
        from services.metadata import get_analysis_service

        response = get_analysis_service().generate_metadata_single(
            "catalog-style-summary",
            b"",
            {
                "provider": provider,
                "model": model,
                "generate_keywords": False,
                "generate_caption": True,
                "generate_title": False,
                "generate_alt_text": False,
                "language": "English",
                "temperature": 0.2,
                "max_tokens": 700,
                "user_prompt": "\n".join(prompt_lines),
                "prompt": "You are an expert photography analyst and photo editor.",
                "submit_keywords": False,
                "submit_folder_names": False,
            },
        )
        summary_text = (response.caption or "").strip() if response.success else ""
        if not summary_text:
            logger.warning(
                "Local signature summary generation failed: %s", response.error
            )
            return None

        path = _summary_file()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as summary_file:
            json.dump({"summary": summary_text}, summary_file)
        os.replace(tmp_path, path)
        return summary_text
    except Exception as exc:
        logger.error(
            "Failed to generate local signature style summary: %s",
            exc,
            exc_info=True,
        )
        return None
