from pathlib import Path
from unittest.mock import patch

from utils import image_processing


def test_resolves_homebrew_exiftool_when_launch_path_is_restricted():
    image_processing._resolve_exiftool_path.cache_clear()
    try:
        with (
            patch("utils.image_processing.shutil.which", return_value=None),
            patch(
                "utils.image_processing.os.path.isfile",
                side_effect=lambda path: path == "/opt/homebrew/bin/exiftool",
            ),
            patch("utils.image_processing.os.access", return_value=True),
        ):
            assert (
                image_processing._resolve_exiftool_path()
                == "/opt/homebrew/bin/exiftool"
            )
    finally:
        image_processing._resolve_exiftool_path.cache_clear()


def test_extract_preview_invokes_resolved_exiftool(tmp_path: Path):
    raw_path = tmp_path / "photo.raw"
    raw_path.write_bytes(b"raw")
    image_processing._resolve_exiftool_path.cache_clear()
    try:
        with (
            patch(
                "utils.image_processing._resolve_exiftool_path",
                return_value="/opt/homebrew/bin/exiftool",
            ),
            patch("utils.image_processing.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = b"not-an-image"
            run.return_value.stderr = b""

            assert image_processing.extract_exiftool_preview(str(raw_path)) is None

        run.assert_called_once_with(
            ["/opt/homebrew/bin/exiftool", "-b", "-PreviewImage", str(raw_path)],
            capture_output=True,
            timeout=10,
        )
    finally:
        image_processing._resolve_exiftool_path.cache_clear()
