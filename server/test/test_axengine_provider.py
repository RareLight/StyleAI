import io
import json

from PIL import Image

from providers.axengine import AXEngineProvider
from providers.base import MetadataGenerationRequest


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _metadata_request():
    return MetadataGenerationRequest(
        image_data=_jpeg_bytes(),
        uuid="photo-1",
        provider="axengine",
        model="gemma-4-vision",
        generate_keywords=True,
        generate_caption=True,
        generate_title=True,
        generate_alt_text=True,
        language="English",
        temperature=0.1,
        max_tokens=1024,
        system_prompt=None,
        user_prompt=None,
        submit_keywords=False,
        submit_folder_names=False,
        existing_keywords=None,
    )


class _Response:
    def __init__(self, payload, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = json.dumps(payload).encode("utf-8")

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        return None


def test_availability_uses_runtime_configuration(mocker):
    provider = AXEngineProvider()
    provider.runtime = mocker.MagicMock()
    provider.runtime.is_configured.return_value = False

    assert provider.is_available() is False


def test_model_discovery_keeps_only_native_multimodal_cards(mocker):
    provider = AXEngineProvider()
    provider.runtime = mocker.MagicMock()
    provider.runtime.ownership.return_value = "external"
    provider.runtime.resident_mapping.return_value = {}
    mocker.patch.object(
        provider,
        "_request_json",
        return_value={
            "object": "list",
            "data": [
                {
                    "id": "gemma-4-vision",
                    "capabilities": {
                        "input": {"image": True},
                        "output": {"text": True},
                    },
                    "context_length": 16384,
                    "max_output_tokens": 2048,
                    "ax_engine": {
                        "native_multimodal_input_supported": True,
                        "model_family": "gemma4",
                        "tensor_format": "safetensors",
                        "support_tier": "native_mlx",
                    },
                },
                {
                    "id": "text-only",
                    "capabilities": {"input": {"image": False}},
                    "ax_engine": {"native_multimodal_input_supported": False},
                },
                {
                    "id": "delegated-vision-claim",
                    "capabilities": {"input": {"image": True}},
                    "ax_engine": {"native_multimodal_input_supported": False},
                },
            ],
        },
    )

    details = provider.list_available_model_details()

    assert [detail["key"] for detail in details] == ["gemma-4-vision"]
    assert details[0]["vision"] is True
    assert details[0]["format"] == "mlx"
    assert details[0]["tensor_format"] == "safetensors"
    assert details[0]["speculation_kind"] == "runtime_managed"


def test_external_server_with_multiple_vision_models_is_hidden(mocker):
    provider = AXEngineProvider()
    provider.runtime = mocker.MagicMock()
    provider.runtime.ownership.return_value = "external"
    provider.runtime.resident_mapping.return_value = {}
    vision_card = {
        "capabilities": {"input": {"image": True}},
        "ax_engine": {"native_multimodal_input_supported": True},
    }
    mocker.patch.object(
        provider,
        "_request_json",
        return_value={
            "data": [
                {**vision_card, "id": "vision-one"},
                {**vision_card, "id": "vision-two"},
            ]
        },
    )

    assert provider.list_available_model_details() == []


def test_metadata_request_uses_inline_image_and_json_object(mocker):
    provider = AXEngineProvider()
    provider.runtime = mocker.MagicMock()
    provider.runtime.ensure_model.return_value = "gemma-4-vision"
    call = mocker.patch.object(
        provider,
        "_request_json",
        side_effect=[
            {
                "data": [
                    {
                        "id": "gemma-4-vision",
                        "capabilities": {"input": {"image": True}},
                        "ax_engine": {"native_multimodal_input_supported": True},
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "keywords": ["Fox", "None"],
                                    "caption": "A fox in a field.",
                                    "title": "Fox in Field",
                                    "alt_text": "A fox standing in a field.",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
                "ax_engine": {"ax_mlx_speculation_profile": "auto"},
            },
        ],
    )

    response = provider.generate_metadata(_metadata_request())

    assert response.success is True
    assert response.keywords == ["Fox"]
    assert response.input_tokens == 120
    assert response.output_tokens == 30
    assert response.inference == {"ax_engine": {"ax_mlx_speculation_profile": "auto"}}
    payload = call.call_args_list[1].kwargs["payload"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "gemma-4-vision"
    assert payload["stream"] is False
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


def test_redirects_are_rejected(mocker):
    provider = AXEngineProvider()
    request = mocker.patch.object(provider.session, "request")
    request.return_value = _Response(
        {}, status_code=302, headers={"Location": "https://example.com"}
    )

    try:
        provider._request_json("GET", "/v1/models")
    except RuntimeError as exc:
        assert "redirect" in str(exc).lower()
    else:
        raise AssertionError("redirect should have failed")
