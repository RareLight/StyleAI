import sys
import os
import unittest

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from services.training import normalize_develop_settings_for_style


class TestNormalizeDevelopSettingsForStyle(unittest.TestCase):
    def test_partial_hsl_preserved(self):
        # Simulate Lightroom exporting only a Red hue adjustment
        raw_settings = {"HueAdjustmentRed": 15.0}
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("hsl", canonical)
        self.assertIn("red", canonical["hsl"])
        self.assertEqual(canonical["hsl"]["red"]["hue"], 15.0)
        self.assertEqual(canonical["hsl"]["red"]["saturation"], 0.0)
        self.assertEqual(canonical["hsl"]["red"]["luminance"], 0.0)
        # Check that other colors were not added or are not required for red to be present
        self.assertEqual(len(canonical["hsl"]), 1)

    def test_partial_color_grading_preserved(self):
        # Simulate Lightroom exporting only Shadows color grading
        raw_settings = {
            "ColorGradeShadowsHue": 210.0,
            "ColorGradeShadowsSat": 25.0,
        }
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("color_grading", canonical)
        self.assertIn("shadows", canonical["color_grading"])
        self.assertEqual(canonical["color_grading"]["shadows"]["hue"], 210.0)
        self.assertEqual(canonical["color_grading"]["shadows"]["saturation"], 25.0)
        self.assertEqual(canonical["color_grading"]["blending"], 50.0)

    def test_partial_tone_curve_preserved(self):
        # Simulate only Master tone curve exported
        raw_settings = {
            "ToneCurvePV2012": [0.0, 0.0, 128.0, 140.0, 255.0, 255.0]
        }
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("tone_curve", canonical)
        self.assertIn("point_curve", canonical["tone_curve"])
        self.assertIn("master", canonical["tone_curve"]["point_curve"])
        self.assertEqual(canonical["tone_curve"]["point_curve"]["master"], [0.0, 0.0, 128.0, 140.0, 255.0, 255.0])
