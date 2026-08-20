import json
from pathlib import Path

from services.axengine_runtime import AXEngineRuntime, discover_ax_models


def _write_candidate(path: Path, *, vision=True, schema="ax.native_model.v1"):
    path.mkdir(parents=True)
    (path / "model-manifest.json").write_text(
        json.dumps({"schema_version": schema, "model_family": "gemma4"}),
        encoding="utf-8",
    )
    config = {
        "model_type": "gemma4",
        "architectures": ["Gemma4ForConditionalGeneration"],
        "quantization": {"bits": 4, "mode": "affine"},
    }
    if vision:
        config["vision_config"] = {"model_type": "gemma4_vision"}
    (path / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_discovery_finds_only_ax_native_vision_packages(tmp_path):
    _write_candidate(tmp_path / "publisher" / "vision-model")
    _write_candidate(tmp_path / "publisher" / "text-model", vision=False)
    _write_candidate(tmp_path / "publisher" / "wrong-schema", schema="other.v1")

    candidates = discover_ax_models(tmp_path)

    assert [candidate.display_name for candidate in candidates] == ["vision-model"]
    assert candidates[0].key.startswith("axlocal-")
    assert candidates[0].quantization == "AFFINE 4-bit"
    assert candidates[0].descriptor()["format"] == "mlx"


def test_discovery_does_not_follow_symlink_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    _write_candidate(outside / "private-model")
    (root / "escaped").symlink_to(outside, target_is_directory=True)

    assert discover_ax_models(root) == []


def test_discovery_requires_absolute_root():
    try:
        discover_ax_models("relative/models")
    except ValueError as exc:
        assert "absolute" in str(exc).lower()
    else:
        raise AssertionError("relative root should have failed")


def test_owned_runtime_restarts_before_loading_different_model(tmp_path, mocker):
    candidate_dir = tmp_path / "model"
    _write_candidate(candidate_dir)
    candidate = discover_ax_models(tmp_path)[0]
    runtime = AXEngineRuntime(str(tmp_path))
    runtime._process = mocker.MagicMock()
    runtime._candidate_to_resident = {"another-key": "old-resident"}

    mocker.patch.object(runtime, "is_server_available", side_effect=[True, True])
    mocker.patch.object(runtime, "_owned_process_is_valid", return_value=True)
    stop = mocker.patch.object(runtime, "stop", return_value=True)
    mocker.patch.object(
        runtime, "find_binary", return_value="/opt/homebrew/bin/ax-engine"
    )
    mocker.patch.object(runtime, "_log_path", return_value=tmp_path / "ax.log")
    process = mocker.MagicMock(pid=1234)
    process.poll.return_value = None
    mocker.patch("services.axengine_runtime.subprocess.Popen", return_value=process)
    observed = mocker.MagicMock()
    observed.create_time.return_value = 10.0
    mocker.patch("services.axengine_runtime.psutil.Process", return_value=observed)
    mocker.patch.object(
        runtime, "_resident_cards", return_value=[{"id": "new-resident"}]
    )

    resident = runtime.start(candidate)

    stop.assert_called_once()
    assert resident == "new-resident"


def test_external_runtime_rejects_multiple_resident_models(tmp_path, mocker):
    runtime = AXEngineRuntime(str(tmp_path))
    mocker.patch.object(runtime, "candidate_for_key", return_value=None)
    mocker.patch.object(
        runtime,
        "_resident_cards",
        return_value=[{"id": "one"}, {"id": "two"}],
    )

    try:
        runtime.ensure_model("one")
    except RuntimeError as exc:
        assert "exactly one" in str(exc).lower()
    else:
        raise AssertionError("multiple external residents should have failed")
