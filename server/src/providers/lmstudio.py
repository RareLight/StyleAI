"""
LM Studio Provider for metadata generation using the lmstudio-python library
"""

import json
import os
import re
from typing import Any
import lmstudio as lms
from .base import (
    LLMProviderBase,
    MetadataGenerationRequest,
    MetadataGenerationResponse,
)
from config import logger, LMSTUDIO_HOST, DEFAULT_MAX_TOKENS, DEBUG_CACHE_DIR


def _extract_json_from_prose(text: str) -> dict:
    open_brace_indices = [i for i, c in enumerate(text) if c == "{"]
    # Try parsing from each '{' until one succeeds and looks like our schema
    for start_idx in open_brace_indices:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start_idx:])
            if isinstance(obj, dict) and (
                "summary" in obj or "global" in obj or "keywords" in obj
            ):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON schema object found in response string")


class LMStudioProvider(LLMProviderBase):
    """
    Provider for LM Studio local inference.
    Uses the lmstudio-python library.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        raw_host = config.get("base_url") if "base_url" in config else LMSTUDIO_HOST
        self.host = self._normalize_host(raw_host) if raw_host else ""
        self.timeout = config.get("timeout", 720)
        # lmstudio-python's synchronous API defaults to timing out after ~60s of
        # inactivity when waiting for a response/stream event. Wire our configured
        # timeout through so metadata generation can run longer (e.g. 720s).
        #
        # Note: this timeout is global to the lmstudio-python sync API.
        try:
            set_sync_timeout = getattr(lms, "set_sync_api_timeout", None)
            if callable(set_sync_timeout):
                set_sync_timeout(self.timeout)
                logger.info(f"LM Studio sync API timeout set to {self.timeout}s")
            else:
                logger.debug(
                    "lmstudio-python set_sync_api_timeout not available; using SDK default timeout"
                )
        except Exception as e:
            logger.warning(
                f"Failed to set lmstudio-python sync API timeout: {e}", exc_info=True
            )

    @staticmethod
    def _normalize_host(host: str | None) -> str:
        """
        Normalize LM Studio host string by stripping http(s):// protocols and path suffixes.
        LM Studio SDK expects 'host:port' format (e.g. '192.168.1.207:12042' or 'localhost:1234').
        """
        if not host:
            return ""
        h = host.strip()
        h = re.sub(r"^https?://", "", h, flags=re.IGNORECASE)
        return h.split("/")[0].strip()

    def _resolve_host(self, host_override: str | None = None) -> str:
        """
        Resolve active LM Studio host using host override, specified default host,
        or automatic discovery via the LM Studio SDK (for dynamic port binding).
        """
        if host_override == "":
            return ""

        raw_candidate = host_override if host_override is not None else self.host
        candidate = self._normalize_host(raw_candidate)

        if not candidate:
            return ""

        # Cheap pre-check short-circuit: host without colon is invalid
        if ":" not in candidate:
            return candidate

        # If an explicit custom host was specified (e.g. 192.168.1.207:12042 or custom port),
        # validate it first before falling back to discovery.
        if candidate != self._normalize_host(LMSTUDIO_HOST):
            try:
                if lms.Client.is_valid_api_host(candidate):
                    self.host = candidate
                    return candidate
            except Exception as e:
                logger.debug(
                    f"Validation for custom LM Studio host {candidate} failed: {e}"
                )

        # Attempt auto-discovery via lmstudio SDK for dynamically assigned local API ports (e.g. 127.0.0.1:41343)
        try:
            find_host = getattr(lms.Client, "find_default_local_api_host", None)
            if callable(find_host):
                discovered = find_host()
                if discovered:
                    norm_discovered = self._normalize_host(discovered)
                    if norm_discovered and lms.Client.is_valid_api_host(
                        norm_discovered
                    ):
                        self.host = norm_discovered
                        return norm_discovered
        except Exception as e:
            logger.debug(f"LM Studio host auto-discovery failed: {e}")

        # Fall back to testing candidate host if valid
        try:
            if lms.Client.is_valid_api_host(candidate):
                return candidate
        except Exception:
            pass

        return candidate

    def is_available(self) -> bool:
        """Check if LM Studio server is reachable with a short timeout"""
        try:
            if not self.host:
                return False

            effective_host = self._resolve_host()
            if not effective_host or ":" not in effective_host:
                return False

            return bool(lms.Client.is_valid_api_host(effective_host))
        except Exception as e:
            logger.warning(f"LM Studio availability check failed for {self.host}: {e}")
            return False

    def _save_debug_cache(
        self,
        uuid_str: str,
        image_data,
        system_prompt: str,
        user_prompt: str,
        raw_response: Any = None,
    ):
        try:
            uuid_str = uuid_str or "unknown_uuid"
            if system_prompt and user_prompt:
                prompt_path = os.path.join(
                    DEBUG_CACHE_DIR, f"{uuid_str}_edit_prompt.txt"
                )
                with open(prompt_path, "w") as f_txt:
                    f_txt.write("==== SYSTEM PROMPT ====\n")
                    f_txt.write(system_prompt + "\n\n")
                    f_txt.write("==== USER PROMPT ====\n")
                    f_txt.write(user_prompt + "\n")

            if image_data is not None:
                if isinstance(image_data, list):
                    for i, img_bytes in enumerate(image_data):
                        if i == 0:
                            suffix = "_edit_dark.jpg"
                        elif i == 1:
                            suffix = "_edit_image.jpg"
                        elif i == 2:
                            suffix = "_edit_bright.jpg"
                        else:
                            suffix = f"_edit_image_{i}.jpg"
                        if isinstance(img_bytes, bytes):
                            img_path = os.path.join(
                                DEBUG_CACHE_DIR, f"{uuid_str}{suffix}"
                            )
                            with open(img_path, "wb") as f_img:
                                f_img.write(img_bytes)
                elif isinstance(image_data, bytes):
                    img_path = os.path.join(
                        DEBUG_CACHE_DIR, f"{uuid_str}_edit_image.jpg"
                    )
                    with open(img_path, "wb") as f_img:
                        f_img.write(image_data)

            if raw_response is not None:
                raw_path = os.path.join(
                    DEBUG_CACHE_DIR, f"{uuid_str}_edit_raw_response.txt"
                )
                with open(raw_path, "w") as f_raw:
                    f_raw.write(
                        raw_response
                        if isinstance(raw_response, str)
                        else json.dumps(raw_response, indent=2)
                    )
        except Exception as cache_err:
            logger.warning(f"Failed to write debug cache: {cache_err}")

    def generate_metadata(
        self, request: MetadataGenerationRequest
    ) -> MetadataGenerationResponse:
        """
        Generate metadata using LM Studio API.

        Args:
            request: MetadataGenerationRequest with image and options

        Returns:
            MetadataGenerationResponse with generated metadata
        """
        try:
            # Resolve host: request override -> provider default -> auto-discovery
            host_override = getattr(request, "lmstudio_base_url", None)
            effective_host = self._resolve_host(host_override)

            # Use a scoped client for this host instead of global default client
            with lms.Client(effective_host) as client:
                # Prepare image via client so we don't depend on the default client
                if request.image_data is None:
                    image_handles = []
                elif isinstance(request.image_data, list):
                    image_handles = [
                        client.files.prepare_image(img)
                        for img in request.image_data
                        if img is not None
                    ]
                else:
                    image_handles = [client.files.prepare_image(request.image_data)]
                model = client.llm.model(request.model)

                # Prepare prompts
                system_prompt = self._prepare_system_prompt(request)
                user_prompt = self._prepare_user_prompt(request)

                # Normalize the compatible response shape returned by LM Studio.
                response_schema = self._prepare_response_structure(request)

                # Make request to LM Studio
                logger.debug("Sending request to LM Studio")

                chat = lms.Chat(system_prompt)
                if image_handles:
                    chat.add_user_message(user_prompt, images=image_handles)
                else:
                    chat.add_user_message(user_prompt)

                self._save_debug_cache(
                    request.uuid, request.image_data, system_prompt, user_prompt
                )

                response = model.respond(
                    chat,
                    response_format=response_schema,
                    config={"temperature": request.temperature},
                )

            # Extract message content
            content = response.parsed
            logger.debug(f"LM Studio raw response: {content}")

            # The lmstudio-python client may return a JSON string instead of a dict.
            # Normalize to a dict so that `.get(...)` access below is always safe.
            if isinstance(content, str):
                try:
                    # Strip out <think> blocks if any
                    content = re.sub(
                        r"<think>.*?</think>", "", content, flags=re.DOTALL
                    )

                    content = _extract_json_from_prose(content)
                except Exception as parse_err:
                    # Check if it was a max tokens issue before raising the generic error
                    if hasattr(response, "stats") and hasattr(
                        response.stats, "stop_reason"
                    ):
                        if response.stats.stop_reason in ("length", "max_tokens"):
                            _max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS
                            return MetadataGenerationResponse(
                                uuid=request.uuid,
                                success=False,
                                error=(
                                    f"LM Studio stopped before finishing the response because the token "
                                    f"limit was reached (num_predict={_max_tokens}). Please raise the "
                                    f"Max Tokens setting in the plugin (General tab → AI Model section) "
                                    f"— try 4096 or higher."
                                ),
                            )
                    raise ValueError(
                        f"Unexpected non-JSON response from LM Studio: {content}"
                    ) from parse_err

            if not isinstance(content, dict):
                raise ValueError(
                    f"Unexpected response type from LM Studio: {type(content)}"
                )

            # Extract metadata
            keywords = self._normalize_keywords_structure(content.get("keywords", []))

            caption = content.get("caption") if request.generate_caption else None
            title = content.get("title") if request.generate_title else None
            alt_text = content.get("alt_text") if request.generate_alt_text else None

            # Token usage reporting
            input_tokens = 0
            output_tokens = 0
            try:
                # 1. Try to get usage from the response object directly (lms 0.4.x+)
                stats = getattr(response, "stats", None) or getattr(
                    response, "usage", None
                )
                if stats:
                    input_tokens = getattr(stats, "prompt_tokens", 0) or getattr(
                        stats, "input_tokens", 0
                    )
                    output_tokens = getattr(stats, "completion_tokens", 0) or getattr(
                        stats, "output_tokens", 0
                    )

                # 2. Fallback: Manual tokenization for accuracy
                if input_tokens == 0 and hasattr(model, "tokenize"):
                    # For input, we should tokenize the full prompt as seen by the model
                    try:
                        # model.apply_prompt_template(chat) returns the raw string if available
                        full_prompt = (
                            model.apply_prompt_template(chat)
                            if hasattr(model, "apply_prompt_template")
                            else user_prompt
                        )
                        input_tokens = len(model.tokenize(full_prompt))
                    except Exception:
                        input_tokens = len(model.tokenize(user_prompt))

                if (
                    output_tokens == 0
                    and hasattr(model, "tokenize")
                    and isinstance(content, dict)
                ):
                    output_tokens = len(model.tokenize(json.dumps(content)))

                logger.info(
                    f"LM Studio token usage for {request.uuid}: input={input_tokens}, output={output_tokens}"
                )
            except Exception as usage_err:
                logger.debug(f"Could not calculate LM Studio token usage: {usage_err}")

            return MetadataGenerationResponse(
                uuid=request.uuid,
                success=True,
                keywords=keywords,
                caption=caption,
                title=title,
                alt_text=alt_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except Exception as e:
            logger.error(
                f"Error generating metadata with LM Studio: {e}", exc_info=True
            )
            return MetadataGenerationResponse(
                uuid=request.uuid, success=False, error=str(e)
            )

    def list_available_models(self) -> list:
        """
        List available LM Studio models using the lmstudio-python library.

        Returns:
            List of model identifiers for vision-capable models.
        """
        try:
            effective_host = self._resolve_host()
            # Use a scoped client so we respect the resolved active host and
            # avoid relying on a hardcoded default API port.
            with lms.Client(effective_host) as client:
                models = client.llm.list_downloaded()
                # Only populate the dropdown with vision-capable models
                vision_models = [
                    model.model_key
                    for model in models
                    if getattr(
                        model,
                        "vision",
                        getattr(getattr(model, "info", None), "vision", False),
                    )
                ]
                return vision_models

        except Exception as e:
            logger.error(
                f"An unexpected error occurred while listing LM Studio models: {e}",
                exc_info=True,
            )
            return []
