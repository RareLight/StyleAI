from types import SimpleNamespace
from unittest.mock import MagicMock


def _downloaded_model(
    *,
    key="google/model@q4_k_m",
    vision=True,
    path="google/model-GGUF/model-Q4_K_M.gguf",
):
    info = SimpleNamespace(
        vision=vision,
        display_name="Model 7B Vision",
        path=path,
        params_string="7B",
        format="gguf",
        size_bytes=4_000_000_000,
    )
    return SimpleNamespace(model_key=key, vision=vision, path=path, info=info)


def _provider_with_client(mocker, models):
    fake_lms = mocker.patch("providers.lmstudio.lms")
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.llm.list_downloaded.return_value = models
    fake_lms.Client.return_value = client

    from providers.lmstudio import LMStudioProvider

    provider = LMStudioProvider()
    mocker.patch.object(provider, "_resolve_host", return_value="127.0.0.1:1234")
    return provider, client


def test_lmstudio_enriches_sdk_keys_with_native_quantization(mocker):
    vision_model = _downloaded_model()
    text_draft = _downloaded_model(
        key="google/model-draft@q4_k_m",
        vision=False,
        path="google/model-draft-GGUF/model-draft-Q4_K_M.gguf",
    )
    provider, client = _provider_with_client(mocker, [vision_model, text_draft])
    response = MagicMock()
    response.json.return_value = {
        "models": [
            {
                "type": "llm",
                "publisher": "google",
                "key": "google/model",
                "display_name": "Model 7B Vision",
                "params_string": "7B",
                "format": "gguf",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "size_bytes": 4_000_000_000,
                "variants": ["google/model@q4_k_m"],
                "selected_variant": "google/model@q4_k_m",
            }
        ]
    }
    session = MagicMock()
    session.get.return_value = response
    session_factory = mocker.patch(
        "providers.lmstudio.requests.Session", return_value=session
    )

    details = provider.list_available_model_details()

    assert [detail["key"] for detail in details] == ["google/model@q4_k_m"]
    assert details[0]["label"] == "Model 7B Vision — Q4_K_M · GGUF · google"
    assert details[0]["bits_per_weight"] == 4
    assert details[0]["selected_variant"] == "google/model@q4_k_m"
    client.llm.list_downloaded.assert_called_once_with()
    session_factory.assert_called_once_with()
    assert session.trust_env is False
    session.get.assert_called_once_with(
        "http://127.0.0.1:1234/api/v1/models",
        headers={"Accept": "application/json"},
        timeout=2.0,
        allow_redirects=False,
    )
    session.close.assert_called_once_with()


def test_lmstudio_uses_sdk_identity_when_native_metadata_is_unavailable(mocker):
    provider, _ = _provider_with_client(mocker, [_downloaded_model()])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    details = provider.list_available_model_details()

    assert details == [
        {
            "key": "google/model@q4_k_m",
            "display_name": "Model 7B Vision",
            "publisher": "google",
            "params_string": "7B",
            "format": "gguf",
            "selected_variant": "q4_k_m",
            "size_bytes": 4_000_000_000,
            "label": "Model 7B Vision — GGUF · google · q4_k_m",
        }
    ]
    session.close.assert_called_once_with()


def test_lmstudio_native_metadata_request_refuses_non_loopback_host(mocker):
    provider, _ = _provider_with_client(mocker, [])
    session_factory = mocker.patch("providers.lmstudio.requests.Session")

    assert provider._list_native_models("example.com:1234") == {}
    session_factory.assert_not_called()
