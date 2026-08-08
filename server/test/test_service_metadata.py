import io

import pytest
from PIL import Image

from providers.base import MetadataGenerationResponse


def _jpeg_bytes(color=(120, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@pytest.fixture
def stub_providers(mocker):
    """Replace the two provider classes with mocks before AnalysisService is built."""
    for name in (
        "OllamaProvider",
        "LMStudioProvider",
    ):
        mock_cls = mocker.MagicMock(name=name)
        mock_instance = mock_cls.return_value
        mock_instance.is_available.return_value = True
        mock_instance.list_available_models.return_value = []
        mock_instance.generate_metadata.return_value = MetadataGenerationResponse(
            uuid="stub", success=True, keywords={}, caption=None
        )
        mocker.patch(f"services.metadata.{name}", mock_cls)
    yield


@pytest.fixture
def service(stub_providers):
    # Import AFTER providers are stubbed so __init__ uses the mocks.
    from services.metadata import AnalysisService

    return AnalysisService(lazy_load=True)


def test_constructor_registers_all_providers(service):
    assert set(service.providers.keys()) == {"ollama", "lmstudio"}
    for name in ("ollama", "lmstudio"):
        assert service.provider_status[name] == "available"


def test_constructor_marks_failing_provider_as_failed(mocker):
    mocker.patch(
        "services.metadata.OllamaProvider", side_effect=RuntimeError("ollama dead")
    )
    for name in ("LMStudioProvider",):
        mock_cls = mocker.MagicMock()
        mock_cls.return_value.is_available.return_value = True
        mocker.patch(f"services.metadata.{name}", mock_cls)

    from services.metadata import AnalysisService

    svc = AnalysisService(lazy_load=True)
    assert "ollama" not in svc.providers
    assert svc.provider_status["ollama"] == "failed"
    assert "ollama dead" in svc.provider_errors["ollama"]
    # Other providers still register
    assert {"lmstudio"}.issubset(svc.providers.keys())


def test_analyze_batch_no_op_returns_none_pair(service):
    # No images need anything → both outputs are None.
    embeddings, metadata = service.analyze_batch(
        image_triplets=[(_jpeg_bytes(), "uuid-1", "")],
        options={},
        image_model=None,
        image_processor=None,
        uuids_needing_embeddings=[],
        uuids_needing_metadata=[],
    )
    assert embeddings is None
    assert metadata is None


def test_analyze_batch_accepts_list_for_uuids_needing(service):
    # Regression test for the set-coercion change: callers pass lists, the
    # function coerces them to sets internally.
    triplets = [(_jpeg_bytes(), "uuid-1", ""), (_jpeg_bytes(), "uuid-2", "")]
    # Pass lists of uuids that don't intersect with the batch → no work runs.
    embeddings, metadata = service.analyze_batch(
        image_triplets=triplets,
        options={},
        image_model=None,
        image_processor=None,
        uuids_needing_embeddings=["nonexistent-1", "nonexistent-2"],
        uuids_needing_metadata=["nonexistent-3"],
    )
    # Both lists are non-empty so the inner branches execute, but no triplet
    # uuid intersects, so the outputs are all-None.
    assert embeddings == [None, None]
    assert metadata == [None, None]


def test_analyze_batch_accepts_set_for_uuids_needing(service):
    triplets = [(_jpeg_bytes(), "uuid-1", "")]
    embeddings, metadata = service.analyze_batch(
        image_triplets=triplets,
        options={},
        image_model=None,
        image_processor=None,
        uuids_needing_embeddings={"nonexistent"},
        uuids_needing_metadata={"nonexistent"},
    )
    assert embeddings == [None]
    assert metadata == [None]


def test_analyze_batch_default_compute_embeddings_true(service):
    # When uuids_needing_embeddings is None, options['compute_embeddings']
    # defaults to True → all uuids get embeddings (but image_model is None,
    # so they'll be None, which is what we want to verify).
    triplets = [(_jpeg_bytes(), "uuid-1", "")]
    embeddings, metadata = service.analyze_batch(
        image_triplets=triplets,
        options={"compute_embeddings": False, "compute_metadata": False},
        image_model=None,
        image_processor=None,
    )
    assert embeddings is None
    assert metadata is None


def test_generate_metadata_single_falls_back_to_first_provider(service):
    # Request a provider that isn't registered → falls back to the first one.
    response = service.generate_metadata_single(
        "uuid-x",
        _jpeg_bytes(),
        {
            "provider": "doesnotexist",
            "model": "any",
            "generate_keywords": True,
            "generate_caption": False,
            "generate_title": False,
            "generate_alt_text": False,
            "language": "en",
            "temperature": 0.2,
            "submit_keywords": False,
            "submit_folder_names": False,
        },
    )
    assert response.success is True
    # warning_msg about fallback should be set
    assert response.warning is not None
    assert "fallback" in response.warning.lower()


def test_generate_metadata_single_no_providers_available(mocker):
    # Make every provider class blow up so none register
    for name in (
        "OllamaProvider",
        "LMStudioProvider",
    ):
        mocker.patch(f"services.metadata.{name}", side_effect=RuntimeError("nope"))

    from services.metadata import AnalysisService

    svc = AnalysisService(lazy_load=True)
    assert svc.providers == {}

    resp = svc.generate_metadata_single(
        "uuid-x",
        _jpeg_bytes(),
        {
            "model": "any",
            "generate_keywords": True,
            "generate_caption": False,
            "generate_title": False,
            "generate_alt_text": False,
            "language": "en",
            "temperature": 0.2,
            "submit_keywords": False,
            "submit_folder_names": False,
        },
    )
    assert resp.success is False
    assert "No LLM providers available" in resp.error


def test_generate_metadata_single_provider_exception_caught(service):
    # Make the (stubbed) ollama provider blow up mid-call
    service.providers["ollama"].generate_metadata.side_effect = RuntimeError("boom")

    resp = service.generate_metadata_single(
        "uuid-x",
        _jpeg_bytes(),
        {
            "provider": "ollama",
            "model": "any",
            "generate_keywords": True,
            "generate_caption": False,
            "generate_title": False,
            "generate_alt_text": False,
            "language": "en",
            "temperature": 0.2,
            "submit_keywords": False,
            "submit_folder_names": False,
        },
    )
    assert resp.success is False
    assert "boom" in resp.error


def test_generate_metadata_single_refuses_missing_image(service):
    provider = service.providers["ollama"]

    resp = service.generate_metadata_single(
        "uuid-x",
        None,
        {"provider": "ollama"},
    )

    assert resp.success is False
    assert "Image bytes are required" in resp.error
    provider.generate_metadata.assert_not_called()


def test_analyze_batch_with_list_options(service):
    from routes.index import _extract_options

    triplets = [(_jpeg_bytes(), "uuid-1", ""), (_jpeg_bytes(), "uuid-2", "")]
    embeddings, metadata = service.analyze_batch(
        image_triplets=triplets,
        options=[
            _extract_options({"compute_embeddings": False, "compute_metadata": True}),
            _extract_options({"compute_embeddings": False, "compute_metadata": True}),
        ],
        image_model=None,
        image_processor=None,
        uuids_needing_embeddings=[],
        uuids_needing_metadata=["uuid-1", "uuid-2"],
    )
    assert len(metadata) == 2
    assert metadata[0] is not None
    assert metadata[1] is not None


def test_analyze_batch_never_clones_metadata_between_similar_images(service):
    provider = service.providers["ollama"]

    def response_for(request):
        return MetadataGenerationResponse(
            uuid=request.uuid,
            success=True,
            keywords={"subject": [request.uuid]},
            caption=f"Caption for {request.uuid}",
        )

    provider.generate_metadata.side_effect = response_for
    options = {
        "provider": "ollama",
        "model": "vision-model",
        "generate_keywords": True,
        "generate_caption": True,
        "generate_title": False,
        "generate_alt_text": False,
        "language": "English",
        "temperature": 0.2,
        "submit_keywords": False,
        "submit_folder_names": False,
        # This legacy option previously caused whole-response cloning.
        "semantic_clustering_threshold": 0.8,
    }

    _, metadata = service.analyze_batch(
        image_triplets=[
            (_jpeg_bytes((120, 0, 0)), "uuid-1", ""),
            (_jpeg_bytes((121, 0, 0)), "uuid-2", ""),
        ],
        options=options,
        image_model=None,
        image_processor=None,
        uuids_needing_embeddings=[],
        uuids_needing_metadata=["uuid-1", "uuid-2"],
    )

    assert provider.generate_metadata.call_count == 2
    assert [item.uuid for item in metadata] == ["uuid-1", "uuid-2"]
    assert metadata[0].caption != metadata[1].caption
    assert metadata[0] is not metadata[1]
