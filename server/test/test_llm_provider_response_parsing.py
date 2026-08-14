"""
Provider response-parsing tests: pin behavior of the dict-vs-JSON-string branch
and the malformed-content/empty-content fallbacks. The SDKs are fully mocked.
"""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from providers.base import MetadataGenerationRequest


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _request(**overrides):
    defaults = dict(
        image_data=_jpeg_bytes(),
        uuid="uuid-1",
        provider="test",
        model="test-model",
        generate_keywords=True,
        generate_caption=True,
        generate_title=False,
        generate_alt_text=False,
        language="English",
        temperature=0.2,
        max_tokens=None,
        system_prompt=None,
        user_prompt=None,
        submit_keywords=False,
        submit_folder_names=False,
        existing_keywords=None,
    )
    defaults.update(overrides)
    return MetadataGenerationRequest(**defaults)


# ----- Ollama --------------------------------------------------------------


@pytest.fixture
def ollama_provider(mocker):
    """Build an OllamaProvider with the SDK Client mocked out."""
    from providers.ollama import OllamaProvider

    fake_client = MagicMock(name="FakeOllamaClient")
    mocker.patch("providers.ollama.Client", return_value=fake_client)
    provider = OllamaProvider()
    return provider, fake_client


def test_ollama_dict_response_parsed_through(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {
        "message": {
            "content": '{"keywords": ["Mountain"], "caption": "scene", "title": "T", "alt_text": "A"}'
        }
    }
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.keywords == ["Mountain"]
    assert resp.caption == "scene"


def test_keyword_normalization_removes_placeholder_values(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {
        "message": {
            "content": """{
                "keywords": {
                    "Animals": ["None", "Dog", "N/A"],
                    "Weather": ["Not Applicable"],
                    "Objects": [
                        {"name": "Null"},
                        {"name": "Car", "aliases": ["None", "Automobile"]}
                    ]
                },
                "caption": "scene"
            }"""
        }
    }

    response = provider.generate_metadata(_request())

    assert response.success is True
    assert response.keywords == {
        "Animals": ["Dog"],
        "Objects": [{"name": "Car", "aliases": ["Automobile"]}],
    }


def test_ollama_typed_object_response_parsed(ollama_provider):
    provider, fake_client = ollama_provider
    typed = MagicMock()
    typed.message.content = '{"keywords": ["X"], "caption": "c"}'
    fake_client.chat.return_value = typed
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.keywords == ["X"]


def test_ollama_exposes_provider_timing_and_token_usage(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {
        "message": {"content": '{"keywords": ["Fox"], "caption": "fox"}'},
        "prompt_eval_count": 100,
        "eval_count": 25,
        "total_duration": 2_000_000_000,
        "load_duration": 500_000_000,
        "prompt_eval_duration": 250_000_000,
        "eval_duration": 1_000_000_000,
    }

    response = provider.generate_metadata(_request())

    assert response.success is True
    assert response.input_tokens == 100
    assert response.output_tokens == 25
    assert response.timing == {
        "provider_total_ms": 2000.0,
        "model_load_ms": 500.0,
        "prompt_evaluation_ms": 250.0,
        "inference_ms": 1000.0,
        "tokens_per_second": 25.0,
    }


def test_ollama_empty_content_returns_failure(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {"message": {"content": ""}}
    resp = provider.generate_metadata(_request())
    assert resp.success is False
    assert "Empty response" in resp.error


def test_ollama_malformed_json_returns_failure(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {"message": {"content": "{not json"}}
    resp = provider.generate_metadata(_request())
    assert resp.success is False
    assert "JSON parsing error" in resp.error


def test_ollama_sdk_exception_returns_failure(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.side_effect = RuntimeError("network down")
    resp = provider.generate_metadata(_request())
    assert resp.success is False
    assert "network down" in resp.error


def test_ollama_caption_omitted_when_not_requested(ollama_provider):
    provider, fake_client = ollama_provider
    fake_client.chat.return_value = {
        "message": {"content": '{"keywords": [], "caption": "ignore me"}'}
    }
    resp = provider.generate_metadata(_request(generate_caption=False))
    assert resp.success is True
    assert resp.caption is None


def test_default_metadata_prompts_request_specific_searchable_details(ollama_provider):
    provider, _ = ollama_provider
    request = _request(generate_alt_text=True)

    system_prompt = provider._prepare_system_prompt(request)
    user_prompt = provider._prepare_user_prompt(request)

    assert "up to 12 distinct, highly relevant terms" in system_prompt
    assert "typically 8-12 total across all categories" in system_prompt
    assert "species, breed, plant type, landmark, vehicle type" in system_prompt
    assert "specific term and one useful broader class" in system_prompt
    assert "important actions, interactions, behavior, or events" in system_prompt
    assert "clearly supported season, weather, time of day" in system_prompt
    assert "supplied context as factual context" in system_prompt
    assert "Never output placeholders such as None, N/A, Unknown" in system_prompt
    assert "for a screen-reader user" in system_prompt
    assert "Return up to 12 highly descriptive tags" in user_prompt
    assert "typically 8-12" in user_prompt
    assert "Do not pad the list" in user_prompt


# ----- LMStudio ------------------------------------------------------------


@pytest.fixture
def lmstudio_provider(mocker):
    """Build an LMStudioProvider with the SDK fully mocked."""
    fake_lms = mocker.patch("providers.lmstudio.lms")
    fake_response = MagicMock(name="FakeLMSResponse")
    fake_model = MagicMock(name="FakeLMSModel")
    fake_model.respond.return_value = fake_response
    # Simulate no tokenize attribute so the fallback token-usage path is skipped
    del fake_model.tokenize
    del fake_model.apply_prompt_template

    fake_client = MagicMock(name="FakeLMSClient")
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.files.prepare_image.return_value = MagicMock(name="image_handle")
    fake_client.llm.model.return_value = fake_model
    fake_lms.Client.return_value = fake_client
    fake_lms.Chat.return_value = MagicMock(name="FakeChat")

    from providers.lmstudio import LMStudioProvider

    provider = LMStudioProvider()
    return provider, fake_response


def test_lmstudio_dict_parsed_pass_through(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = {
        "keywords": ["Lake"],
        "caption": "view",
        "title": "T",
        "alt_text": "A",
    }
    fake_response.stats = None
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.keywords == ["Lake"]
    assert resp.caption == "view"


def test_lmstudio_json_string_parsed(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = '{"keywords": ["Lake"], "caption": "view"}'
    fake_response.stats = None
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.keywords == ["Lake"]


def test_lmstudio_malformed_string_returns_failure(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = "not json at all"
    fake_response.stats = None
    resp = provider.generate_metadata(_request())
    assert resp.success is False
    assert "Unexpected non-JSON response" in resp.error


def test_lmstudio_unexpected_type_returns_failure(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = 42  # neither dict nor str
    fake_response.stats = None
    resp = provider.generate_metadata(_request())
    assert resp.success is False
    assert "Unexpected response type" in resp.error


def test_lmstudio_token_usage_from_stats(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = {"keywords": [], "caption": "x"}
    stats = SimpleNamespace(
        prompt_tokens_count=12,
        predicted_tokens_count=34,
        time_to_first_token_sec=0.5,
        tokens_per_second=20.0,
    )
    fake_response.stats = stats
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.input_tokens == 12
    assert resp.output_tokens == 34
    assert resp.timing["time_to_first_token_ms"] == 500.0
    assert resp.timing["tokens_per_second"] == 20.0


def test_lmstudio_passes_configured_max_tokens(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = {"keywords": [], "caption": "x"}
    fake_response.stats = None

    response = provider.generate_metadata(_request(max_tokens=777))

    assert response.success is True
    from providers import lmstudio as lmstudio_module

    fake_model = lmstudio_module.lms.Client.return_value.__enter__.return_value.llm.model.return_value
    assert fake_model.respond.call_args.kwargs["config"] == {
        "temperature": 0.2,
        "maxTokens": 777,
    }
    fake_client = lmstudio_module.lms.Client.return_value.__enter__.return_value
    fake_client.llm.model.assert_called_once_with(
        "test-model",
        ttl=600,
        config={"contextLength": 8192, "flashAttention": True},
    )


def test_lmstudio_zero_tokens_when_no_stats_no_tokenize(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = {"keywords": [], "caption": "x"}
    fake_response.stats = None
    fake_response.usage = None
    resp = provider.generate_metadata(_request())
    assert resp.success is True
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


def test_lmstudio_does_not_tokenize_after_scoped_client_closes(lmstudio_provider):
    provider, fake_response = lmstudio_provider
    fake_response.parsed = {"keywords": [], "caption": "x"}
    fake_response.stats = None
    fake_response.usage = None

    from providers import lmstudio as lmstudio_module

    fake_model = lmstudio_module.lms.Client.return_value.__enter__.return_value.llm.model.return_value
    fake_model.tokenize = MagicMock(return_value=[1, 2, 3])
    fake_model.apply_prompt_template = MagicMock(return_value="prompt")

    response = provider.generate_metadata(_request())

    assert response.success is True
    assert response.input_tokens == 0
    assert response.output_tokens == 0
    fake_model.tokenize.assert_not_called()
    fake_model.apply_prompt_template.assert_not_called()
