"""Tests for services/preset_generator.py — covers XMP preset and ZIP archive generation."""

import io
import zipfile
from services.preset_generator import generate_xmp_preset, create_presets_zip


def test_generate_xmp_preset_basic():
    settings = {
        "Exposure2012": 0.5,
        "Contrast2012": 15,
        "EnableToneCurve": True,
        "CameraProfile": "Adobe Standard",
    }

    xmp = generate_xmp_preset("Cinematic Warm", "StyleAI Presets", settings)

    assert '<x:xmpmeta xmlns:x="adobe:ns:meta/"' in xmp
    assert 'crs:Exposure2012="0.5"' in xmp
    assert 'crs:Contrast2012="15"' in xmp
    assert 'crs:EnableToneCurve="True"' in xmp
    assert 'crs:CameraProfile="Adobe Standard"' in xmp
    assert '<rdf:li xml:lang="x-default">Cinematic Warm</rdf:li>' in xmp
    assert '<rdf:li xml:lang="x-default">StyleAI Presets</rdf:li>' in xmp


def test_create_presets_zip():
    styles = [
        {
            "style_id": "style-1",
            "style_name": "Moody Film",
            "camera_profile": "Leica M Standard",
        },
        {
            "style_id": "style-2",
            "style_name": "Skip Me Without Recipe",
        },
    ]

    def mock_recipe_fetcher(style_id):
        if style_id == "style-1":
            return {"Exposure2012": -0.2, "Contrast2012": 25}
        return None

    zip_bytes = create_presets_zip(styles, mock_recipe_fetcher)
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        namelist = zf.namelist()
        assert len(namelist) == 1
        assert "Leica M Standard Signature Styles/Moody Film.xmp" in namelist[0]
        content = zf.read(namelist[0]).decode("utf-8")
        assert 'crs:Exposure2012="-0.2"' in content
        assert "Moody Film" in content
