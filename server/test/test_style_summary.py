import json

from services import style_summary


def _style_fixture(mocker):
    mocker.patch.object(
        style_summary.catalog_service,
        "list_styles",
        return_value=[
            {
                "style_name": "Warm Landscape",
                "genre": "scene_landscape",
                "description": "Warm highlights",
            }
        ],
    )


def test_summary_uses_configured_local_provider_and_persists_result(
    mocker, monkeypatch, tmp_path
):
    _style_fixture(mocker)
    monkeypatch.setattr(style_summary.config, "DB_PATH", str(tmp_path))
    monkeypatch.setenv("STYLEAI_SUMMARY_MODEL", "lmstudio::local-model")
    response = mocker.Mock(success=True, caption="Warm, natural contrast.", error=None)
    analysis = mocker.Mock()
    analysis.generate_metadata_single.return_value = response
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)

    assert style_summary.summarize_catalog_styles() == "Warm, natural contrast."
    options = analysis.generate_metadata_single.call_args.args[2]
    assert options["provider"] == "lmstudio"
    assert options["model"] == "local-model"
    assert (
        json.loads((tmp_path / "signature_style.json").read_text())["summary"]
        == "Warm, natural contrast."
    )


def test_summary_skips_when_no_local_model_is_configured(mocker, monkeypatch, tmp_path):
    _style_fixture(mocker)
    monkeypatch.setattr(style_summary.config, "DB_PATH", str(tmp_path))
    monkeypatch.delenv("STYLEAI_SUMMARY_MODEL", raising=False)

    assert style_summary.summarize_catalog_styles() is None
    assert not (tmp_path / "signature_style.json").exists()


def test_summary_returns_none_when_local_runner_fails(mocker, monkeypatch, tmp_path):
    _style_fixture(mocker)
    monkeypatch.setattr(style_summary.config, "DB_PATH", str(tmp_path))
    monkeypatch.setenv("STYLEAI_SUMMARY_MODEL", "ollama::local-model")
    analysis = mocker.Mock()
    analysis.generate_metadata_single.side_effect = TimeoutError(
        "local runner unavailable"
    )
    mocker.patch("services.metadata.get_analysis_service", return_value=analysis)

    assert style_summary.summarize_catalog_styles() is None
