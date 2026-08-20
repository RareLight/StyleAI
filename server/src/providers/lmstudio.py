"""
LM Studio Provider for metadata generation using the lmstudio-python library
"""

import json
from importlib.metadata import PackageNotFoundError, version
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

try:
    LMSTUDIO_SDK_VERSION = version("lmstudio")
except PackageNotFoundError:
    LMSTUDIO_SDK_VERSION = "unknown"
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
        self._load_config_evidence_by_model: dict[str, dict[str, object]] = {}
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
        speculation_mode = (
            str(
                request.speculation_mode
                or ("full_draft" if request.draft_model else "baseline")
            )
            .strip()
            .lower()
        )
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
                load_config_evidence = dict(
                    self._load_config_evidence_by_model.get(request.model, {})
                )
                if request.model not in self._load_config_evidence_by_model:
                    try:
                        load_config = model.get_load_config()
                        for output_key, attribute_name in (
                            ("context_length", "context_length"),
                            ("flash_attention", "flash_attention"),
                            ("offload_kv_cache_to_gpu", "offload_kv_cache_to_gpu"),
                            ("use_fp16_for_kv_cache", "use_fp16_for_kv_cache"),
                            ("num_experts", "num_experts"),
                        ):
                            value = getattr(load_config, attribute_name, None)
                            if isinstance(value, (str, int, float, bool)):
                                load_config_evidence[output_key] = value
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
                    self._load_config_evidence_by_model[request.model] = dict(
                        load_config_evidence
                    )

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
                    if speculation_mode == "full_draft" and request.draft_model
                    else None
                )
                use_saved_load_time_draft = bool(
                    draft_pair and draft_pair in self._load_time_draft_pairs
                )
                if (
                    speculation_mode == "full_draft"
                    and request.draft_model
                    and not use_saved_load_time_draft
                ):
                    prediction_config["draftModel"] = request.draft_model
                # Integrated MTP belongs to the target artifact and is activated
                # by LM Studio while loading that model. Never misroute its head
                # through prediction-time ``draftModel``. ``load_time_fallback``
                # is reserved for an explicitly selected full draft whose engine
                # rejects prediction-time configuration.
                load_time_fallback = use_saved_load_time_draft
                try:
                    response = model.respond(
                        chat,
                        response_format=response_schema,
                        config=prediction_config,
                    )
                except Exception as exc:
                    if (
                        speculation_mode != "full_draft"
                        or not request.draft_model
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
            inference["lmstudio_sdk_version"] = LMSTUDIO_SDK_VERSION
            inference["vision_input_present"] = bool(request.image_data)
            if load_config_evidence:
                inference["load_config"] = load_config_evidence
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
            inference["requested_speculation_mode"] = speculation_mode
            if speculation_mode != "baseline":
                inference["draft_depth"] = "lmstudio_default_not_exposed"
            if speculation_mode == "automatic_mtp":
                inference["speculation_configuration"] = "automatic_model_load"
                has_draft_activity = bool(inference.get("used_draft_model")) or (
                    isinstance(total_draft_tokens, int) and total_draft_tokens > 0
                )
                if has_draft_activity:
                    inference["effective_speculation_mode"] = "automatic_mtp"
                    inference["speculation_active_for_vision_request"] = True
                    inference["verification_status"] = "observed"
                else:
                    # LM Studio can load an integrated head automatically without
                    # exposing draft counters through the SDK. Preserve the valid
                    # metadata response, but do not claim that MTP was active.
                    inference["effective_speculation_mode"] = "unknown"
                    inference["speculation_active_for_vision_request"] = "unknown"
                    inference["verification_status"] = "runtime_managed_unreported"
            elif load_time_fallback:
                inference["speculation_configuration"] = "saved_load_time"
                inference["fallback_reason"] = "engine_protocol_requires_load_time"
                used_draft_model = inference.get("used_draft_model")
                has_draft_activity = bool(used_draft_model) or (
                    isinstance(total_draft_tokens, int) and total_draft_tokens > 0
                )
                if not has_draft_activity:
                    inference["verification_status"] = "speculation_not_verified"
                    return MetadataGenerationResponse(
                        uuid=request.uuid,
                        success=False,
                        error=(
                            "LM Studio full-draft speculative decoding is not active "
                            "for the loaded main model: the retry reported no "
                            "draft-token activity. Configure the model in LM Studio's "
                            "load settings, unload and reload it, then rerun the benchmark."
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
                inference["effective_speculation_mode"] = speculation_mode
                inference["speculation_active_for_vision_request"] = True
                inference["verification_status"] = "verified"
            elif inference.get("used_draft_model") or (
                isinstance(total_draft_tokens, int) and total_draft_tokens > 0
            ):
                inference["effective_speculation_mode"] = "full_draft"
                inference["speculation_active_for_vision_request"] = True
                inference["verification_status"] = "verified"
            else:
                inference["effective_speculation_mode"] = "baseline"
                inference["speculation_active_for_vision_request"] = False
                inference["verification_status"] = "not_applicable"
            warning = None
            if (
                speculation_mode == "automatic_mtp"
                and inference["verification_status"] == "runtime_managed_unreported"
            ):
                warning = (
                    "LM Studio returned the vision result but exposed no draft-token "
                    "telemetry, so automatic MTP activity could not be verified for "
                    "this request"
                )
            elif request.benchmark_variant == "baseline" and (
                inference.get("used_draft_model")
                or (isinstance(total_draft_tokens, int) and total_draft_tokens > 0)
            ):
                warning = (
                    "LM Studio reported speculative decoding during a baseline "
                    "benchmark run; rerun this model as automatic MTP or disable its "
                    "saved full-draft configuration"
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
                uuid=request.uuid,
                success=False,
                error=str(e),
                inference={
                    "lmstudio_sdk_version": LMSTUDIO_SDK_VERSION,
                    "vision_input_present": bool(request.image_data),
                    "requested_speculation_mode": speculation_mode,
                    "effective_speculation_mode": "unknown",
                    "speculation_active_for_vision_request": "unknown",
                    **self._classify_runtime_failure(e),
                },
            )

    @staticmethod
    def _classify_runtime_failure(exc: Exception) -> dict[str, str]:
        """Classify common LM Studio package/runtime failures for reports."""
        normalized = str(exc).lower()
        if (
            "missing " in normalized
            and " parameters" in normalized
            and any(
                marker in normalized
                for marker in ("vision_tower", "embed_vision", "vision_model")
            )
        ):
            return {
                "failure_stage": "model_load",
                "failure_category": "vision_weights_unavailable",
                "failure_reason": "model_package_or_vision_sidecar_incompatible",
            }
        if "parameters not in model" in normalized and ".mtp." in normalized:
            return {
                "failure_stage": "model_load",
                "failure_category": "integrated_mtp_runtime_unsupported",
                "failure_reason": "runtime_rejected_integrated_mtp_tensors",
            }
        if "failed to process speculative batch" in normalized:
            return {
                "failure_stage": "generation",
                "failure_category": "speculative_decode_runtime_failure",
                "failure_reason": "runtime_failed_speculative_batch",
            }
        if LMStudioProvider._requires_load_time_speculation(exc):
            return {
                "failure_stage": "configuration",
                "failure_category": "speculation_requires_load_time",
                "failure_reason": "engine_protocol_rejected_prediction_override",
            }
        if (
            "failed to load model" in normalized
            or "error when loading model" in normalized
        ):
            return {
                "failure_stage": "model_load",
                "failure_category": "model_runtime_incompatible",
                "failure_reason": "lmstudio_model_load_failed",
            }
        return {
            "failure_stage": "generation",
            "failure_category": "provider_error",
            "failure_reason": "unclassified_lmstudio_error",
        }

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
        """Identify standalone MTP sidecars, not complete MTP-enabled models.

        LM Studio may report an MTP artifact as vision-capable when it lives in
        the same repository as its multimodal target. Sidecars conventionally
        begin with ``mtp-``; a complete model whose repository contains ``MTP``
        must remain available as a main model.
        """
        for identifier in identifiers:
            if not isinstance(identifier, str):
                continue
            normalized = identifier.strip().lower().replace("\\", "/")
            leaf = normalized.rsplit("/", 1)[-1]
            if re.match(r"^mtp(?:$|[-_. ])", leaf) or re.match(
                r"^mtp(?:$|[-_. ])", normalized
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
    def _native_text_values(value: object, prefix: str = "") -> list[tuple[str, str]]:
        """Flatten bounded native metadata for capability evidence."""
        values: list[tuple[str, str]] = []
        if len(prefix) > 256:
            return values
        if isinstance(value, dict):
            for key, item in value.items():
                values.extend(
                    LMStudioProvider._native_text_values(
                        item, f"{prefix}.{key}" if prefix else str(key)
                    )
                )
        elif isinstance(value, list):
            for index, item in enumerate(value[:100]):
                values.extend(
                    LMStudioProvider._native_text_values(item, f"{prefix}[{index}]")
                )
        elif isinstance(value, (str, int, float, bool)):
            values.append((prefix.lower(), str(value).strip().lower()))
        return values

    @classmethod
    def _native_mtp_capability(cls, native: dict) -> tuple[str, str]:
        """Return capability only when LM Studio explicitly reports MTP evidence."""
        for key, value in cls._native_text_values(native):
            if "mtp" not in key and "speculative" not in key:
                continue
            if value in {"true", "supported", "enabled", "mtp", "draft-mtp"}:
                return "supported", "lmstudio_native_capability"
            if value in {"false", "unsupported", "disabled"}:
                return "unsupported", "lmstudio_native_capability"
        return "unknown", "runtime_probe_required"

    @staticmethod
    def _native_identity(native: dict, *names: str) -> str | None:
        wanted = {name.lower() for name in names}
        for key, value in LMStudioProvider._native_text_values(native):
            if key.rsplit(".", 1)[-1] in wanted and value:
                return value
        return None

    @staticmethod
    def _format_model_label(detail: dict) -> str:
        display_name = str(detail.get("display_name") or detail["key"])
        qualifiers: list[str] = []
        params = detail.get("params_string")
        if params and str(params).lower() not in display_name.lower():
            qualifiers.append(str(params))
        architecture = detail.get("architecture")
        if architecture and str(architecture).lower() not in display_name.lower():
            qualifiers.append(str(architecture))
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
        if detail.get("speculation_kind") == "mtp_integrated":
            qualifiers.append("MTP")
        elif detail.get("speculation_kind") == "mtp_packaged_unverified":
            qualifiers.append("MTP package")
        return (
            f"{display_name} — {' · '.join(dict.fromkeys(qualifiers))}"
            if qualifiers
            else display_name
        )

    def _list_available_model_details(
        self, *, vision_only: bool, include_mtp_sidecars: bool = False
    ) -> list[dict]:
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
                native_mtp_capability, native_mtp_reason = self._native_mtp_capability(
                    native
                )
                mtp_model = mtp_model or native_mtp_capability == "supported"
                if draft_only and not include_mtp_sidecars:
                    continue
                if vision_only and not vision:
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
                architecture = self._native_identity(
                    native, "architecture", "model_type", "modeltype"
                )
                vocabulary = self._native_identity(
                    native, "vocabulary", "vocabulary_id", "tokenizer", "tokenizer_id"
                )
                if architecture:
                    detail["architecture"] = architecture
                if vocabulary:
                    detail["vocabulary_identity"] = vocabulary
                runtime = self._native_identity(
                    native, "runtime", "runtime_name", "engine", "engine_name"
                )
                revision = self._native_identity(
                    native, "revision", "commit", "model_revision"
                )
                repository = self._native_identity(
                    native, "repository", "repo", "repository_id", "repo_id"
                )
                if runtime:
                    detail["runtime"] = runtime
                if revision:
                    detail["revision"] = revision
                if repository:
                    detail["repository"] = repository
                if draft_only:
                    detail.update(
                        {
                            "speculation_kind": "mtp_sidecar",
                            "speculation_capability": "unknown",
                            "speculation_reason": "sidecar_requires_runtime_match",
                            "speculation_configuration": "saved_load_time",
                        }
                    )
                elif native_mtp_capability == "supported" or (
                    mtp_model and detail.get("format") == "gguf"
                ):
                    capability, reason = native_mtp_capability, native_mtp_reason
                    if capability == "unknown":
                        reason = "integrated_mtp_name_hint"
                    detail.update(
                        {
                            "speculation_kind": "mtp_integrated",
                            "speculation_capability": capability,
                            "speculation_reason": reason,
                            "speculation_configuration": "automatic_model_load",
                        }
                    )
                elif mtp_model:
                    # A name alone cannot establish that an MLX/Safetensors
                    # package contains a loadable MTP head. Keep it available as
                    # a target, but do not present it as automatic MTP or as a
                    # complete drafting model.
                    detail.update(
                        {
                            "speculation_kind": "mtp_packaged_unverified",
                            "speculation_capability": "unsupported",
                            "speculation_reason": "mlx_mtp_runtime_unverified",
                            "speculation_configuration": "model_package",
                        }
                    )
                else:
                    detail["speculation_kind"] = "complete_model"
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
        """List complete models only; MTP sidecars are never ordinary drafts."""
        details = []
        for detail in self._list_available_model_details(vision_only=False):
            if detail.get("speculation_kind") in {
                "mtp_integrated",
                "mtp_sidecar",
                "mtp_packaged_unverified",
            }:
                continue
            detail["speculation_kind"] = "full_draft"
            detail["speculation_capability"] = "unknown"
            detail["speculation_reason"] = "lmstudio_pair_check_required"
            details.append(detail)
        return details

    def list_unavailable_speculation_details(self) -> list[dict]:
        """Expose hidden MTP sidecars with an explicit reason for the UI."""
        details = self._list_available_model_details(
            vision_only=False, include_mtp_sidecars=True
        )
        unavailable = []
        for detail in details:
            if detail.get("speculation_kind") != "mtp_sidecar":
                continue
            item = dict(detail)
            item["speculation_capability"] = "unsupported"
            item["speculation_reason"] = "sidecar_pairing_not_exposed_by_lmstudio_sdk"
            item["message"] = (
                "Standalone MTP heads require an exact load-time target pairing that "
                "the installed LM Studio SDK cannot configure or verify."
            )
            unavailable.append(item)
        return unavailable

    def preflight_speculation(
        self, model: str, speculation_mode: str, draft_model: str | None = None
    ) -> dict:
        """Fail known-invalid pairs before exporting or submitting photos."""
        mode = str(speculation_mode or "baseline").strip().lower()
        all_details = self._list_available_model_details(
            vision_only=False, include_mtp_sidecars=True
        )
        by_key = {str(detail.get("key")): detail for detail in all_details}
        target = by_key.get(model)
        base = {
            "model": model,
            "speculation_mode": mode,
            "draft_model": draft_model,
        }
        if target is None:
            return base | {
                "capability": "unsupported",
                "reason": "target_not_downloaded",
                "message": "The selected LM Studio target model is not downloaded.",
            }
        if mode == "baseline":
            return base | {
                "capability": "supported",
                "reason": "baseline",
                "message": "Speculative decoding is disabled for this run.",
            }
        if mode == "automatic_mtp":
            if draft_model:
                return base | {
                    "capability": "unsupported",
                    "reason": "automatic_mtp_does_not_use_full_draft",
                    "message": "Automatic MTP cannot use a separate draft-model selection.",
                }
            if target.get("speculation_kind") != "mtp_integrated":
                return base | {
                    "capability": "unsupported",
                    "reason": "target_not_integrated_mtp",
                    "message": (
                        "The selected target is not a complete model with integrated "
                        "MTP tensors. Standalone mtp-* files are not selectable as drafts."
                    ),
                }
            capability = str(target.get("speculation_capability") or "unknown")
            return base | {
                "capability": capability,
                "reason": str(
                    target.get("speculation_reason") or "runtime_probe_required"
                ),
                "message": (
                    "LM Studio reports integrated MTP support. The runtime will "
                    "activate it automatically while loading the target model."
                    if capability == "supported"
                    else "This GGUF appears to contain integrated MTP. StyleAI will "
                    "use ordinary inference and let LM Studio activate it automatically."
                ),
                "target": target,
            }
        if mode != "full_draft":
            return base | {
                "capability": "unsupported",
                "reason": "unknown_speculation_mode",
                "message": f"Unknown speculation mode: {mode}",
            }
        draft = by_key.get(str(draft_model or ""))
        if draft is None:
            return base | {
                "capability": "unsupported",
                "reason": "draft_not_downloaded",
                "message": "The selected full draft model is not downloaded.",
            }
        if model == draft_model:
            return base | {
                "capability": "unsupported",
                "reason": "target_equals_draft",
                "message": "A model cannot draft for itself.",
            }
        if draft.get("speculation_kind") == "mtp_sidecar":
            return base | {
                "capability": "unsupported",
                "reason": "mtp_sidecar_is_not_full_model",
                "message": "An mtp-* sidecar is not a complete drafting model.",
            }
        if target.get("format") != draft.get("format"):
            return base | {
                "capability": "unsupported",
                "reason": "format_mismatch",
                "message": "Target and draft models must use the same LM Studio runtime format.",
            }
        if (
            target.get("runtime")
            and draft.get("runtime")
            and target.get("runtime") != draft.get("runtime")
        ):
            return base | {
                "capability": "unsupported",
                "reason": "runtime_mismatch",
                "message": "Target and draft models must use the same LM Studio runtime.",
            }
        target_vocab = target.get("vocabulary_identity")
        draft_vocab = draft.get("vocabulary_identity")
        if target_vocab and draft_vocab and target_vocab != draft_vocab:
            return base | {
                "capability": "unsupported",
                "reason": "vocabulary_mismatch",
                "message": "LM Studio reports different tokenizer/vocabulary identities.",
            }
        capability = (
            "supported" if target_vocab and target_vocab == draft_vocab else "unknown"
        )
        return base | {
            "capability": capability,
            "reason": (
                "matching_vocabulary_identity"
                if capability == "supported"
                else "lmstudio_pair_check_required"
            ),
            "message": (
                "The models report a matching vocabulary identity."
                if capability == "supported"
                else "Format checks passed; LM Studio will make the final vocabulary compatibility check."
            ),
            "target": target,
            "draft": draft,
        }

    def list_available_models(self) -> list[str]:
        """
        List available LM Studio models using the lmstudio-python library.

        Returns:
            List of model identifiers for vision-capable models.
        """
        return [detail["key"] for detail in self.list_available_model_details()]
