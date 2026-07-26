"""Tests for local signature-style summary generation."""

from services import style_summary


def test_summary_uses_bounded_local_request_and_persists_result(mocker, tmp_path):
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
    mocker.patch.object(style_summary, "SUMMARY_FILE", str(tmp_path / "summary.json"))
    response = mocker.Mock(status_code=200)
    response.json.return_value = {"response": "Warm, natural contrast."}
    post = mocker.patch("requests.post", return_value=response)

    assert style_summary.summarize_catalog_styles() == "Warm, natural contrast."
    post.assert_called_once()
    assert post.call_args.kwargs["timeout"] == 30


def test_summary_returns_none_when_local_runner_fails(mocker, tmp_path):
    mocker.patch.object(
        style_summary.catalog_service,
        "list_styles",
        return_value=[{"style_name": "Style", "genre": "scene_general"}],
    )
    mocker.patch.object(style_summary, "SUMMARY_FILE", str(tmp_path / "summary.json"))
    mocker.patch("requests.post", side_effect=TimeoutError("local runner unavailable"))

    assert style_summary.summarize_catalog_styles() is None
