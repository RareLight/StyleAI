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
            "vision": True,
            "speculation_kind": "complete_model",
            "label": "Model 7B Vision — GGUF · google · q4_k_m",
        }
    ]
    session.close.assert_called_once_with()


def test_lmstudio_displays_safetensors_models_as_mlx(mocker):
    model = _downloaded_model(
        key="mlx-community/model@4bit",
        path="mlx-community/model-MLX/model-4bit.safetensors",
    )
    model.info.format = "safetensors"
    provider, _ = _provider_with_client(mocker, [model])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    details = provider.list_available_model_details()

    assert details[0]["format"] == "mlx"
    assert details[0]["label"] == "Model 7B Vision — MLX · mlx-community · 4bit"
    assert "SAFETENSORS" not in details[0]["label"]


def test_lmstudio_draft_candidates_include_downloaded_nonvision_models(mocker):
    vision_model = _downloaded_model()
    text_draft = _downloaded_model(
        key="google/model-draft@q4_k_m",
        vision=False,
        path="google/model-draft-GGUF/model-draft-Q4_K_M.gguf",
    )
    provider, _ = _provider_with_client(mocker, [vision_model, text_draft])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    details = provider.list_available_draft_model_details()

    assert [detail["key"] for detail in details] == [
        "google/model@q4_k_m",
        "google/model-draft@q4_k_m",
    ]
    assert [detail["vision"] for detail in details] == [True, False]


def test_lmstudio_hides_dedicated_mtp_sidecars_from_main_and_draft_lists(mocker):
    vision_model = _downloaded_model()
    mtp_draft = _downloaded_model(
        key="google/model-GGUF/mtp-model-Q8_0.gguf",
        vision=True,
        path="google/model-GGUF/mtp-model-Q8_0.gguf",
    )
    provider, _ = _provider_with_client(mocker, [vision_model, mtp_draft])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    main_models = provider.list_available_model_details()
    draft_models = provider.list_available_draft_model_details()
    unavailable = provider.list_unavailable_speculation_details()

    assert [detail["key"] for detail in main_models] == ["google/model@q4_k_m"]
    assert [detail["key"] for detail in draft_models] == ["google/model@q4_k_m"]
    assert draft_models[0]["speculation_kind"] == "full_draft"
    assert [detail["key"] for detail in unavailable] == [mtp_draft.model_key]
    assert unavailable[0]["speculation_capability"] == "unsupported"


def test_lmstudio_keeps_integrated_mtp_model_in_main_list(mocker):
    integrated = _downloaded_model(
        key="unsloth/qwen3.6-27b-mtp@q4_k_m",
        path="unsloth/Qwen3.6-27B-MTP-GGUF/Qwen3.6-27B-MTP-Q4_K_M.gguf",
    )
    provider, _ = _provider_with_client(mocker, [integrated])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    details = provider.list_available_model_details()

    assert [detail["key"] for detail in details] == [integrated.model_key]
    assert details[0]["speculation_kind"] == "mtp_integrated"
    assert details[0]["speculation_capability"] == "unknown"
    assert details[0]["speculation_configuration"] == "automatic_model_load"


def test_lmstudio_does_not_treat_mlx_mtp_name_as_integrated_capability(mocker):
    packaged = _downloaded_model(
        key="mlx-works/gemma-4-mtp@4bit",
        path="mlx-works/gemma-4-mtp/model.safetensors",
    )
    packaged.info.format = "safetensors"
    provider, _ = _provider_with_client(mocker, [packaged])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    detail = provider.list_available_model_details()[0]

    assert detail["speculation_kind"] == "mtp_packaged_unverified"
    assert detail["speculation_capability"] == "unsupported"
    assert provider.list_available_draft_model_details() == []


def test_lmstudio_prefers_native_mtp_capability_over_filename(mocker):
    model = _downloaded_model(key="qwen/model@q4", path="qwen/model/model-Q4.gguf")
    provider, _ = _provider_with_client(mocker, [model])
    response = MagicMock()
    response.json.return_value = {
        "models": [
            {
                "type": "llm",
                "key": "qwen/model",
                "variants": ["qwen/model@q4"],
                "format": "gguf",
                "capabilities": {"mtp": True},
                "architecture": "qwen35",
            }
        ]
    }
    session = MagicMock()
    session.get.return_value = response
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    detail = provider.list_available_model_details()[0]

    assert detail["speculation_kind"] == "mtp_integrated"
    assert detail["speculation_capability"] == "supported"
    assert detail["speculation_reason"] == "lmstudio_native_capability"
    assert detail["architecture"] == "qwen35"


def test_lmstudio_preflight_rejects_cross_format_full_draft(mocker):
    target = _downloaded_model()
    draft = _downloaded_model(
        key="mlx-community/model-small@4bit",
        vision=False,
        path="mlx-community/model-small-MLX/model-small-4bit.safetensors",
    )
    draft.info.format = "safetensors"
    provider, _ = _provider_with_client(mocker, [target, draft])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    result = provider.preflight_speculation(
        target.model_key, "full_draft", draft.model_key
    )

    assert result["capability"] == "unsupported"
    assert result["reason"] == "format_mismatch"


def test_lmstudio_draft_token_matching_does_not_hide_unrelated_names(mocker):
    drafting_model = _downloaded_model(
        key="publisher/drafting-assistant@q4",
        path="publisher/drafting-assistant-GGUF/model-Q4.gguf",
    )
    provider, _ = _provider_with_client(mocker, [drafting_model])
    session = MagicMock()
    session.get.side_effect = RuntimeError("native endpoint unavailable")
    mocker.patch("providers.lmstudio.requests.Session", return_value=session)

    assert [detail["key"] for detail in provider.list_available_model_details()] == [
        "publisher/drafting-assistant@q4"
    ]


def test_lmstudio_native_metadata_request_refuses_non_loopback_host(mocker):
    provider, _ = _provider_with_client(mocker, [])
    session_factory = mocker.patch("providers.lmstudio.requests.Session")

    assert provider._list_native_models("example.com:1234") == {}
    session_factory.assert_not_called()
