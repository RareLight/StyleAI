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
        raw_settings = {"ToneCurvePV2012": [0.0, 0.0, 128.0, 140.0, 255.0, 255.0]}
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("tone_curve", canonical)
        self.assertIn("point_curve", canonical["tone_curve"])
        self.assertIn("master", canonical["tone_curve"]["point_curve"])
        self.assertEqual(
            canonical["tone_curve"]["point_curve"]["master"],
            [0.0, 0.0, 128.0, 140.0, 255.0, 255.0],
        )

    def test_compute_scene_tags_none(self):
        from services.training import compute_scene_tags

        self.assertEqual(compute_scene_tags(None), [])

    def test_compute_scene_tags_no_model(self):
        from unittest.mock import patch
        from services.training import compute_scene_tags

        with patch("server_lifecycle.get_model", return_value=None):
            self.assertEqual(compute_scene_tags([0.1] * 512), [])


def make_dummy_jpeg(width=100, height=100, color=(128, 128, 128)) -> bytes:
    import io
    from PIL import Image

    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestExposureMetrics(unittest.TestCase):
    def test_compute_exposure_metrics_dark_vs_bright(self):
        from services.training import compute_exposure_metrics

        dark_bytes = make_dummy_jpeg(100, 100, color=(15, 15, 15))
        bright_bytes = make_dummy_jpeg(100, 100, color=(245, 245, 245))

        dark_metrics = compute_exposure_metrics(dark_bytes)
        bright_metrics = compute_exposure_metrics(bright_bytes)

        self.assertIn("exp_luminance_mean", dark_metrics)
        self.assertLess(dark_metrics["exp_luminance_mean"], bright_metrics["exp_luminance_mean"])
        self.assertGreater(dark_metrics["zone_deep_shadows"], 0.5)
        self.assertGreater(bright_metrics["zone_bright_highlights"], 0.5)

    def test_compute_exposure_metrics_invalid_bytes_fallback(self):
        from services.training import compute_exposure_metrics

        metrics = compute_exposure_metrics(b"invalid image bytes")
        self.assertEqual(metrics["exp_luminance_mean"], 0.5)
        self.assertEqual(metrics["exp_contrast"], 0.0)


class TestFocalLengthAndTODBuckets(unittest.TestCase):
    def test_focal_length_bucket_boundaries(self):
        from services.training import focal_length_bucket

        self.assertEqual(focal_length_bucket(None), "unknown")
        self.assertEqual(focal_length_bucket(16.0), "ultra_wide")
        self.assertEqual(focal_length_bucket(24.0), "wide")
        self.assertEqual(focal_length_bucket(50.0), "normal")
        self.assertEqual(focal_length_bucket(85.0), "short_tele")
        self.assertEqual(focal_length_bucket(200.0), "tele")
        self.assertEqual(focal_length_bucket(400.0), "super_tele")

    def test_time_of_day_bucket_local_hours(self):
        from datetime import datetime
        from services.training import time_of_day_bucket

        self.assertEqual(time_of_day_bucket(None), "unknown")

        # Pick specific timestamps and check against their local hour bucket mapping
        ts = 1700000000.0
        bucket = time_of_day_bucket(ts)
        local_hour = datetime.fromtimestamp(ts).hour
        if 5 <= local_hour < 8:
            expected = "dawn"
        elif 8 <= local_hour < 12:
            expected = "morning"
        elif 12 <= local_hour < 17:
            expected = "afternoon"
        elif 17 <= local_hour < 20:
            expected = "evening"
        else:
            expected = "night"
        self.assertEqual(bucket, expected)


class TestBurstClusteringHeroSelection(unittest.TestCase):
    def test_burst_clustering_selects_hero(self):
        from services.predictive_engine import _curate_training_cluster

        # Create 3 examples in a burst (timestamps within 10s, identical embedding)
        emb = [1.0] + [0.0] * 1151
        ex1 = (emb, {"capture_time": 1000.0, "rating": 3, "pick_status": 0}, {"exposure": 0.1})
        ex2 = (emb, {"capture_time": 1002.0, "rating": 5, "pick_status": 1}, {"exposure": 0.2})
        ex3 = (emb, {"capture_time": 1004.0, "rating": 2, "pick_status": 0}, {"exposure": 0.3})

        curated, weights = _curate_training_cluster([ex1, ex2, ex3])
        # Burst clustering should group all three into 1 cluster and choose ex2 as the hero
        self.assertEqual(len(curated), 1)
        self.assertEqual(curated[0][1]["rating"], 5)
        self.assertEqual(weights[0], 1.0)


class TestColorAndHistogramFeatures(unittest.TestCase):
    def test_compute_dominant_colors(self):
        from services.training import compute_dominant_colors

        img_bytes = make_dummy_jpeg(50, 50, color=(200, 50, 50))
        colors = compute_dominant_colors(img_bytes, n_colors=2)
        self.assertIsInstance(colors, list)
        if colors:
            self.assertTrue(colors[0].startswith("#"))

    def test_histogram_signature_and_distance(self):
        from services.training import compute_histogram_signature, histogram_distance

        img1 = make_dummy_jpeg(60, 60, color=(10, 10, 10))
        img2 = make_dummy_jpeg(60, 60, color=(240, 240, 240))

        sig1 = compute_histogram_signature(img1)
        sig2 = compute_histogram_signature(img2)

        self.assertIn("hist_L", sig1)
        dist_same = histogram_distance(sig1, sig1)
        dist_diff = histogram_distance(sig1, sig2)
        self.assertAlmostEqual(dist_same, 0.0, places=4)
        self.assertGreater(dist_diff, 0.0)


if __name__ == "__main__":
    unittest.main()

