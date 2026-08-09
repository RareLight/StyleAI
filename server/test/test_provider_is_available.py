"""
Health-check tests for provider is_available().
These run on the /health hot path, so false positives/negatives have
user-visible consequences.
"""


# ----- Ollama --------------------------------------------------------------


def test_ollama_is_available_returns_false_when_sdk_missing(mocker):
    mocker.patch("providers.ollama.Client", None)
    from providers.ollama import OllamaProvider

    provider = OllamaProvider()
    assert provider.is_available() is False


def test_ollama_is_available_returns_false_when_sdk_raises(mocker):
    fake_client_class = mocker.MagicMock()
    fake_client_class.return_value.list.side_effect = ConnectionError(
        "connection refused"
    )
    mocker.patch("providers.ollama.Client", fake_client_class)

    from providers.ollama import OllamaProvider

    provider = OllamaProvider()
    assert provider.is_available() is False


def test_ollama_is_available_returns_true_when_sdk_responds(mocker):
    fake_client_class = mocker.MagicMock()
    fake_client_class.return_value.list.return_value = {"models": []}
    mocker.patch("providers.ollama.Client", fake_client_class)

    from providers.ollama import OllamaProvider

    provider = OllamaProvider()
    assert provider.is_available() is True


# ----- LMStudio ------------------------------------------------------------


def test_lmstudio_is_available_returns_false_on_sdk_exception(mocker):
    fake_lms = mocker.patch("providers.lmstudio.lms")
    fake_lms.Client.is_valid_api_host.side_effect = RuntimeError("sdk borked")
    from providers.lmstudio import LMStudioProvider

    provider = LMStudioProvider()
    assert provider.is_available() is False


def test_lmstudio_is_available_uses_auto_discovery_when_default_host_invalid(mocker):
    fake_lms = mocker.patch("providers.lmstudio.lms")
    fake_lms.Client.is_valid_api_host.side_effect = lambda host: (
        host == "127.0.0.1:41343"
    )
    fake_lms.Client.find_default_local_api_host.return_value = "127.0.0.1:41343"
    from providers.lmstudio import LMStudioProvider

    provider = LMStudioProvider()
    assert provider.is_available() is True
    assert provider.host == "127.0.0.1:41343"


def test_lmstudio_rejects_non_loopback_auto_discovery(mocker):
    fake_lms = mocker.patch("providers.lmstudio.lms")
    fake_lms.Client.find_default_local_api_host.return_value = "192.168.1.207:12042"
    fake_lms.Client.is_valid_api_host.return_value = False
    from providers.lmstudio import LMStudioProvider

    provider = LMStudioProvider()
    assert provider.is_available() is False
    assert provider.host == "localhost:1234"
    assert all(
        call.args[0] != "192.168.1.207:12042"
        for call in fake_lms.Client.is_valid_api_host.call_args_list
    )
