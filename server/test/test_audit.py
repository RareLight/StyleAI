from pathlib import Path

import pytest

from services import audit


def test_resolve_audit_dir_rejects_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(ValueError):
        audit.resolve_audit_dir(str(tmp_path))


def test_diagnostic_capture_is_lazy_and_bounded(monkeypatch, tmp_path):
    capture_dir = tmp_path / "captures"
    assert not capture_dir.exists()

    monkeypatch.setattr(audit, "MAX_CAPTURE_GROUPS", 2)
    monkeypatch.setattr(audit, "MAX_CAPTURE_BYTES", 1024 * 1024)

    for index in range(3):
        audit.log_diagnostic_image(
            b"jpeg-bytes",
            "indexing",
            f"photo-{index}.jpg",
            output_dir=str(capture_dir),
        )

    originals = list(capture_dir.glob("*_original.*"))
    assert len(originals) == 2


def test_default_capture_path_is_platform_neutral(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(audit.sys, "platform", "darwin")
    assert audit.get_default_audit_dir() == (
        tmp_path / "Library" / "Application Support" / "StyleAI" / "debug"
    )


def test_clear_removes_only_recognized_capture_groups(tmp_path):
    capture_dir = tmp_path / "captures"
    audit.log_diagnostic_image(
        b"jpeg-bytes", "indexing", "photo.jpg", output_dir=str(capture_dir)
    )
    unrelated = capture_dir / "keep-me.txt"
    unrelated.write_text("unrelated")

    result = audit.clear_diagnostic_captures(str(capture_dir))

    assert result["capture_count"] == 0
    assert result["deleted_files"] == 1
    assert unrelated.read_text() == "unrelated"
