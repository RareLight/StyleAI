"""AX Engine provider for local, loopback-only vision metadata generation."""

from __future__ import annotations

import json
import time
from typing import Any, override

import requests

from config import AX_ENGINE_BASE_URL, DEFAULT_MAX_TOKENS, logger
from services.axengine_runtime import get_axengine_runtime
from .base import (
    LLMProviderBase,
    MetadataGenerationRequest,
    MetadataGenerationResponse,
)


class AXEngineProvider(LLMProviderBase):
    """OpenAI-shaped adapter for a local AX Engine server."""

    _MAX_RESPONSE_BYTES = 4 * 1024 * 1024

    @override
    def __init__(self):
        super().__init__()
        self.base_url = AX_ENGINE_BASE_URL.rstrip("/")
        self.timeout = (5.0, 720.0)
        self.session = requests.Session()
        # Never inherit proxy settings for photo-bearing loopback requests.
        self.session.trust_env = False
        self.runtime = get_axengine_runtime()

    def configure_model_root(self, model_root: str | None) -> None:
        self.runtime.configure_model_root(model_root)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: tuple[float, float] | float | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=payload,
            timeout=timeout or self.timeout,
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/json"},
        )
        if 300 <= response.status_code < 400:
            response.close()
            raise RuntimeError("AX Engine attempted an unsupported redirect")

        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > self._MAX_RESPONSE_BYTES:
                    raise RuntimeError("AX Engine response exceeded the safety limit")
                chunks.append(chunk)
        finally:
            response.close()

        body = b"".join(chunks)
        if response.status_code >= 400:
            detail = ""
            try:
                error_payload = json.loads(body.decode("utf-8"))
                error = (
                    error_payload.get("error")
                    if isinstance(error_payload, dict)
                    else None
                )
                if isinstance(error, dict):
                    detail = str(error.get("message") or error.get("type") or "")
                elif error:
                    detail = str(error)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            suffix = f": {detail[:500]}" if detail else ""
            raise RuntimeError(f"AX Engine HTTP {response.status_code}{suffix}")

        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("AX Engine returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("AX Engine returned an invalid response envelope")
        return parsed

    @override
    def is_available(self) -> bool:
        return self.runtime.is_configured()

    @staticmethod
    def _is_vision_card(card: dict[str, Any]) -> bool:
        capabilities = card.get("capabilities")
        inputs = capabilities.get("input") if isinstance(capabilities, dict) else None
        ax_metadata = card.get("ax_engine")
        return bool(
            isinstance(inputs, dict)
            and inputs.get("image") is True
            and isinstance(ax_metadata, dict)
            and ax_metadata.get("native_multimodal_input_supported") is True
        )

    @staticmethod
    def _model_label(card: dict[str, Any]) -> str:
        model_id = str(card.get("id") or "")
        ax_metadata = card.get("ax_engine")
        ax_metadata = ax_metadata if isinstance(ax_metadata, dict) else {}
        family = str(ax_metadata.get("model_family") or "").strip()
        tensor_format = "MLX"
        support_tier = str(ax_metadata.get("support_tier") or "AX Engine").strip()
        qualifiers = [value for value in (family, tensor_format, support_tier) if value]
        return f"{model_id} — {' · '.join(qualifiers)}" if qualifiers else model_id

    @override
    def list_available_model_details(self) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        ownership = self.runtime.ownership()
        resident_cards_by_id: dict[str, dict[str, Any]] = {}
        resident_mapping = self.runtime.resident_mapping()
        mapped_resident_ids = set(resident_mapping.values())
        if ownership != "stopped":
            payload = self._request_json("GET", "/v1/models", timeout=(2.0, 5.0))
            cards = payload.get("data")
            for card in cards if isinstance(cards, list) else []:
                if not isinstance(card, dict) or not self._is_vision_card(card):
                    continue
                model_id = str(card.get("id") or "").strip()
                if not model_id:
                    continue
                resident_cards_by_id[model_id] = card
                if ownership == "owned" and model_id in mapped_resident_ids:
                    continue
                ax_metadata = card.get("ax_engine")
                ax_metadata = ax_metadata if isinstance(ax_metadata, dict) else {}
                details.append(
                    {
                        "key": model_id,
                        "label": self._model_label(card),
                        "display_name": model_id,
                        "provider": "axengine",
                        "format": "mlx",
                        "tensor_format": ax_metadata.get("tensor_format"),
                        "model_family": ax_metadata.get("model_family"),
                        "support_tier": ax_metadata.get("support_tier"),
                        "backend": ax_metadata.get("backend"),
                        "context_length": card.get("context_length"),
                        "max_output_tokens": card.get("max_output_tokens"),
                        "vision": True,
                        "native_multimodal": True,
                        "resident": True,
                        "loadable": True,
                        "speculation_kind": "runtime_managed",
                        "ax_engine": dict(ax_metadata),
                    }
                )
            if ownership == "external" and len(details) > 1:
                logger.warning(
                    "External AX Engine has multiple vision models resident; "
                    "StyleAI requires exactly one"
                )
                return []
        # An external server is user-managed; do not offer filesystem models that
        # would require StyleAI to mutate its residency.
        if ownership != "external":
            resident_keys = {detail["key"] for detail in details}
            for candidate in self.runtime.candidates():
                descriptor = candidate.descriptor()
                resident_id = resident_mapping.get(candidate.key)
                resident_card = resident_cards_by_id.get(resident_id or "")
                if resident_card is not None:
                    descriptor["resident"] = True
                    descriptor["native_multimodal"] = self._is_vision_card(
                        resident_card
                    )
                    descriptor["resident_model_id"] = resident_id
                    descriptor["context_length"] = resident_card.get("context_length")
                    descriptor["max_output_tokens"] = resident_card.get(
                        "max_output_tokens"
                    )
                if descriptor["key"] not in resident_keys:
                    details.append(descriptor)
        return details

    @override
    def list_available_models(self) -> list[str]:
        return [detail["key"] for detail in self.list_available_model_details()]

    @override
    def generate_metadata(
        self, request: MetadataGenerationRequest
    ) -> MetadataGenerationResponse:
        with self.runtime.inference_guard():
            return self._generate_metadata_guarded(request)

    def _generate_metadata_guarded(
        self, request: MetadataGenerationRequest
    ) -> MetadataGenerationResponse:
        started = time.perf_counter()
        try:
            resolved_model = self.runtime.ensure_model(request.model)
            model_payload = self._request_json("GET", "/v1/models", timeout=(2.0, 5.0))
            model_cards = model_payload.get("data")
            model_cards = model_cards if isinstance(model_cards, list) else []
            selected_card = next(
                (
                    card
                    for card in model_cards
                    if isinstance(card, dict)
                    and str(card.get("id") or "") == resolved_model
                ),
                None,
            )
            if not isinstance(selected_card, dict) or not self._is_vision_card(
                selected_card
            ):
                raise RuntimeError(
                    "Selected AX Engine model does not advertise native image input"
                )
            image_b64 = self._image_to_base64(request.image_data)
            payload = {
                "model": resolved_model,
                "messages": [
                    {"role": "system", "content": self._prepare_system_prompt(request)},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self._prepare_user_prompt(request),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                },
                            },
                        ],
                    },
                ],
                "temperature": request.temperature,
                "top_p": 0.9,
                "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
                "stream": False,
                # AX validates JSON objects post hoc; JSON Schema is not supported.
                "response_format": {"type": "json_object"},
            }
            result = self._request_json("POST", "/v1/chat/completions", payload=payload)
            choices = result.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("AX Engine returned no chat completion choice")
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("AX Engine returned empty chat content")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("metadata output was not a JSON object")

            usage = result.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            timing = {"provider_total_ms": (time.perf_counter() - started) * 1000.0}
            ax_metadata = result.get("ax_engine")
            inference = (
                {"ax_engine": dict(ax_metadata)}
                if isinstance(ax_metadata, dict)
                else None
            )
            return MetadataGenerationResponse(
                uuid=request.uuid,
                success=True,
                keywords=self._normalize_keywords_structure(parsed.get("keywords", [])),
                caption=parsed.get("caption") if request.generate_caption else None,
                title=parsed.get("title") if request.generate_title else None,
                alt_text=parsed.get("alt_text") if request.generate_alt_text else None,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                timing=timing,
                inference=inference,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("AX Engine returned invalid metadata JSON: %s", exc)
            return MetadataGenerationResponse(
                uuid=request.uuid,
                success=False,
                error=f"AX Engine JSON output error: {exc}",
            )
        except Exception as exc:
            logger.error("AX Engine metadata generation failed: %s", exc, exc_info=True)
            return MetadataGenerationResponse(
                uuid=request.uuid,
                success=False,
                error=str(exc),
            )

    @override
    def preflight_speculation(
        self, model: str, speculation_mode: str, draft_model: str | None = None
    ) -> dict[str, Any]:
        if draft_model:
            return {
                "capability": "unsupported",
                "reason": "runtime_managed_speculation",
                "message": "AX Engine manages MTP and speculative decoding at model load time.",
            }
        return {
            "capability": "supported",
            "reason": "runtime_managed",
            "message": "AX Engine controls speculative decoding for the loaded model.",
        }
