"""
LM Studio Provider for metadata generation using the lmstudio-python library
"""

import json
import os
import re
import time
from urllib.parse import urlsplit

import lmstudio as lms
import requests
from .base import (
    LLMProviderBase,
    MetadataGenerationRequest,
    MetadataGenerationResponse,
)
from config import (
    DEFAULT_MAX_TOKENS,
    LMSTUDIO_CONTEXT_LENGTH,
    LMSTUDIO_HOST,
    LMSTUDIO_IDLE_TTL_SECONDS,
    logger,
)


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

    def __init__(self):
        super().__init__()
        self.host = self._normalize_host(LMSTUDIO_HOST)
        self._logged_load_configs: set[str] = set()
        # Warm-up discovers engine-protocol pairs whose speculative context is
        # supplied by LM Studio's saved load configuration. Measured requests
        # can then avoid repeating a known-invalid prediction-time override.
        self._load_time_draft_pairs: set[tuple[str, str]] = set()
        self.timeout = 720
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
        LM Studio SDK expects ``host:port`` format.
        """
        if not host:
            return ""
        h = host.strip()
        h = re.sub(r"^https?://", "", h, flags=re.IGNORECASE)
        return h.split("/")[0].strip()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        hostname = urlsplit(f"//{host}").hostname
        return hostname == "localhost" or bool(
            hostname and (hostname == "::1" or hostname.startswith("127."))
        )

    @staticmethod
    def _requires_load_time_speculation(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "speculativedecoding capability gap" in message
            and "must be configured at load time" in message
        )

    def _resolve_host(self) -> str:
        """
        Resolve the loopback LM Studio host, including dynamic local API ports.
        """
        candidate = self._normalize_host(self.host)

        if not candidate or not self._is_loopback_host(candidate):
            return ""

        # Cheap pre-check short-circuit: host without colon is invalid
        if ":" not in candidate:
            return candidate

        # LM Studio can assign a dynamic loopback API port.
        try:
            find_host = getattr(lms.Client, "find_default_local_api_host", None)
            if callable(find_host):
                discovered = find_host()
                if discovered:
                    norm_discovered = self._normalize_host(discovered)
                    if (
                        norm_discovered
                        and self._is_loopback_host(norm_discovered)
                        and lms.Client.is_valid_api_host(norm_discovered)
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
            request_started = time.perf_counter()
            effective_host = self._resolve_host()

            # Use a scoped client for this host instead of global default client
            with lms.Client(effective_host) as client:
                # Prepare image via client so we don't depend on the default client
                upload_started = time.perf_counter()
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
                upload_seconds = time.perf_counter() - upload_started

                model_started = time.perf_counter()
                model = client.llm.model(
                    request.model,
                    ttl=LMSTUDIO_IDLE_TTL_SECONDS,
                    config={
                        "contextLength": LMSTUDIO_CONTEXT_LENGTH,
                        "flashAttention": True,
                    },
                )
                model_seconds = time.perf_counter() - model_started
                if request.model not in self._logged_load_configs:
                    try:
                        load_config = model.get_load_config()
                        logger.info(
                            "LM Studio model load config "
                            "(context_length=%s, flash_attention=%s, "
                            "offload_kv_cache_to_gpu=%s, fp16_kv_cache=%s, ttl=%ds)",
                            getattr(load_config, "context_length", None),
                            getattr(load_config, "flash_attention", None),
                            getattr(load_config, "offload_kv_cache_to_gpu", None),
                            getattr(load_config, "use_fp16_for_kv_cache", None),
                            LMSTUDIO_IDLE_TTL_SECONDS,
                        )
                    except Exception:
                        logger.debug(
                            "Could not inspect LM Studio model load config",
                            exc_info=True,
                        )
                    self._logged_load_configs.add(request.model)

                # Prepare prompts
                system_prompt = self._prepare_system_prompt(request)
                user_prompt = self._prepare_user_prompt(request)

                # Normalize the compatible response shape returned by LM Studio.
                response_schema = self._prepare_response_structure(request)
                schema_chars = len(json.dumps(response_schema, separators=(",", ":")))
                image_bytes = sum(
                    len(image)
                    for image in (
                        request.image_data
                        if isinstance(request.image_data, list)
                        else [request.image_data]
                    )
                    if image is not None
                )

                # Make request to LM Studio
                logger.debug("Sending request to LM Studio")

                chat = lms.Chat(system_prompt)
                if image_handles:
                    chat.add_user_message(user_prompt, images=image_handles)
                else:
                    chat.add_user_message(user_prompt)

                inference_started = time.perf_counter()
                max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS
                prediction_config = {
                    "temperature": request.temperature,
                    "maxTokens": max_tokens,
                }
                draft_pair = (
                    (request.model, request.draft_model)
                    if request.draft_model
                    else None
                )
                use_saved_load_time_draft = bool(
                    draft_pair and draft_pair in self._load_time_draft_pairs
                )
                if request.draft_model and not use_saved_load_time_draft:
                    prediction_config["draftModel"] = request.draft_model
                load_time_fallback = use_saved_load_time_draft
                try:
                    response = model.respond(
                        chat,
                        response_format=response_schema,
                        config=prediction_config,
                    )
                except Exception as exc:
                    if (
                        not request.draft_model
                        or not self._requires_load_time_speculation(exc)
                    ):
                        raise
                    # The public SDK does not expose LM Studio's engine-protocol
                    # load fields. Retry without the prediction override so the
                    # saved per-model load configuration can take effect. Draft
                    # activity is verified from response statistics below.
                    logger.info(
                        "LM Studio requires load-time speculative decoding for "
                        "main=%s draft=%s; retrying with saved model load settings",
                        request.model,
                        request.draft_model,
                    )
                    fallback_config = dict(prediction_config)
                    fallback_config.pop("draftModel", None)
                    response = model.respond(
                        chat,
                        response_format=response_schema,
                        config=fallback_config,
                    )
                    load_time_fallback = True
                inference_seconds = time.perf_counter() - inference_started

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
                        if response.stats.stop_reason in (
                            "length",
                            "max_tokens",
                            "maxPredictedTokensReached",
                        ):
                            return MetadataGenerationResponse(
                                uuid=request.uuid,
                                success=False,
                                error=(
                                    f"LM Studio stopped before finishing the response because the token "
                                    f"limit was reached (maxTokens={max_tokens}). Please raise the "
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
            time_to_first_token = 0
            tokens_per_second = 0
            total_seconds = time.perf_counter() - request_started
            try:
                # 1. Try to get usage from the response object directly (lms 0.4.x+)
                stats = getattr(response, "stats", None) or getattr(
                    response, "usage", None
                )
                if stats:

                    def numeric_stat(*names):
                        for name in names:
                            value = getattr(stats, name, None)
                            if isinstance(value, (int, float)) and not isinstance(
                                value, bool
                            ):
                                return value
                        return 0

                    input_tokens = int(
                        numeric_stat(
                            "prompt_tokens_count", "prompt_tokens", "input_tokens"
                        )
                    )
                    output_tokens = int(
                        numeric_stat(
                            "predicted_tokens_count",
                            "completion_tokens",
                            "output_tokens",
                        )
                    )
                    time_to_first_token = numeric_stat("time_to_first_token_sec")
                    tokens_per_second = numeric_stat("tokens_per_second")

                # Do not call model tokenization after the scoped client has
                # closed. Some lmstudio-python versions try to reconnect via an
                # async handler from this synchronous path, leaking an un-awaited
                # coroutine. Missing SDK usage statistics are safely reported as
                # zero instead of opening another model session.

                total_seconds = time.perf_counter() - request_started
                logger.info(
                    "LM Studio request completed in %.2fs "
                    "(upload=%.2fs, model=%.2fs, inference=%.2fs, "
                    "time_to_first_token=%.2fs, tokens_per_second=%.2f, "
                    "input_tokens=%d, output_tokens=%d, max_tokens=%d, "
                    "image_bytes=%d, system_chars=%d, user_chars=%d, schema_chars=%d)",
                    total_seconds,
                    upload_seconds,
                    model_seconds,
                    inference_seconds,
                    time_to_first_token,
                    tokens_per_second,
                    input_tokens,
                    output_tokens,
                    max_tokens,
                    image_bytes,
                    len(system_prompt),
                    len(user_prompt),
                    schema_chars,
                )
            except Exception as usage_err:
                logger.debug(f"Could not calculate LM Studio token usage: {usage_err}")

            inference = {}
            stats = getattr(response, "stats", None)
            for output_key, attribute_name in (
                ("used_draft_model", "used_draft_model_key"),
                ("total_draft_tokens", "total_draft_tokens_count"),
                ("accepted_draft_tokens", "accepted_draft_tokens_count"),
                ("rejected_draft_tokens", "rejected_draft_tokens_count"),
                ("ignored_draft_tokens", "ignored_draft_tokens_count"),
            ):
                value = getattr(stats, attribute_name, None) if stats else None
                if value is not None:
                    inference[output_key] = value
            total_draft_tokens = inference.get("total_draft_tokens")
            accepted_draft_tokens = inference.get("accepted_draft_tokens")
            if isinstance(total_draft_tokens, int) and total_draft_tokens > 0:
                inference["draft_acceptance_rate"] = round(
                    int(accepted_draft_tokens or 0) / total_draft_tokens, 4
                )
            if request.draft_model:
                inference["requested_draft_model"] = request.draft_model
            if load_time_fallback:
                inference["speculation_configuration"] = "saved_load_time"
                used_draft_model = inference.get("used_draft_model")
                has_draft_activity = bool(used_draft_model) or (
                    isinstance(total_draft_tokens, int) and total_draft_tokens > 0
                )
                if not has_draft_activity:
                    return MetadataGenerationResponse(
                        uuid=request.uuid,
                        success=False,
                        error=(
                            "LM Studio requires speculative decoding to be configured "
                            "when the main model is loaded, but the retry reported no "
                            "draft-token activity. Configure the draft/MTP model in "
                            "LM Studio's saved settings for the main model, unload and "
                            "reload the main model, then rerun this benchmark."
                        ),
                        timing={
                            "provider_total_ms": total_seconds * 1000.0,
                            "image_upload_ms": upload_seconds * 1000.0,
                            "model_load_ms": model_seconds * 1000.0,
                            "inference_ms": inference_seconds * 1000.0,
                            "time_to_first_token_ms": time_to_first_token * 1000.0,
                            "tokens_per_second": float(tokens_per_second),
                        },
                        inference=inference,
                    )
                if draft_pair:
                    self._load_time_draft_pairs.add(draft_pair)
            warning = None
            if request.benchmark_variant == "baseline" and (
                inference.get("used_draft_model")
                or (isinstance(total_draft_tokens, int) and total_draft_tokens > 0)
            ):
                warning = (
                    "LM Studio applied a saved draft model to a baseline benchmark "
                    "run; disable the model's speculative-decoding default and rerun"
                )

            return MetadataGenerationResponse(
                uuid=request.uuid,
                success=True,
                keywords=keywords,
                caption=caption,
                title=title,
                alt_text=alt_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                timing={
                    "provider_total_ms": total_seconds * 1000.0,
                    "image_upload_ms": upload_seconds * 1000.0,
                    "model_load_ms": model_seconds * 1000.0,
                    "inference_ms": inference_seconds * 1000.0,
                    "time_to_first_token_ms": time_to_first_token * 1000.0,
                    "tokens_per_second": float(tokens_per_second),
                },
                inference=inference,
                warning=warning,
            )

        except Exception as e:
            logger.error(
                f"Error generating metadata with LM Studio: {e}", exc_info=True
            )
            return MetadataGenerationResponse(
                uuid=request.uuid, success=False, error=str(e)
            )

    @staticmethod
    def _native_model_lookup(payload: object) -> dict[str, dict]:
        """Index native v1 model records by base, variant, and selected keys."""
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return {}
        lookup: dict[str, dict] = {}
        for record in payload["models"]:
            if not isinstance(record, dict) or record.get("type") != "llm":
                continue
            keys = [record.get("key"), record.get("selected_variant")]
            variants = record.get("variants")
            if isinstance(variants, list):
                keys.extend(variants)
            for key in keys:
                if isinstance(key, str) and key.strip():
                    lookup[key.strip()] = record
        return lookup

    def _list_native_models(self, effective_host: str) -> dict[str, dict]:
        """Fetch optional rich metadata without proxies, redirects, or egress."""
        if not effective_host or not self._is_loopback_host(effective_host):
            return {}
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                f"http://{effective_host}/api/v1/models",
                headers={"Accept": "application/json"},
                timeout=2.0,
                allow_redirects=False,
            )
            response.raise_for_status()
            return self._native_model_lookup(response.json())
        except Exception as exc:
            logger.debug("LM Studio native model metadata unavailable: %s", exc)
            return {}
        finally:
            session.close()

    @staticmethod
    def _match_native_model(
        model_key: str, native_models: dict[str, dict]
    ) -> dict | None:
        exact = native_models.get(model_key)
        if exact is not None:
            return exact
        base_key = model_key.split("@", 1)[0]
        return native_models.get(base_key)

    @staticmethod
    def _publisher_from_path(path: object) -> str | None:
        if not isinstance(path, str):
            return None
        normalized = path.replace("\\", "/").strip("/")
        if "/" not in normalized:
            return None
        publisher = normalized.split("/", 1)[0].strip()
        return publisher or None

    @staticmethod
    def _fallback_variant(model_key: str, path: object) -> str | None:
        if "@" in model_key:
            variant = model_key.rsplit("@", 1)[1].strip()
            return variant or None
        if not isinstance(path, str):
            return None
        filename = os.path.basename(path.replace("\\", "/"))
        stem, _ = os.path.splitext(filename)
        return stem if stem and stem != model_key else None

    @staticmethod
    def _normalize_distribution_format(model_format: object) -> str | None:
        """Use LM Studio's familiar GGUF/MLX distribution labels in the UI."""
        if not isinstance(model_format, str) or not model_format.strip():
            return None
        normalized = model_format.strip().lower()
        if normalized == "safetensors":
            return "mlx"
        return normalized

    @staticmethod
    def _is_draft_only_model(*identifiers: object) -> bool:
        """Identify dedicated draft/speculator artifacts from stable model names.

        LM Studio may report an MTP artifact as vision-capable when it lives in
        the same repository as its multimodal target. The public discovery API
        does not currently expose a draft-only capability flag, so use only
        explicit filename/name tokens rather than parameter-size heuristics.
        """
        for identifier in identifiers:
            if not isinstance(identifier, str):
                continue
            normalized = identifier.strip().lower().replace("\\", "/")
            if re.search(
                r"(?:^|[/_.@-])(?:mtp|draft|speculator)(?:$|[/_.@-])",
                normalized,
            ):
                return True
        return False

    @staticmethod
    def _is_mtp_model(*identifiers: object) -> bool:
        for identifier in identifiers:
            if not isinstance(identifier, str):
                continue
            normalized = identifier.strip().lower().replace("\\", "/")
            if re.search(r"(?:^|[/_.@-])mtp(?:$|[/_.@-])", normalized):
                return True
        return False

    @staticmethod
    def _format_model_label(detail: dict) -> str:
        display_name = str(detail.get("display_name") or detail["key"])
        qualifiers: list[str] = []
        params = detail.get("params_string")
        if params and str(params).lower() not in display_name.lower():
            qualifiers.append(str(params))
        quantization = detail.get("quantization")
        if quantization:
            qualifiers.append(str(quantization))
        model_format = detail.get("format")
        if model_format:
            qualifiers.append(str(model_format).upper())
        publisher = detail.get("publisher")
        if publisher:
            qualifiers.append(str(publisher))
        variant = detail.get("selected_variant")
        if not quantization and variant:
            qualifiers.append(str(variant))
        return (
            f"{display_name} — {' · '.join(dict.fromkeys(qualifiers))}"
            if qualifiers
            else display_name
        )

    def _list_available_model_details(self, *, vision_only: bool) -> list[dict]:
        """List downloaded LLMs with rich labels and stable SDK inference keys."""
        try:
            effective_host = self._resolve_host()
            with lms.Client(effective_host) as client:
                models = client.llm.list_downloaded()
            native_models = self._list_native_models(effective_host)
            details: list[dict] = []
            for model in models:
                info = getattr(model, "info", None)
                vision = bool(getattr(model, "vision", getattr(info, "vision", False)))
                model_key = str(model.model_key)
                native = self._match_native_model(model_key, native_models) or {}
                path = getattr(model, "path", getattr(info, "path", None))
                draft_only = self._is_draft_only_model(
                    model_key,
                    path,
                    native.get("display_name"),
                    native.get("selected_variant"),
                    getattr(info, "display_name", None),
                )
                mtp_model = self._is_mtp_model(
                    model_key,
                    path,
                    native.get("display_name"),
                    native.get("selected_variant"),
                    getattr(info, "display_name", None),
                )
                if vision_only and (not vision or draft_only):
                    continue
                quantization = native.get("quantization")
                quantization_name = (
                    quantization.get("name") if isinstance(quantization, dict) else None
                )
                model_format = native.get("format") or getattr(info, "format", None)
                detail = {
                    "key": model_key,
                    "display_name": native.get("display_name")
                    or getattr(info, "display_name", None)
                    or model_key,
                    "publisher": native.get("publisher")
                    or self._publisher_from_path(path),
                    "params_string": native.get("params_string")
                    or getattr(info, "params_string", None),
                    "format": self._normalize_distribution_format(model_format),
                    "quantization": quantization_name,
                    "bits_per_weight": (
                        quantization.get("bits_per_weight")
                        if isinstance(quantization, dict)
                        else None
                    ),
                    "selected_variant": native.get("selected_variant")
                    or self._fallback_variant(model_key, path),
                    "size_bytes": native.get("size_bytes")
                    or getattr(info, "size_bytes", None),
                    "vision": vision,
                }
                if mtp_model:
                    detail["speculation_configuration"] = "saved_load_time"
                detail["label"] = self._format_model_label(detail)
                details.append(
                    {key: value for key, value in detail.items() if value is not None}
                )
            return details
        except Exception as exc:
            logger.error(
                "An unexpected error occurred while listing LM Studio models: %s",
                exc,
                exc_info=True,
            )
            return []

    def list_available_model_details(self) -> list[dict]:
        """List vision models with rich labels and stable SDK inference keys."""
        return self._list_available_model_details(vision_only=True)

    def list_available_draft_model_details(self) -> list[dict]:
        """List all downloaded LM Studio LLMs as explicit draft candidates."""
        return self._list_available_model_details(vision_only=False)

    def list_available_models(self) -> list[str]:
        """
        List available LM Studio models using the lmstudio-python library.

        Returns:
            List of model identifiers for vision-capable models.
        """
        return [detail["key"] for detail in self.list_available_model_details()]
