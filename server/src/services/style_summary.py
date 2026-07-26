import json
import os
from config import logger, DEFAULT_METADATA_PROVIDER, DB_PATH
from . import style_catalog as catalog_service

SUMMARY_FILE = os.path.join(DB_PATH or ".", "signature_style.json")


def get_signature_style_summary() -> str | None:
    if os.path.exists(SUMMARY_FILE):
        try:
            with open(SUMMARY_FILE, "r") as f:
                return json.load(f).get("summary")
        except Exception:
            pass
    return None


def summarize_catalog_styles() -> str | None:
    """
    Summarize all discovered styles in the catalog using an LLM.
    Returns a 'Signature Style' summary string.
    """
    styles = catalog_service.list_styles()
    if not styles:
        return "No editing styles discovered yet."

    # Build prompt
    prompt_lines = [
        "You are an expert photography analyst and photo editor. "
        "Review the following editing styles automatically clustered from the user's Lightroom catalog, "
        "and write a cohesive, 2-3 paragraph 'Signature Style Summary'.",
        "This summary will be injected into future AI editing prompts to guide the AI to match the user's personal aesthetic.",
        "Focus on recurring patterns in contrast, exposure, color grading, tone curves, and overall mood.",
        "Do not list the styles one by one; synthesize them into a unified 'Signature Style'.",
        "",
        "## User's Discovered Styles:",
    ]

    for style in styles:
        name = style.get("style_name", "Unknown")
        genre = style.get("genre", "Unknown")
        desc = style.get("description", "")
        prompt_lines.append(f"- Style: {name} (Genre: {genre})")
        prompt_lines.append(f"  Description: {desc}")
        prompt_lines.append("")

    system_prompt = "\n".join(prompt_lines)

    provider = DEFAULT_METADATA_PROVIDER.lower()

    summary_text = None
    try:
        if provider == "ollama":
            import requests

            # Assume local ollama
            payload = {
                "model": "llama3",  # default fallback
                "prompt": system_prompt,
                "stream": False,
            }
            # A signature summary is optional background enrichment. Keep a
            # stopped or unhealthy local runner from leaving a discovery task
            # blocked indefinitely.
            resp = requests.post(
                "http://localhost:11434/api/generate", json=payload, timeout=30
            )
            if resp.status_code == 200:
                summary_text = resp.json().get("response", "").strip()

        if not summary_text:
            summary_text = "Signature Style (LLM Summary unavailable. Relying on individual style matching)."

        with open(SUMMARY_FILE, "w") as f:
            json.dump({"summary": summary_text}, f)

        return summary_text

    except Exception as e:
        logger.error(f"Failed to generate LLM signature style summary: {e}")
        return None
